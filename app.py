import os
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

# Kun jeres egen shop må kalde dette API. Tilføj flere domæner her hvis
# I får en staging-URL eller lignende senere.
CORS(app, resources={r"/*": {"origins": [
    "https://mscards.dk",
    "https://www.mscards.dk",
]}})

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_db():
    """Åbner en ny forbindelse til Postgres-databasen på Render."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    """Opretter tabellerne, hvis de ikke findes endnu. Køres ved opstart."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS customer_avatars (
                    customer_id TEXT PRIMARY KEY,
                    avatar_url TEXT NOT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS customer_display_names (
                    customer_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS set_cards (
                    id SERIAL PRIMARY KEY,
                    set_name TEXT NOT NULL,
                    is_promo BOOLEAN NOT NULL DEFAULT FALSE,
                    cardmarket_name TEXT NOT NULL,
                    card_number TEXT NOT NULL,
                    image_url TEXT NOT NULL,
                    UNIQUE (set_name, cardmarket_name, card_number)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS customer_collection (
                    customer_id TEXT NOT NULL,
                    card_id INTEGER NOT NULL REFERENCES set_cards(id),
                    owned BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (customer_id, card_id)
                );
            """)
        conn.commit()


@app.route("/health", methods=["GET"])
def health():
    """Simpelt endpoint til at tjekke at appen kører. Render bruger dette."""
    return jsonify({"status": "ok"})


@app.route("/avatar", methods=["POST"])
def save_avatar():
    """
    Gemmer eller opdaterer en kundes valgte avatar.
    Forventer JSON: { "customer_id": "1234567890", "avatar_url": "https://..." }
    """
    data = request.get_json(silent=True) or {}
    customer_id = str(data.get("customer_id", "")).strip()
    avatar_url = str(data.get("avatar_url", "")).strip()

    if not customer_id or not avatar_url:
        return jsonify({"error": "customer_id og avatar_url er påkrævet"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO customer_avatars (customer_id, avatar_url, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (customer_id)
                DO UPDATE SET avatar_url = EXCLUDED.avatar_url, updated_at = EXCLUDED.updated_at;
            """, (customer_id, avatar_url, datetime.utcnow()))
        conn.commit()

    return jsonify({"success": True, "customer_id": customer_id, "avatar_url": avatar_url})


@app.route("/avatar", methods=["GET"])
def get_avatar():
    """
    Henter en kundes valgte avatar.
    Kaldes som: /avatar?customer_id=1234567890
    """
    customer_id = str(request.args.get("customer_id", "")).strip()
    if not customer_id:
        return jsonify({"error": "customer_id er påkrævet"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT avatar_url FROM customer_avatars WHERE customer_id = %s;",
                (customer_id,),
            )
            row = cur.fetchone()

    if not row:
        return jsonify({"avatar_url": None})

    return jsonify({"avatar_url": row["avatar_url"]})


@app.route("/name", methods=["POST"])
def save_name():
    """
    Gemmer eller opdaterer en kundes selvvalgte visningsnavn.
    Bruges kun når Shopify ikke kender kundens navn (fx nye passwordless-konti).
    Forventer JSON: { "customer_id": "1234567890", "display_name": "Mads" }
    """
    data = request.get_json(silent=True) or {}
    customer_id = str(data.get("customer_id", "")).strip()
    display_name = str(data.get("display_name", "")).strip()

    if not customer_id or not display_name:
        return jsonify({"error": "customer_id og display_name er påkrævet"}), 400

    # Simpel længdebegrænsning, så ingen kan smide en roman ind i feltet
    display_name = display_name[:40]

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO customer_display_names (customer_id, display_name, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (customer_id)
                DO UPDATE SET display_name = EXCLUDED.display_name, updated_at = EXCLUDED.updated_at;
            """, (customer_id, display_name, datetime.utcnow()))
        conn.commit()

    return jsonify({"success": True, "customer_id": customer_id, "display_name": display_name})


@app.route("/name", methods=["GET"])
def get_name():
    """
    Henter en kundes selvvalgte visningsnavn.
    Kaldes som: /name?customer_id=1234567890
    """
    customer_id = str(request.args.get("customer_id", "")).strip()
    if not customer_id:
        return jsonify({"error": "customer_id er påkrævet"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT display_name FROM customer_display_names WHERE customer_id = %s;",
                (customer_id,),
            )
            row = cur.fetchone()

    if not row:
        return jsonify({"display_name": None})

    return jsonify({"display_name": row["display_name"]})


@app.route("/set-cards/bulk-upsert", methods=["POST"])
def bulk_upsert_set_cards():
    """
    Kaldes af populate_set_v3.py efter hvert kort er uploadet til Shopify.
    Body: { "set_name": "...", "is_promo": false, "cardmarket_name": "...",
             "card_number": "...", "image_url": "..." }
    """
    data = request.get_json(silent=True) or {}
    required = ["set_name", "cardmarket_name", "card_number", "image_url"]
    if not all(data.get(k) for k in required):
        return jsonify({"error": f"{required} er påkrævet"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO set_cards (set_name, is_promo, cardmarket_name, card_number, image_url)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (set_name, cardmarket_name, card_number)
                DO UPDATE SET image_url = EXCLUDED.image_url;
            """, (data["set_name"], bool(data.get("is_promo", False)),
                  data["cardmarket_name"], data["card_number"], data["image_url"]))
        conn.commit()

    return jsonify({"success": True})


@app.route("/set-cards", methods=["GET"])
def get_set_cards():
    """
    Henter kortkataloget for ét sæt.
    /set-cards?set=Crown%20Zenith&promo=true
    """
    set_name = request.args.get("set", "").strip()
    include_promo = request.args.get("promo", "false").lower() == "true"
    if not set_name:
        return jsonify({"error": "set er påkrævet"}), 400

    set_names = [set_name]
    if include_promo:
        set_names.append(f"{set_name} Promos")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, set_name, is_promo, cardmarket_name, card_number, image_url
                FROM set_cards WHERE set_name = ANY(%s)
                ORDER BY is_promo, card_number;
            """, (set_names,))
            rows = cur.fetchall()

    return jsonify({"cards": rows})


@app.route("/collection", methods=["GET"])
def get_collection():
    """/collection?customer_id=123&set=Base%20Set&promo=true"""
    customer_id = request.args.get("customer_id", "").strip()
    set_name = request.args.get("set", "").strip()
    include_promo = request.args.get("promo", "false").lower() == "true"
    if not customer_id or not set_name:
        return jsonify({"error": "customer_id og set er påkrævet"}), 400

    set_names = [set_name]
    if include_promo:
        set_names.append(f"{set_name} Promos")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT cc.card_id FROM customer_collection cc
                JOIN set_cards sc ON sc.id = cc.card_id
                WHERE cc.customer_id = %s AND cc.owned = TRUE AND sc.set_name = ANY(%s);
            """, (customer_id, set_names))
            owned_ids = [r["card_id"] for r in cur.fetchall()]

    return jsonify({"owned_card_ids": owned_ids})


@app.route("/collection", methods=["POST"])
def toggle_collection():
    """Body: { customer_id, card_id, owned (true/false) }"""
    data = request.get_json(silent=True) or {}
    customer_id = str(data.get("customer_id", "")).strip()
    card_id = data.get("card_id")
    owned = bool(data.get("owned", True))
    if not customer_id or card_id is None:
        return jsonify({"error": "customer_id og card_id er påkrævet"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO customer_collection (customer_id, card_id, owned, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (customer_id, card_id)
                DO UPDATE SET owned = EXCLUDED.owned, updated_at = EXCLUDED.updated_at;
            """, (customer_id, card_id, owned, datetime.utcnow()))
        conn.commit()

    return jsonify({"success": True})


# Opret tabellerne så snart appen starter op på Render.
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

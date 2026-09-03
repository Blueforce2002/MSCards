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


# Opret tabellerne så snart appen starter op på Render.
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

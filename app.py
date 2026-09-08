import os
import re
import hmac
import hashlib
import base64
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
import requests

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
                ALTER TABLE set_cards ADD COLUMN IF NOT EXISTS set_code TEXT;
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS customer_wishlist (
                    id SERIAL PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    card_id INTEGER REFERENCES set_cards(id),
                    manual_name TEXT,
                    added_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE (customer_id, card_id)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS customer_notify_consent (
                    customer_id TEXT PRIMARY KEY,
                    email TEXT,
                    consent BOOLEAN NOT NULL DEFAULT FALSE,
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                ALTER TABLE customer_wishlist ADD COLUMN IF NOT EXISTS notified BOOLEAN NOT NULL DEFAULT FALSE;
            """)
            cur.execute("""
                ALTER TABLE customer_wishlist ADD COLUMN IF NOT EXISTS notified_at TIMESTAMP;
            """)
            cur.execute("""
                ALTER TABLE customer_notify_consent ADD COLUMN IF NOT EXISTS email TEXT;
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
             "card_number": "...", "image_url": "...", "set_code": "BS" }
    """
    data = request.get_json(silent=True) or {}
    required = ["set_name", "cardmarket_name", "card_number", "image_url"]
    if not all(data.get(k) for k in required):
        return jsonify({"error": f"{required} er påkrævet"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO set_cards (set_name, is_promo, cardmarket_name, card_number, image_url, set_code)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (set_name, cardmarket_name, card_number)
                DO UPDATE SET image_url = EXCLUDED.image_url, set_code = EXCLUDED.set_code;
            """, (data["set_name"], bool(data.get("is_promo", False)),
                  data["cardmarket_name"], data["card_number"], data["image_url"],
                  data.get("set_code")))
        conn.commit()

    return jsonify({"success": True})


@app.route("/set-cards/backfill-set-code", methods=["POST"])
def backfill_set_code():
    """
    Opdaterer KUN set_code for allerede-eksisterende kort i et sæt - rører
    hverken billeder eller Shopify. Bruges når kort blev hentet, før
    set_code fandtes som felt.
    Body: { "set_name": "Base Set", "set_code": "BS" }
    """
    data = request.get_json(silent=True) or {}
    set_name = data.get("set_name")
    set_code = data.get("set_code")
    if not set_name or not set_code:
        return jsonify({"error": "set_name og set_code er påkrævet"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE set_cards SET set_code = %s
                WHERE set_name = %s AND (set_code IS NULL OR set_code = '');
            """, (set_code, set_name))
            updated = cur.rowcount
        conn.commit()

    return jsonify({"success": True, "updated": updated})


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


@app.route("/wishlist", methods=["GET"])
def get_wishlist():
    customer_id = request.args.get("customer_id", "").strip()
    if not customer_id:
        return jsonify({"error": "customer_id er påkrævet"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT w.id, w.card_id, w.manual_name, w.added_at,
                       sc.cardmarket_name, sc.card_number, sc.image_url, sc.set_name
                FROM customer_wishlist w
                LEFT JOIN set_cards sc ON sc.id = w.card_id
                WHERE w.customer_id = %s
                ORDER BY w.added_at DESC;
            """, (customer_id,))
            rows = cur.fetchall()

    return jsonify({"wishlist": rows})


@app.route("/wishlist", methods=["POST"])
def add_to_wishlist():
    """Body: { customer_id, card_id } ELLER { customer_id, manual_name } for fritekst."""
    data = request.get_json(silent=True) or {}
    customer_id = str(data.get("customer_id", "")).strip()
    card_id = data.get("card_id")
    manual_name = (data.get("manual_name") or "").strip() or None

    if not customer_id or (not card_id and not manual_name):
        return jsonify({"error": "customer_id + (card_id eller manual_name) er påkrævet"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            if card_id:
                cur.execute("""
                    INSERT INTO customer_wishlist (customer_id, card_id)
                    VALUES (%s, %s) ON CONFLICT (customer_id, card_id) DO NOTHING;
                """, (customer_id, card_id))
            else:
                cur.execute("""
                    INSERT INTO customer_wishlist (customer_id, manual_name)
                    VALUES (%s, %s);
                """, (customer_id, manual_name))
        conn.commit()

    return jsonify({"success": True})


@app.route("/wishlist/bulk", methods=["POST"])
def add_to_wishlist_bulk():
    """Body: { customer_id, card_ids: [1,2,3] } - tilføjer flere på én gang, springer dubletter over."""
    data = request.get_json(silent=True) or {}
    customer_id = str(data.get("customer_id", "")).strip()
    card_ids = data.get("card_ids") or []
    if not customer_id or not card_ids:
        return jsonify({"error": "customer_id og card_ids er påkrævet"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            for cid in card_ids:
                cur.execute("""
                    INSERT INTO customer_wishlist (customer_id, card_id)
                    VALUES (%s, %s) ON CONFLICT (customer_id, card_id) DO NOTHING;
                """, (customer_id, cid))
        conn.commit()

    return jsonify({"success": True, "added": len(card_ids)})


@app.route("/wishlist/<int:wishlist_id>", methods=["DELETE"])
def remove_from_wishlist(wishlist_id):
    customer_id = request.args.get("customer_id", "").strip()
    if not customer_id:
        return jsonify({"error": "customer_id er påkrævet"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM customer_wishlist WHERE id = %s AND customer_id = %s;
            """, (wishlist_id, customer_id))
        conn.commit()

    return jsonify({"success": True})


@app.route("/cards/search", methods=["GET"])
def search_cards():
    """/cards/search?q=charizard - bruges til autocomplete ved manuel tilføjelse."""
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"cards": []})

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, cardmarket_name, card_number, set_name, image_url
                FROM set_cards
                WHERE cardmarket_name ILIKE %s
                ORDER BY cardmarket_name
                LIMIT 10;
            """, (f"%{q}%",))
            rows = cur.fetchall()

    return jsonify({"cards": rows})


@app.route("/my-sets", methods=["GET"])
def get_my_sets():
    """Sæt kunden har mindst ét markeret kort i - bruges til 'Mine samlinger'."""
    customer_id = request.args.get("customer_id", "").strip()
    if not customer_id:
        return jsonify({"error": "customer_id er påkrævet"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT sc.set_name,
                       COUNT(*) FILTER (WHERE cc.owned) AS owned_count,
                       (SELECT COUNT(*) FROM set_cards sc2 WHERE sc2.set_name = sc.set_name) AS total_count
                FROM customer_collection cc
                JOIN set_cards sc ON sc.id = cc.card_id
                WHERE cc.customer_id = %s AND cc.owned = TRUE
                GROUP BY sc.set_name;
            """, (customer_id,))
            rows = cur.fetchall()

    return jsonify({"sets": rows})


@app.route("/notify-consent", methods=["GET"])
def get_notify_consent():
    customer_id = request.args.get("customer_id", "").strip()
    if not customer_id:
        return jsonify({"error": "customer_id er påkrævet"}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT consent FROM customer_notify_consent WHERE customer_id = %s;", (customer_id,))
            row = cur.fetchone()
    response = jsonify({"consent": bool(row["consent"]) if row else True, "has_preference": row is not None})
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


@app.route("/notify-consent", methods=["POST"])
def set_notify_consent():
    data = request.get_json(silent=True) or {}
    customer_id = str(data.get("customer_id", "")).strip()
    consent = bool(data.get("consent", False))
    email = (data.get("email") or "").strip()
    if not customer_id:
        return jsonify({"error": "customer_id er påkrævet"}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO customer_notify_consent (customer_id, email, consent, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (customer_id) DO UPDATE SET
                    email = EXCLUDED.email, consent = EXCLUDED.consent, updated_at = EXCLUDED.updated_at;
            """, (customer_id, email, consent, datetime.utcnow()))
        conn.commit()
    return jsonify({"success": True})


RESEND_API_KEY = os.environ.get("RESEND_API_KEY")

# Bruges til at slå det specifikke produkt-link op, når en ønske-mail sendes.
SHOP = os.environ.get("SHOPIFY_STORE")
SHOPIFY_CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET")


def find_product_url(cardmarket_name, card_number, set_code):
    """
    Leder efter et Shopify-produkt hvis titel PRÆCIST matcher jeres egen
    navnekonvention, fx "Alakazam (BS 001)" - ikke bare et løst navne-match,
    som ville ramme forkert på tværs af sæt (fx andre Alakazam-kort).
    Returnerer None hvis intet findes, eller hvis set_code mangler.
    """
    if not SHOP or not SHOPIFY_CLIENT_ID or not SHOPIFY_CLIENT_SECRET or not set_code:
        return None
    try:
        token_resp = requests.post(
            f"https://{SHOP}/admin/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": SHOPIFY_CLIENT_ID,
                "client_secret": SHOPIFY_CLIENT_SECRET,
            },
            timeout=10,
        )
        token = token_resp.json().get("access_token")
        if not token:
            return None

        # Jeres rigtige produkter bruger kortnummeret UDEN foranstillede
        # nuller (fx "Alakazam (BS 1)"), mens vores katalog gemmer det som
        # PokéWallet leverer det (fx "001") - så vi fjerner nullerne her.
        card_number_no_zeros = str(int(card_number)) if card_number.isdigit() else card_number
        expected_title = f"{cardmarket_name} ({set_code} {card_number_no_zeros})"

        headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
        graphql_query = """
        query findProduct($searchQuery: String!) {
          products(first: 20, query: $searchQuery) {
            edges { node { title handle } }
          }
        }
        """
        # Bevidst kun søgt på selve navnet, UDEN parenteser - Shopifys
        # søgesprog bruger parenteser til gruppering, så de ville forstyrre
        # selve søgningen. Den præcise, sikre filtrering sker i stedet
        # bagefter i Python, hvor vi kræver 100% match på hele titlen.
        search_query = f'title:*{cardmarket_name}*'
        resp = requests.post(
            f"https://{SHOP}/admin/api/2026-01/graphql.json",
            headers=headers,
            json={"query": graphql_query, "variables": {"searchQuery": search_query}},
            timeout=10,
        )
        edges = resp.json().get("data", {}).get("products", {}).get("edges", [])
        print(f"🔍 Leder efter præcis titel: '{expected_title}'")
        print(f"🔍 Kandidater fundet i Shopify: {[e['node']['title'] for e in edges]}")

        for edge in edges:
            if edge["node"]["title"].strip() == expected_title:
                return f"https://{SHOP}/products/{edge['node']['handle']}"
        return None
    except Exception as e:
        print(f"⚠️ Kunne ikke slå produkt-URL op: {e}")
        return None


RESEND_TEMPLATE_ID = os.environ.get("RESEND_TEMPLATE_ID")


def send_email(to_email, subject, html_body=None, template_variables=None):
    """
    Bruger jeres publicerede Resend-skabelon (RESEND_TEMPLATE_ID sat) hvis den
    findes - ellers falder tilbage til rå HTML, så det stadig virker, selvom
    skabelonen ikke er koblet på endnu.
    """
    payload = {"from": "MSCards <kontakt@mscards.dk>", "to": [to_email]}

    if RESEND_TEMPLATE_ID and template_variables:
        payload["template"] = {"id": RESEND_TEMPLATE_ID, "variables": template_variables}
    else:
        payload["subject"] = subject
        payload["html"] = html_body

    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    return response.status_code, response.text


def _run_notify_check(card_id):
    """Selve logikken - finder matches, sender mails, markerer som kontaktet."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT cardmarket_name, card_number, image_url, set_name, set_code FROM set_cards WHERE id = %s;", (card_id,))
            card = cur.fetchone()
            if not card:
                return {"error": "Kort ikke fundet"}, 404

            cur.execute("""
                SELECT w.id AS wishlist_id, ncc.email
                FROM customer_wishlist w
                JOIN customer_notify_consent ncc ON ncc.customer_id = w.customer_id
                WHERE w.card_id = %s
                  AND (w.notified = FALSE OR w.notified_at < NOW() - INTERVAL '3 days')
                  AND ncc.consent = TRUE AND ncc.email IS NOT NULL AND ncc.email != '';
            """, (card_id,))
            matches = cur.fetchall()

    product_url = find_product_url(card["cardmarket_name"], card["card_number"], card["set_code"]) or "https://mscards.dk"

    sent = 0
    errors = []
    for m in matches:
        subject = f"{card['cardmarket_name']} er tilbage på lager hos MSCards!"
        html = f"""
            <p>Hej!</p>
            <p>Godt nyt — <strong>{card['cardmarket_name']} ({card['card_number']})</strong> fra din ønskeliste er nu på lager hos MSCards.</p>
            <p><img src="{card['image_url']}" alt="" style="max-width:200px;"></p>
            <p><a href="{product_url}">Køb den nu!</a></p>
        """
        variables = {
            "CARD_NAME": card["cardmarket_name"],
            "CARD_NUMBER": card["card_number"],
            "SET_NAME": card["set_name"],
            "IMAGE_URL": card["image_url"],
            "PRODUCT_URL": product_url,
        }
        status, body = send_email(m["email"].strip().lower(), subject, html_body=html, template_variables=variables)
        if status in (200, 201):
            sent += 1
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE customer_wishlist SET notified = TRUE, notified_at = NOW() WHERE id = %s;", (m["wishlist_id"],))
                conn.commit()
        else:
            errors.append({"email": m["email"], "status": status, "body": body})

    return {"matches_found": len(matches), "emails_sent": sent, "errors": errors}, 200


@app.route("/notify-check", methods=["GET"])
def notify_check():
    """Manuel test-udgave: /notify-check?card_id=123"""
    card_id = request.args.get("card_id")
    if not card_id:
        return jsonify({"error": "card_id er påkrævet"}), 400
    body, status = _run_notify_check(card_id)
    return jsonify(body), status


@app.route("/notify-check-by-title", methods=["POST"])
def notify_check_by_title():
    """
    Kaldes af jeres daglige sync-script, hver gang et produkt sættes til
    'active' med lager > 0. Body: { "title": "Alakazam (BS 1)" }
    Finder selv det rigtige kort ud fra titlen - scriptet skal ikke kende
    vores interne card_id.
    """
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()

    match = re.match(r"^(.+?)\s+\(([A-Za-z0-9]+)\s+(\d+)\)$", title)
    if not match:
        return jsonify({"skipped": True, "reason": "Titel matcher ikke det forventede format (Navn (KODE NR))"}), 200

    name, code, number = match.groups()

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, card_number FROM set_cards WHERE cardmarket_name = %s AND set_code = %s;
            """, (name, code))
            candidates = cur.fetchall()

    card_id = None
    for c in candidates:
        stored = c["card_number"]
        if stored.isdigit() and number.isdigit() and int(stored) == int(number):
            card_id = c["id"]
            break

    if not card_id:
        return jsonify({"skipped": True, "reason": "Intet matchende kort fundet i kataloget", "parsed": {"name": name, "code": code, "number": number}}), 200

    body, status = _run_notify_check(card_id)
    return jsonify(body), status


SHOPIFY_WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET")


def verify_shopify_webhook(request):
    """Bekræfter at kaldet reelt kommer fra Shopify, ikke bare nogen der har
    gættet URL'en. Shopify signerer hver webhook-besked med jeres hemmelige
    nøgle - matcher signaturen ikke, afviser vi kaldet."""
    if not SHOPIFY_WEBHOOK_SECRET:
        return False
    hmac_header = request.headers.get("X-Shopify-Hmac-Sha256", "")
    digest = hmac.new(SHOPIFY_WEBHOOK_SECRET.encode("utf-8"), request.get_data(), hashlib.sha256).digest()
    computed_hmac = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed_hmac, hmac_header)


@app.route("/webhooks/order-paid", methods=["POST"])
def webhook_order_paid():
    """
    Kaldes automatisk af Shopify, hver gang en ordre bliver betalt. Fjerner
    de købte kort fra kundens ønskeliste, så vores "prøv igen efter 3 dage"-
    logik aldrig sender en ny mail om noget, de allerede har fået fat i.
    """
    if not verify_shopify_webhook(request):
        return jsonify({"error": "Ugyldig signatur"}), 401

    order = request.get_json(silent=True) or {}
    customer_id = str((order.get("customer") or {}).get("id", "")).strip()
    if not customer_id:
        return jsonify({"skipped": True, "reason": "Ingen kunde tilknyttet ordren"}), 200

    removed = 0
    with get_db() as conn:
        with conn.cursor() as cur:
            for line_item in order.get("line_items", []):
                title = (line_item.get("title") or "").strip()
                match = re.match(r"^(.+?)\s+\(([A-Za-z0-9]+)\s+(\d+)\)$", title)
                if not match:
                    continue
                name, code, number = match.groups()

                cur.execute("""
                    SELECT id, card_number FROM set_cards WHERE cardmarket_name = %s AND set_code = %s;
                """, (name, code))
                candidates = cur.fetchall()

                for c in candidates:
                    stored = c["card_number"]
                    if stored.isdigit() and number.isdigit() and int(stored) == int(number):
                        cur.execute("""
                            DELETE FROM customer_wishlist WHERE customer_id = %s AND card_id = %s;
                        """, (customer_id, c["id"]))
                        removed += cur.rowcount
                        break
        conn.commit()

    return jsonify({"success": True, "removed_from_wishlist": removed})


@app.route("/set-cards/cleanup-duplicate-names", methods=["POST"])
def cleanup_duplicate_names():
    """
    Engangs-oprydning: retter kort, der blev gemt med en overflødig,
    dobbelt "(KODE NR)"-parentes i cardmarket_name FØR navne-fixet i
    populate_set_v3.py (fx "Tropius (PBL 001)" i stedet for bare
    "Tropius" for et kort med card_number "001").

    For hvert "beskidt" kort:
    - Findes der allerede en "ren" udgave af samme kort (samme sæt +
      nummer, uden den overflødige parentes) MED et gyldigt billede,
      slettes den beskidte dublet.
    - Findes der IKKE en ren udgave, renses navnet i stedet for at
      slette rækken, så kortet ikke forsvinder helt.

    Body: { "set_name": "PBL" } (valgfrit - udelades, ryddes ALLE sæt op)
    """
    data = request.get_json(silent=True) or {}
    set_name_filter = data.get("set_name")

    with get_db() as conn:
        with conn.cursor() as cur:
            if set_name_filter:
                cur.execute("SELECT * FROM set_cards WHERE set_name = %s;", (set_name_filter,))
            else:
                cur.execute("SELECT * FROM set_cards;")
            all_cards = cur.fetchall()

            deleted = 0
            renamed = 0

            for card in all_cards:
                match = re.search(r"^(.*)\s+\(([^)]*)\)$", card["cardmarket_name"])
                if not match:
                    continue
                clean_name, paren_content = match.groups()
                if card["card_number"] not in paren_content:
                    continue  # parentesen er ikke den overflødige type - lad den være

                clean_counterpart = None
                for other in all_cards:
                    if (other["id"] != card["id"]
                            and other["set_name"] == card["set_name"]
                            and other["card_number"] == card["card_number"]
                            and other["cardmarket_name"] == clean_name):
                        clean_counterpart = other
                        break

                if clean_counterpart and clean_counterpart.get("image_url"):
                    # Flyt evt. "ejet i samling"- og "ønske"-markeringer over
                    # på det rene kort, før det beskidte slettes - ellers
                    # nægter databasen (med rette) at slette, og en kundes
                    # markering ville gå tabt.
                    cur.execute("""
                        INSERT INTO customer_collection (customer_id, card_id, owned, updated_at)
                        SELECT customer_id, %s, owned, updated_at FROM customer_collection WHERE card_id = %s
                        ON CONFLICT (customer_id, card_id) DO NOTHING;
                    """, (clean_counterpart["id"], card["id"]))
                    cur.execute("DELETE FROM customer_collection WHERE card_id = %s;", (card["id"],))

                    cur.execute("""
                        INSERT INTO customer_wishlist (customer_id, card_id, added_at, notified, notified_at)
                        SELECT customer_id, %s, added_at, notified, notified_at FROM customer_wishlist WHERE card_id = %s
                        ON CONFLICT (customer_id, card_id) DO NOTHING;
                    """, (clean_counterpart["id"], card["id"]))
                    cur.execute("DELETE FROM customer_wishlist WHERE card_id = %s;", (card["id"],))

                    cur.execute("DELETE FROM set_cards WHERE id = %s;", (card["id"],))
                    deleted += 1
                else:
                    cur.execute("""
                        UPDATE set_cards SET cardmarket_name = %s WHERE id = %s;
                    """, (clean_name, card["id"]))
                    renamed += 1

        conn.commit()

    return jsonify({"success": True, "deleted_duplicates": deleted, "renamed_in_place": renamed})


# Opret tabellerne så snart appen starter op på Render.
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

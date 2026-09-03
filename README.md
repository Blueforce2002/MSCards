# MSCards Backend

Lille Flask-API der gemmer kundedata for mscards.dk — starter med
avatar-valg, udvides senere med ønskeliste og samlings-data.

## Endpoints

- `GET /health` — tjekker at appen kører
- `POST /avatar` — gemmer/opdaterer en kundes avatar
  - Body: `{"customer_id": "123", "avatar_url": "https://..."}`
- `GET /avatar?customer_id=123` — henter en kundes avatar

## Deploy på Render

1. Læg denne mappe i en ny GitHub-repo (fx `mscards-backend`)
2. På Render: **New → Web Service** → forbind repo'en
3. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
4. Opret en Postgres-database på Render (**New → Postgres**)
5. Kopiér databasens **Internal Database URL**
6. På web service'en: **Environment** → tilføj env var:
   - `DATABASE_URL` = (den URL du kopierede)
7. Deploy. Tjek at `https://<dit-service-navn>.onrender.com/health` svarer `{"status": "ok"}`

## Test lokalt (valgfrit)

```
pip install -r requirements.txt
export DATABASE_URL=postgresql://...
python app.py
```

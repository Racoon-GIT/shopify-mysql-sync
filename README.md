# shopify-mysql-sync

Sync giornaliero prodotti Shopify -> MySQL via GraphQL Admin API.
Popola la tabella `online_products` letta da Feed-Exporter, SEO-PILOT e altri progetti.

## Stack

- **Python 3** + Flask + Gunicorn
- **Shopify GraphQL Admin API** (~10 prodotti/pagina, ~75 chiamate per ~750 prodotti attivi)
- **MySQL** (database condiviso `racoon`)
- **Deploy**: Render.com (web service free tier, Frankfurt)

## Servizi

| Servizio | Tipo | Descrizione |
|----------|------|-------------|
| `shopify-mysql-sync` | Web Service | Sync giornaliero, triggerato da Scheduler |
| `reset-variants` | Cron Job | Reset varianti con backup inventory (solo manuale) |

## Endpoints

| Endpoint | Metodo | Descrizione |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/trigger` | GET/POST | Avvia sync (richiede `TRIGGER_SECRET`) |
| `/api/status` | GET | Stato ultima sincronizzazione |
| `/` | GET | Info servizio |

## Env vars

```
SHOPIFY_DOMAIN          # es. racoon-lab.myshopify.com
SHOPIFY_TOKEN           # Access token Shopify Admin API
SHOPIFY_API_VERSION     # Default: 2024-04
DB_HOST                 # MySQL host
DB_USER                 # MySQL user
DB_PASS                 # MySQL password (NON DB_PASSWORD)
DB_NAME                 # MySQL database name
TRIGGER_SECRET          # Token auth per /api/trigger (opzionale)
PRODUCT_IDS             # Solo per reset_variants (comma-separated)
```

## Flusso

```
Scheduler (03:00 Roma) --> GET /api/trigger?secret=XXX --> 202
                           |
                           v
                    Background thread:
                    1. Fetch prodotti via GraphQL (filtro per tag)
                    2. Upsert su online_products
                    3. Track price_history se prezzi cambiano
                    4. Rimuovi varianti scomparse
```

## Test

```bash
pytest
```

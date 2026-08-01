# shopify-mysql-sync

Daily Shopify -> MySQL product sync via GraphQL Admin API.
Populates the `online_products` table read by Feed-Exporter, SEO-PILOT and other projects.

## Stack

- **Python 3** + Flask + Gunicorn
- **Shopify GraphQL Admin API** (~10 products/page, ~75 calls for ~750 active products)
- **MySQL** (shared database `racoon`)
- **Deploy**: Render.com (web service free tier, Frankfurt)

## Services

| Service | Type | Description |
|----------|------|-------------|
| `shopify-mysql-sync` | Web Service | Daily sync, triggered by Scheduler |
| `reset-variants` | Cron Job | Variant reset with inventory backup (manual only) |

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/trigger` | GET/POST | Starts sync. Accepts `Authorization: Bearer <TRIGGER_SECRET>` (preferred), `X-Trigger-Secret` header, or `?secret=` query param (deprecated, still supported) |
| `/api/status` | GET | Status of last sync |
| `/` | GET | Service info |

## Env vars

```
SHOPIFY_DOMAIN          # e.g. racoon-lab.myshopify.com
SHOPIFY_TOKEN           # Shopify Admin API access token
SHOPIFY_API_VERSION     # Default: 2024-04
DB_HOST                 # MySQL host
DB_USER                 # MySQL user
DB_PASS                 # MySQL password (NOT DB_PASSWORD)
DB_NAME                 # MySQL database name
TRIGGER_SECRET          # Auth token for /api/trigger (optional)
PRODUCT_IDS             # Only for reset_variants (comma-separated)
```

## Flow

```
Scheduler (03:00 Rome) --> GET /api/trigger (Authorization: Bearer XXX) --> 202
                           |  (?secret=XXX still accepted but deprecated,
                           |   pending SERVER updating the Scheduler job record)
                           v
                    Background thread:
                    1. Fetch products via GraphQL (filter by tag)
                    2. Upsert into online_products
                    3. Track price_history if prices change
                    4. Remove disappeared variants
```

## Test

```bash
pytest
```

# shopify-mysql-sync

Daily **Shopify → MySQL** sync via GraphQL. Feeds the `online_products` table read by multiple downstream projects (authoritative reader list: `../docs/shared-database.md`).

## Stack
- Python + Flask + Shopify GraphQL → Render FREE web service `shopify-sync-ws`.
- DB: shared MySQL `racoon`.

## Trigger architecture

- Render Web Service `shopify-sync-ws` (FREE, Frankfurt).
- Triggered by Scheduler at **03:00 Rome** via `GET /api/trigger`, auth `Authorization: Bearer` (`?secret=` deprecated but still accepted, pending SERVER updating the job record — handoff 2026-08-01).
- `/api/trigger` returns **immediate `202`**, sync runs in a background thread (~60s).
- `/api/status` to check last execution status.
- KeepAlive from Scheduler `*/5 2-3 * * *` to wake the service before the sync.

## Always-on rules

1. **`online_products` is read by multiple downstream projects**: do NOT change the schema without checking consumers — authoritative reader list in `../docs/shared-database.md`.
2. **Mandatory tag filter**: the sync only includes products with tags `sneakers personalizzate`, `scarpe personalizzate`, `ciabatte personalizzate`, `stivali personalizzati`. Changing the list impacts all consumers.
3. **Sleep 0.5s between mutating REST calls** (POST/DELETE), exponential backoff on 429/502-504.
4. **GraphQL preferred**: ~75 calls vs ~9000 with REST. 10 products/page, 1000 points/query limit.
5. **Coverage test** (`/usr/bin/python3 -m pytest`): mock Shopify/MySQL (no external deps). Files: `test_sync.py`, `test_app.py`, `test_reset.py`.
6. **Keepalive must precede the trigger**: same pattern as Feed-Exporter. Reversed = cold-start fail.

## Env vars

```
SHOPIFY_DOMAIN, SHOPIFY_TOKEN, SHOPIFY_API_VERSION    # Shopify Admin API
DB_HOST, DB_USER, DB_PASS, DB_NAME                     # MySQL `racoon` (NB: DB_PASS, NOT DB_PASSWORD)
TRIGGER_SECRET                                          # Auth for /api/trigger (optional but recommended)
PRODUCT_IDS                                             # Only for reset_variants (comma-separated)
```

## `reset_variants` (separate script)

- Variant recreation with inventory backup/restore.
- Render cron `reset-variants` with **impossible** schedule (`0 0 31 2 *`) — manual trigger only from Render dashboard.
- Requires `PRODUCT_IDS` env var (comma-separated) set **before** trigger.
- Detailed docs in `ANALISI_FUNZIONALE_reset_variants.md`.

## Lazy docs — read ONLY if the task matches

| Current task trigger | File to read |
|---|---|
| **Schema change on `online_products`** or finding writer/reader of a `racoon` table | `../docs/shared-database.md` |
| **Modifying keepalive/trigger time** or understanding the downstream cron pipeline (Feed-Exporter depends on you) | `../docs/dependencies-graph.md` |
| **Rotating Shopify token** | `../docs/shared-secrets.md` |

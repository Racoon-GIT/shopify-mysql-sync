# CLAUDE.md — shopify-mysql-sync

Sync giornaliero Shopify → MySQL via GraphQL. Alimenta `online_products` letta da Feed-Exporter, SEO-PILOT e altri.

## Shared Database (CRITICO)
La tabella `online_products` è letta da 5+ progetti. NON modificare lo schema senza verificare i consumatori a valle (Feed-Exporter, SEO-PILOT, Price_Bulk-UPDT).

## Trigger
- Render Web Service `shopify-mysql-sync` (FREE, Frankfurt)
- Triggerato da Scheduler alle 03:00 Roma via `GET /api/trigger`
- `/api/trigger` ritorna `202` immediato, sync gira in background thread (~60s)
- `/api/status` per verificare stato ultima esecuzione
- KeepAlive dallo Scheduler `*/5 2-3 * * *` per svegliare il servizio prima del sync

## Shopify API
- GraphQL: ~75 chiamate (vs ~9000 con REST) — 10 prodotti/pagina, limite 1000 punti/query
- Sleep 0.5s tra chiamate REST mutanti (POST/PUT/DELETE), exponential backoff su 429/502-504
- Filtra solo prodotti con tag: `sneakers personalizzate`, `scarpe personalizzate`, `ciabatte personalizzate`, `stivali personalizzati`

## reset_variants
- Script separato per ricreazione varianti con backup/restore inventory
- Cron Render `reset-variants` con schedule impossibile (`0 0 31 2 *`) — solo trigger manuale dalla dashboard Render
- Richiede `PRODUCT_IDS` env var (comma-separated) impostata PRIMA del trigger
- Documentazione dettagliata in `ANALISI_FUNZIONALE_reset_variants.md`

## Env vars
```
SHOPIFY_DOMAIN, SHOPIFY_TOKEN, SHOPIFY_API_VERSION  # Shopify Admin API
DB_HOST, DB_USER, DB_PASS, DB_NAME                   # MySQL condiviso `racoon` (DB_PASS, NON DB_PASSWORD)
TRIGGER_SECRET                                        # Auth per /api/trigger (opzionale ma raccomandato)
PRODUCT_IDS                                           # Solo per reset_variants (comma-separated)
```

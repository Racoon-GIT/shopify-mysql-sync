# TRACKER — shopify-mysql-sync

## Current state
Pipeline: shipped · item: daily Shopify → MySQL sync (writer of `online_products`) · plan: — · gate-1: — · gate-2: Ale 2026-08-18 (TLS pilot closed)

**Phase**: in production on Render (`shopify-sync-ws`, FREE, Frankfurt), triggered by Scheduler at 03:00 Rome. Ale, 2026-08-21: this is a daily programme and **must be monitored** — monitoring is the next item.

**Next**:
- [ ] Monitoring of the daily run: decide the signal (Render logs query, Scheduler outcome, or an alert on a missed run) and where it surfaces — `/api/status` is in-memory and is NOT a proof of a run (reset on every deploy/restart); proof lives in Render logs.
- [ ] Once defined, record the oracle of "today's sync ran" here and in `CLAUDE.md` §Operations.

**Active constraints & decisions**:
- TLS to `racoon`: `ssl_verify_identity=True` with `DB_CA_CERT` (commit `97b653b`, `src/db.py` `build_ssl_config`, shared by sync and `reset_variants`). Negative control measured 2026-08-18: a host name absent from the SAN fails with `certificate verify failed`; the encrypt-only branch accepts both — the red is the verification, not the network.
- Not transferable as-is to the Node consumers (`mysql2` compares against `localhost` when connecting by IP): `../docs/shared-database.md`.
- Keep the service FREE-tier safe: in-memory state, 256 MB (`../docs/deploy-conventions.md`).

**Tried & discarded (still relevant)**:
- Reading `/api/status` after a deploy as evidence of the last run — wiped by the deploy itself (2026-08-18).

**Key refs**: `CLAUDE.md` · `src/db.py` · `../docs/shared-database.md` · `../docs/dependencies-graph.md` (Scheduler → sync → Feed-Exporter order)

## Archive
Previous TRACKER (Italian, TLS pilot narrative): `git show 69751ef^:TRACKER.md`.

# TRACKER — shopify-mysql-sync

## Current state
Pipeline: gate-2 · item: sync-lag watchdog `/api/lag-check` (option (d)) · plan: docs/sync-lag-plan.md · gate-1: Ale 2026-09-23 · gate-2: mutate-verify 3/3 PASS, `GATE:push` granted

**Phase**: the daily sync stays in production on Render (`shopify-sync-ws`, FREE, Frankfurt, 03:00 Rome). The open item is the **monitoring** Ale asked for on 2026-08-21 — now built as the sync-lag watchdog, committed and **not deployed**.

**Next**:
- [ ] `GATE:push` for `e81197d`/`d572957`/`fd4622d` (via coordinator), then the **live negative control**: fire `lag` and `drift` on purpose against real Shopify + the real mirror. The suite refusals and the mutants are receipts; the live half is not obtainable before deploy.
- [ ] Register the Scheduler job + **two** alert rules (`response_match` on `$.ok` and on `$.checked`), schedule avoiding 03:00–03:15. In-lane (`Scheduler` `POST /api/jobs`, `POST /api/jobs/{id}/alerts`) — no SERVER handoff.
- [ ] Verify live grants for `shopify-sync-ws`'s DB user on `stock`, `sku_root`, `scheduler_jobs`, `scheduler_job_logs`. The code degrades honestly if they are missing, but degraded is not verified.
- [ ] Handoff row 2026-08-22 (SERVER → SVILUPPO) stays `in-progress`. Delete it only after the live control passes, in our own commit.
- [ ] Give SERVER the two corrections for `infra-hosts.md`: worst case is a measured **51.2 h**, not 24 h; and pre-2026-06-16 `log.DataOra` is DST-aware `Europe/Rome`, not fixed CEST.

**Active constraints & decisions**:
- **`ok` is true IFF `checked` is true AND `lag_count == 0` AND `drift_count == 0`** — one invariant, asserted table-driven across every status. `skipped` is `ok:false` on purpose: a watchdog must not be able to go green because a rule in another repo was wired wrong.
- **`lag` vs `drift`**: `lag` = created after the last sync, not yet mirrored (the handoff's defect). `drift` = the sync had time and did not absorb it — this is what covers a 202 accepted then crashed, i.e. the **14 unlogged July nights** the measurement found.
- **Real-stock lookup is batched** (`get_real_stock_bulk`, 500 skus/chunk): all three counts are true totals; `MAX_REPORTED_ENTRIES = 50` is only a serialisation limit. Per-pair contract: no stock rows → **0** (a fact), failed chunk → **None** (unknown), seeded **per chunk after it succeeds**.
- Read-only everywhere: no second writer into `online_products` (single-writer table, `../docs/shared-database.md`).
- TLS to `racoon`: `ssl_verify_identity=True` with `DB_CA_CERT` (`src/db.py` `build_ssl_config`).
- Keep the service FREE-tier safe: in-memory state, 256 MB.

**Tried & discarded (still relevant)**:
- Reading `/api/status` as evidence of the last run — in-memory, and **any** push (docs-only included) auto-deploys, restarts the service and wipes it. A reset `/api/status` is evidence of a restart, never of a missed run.
- Options (a) Make alert, (b) targeted mirror, (c) more frequent sync, (e) webhook: priced in `docs/sync-lag-plan.md` §3 and not chosen — ~15 events a year does not pay for them.
- Amending a running subagent by message: it arrives indistinguishable from prompt injection and two executors correctly refused it. Put amendments in a fresh opening prompt.

**Key refs**: `docs/sync-lag-plan.md` (measurement + option map + build record) · `docs/gate-2-log.md` (mutant receipts) · `src/lag_check.py` · `../docs/shared-database.md` · `../docs/dependencies-graph.md`

## Archive
Previous TRACKER (TLS pilot narrative, then the daily-programme state): `git show 163aee3:TRACKER.md`.

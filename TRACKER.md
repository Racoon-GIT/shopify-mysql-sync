# TRACKER — shopify-mysql-sync

## Current state
Pipeline: gate-2 · item: sync-lag watchdog `/api/lag-check` (option (d)), fix of the last-sync read `5d8bc6a` · plan: docs/sync-lag-plan.md · gate-1: Ale 2026-09-23 · gate-2: mutate-verify 5/5 PASS on `5d8bc6a` clean-tree (docs/gate-2-log.md), `/ship` + `GATE:push` pending

**Phase**: the daily sync stays in production on Render (`shopify-sync-ws`, FREE, Frankfurt, 03:00 Rome). The open item is the **monitoring** Ale asked for on 2026-08-21: the sync-lag watchdog, deployed 2026-09-23 at `39b12ab`. A defect found live is fixed at `5d8bc6a`; the fix is committed and **not pushed**.

**Live checks, 2026-09-23**, run through the Scheduler job `f5798d47-49bf-4fc2-bd00-3967af175293` `Shopify-MySQL-Sync_LagCheck` (`enabled:false`, headers copied Scheduler→Scheduler, no value printed):
- **S1 green**: `?hours=168` → `ok:false, checked:false, inconclusive/page_cap_reached`. This is the first live refusal; the cause was 248 products in 168 h, above the 200-product page cap.
- **S2 red**: `?hours=48` → `clean`, but `last_sync_source:assumed_schedule`. Cause: `DATE_FORMAT('%%…%%s')` next to a bound parameter. mysql-connector's `(%s)` regex consumed the parameter, raised "Not enough parameters", and the code fell back. The observed-log path had never worked anywhere. Fixed with `UNIX_TIMESTAMP`, and all four watchdog statements now also run through the real driver's substitution. Coordinator authorised the fix in-lane on 2026-09-24.

**Next**:
- [ ] `/ship` + `GATE:push` via the coordinator for `39b12ab..HEAD`, then deploy verification.
- [ ] S2 again: `last_sync_source` must be `scheduler_job_logs` and `last_sync_utc` must equal the latest `Shopify-MySQL-Sync` log row.
- [ ] S3–S5: rules `response_match $.ok eq false`, `response_match $.checked eq false`, `fail` (`it-services@racoon-lab.it`, internal). Exercise them: `?hours=168` should fire both, `?hours=48` should fire none. Then `enabled:true`, cron `0 8-21 * * *` Europe/Rome.
- [ ] `docs/sync-lag-plan.md` §7: record the live receipts. Record G1 as a named gap: `lag`/`drift` never fired live, Ale declined a Shopify write, and the first real occurrence closes it. Record G2 as documented-not-measured: `RacAdmin` has ALL on `racoon.*`, but the service's `DB_USER` is not read.
- [ ] `../docs/shared-secrets.md`: the job is a third consumer of the shopify-sync `TRIGGER_SECRET` (census). Add an exposure-register row for `.claude/settings.local.json` (`DB_USER=root` + `DB_PASS`). The file must not be opened, and the secret must not be rotated (frozen).
- [ ] Handoff row 2026-08-22 (SERVER → SVILUPPO) stays `in-progress`. Delete it only after S1–S5 are green and the gap is written.
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

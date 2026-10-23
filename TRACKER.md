# TRACKER — shopify-mysql-sync

<!-- RULES (parent CLAUDE.md rule 5 — Current-state protocol):
     - Agents read ONLY "Current state" at session start (Read with limit ~45 lines).
     - REWRITE "Current state" entirely at session end (max 45 lines). Never append to it.
     - Superseded detail goes to TRACKER-ARCHIVE.md (or git log for committed work).
     - Keep "Tried & discarded" entries while still relevant: they prevent re-proposing dead ends. -->

## Current state
Pipeline: shipped · item: sync-lag watchdog `/api/lag-check` (option (d)) · plan: docs/sync-lag-plan.md · gate-1: Ale 2026-09-23 · gate-2: mutate-verify 5/5 PASS on `5d8bc6a` clean-tree (docs/gate-2-log.md), `/ship` run, pushed `45acf9f` 2026-09-25, deploy verified by the live S2 read

**Phase**: the daily sync runs in production on Render (`shopify-sync-ws`, FREE, Frankfurt, 03:00 Rome). The sync-lag watchdog is live and watched.
- Scheduler job `f5798d47-49bf-4fc2-bd00-3967af175293` `Shopify-MySQL-Sync_LagCheck`: cron `0 8-21 * * *` Europe/Rome, `?hours=48`, timeout 60 s.
- Three rules, to `it-services@racoon-lab.it`, dedup 720: `$.status matches "lag|drift"` (a gap, rule `24ce17cd`), `$.checked eq false` (could not look), `fail` (could not reach). The gap rule was `$.ok eq false` until 2026-10-11 09:15Z: by the `ok` invariant it co-fired with `checked` on every inconclusive run (2 mails per event) and its dedup let an inconclusive mask a real gap for 12 h. Checked offline with Scheduler's own `evaluateResponseMatch` @`cbd2f8b`: exactly 1 mail per `ok:false` body (inconclusive, skipped, lag, drift, lag+drift), 0 on clean. Old config: `{"path":"$.ok","operator":"eq","value":false}`. The new gap rule has never fired live (G1).
- First scheduled run: 2026-09-25 14:00:26Z, success.
- Handoff row 2026-08-22 (SERVER → SVILUPPO) closed in `a525849`. Receipts and gaps are in `docs/sync-lag-plan.md` §7.

**Next**:
- [x] **LagCheck timeouts, fixed 2026-10-23 with a pre-warm job, not a longer timeout**: 7 `Timeout after 60s` in 10-16..23. Cause (Render logs): FREE spin-down wake. Failing runs 10-22 11:01Z / 16:01Z: container `Running` 58-70 s after the call, no `lag-check` log line. Fix: Scheduler job `787da930-3740-4ceb-b5ed-64226137055d` `Shopify-MySQL-Sync_LagCheck_Warmup` (type `scheduler`, GET `/health`, `55 7-20 * * *` Europe/Rome, timeout 60, retries 0, no alerts), created via API. Not A (raise the Scheduler cap: `isValidTimeout` is 60 s, needs GATE:xproj+push) nor B (row UPDATE: the job form then 400s). First proof: warm-up 13:55:34Z success 32.5 s (absorbs the wake); LagCheck 14:00:05Z success 1.7 s (was 23-44 s). Cost ~35 Render FREE instance-h/month; workspace free-hours headroom NOT assessed (not readable via MCP). Rollback: disable the job. A `Timeout after 60s` on LagCheck after this re-opens the item.
- [ ] **G1, named gap**: `lag` and `drift` have never fired live. Ale declined a Shopify write (2026-09-24). The first real occurrence closes it: keep that run's body and its `scheduler_alert_history` row, then write them into plan §7. It is also the first live read of `stock`/`sku_root`.
- [ ] Give SERVER two corrections for `infra-hosts.md`, as knowledge, not a handoff: the worst case is a measured **51.2 h**, not 24 h; and pre-2026-06-16 `log.DataOra` is DST-aware `Europe/Rome`, not fixed CEST.
- [ ] The cause of the 14 unlogged July nights (plan §1.7): observed, never diagnosed.
- [ ] Tell the Scheduler lane: `GET /api/jobs/{id}/logs` answered 500 for two jobs on 2026-09-25. It is Scheduler's to fix, not ours.

**Active constraints & decisions**:
- **`ok` is true IFF `checked` is true AND `lag_count == 0` AND `drift_count == 0`**. This is one invariant, asserted table-driven across every status. `skipped` is `ok:false` on purpose.
- **Every SQL the watchdog sends goes through `TestWatchdogSqlThroughRealDriver`** (real driver substitution, no DB). A MagicMock cursor proves nothing about the driver: that is how `get_last_sync_run` stayed green while broken in production until `5d8bc6a`. mysql-connector never turns `%%` back into `%`, so a literal `%` must never share a statement with a bound parameter.
- **`page_cap_reached` = more than 200 products updated inside the window.** `?hours=168` hits it (248 measured 2026-09-23), so the job stays on 48 h — but **48 h is not immune**: 28 consecutive runs `inconclusive/page_cap_reached` from 2026-10-06 18:00Z to 10-08 17:00Z, self-recovered 18:00Z, clean since (`scheduler_job_logs`, read 2026-10-11). A burst is a blind spell, not a fault; act only if it outlasts 48 h.
- **`lag` vs `drift`**: `lag` = created after the last sync; `drift` = the sync had time and did not absorb it. The label keys on the variant's `createdAt`, not on when the product became eligible (G4). `ok` is unaffected.
- The job's header is a third consumer of this service's `TRIGGER_SECRET` (`../docs/shared-secrets.md`). On rotation, update both job rows and the Render env. Rotation is frozen.
- Read-only everywhere: there is no second writer into `online_products`. TLS to `racoon` with `DB_CA_CERT`. Keep the service FREE-tier safe.

**Tried & discarded (still relevant)**:
- Treating `/api/status` as proof a run happened. It lives in memory, and any push restarts the service and wipes it.
- Options (a) Make alert, (b) targeted mirror, (c) more frequent sync and (e) webhook were priced in plan §3 and not chosen: about 15 events a year does not pay for them.
- Provoking `lag` with a Shopify variant: declined by Ale. It would be a buyable variant on the exact made-to-order path.
- Asserting `$.ok == false` alone: it cannot fire on a 401, a timeout or a non-JSON body, hence the `fail` rule.
- Amending a running subagent by message: it gets refused as injection. Re-delegate fresh instead.

**Key refs**: `docs/sync-lag-plan.md` §7 (build record, live receipts, named gaps) · `docs/gate-2-log.md` · `src/lag_check.py` · `src/db.py` (`get_last_sync_run`) · `test_lag_check.py` (`TestWatchdogSqlThroughRealDriver`) · `../docs/shared-database.md` · `../docs/dependencies-graph.md`

## Archive
Previous TRACKER (TLS pilot narrative, then the daily-programme state): `git show 163aee3:TRACKER.md`.

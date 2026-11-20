# Plan extract — new-model collections pass (A1)

Project extract of `IT/SVILUPPO/docs/shopify-api-2026-10/plan.md` rev. 2.1 (Racoon-LAB repo, `55e28ff`) — that file is the authority. The sections below are copied verbatim; if they differ from it, it wins.

## §1 Business (verbatim)

Business (Ale, 2026-11-05, chat)
- SALDI and OUTLET products never go to ADV, on any platform: they are discounted. ADV campaigns select products by
  collection, tag, brand and product. **The handoff's premise (feeds need SALDI/OUTLET) does not hold; the reason for
  A1 is the 5 other collections — NUOVA COLLEZIONE 2026, Scarpe in Jeans Denim, Scarpe con teschi, Scarpe
  personalizzate più di tendenza, Birkenstock Denim — and every future collection built with the new rules.**

## A1 (verbatim)

### A1 — P1 — shopify-mysql-sync: additive pass for collections REST cannot see

Target: in production by 2026-11-12. Executor: a new session in `shopify-mysql-sync` (sonnet, high).

Scope
- After the existing REST map, one GraphQL pass at **2026-10**, its own constant (the service pin is not touched):
  list all collections (id, title); keep those whose numeric id is **not** in the REST listing of the same run; page
  each one's `products` (first 250); append their titles, collection id ascending, after the product's legacy titles.
  A collection created or converted later (e.g. a Black Friday one) flows in without a code change.
- All-or-nothing: any error, `errors` in a 200, or exhausted throttle in the pass → the run continues with the legacy
  map only (today's output), an ERROR log line and a `new_model_collections` field in `/api/status`. No new alert:
  one is added only on a measured failure.
- OUT: the legacy REST path, the service pin and the Render env (A1b); the `Collections` format (D3); Feed-Exporter
  and Price_Bulk-UPDT code.

Tests (pytest, mocked HTTP, no live call)
- id diff REST vs GraphQL (GID → int); pagination 0/1/2 pages on listing and products; a product in two new-model
  collections (asymmetric fixtures); titles with `", "` and with `più`; append order; legacy output byte-identical
  when the pass finds nothing (literal expectations); each failure mode → legacy-only output + status field.

Acceptance
- **AC1** After the first run on the new code, rows per title = rows of products in (live membership ∩ ACTIVE ∩
  `VALID_TAGS`) at run time, recounted by the coordinator through `shopify-operator`; today's reference: §1.
- **AC2** Byte parity, two oracles neither written by the executor: (a) before the push, old and new map builders run
  read-only back to back on the branch code: new = old + appended titles, nothing else; (b) after the manual run, the
  diff of `Variant_id, Collections` against a snapshot taken just before it touches only rows of the 621 products, and
  only by an appended suffix (drift explained).
- **AC3** On the manual run, `/api/status` start/end: the pass adds ≤ 60 s, and the whole run ends before 03:10 would
  have fired (≤ 10 min).
- **AC4** Lag-check watchdog green the next day.
- **AC5** Next feeds: on Meta, each of the 5 advertised collections is an `internal_label` on every row of its
  products the feed carries; on Google, label coverage matches the A2(i) simulation figures (±drift).

Gate and run: `GATE:2+push` for the shas; the same answer covers one manual sync run 19:00–21:00 Rome on deploy day,
so a red result is reverted before 03:00. Rollback: revert the sha; the next run rewrites the column.

## §3 Decisions D2–D5 (verbatim)

- **D2** A1 is additive; the REST legacy path stays until A1b. Why: today's order cannot be rebuilt from GraphQL
  (measured), the pass is about ten calls inside a 10-minute window, and the deletion risk of a full version change
  stays out of the Black Friday weeks.
- **D3** `Collections` format unchanged (`", "`-joined titles), a contract with two readers.
- **D4** New titles appended after legacy ones, collection id ascending: no current Google label is pushed out
  (simulated). Which collections Google labels favour is Feed-Exporter's concern (A2(i)).
- **D5** The pass fails soft to today's output; all-or-nothing, never partial membership.

## Gates

- gate-1: Ale 2026-11-05 (GATE:1+xproj, programme plan rev. 2.1 `55e28ff`).
- gate-2: mutate-verify 5/5 PASS on `377489d` clean-tree (`docs/gate-2-log.md`); GATE:2+push Ale 2026-11-20 (programme `PROGRAM.md` `c178f93`).

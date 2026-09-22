# sync-lag — measurement and option map

> Plan document for OPEN-HANDOFFS row **2026-08-22, SERVER → SVILUPPO**:
> *"Alert or reduce the lag when a Shopify variant sells before shopify-mysql-sync mirrors it into
> `online_products`."*
> Written 2026-09-22 by SVILUPPO. **Stage: plan, awaiting `gate-1` (Ale).** Nothing has been built.
> Raw measurement artifact and the Make live-state report are session scratch, not committed; every
> number and query that matters is reproduced below.

---

## 0. The defect, in one paragraph (established, not re-derived here)

`shopify-mysql-sync` mirrors Shopify → `racoon.online_products` **once a day, 03:00 Europe/Rome**,
~212 s. `racoon.SP_SHOP_ORDER_IN` (called by Make `678434` on every order line) returns a fan-out
recordset: one row per sellable sku sharing the ordered sku's `SKU_ROOT` at the ordered size **that is
present in the mirror**. A Shopify variant sold before the mirror catches up is simply absent from its
own recordset. `racoon.stock` and `racoon.log` are written regardless; only the Shopify inventory
re-levelling of the sold item is skipped. Since 2026-08-22 (blueprint v257) a guard on router `193`
route 1 stops the execution from *breaking* — the failure is now a clean, **silent** skip.
Source: `IT/SERVER/infra-hosts.md` §`SP_SHOP_ORDER_IN` — recordset contract.

**What the guard fixed and what it did not.** It fixed the 28-hour queue block of 2026-08-21
(`sequential: true` + `Missing value of required parameter 'quantity'`). It did not fix, and was never
meant to fix, the missed re-levelling. Today the failure produces **no error, no DLQ row, no mail, no
trace of any kind** — verified live, §2.2.

---

## 1. The measurement

Read-only, executed 2026-09-22 against live `racoon` (Node + `mysql2`, `dateStrings: true`,
`timezone: 'Z'`, every timestamp also read through `DATE_FORMAT(...)` as a string) and the live Shopify
Admin API (GraphQL queries only, API version `2026-07`). The runner hard-refused any non-`SELECT`
statement; `online_products` (9,195), `log` (14,299) and `stock` (2,454) were re-counted at close and
were unchanged.

### 1.1 The key that made it measurable

`racoon.log` carries **`pid` and `vid`** — the Shopify product id and variant id of the ordered line.
The exposure test therefore runs on variant id, with no sku/size string pairing. Also settled in
passing: `log.SKU` is the **order-side** sku, not the base.

```
SHOW COLUMNS FROM racoon.log;
ID · DataOra · Verso · Canale · Order_ID · Order_Name · Barcode · Barcode_or
SKU · SIZE · STATO · pid · vid · QTY
```

| Test (n = 1,924, `Verso='OUT' AND Canale='WEB'`, from 2026-06-16) | Matches |
|---|---|
| `log.SKU` exists in `online_products.SKU` | 1924 / 1924 |
| `log.SKU` exists in `sku_root.SKU_ROOT` (i.e. is a base sku) | **0 / 1924** |
| `log.vid` exists in `online_products.Variant_id` | 1924 / 1924 |
| `log.vid` + `log.SIZE` = `Variant_Title` on the same mirror row | 1924 / 1924 |

### 1.2 Window and why

**Primary: 2026-06-17 → 2026-09-22 (97 days), denominator 1,886 order lines.**
It begins the day **after** the 2026-06-16 UTC cutover, so no era normalisation touches the headline
number; it lies entirely inside `scheduler_job_logs` retention, so `mirrored_at` is computed from
**observed** sync runs instead of an assumed schedule; and variant-deletion attrition in it is zero.
A 24-month context window is reported separately and labelled a floor, not a measurement.

**Timezone, validated rather than assumed.** `log.DataOra` read as UTC vs Shopify `Order.createdAt`:
post-cutover **0 min** (6 orders), pre-cutover CET season **+60…75 min**, pre-cutover CEST season
**+122…132 min**. **Refinement to the note on record in `infra-hosts.md`:** the pre-2026-06-16 era is
DST-aware `Europe/Rome`, not a fixed CEST — reading it as +2 puts every winter row one hour wrong.

### 1.3 The classification rule

The function below is the **idealised baseline**: it assumes the 03:00 Rome schedule fired every
night. The classification was then **re-run against the runs actually logged** in
`racoon.scheduler_job_logs` (§2 query set, job `Shopify-MySQL-Sync`), substituting each idealised
`mirrored_at` with the first **observed** trigger strictly after the variant's `createdAt`.
**Both passes return the same count — 4.** The only difference is occurrence #3, whose gap grows from
15.7 h to 51.2 h because no run was logged on 07-15 or 07-16. **The `mirrored_at` column reported in
§1.5 is the observed one**, which is why case 3 reads `07-17 03:04` and not `07-15 03:04`. The oracle
is the scheduler's own log, not this function.

```python
def mirrored_at_utc(created_utc):
    """IDEALISED baseline: first 03:00 Europe/Rome sync STRICTLY AFTER createdAt, + 4 min runtime.
    Re-run against observed scheduler_job_logs triggers; count unchanged, only #3's gap lengthens."""
    c_rome = created_utc.astimezone(ROME); day = c_rome.date()
    cand = datetime.combine(day, datetime.min.time(), tzinfo=ROME).replace(hour=3)
    if cand <= c_rome:
        cand = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=ROME).replace(hour=3)
    return (cand + timedelta(minutes=4)).astimezone(UTC)

# A  = in mirror today AND order_utc < mirrored_at   (the mandate's defect)
# B  = not in the mirror today                       (a different defect: tags/status)
# C  = variant id unresolvable in Shopify today      (attrition)
# OK = otherwise
```

### 1.4 The population query

```sql
SELECT l.ID, DATE_FORMAT(l.DataOra,'%Y-%m-%d %H:%i:%s') AS dt_str, l.Order_ID, l.Order_Name,
       l.SKU, l.SIZE, l.QTY, l.Barcode, l.pid, l.vid,
       (SELECT COUNT(*) FROM racoon.online_products op WHERE op.Variant_id = l.vid) AS vid_in_mirror,
       (SELECT COUNT(*) FROM racoon.online_products op2
          WHERE op2.SKU = l.SKU AND op2.Variant_Title = l.SIZE
            AND op2.Inventory_Item_ID IS NOT NULL AND op2.Inventory_Item_ID <> ''
            AND op2.Product_Title NOT LIKE '%utlet%') AS sp_match_rows_today
FROM racoon.log l
WHERE l.DataOra >= '2024-09-22 00:00:00' AND l.Verso='OUT' AND l.Canale='WEB'
ORDER BY l.ID;
```

Shopify side, batched 100 ids per call (32 calls, not one per line), ids taken straight from `log.vid`:

```graphql
query V($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on ProductVariant {
      id sku title barcode createdAt
      inventoryItem { id }
      product { id title status tags createdAt vendor }
    }
  }
}
```

### 1.5 The numbers

**Primary window — 97 days, 1,886 order lines, 1,825 distinct orders, 19.4 lines/day**

| Class | Count | Rate |
|---|---|---|
| OK | 1,882 | 99.79 % |
| **A — LAG (the defect)** | **4** | **0.212 % — 1 line in 472, ≈ 1 occurrence every 24 days** |
| B — permanently absent | **0** | 0 % |
| C — unresolvable | **0** | 0 % |

**Context window — 24 months, 9,799 order lines** *(floor, not a measurement)*: A = 8, B = 6, C = 53
lines / 29 variants. The 6 class-B rows are explained by **status today** (4 `ARCHIVED`, 2 `DRAFT`),
most likely archived *after* the order. **The tag-misconfiguration failure mode has no instance in
24 months** — a product missing a sync tag has never lost an order line this way.

**The four occurrences** (times Europe/Rome; `gap` = order → mirror, `age` = variant creation → order):

| # | Order | Order_Name | SKU | Size | Variant id | `createdAt` | `mirrored_at` *(observed)* | gap (h) | age (h) | barcode |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-06-17 11:25 | *(pre-cutover)* | `GL-R-CAMO-33TS-DIR` | 43 | 57830726369612 | 06-17 11:04 | 06-18 03:04 | 15.7 | **0.35** | `null` |
| 2 | 2026-06-19 10:54 | *(pre-cutover)* | `GH-W-34PC-DIR` | 43 | 57847745216844 | 06-19 07:37 | 06-20 03:04 | 16.2 | 3.29 | `null` |
| 3 | 2026-07-14 23:55 | `RAC-40238IT` | `GH-CAMO-DIR` | 44 | 58081351991628 | 07-14 13:46 | **07-17 03:04** | **51.2** | 10.15 | `null` |
| 4 | 2026-08-21 12:05 | `RAC-40978IT` | `ASHC-K-SKULLS-DIR-LGR` | 45 | 58501950505292 | 08-21 11:22 | 08-22 03:04 | 15.0 | **0.71** | `null` |

Case 4 is the incident `infra-hosts.md` already documents. The other three were not known.

**Corroboration.** Replaying the SP's own derived table against today's mirror returns, for each case,
the ordered sku's row present exactly once — and the live catalog holds exactly one variant per
(sku, size) for all four pairs, so no duplicate could have supplied it at order time. All four `log`
rows carry a populated `Barcode` taken from `racoon.stock`, proving the DB half of the SP ran normally.
The recordset came back **without the sold item while 1–6 sibling rows were still present and were
re-levelled**. The sold variant is the one that was not zeroed.

### 1.6 It clusters — on size, not on brand

| Signal | Class A (n=4) | Denominator | Verdict |
|---|---|---|---|
| Size 43 / 44 / 45 | **4 / 4** | 64 / 1,886 (3.4 %) | **strong cluster** |
| Shopify `barcode` null | **4 / 4** | 588 / 9,195 variants (6.4 %) | **strong cluster** (fresh variant) |
| Vendor Birkenstock | **0 / 4** | 1,303 / 1,886 (**69 %**) | the volume leader never triggers it |
| Weekday · hour-of-day | Fri×2 Wed×1 Tue×1 · 10,11,12,23 | flat | **no cluster** |
| Same product twice | **0** — 4 distinct products | — | **no repeat** |

**The mechanism the cluster names.** Every one of the 18 variants created in the window outside the
bulk event is an **out-of-grid size** with a null barcode: the made-to-order workflow — a customer asks
for a size outside the published grid, ops creates the variant, the customer buys it almost
immediately. Two of the four sold within **43 minutes** of the variant existing.

> **The rate that decides this is not the headline rate.**
> 18 variants were created in the window outside the bulk event. **4 of those 18 — 22 % — were sold
> before the next sync mirrored them.** The trigger is rare; conditional on the trigger the failure is
> close to a coin-toss, because the variant exists precisely because one specific customer is about to
> buy it.

**Structural exposure**: mean window per new variant **6.6 h** (min 3.1, max 19.4 idealised);
5,521 exposed variant-hours ≈ **2.4 variants exposed at any instant** on average.

### 1.7 Two facts that change the premise

- **Worst case is not 24 h — it is a measured 51.2 h.** `scheduler_job_logs` (capped at exactly 100
  rows/job; verified against 13 jobs sitting at n=100, so gaps are real and not eviction) shows **14
  July nights with no logged sync trigger**: 07-06…07-09, 07-11, 07-14…07-16, 07-21…07-26. Job config
  confirmed: `Shopify-MySQL-Sync`, `0 3 * * *`, `Europe/Rome`, enabled. Re-running the classification
  against observed runs instead of the idealised schedule does not change the count (4 either way); it
  only stretches occurrence #3 from 15.7 h to 51.2 h. **Cause not diagnosed** — it belongs to the open
  TRACKER item "monitoring of the daily run", and it means the exposure window is bounded by the
  scheduler's reliability, not by the 24-hour cycle.
- **The largest creation event in the window escaped by luck.** 836 variants were created in 97 days
  but on only 10 distinct days, and **818 of them in one event on 2026-08-22, 19:00–23:00 Rome**
  (361 products, a mass size-grid extension). Zero occurrences — because it ran in the evening. At
  10:00 the same event would have exposed ~17 h across a full trading day.

### 1.8 Standing snapshot, 2026-09-22

| Measure | Value |
|---|---|
| ACTIVE Shopify products | 887 |
| …carrying one of the 4 sync tags (`src/config.py:20`) | 737 |
| Their variants | **9,195** |
| Rows in `racoon.online_products` | **9,195** |
| **ACTIVE + tagged variants absent from the mirror** | **0** |
| Stale mirror rows | **0** |

The mirror is currently a row-for-row match with Shopify. **That zero is a snapshot, not a property**:
it means no variant has been created since 03:04 today. On ~10 % of days the number is non-zero for a
mean of 6.6 h.

### 1.9 What the measurement could NOT establish

1. **That the re-levelling truly did not happen.** The *cause* is measured (the sold item's row was
   absent from the recordset); the *effect* is inferred from the SP's and Make's logic. Shopify exposes
   no per-inventory-item adjustment history for 2026-06/08.
2. **That each logged 202 became a completed sync.** `/api/trigger` answers 202 and works in a
   background thread; `/api/status` is in-memory. **Class A = 4 is a lower bound.**
3. **Historical product `status` and `tags`** — read as of today, so anything DRAFT-or-untagged at order
   time scores OK. **Second reason class A is a lower bound.**
4. **Statistical significance**: n = 4. Every cluster in §1.6 is descriptive. The size cluster is
   strong enough to act on as a *mechanism* because it has an operational explanation that predicts it,
   not because n = 4 supports an inference.
5. **The direction of the customer-facing harm** — see §3.1. Measurable, not yet measured.

---

## 2. What exists today, verified live

### 2.1 This repo (`shopify-mysql-sync`)

| Anchor | What it is | Relevance |
|---|---|---|
| `shopify_to_mysql.py:69` `sync_products_graphql()` | the whole sync: one monolithic crawl | **no code path exists for a single variant** |
| `src/shopify_client.py:306` `get_products_graphql()` | paginated full-catalog GraphQL | would need a targeted sibling |
| `src/shopify_client.py:592` `build_product_collections_map()` | whole-shop **REST** crawl for collections | the obstacle to a cheap single-variant mirror |
| `src/db.py:430` `upsert_product()` | per-variant upsert, 30+ named fields | **reusable as-is** |
| `app.py:89` `/api/trigger` · `app.py:62` `_is_authorized()` | Bearer / `X-Trigger-Secret` auth | reusable for any new endpoint |
| `src/config.py:20` `VALID_TAGS` | the 4 mandatory tags | the mirror's inclusion rule |
| `render.yaml` | web service, **plan free**, Frankfurt | spins down after 15 min; ~30 s cold start |

### 2.2 Make `678434` — read live from the blueprint, 2026-09-22

- **v263, `isActive: true`, `isinvalid: false`**, 44 modules, `dlqCount` / `allDlqCount` = **0 / 0**
  (also confirmed against `GET /dlqs`, not just the counter). `sequential: true`, `maxErrors: 3`,
  `dlq: true`, scheduling `{"type":"immediately","maximum_runs_per_minute":100}`.
- **The v257 guard survived the scansia cutover, verbatim** — router `193` route 1, filter
  *"SE DISPO OK E LO SKU ORDINATO E NEL RECORDSET"*:
  `{{length(first(map(197.array[].recordset; "sku"; "sku"; 53.sku)))}} number:greater 0`, AND'd with
  the `Dispo ≥ 1` clause.
- **Module `200`** unchanged: `quantity` = `{{first(map(197.array[].recordset; "Dispo"; "sku"; 53.sku))}}`.
- **Module `190`** = `mysql:StoredProcedure` calling `` `racoon`.`SP_SHOP_ORDER_IN` `` on connection
  `9871406` (`MySQL_Racoon_Hetzner`), behind router `93` route 0, filter
  *"SE NO OUTLET - SI SKU - NO SCANSIA"*.
- **There is no alert path of any kind.** All 44 modules and all 8 `onerror` handlers were enumerated:
  every handler is scoped to its own module's runtime error, none to a router's filter outcome; there
  is no catch-all route on `193`; a full-text search for webhook / mail / notify / alert / datastore
  write found nothing further. **A non-firing `193.r1` leaves zero trace** — verified, not assumed.
- **Cost basis**: 44 recent executions (2026-09-17→22, all success) — median **21 ops**, mean 27,
  range 10–79.

### 2.3 `Scheduler` (SVILUPPO project) — the alerting surface already exists

- `POST /api/jobs` (`src/app/api/jobs/route.ts:29`) creates a job; `POST /api/jobs/[id]/alerts`
  (`src/app/api/jobs/[id]/alerts/route.ts:30`) attaches a rule. **Registering a watchdog job is an
  in-lane app operation, not a SERVER handoff.**
- `src/lib/alerts/evaluator.ts:68` implements **`response_match`** (`{path, operator, value}` over the
  response body) — the rule type `docs/dependencies-graph.md` prescribes for a route whose 200 means
  *"it ran"* rather than *"it worked"*. Mailer at `src/lib/alerts/mailer.ts`.
- `docs/dependencies-graph.md` is explicit: every enabled job is **already** watched for silence by the
  VPS #1 walker; *"build one only for what the Scheduler cannot see, which is what happened after the
  call was accepted."* A mirror-vs-Shopify comparison is exactly that.

---

## 3. Option map

Cost is engineering effort in this workspace's units (a *session* ≈ half a day of one lane).
"Breaks if wrong" is the realistic worst case, not the theoretical one.

### 3.0 Impact each option is trying to buy

The blast radius per occurrence is **one order line whose sold variant was not re-levelled in Shopify**
— roughly once every 24 days, ~15 times a year at current volume, concentrated on made-to-order sizes.
That is the whole prize. Every option below must be weighed against it.

### 3.1 The direction of the harm — the one thing worth measuring before choosing

Module `200` pushes `Dispo` (a deliberate 1-or-2 scarcity display) into Shopify `available`. Shopify
decrements `available` natively on a sale. So when the re-level is skipped, Shopify keeps *its own*
decremented number instead of Racoon's authoritative one. If Shopify's residual is **0**, nothing
visible happens; if the scarcity display had inflated it above real stock, the variant **stays
sellable with no pair behind it** — an oversell on a made-to-order size. Which of the two occurred in
the four cases is **not measured** (§1.9.1). It is cheaply measurable going forward and it is the
difference between "cosmetic" and "we sell shoes we do not have". *Stated as reasoning from the SP and
blueprint logic, not as a finding.*

### 3.2 The options

| | Option | Lane | Code lives in | Cost | Closes the window? |
|---|---|---|---|---|---|
| **a** | Alert in the caller (Make) | **AUTOMATE** | Make `678434` | 1 session | no — reports after the fact |
| **b** | Targeted single-variant mirror on demand | **SVILUPPO** + **AUTOMATE** caller | this repo + `678434` | 2–3 sessions | partially |
| **c** | More frequent sync | **SVILUPPO** (+ Ale on Render cost) | `Scheduler` job row | 1 session + running cost | shrinks it, never closes it |
| **d** | Read-only watchdog | **SVILUPPO** only | this repo + `Scheduler` | 1 session | no — but warns **before** the sale |
| **e** | Shopify webhook → targeted mirror | **Shopify-Management** + **SVILUPPO** | Shopify subscription + this repo | 3–4 sessions | **yes, essentially** |
| **f** | Webhook/manual → debounced **full** `/api/trigger` | **SVILUPPO** (+ **Shopify-Management** if automated) | no new code in this repo | 0–1 session | **yes, essentially** |

---

#### a. Alert in the caller — Make `678434`, lane **AUTOMATE**

**Shape.** A fourth route on router `193` carrying the inverse of the v257 guard —
`Qty ≥ 1` **AND** `{{length(first(map(197.array[].recordset; "sku"; "sku"; 53.sku)))}} = 0` — feeding
one notification module (mail to `it-services@`, or a comment on the order's Trello card, which the
scenario already creates). It fires on exactly the population measured here: ~1 order line every
24 days.

**Cost.** One route, one module, one `onerror`. ~+1 op on the rare orders that hit it, against a
median execution of 21. One stop → `PATCH` → re-read → start cycle (the D14 precedent measured 29.4 s
of downtime for a single PATCH, hook queue 0 throughout).

**Breaks if wrong.** Two real risks, both on record for this scenario. A filter written wrong fires on
every order → ~19 mails/day. Worse: the scenario is `sequential: true`, so a notification module that
*errors* blocks the whole queue — the exact shape of the 28-hour block of 2026-08-21. **Mandatory
mitigation**: a `builtin:Ignore` `onerror` on the alert module. Measured semantics say an unfiltered
`Ignore` drops the bundle and stops the route — for an alert-only route that is precisely the desired
failure mode.

**What it does not do.** Nothing is repaired. The re-level is still skipped; a human now knows, after
the customer has already bought.

---

#### b. Targeted mirror of a single variant on demand — lane **SVILUPPO** (+ **AUTOMATE** for the caller)

**Shape.** `POST /api/mirror-variant` in `app.py` (auth reuses `_is_authorized()`, `app.py:62`) →
a new `sync_one_variant(variant_id)` → a new targeted GraphQL query in `src/shopify_client.py` →
the existing `src/db.py:430 upsert_product()`, which is already per-variant and needs no change.

**Two obstacles, both verified in the code.** `build_product_collections_map()`
(`src/shopify_client.py:592`) is a whole-shop REST crawl — for one product it must be replaced by a
per-product collections query or skipped, leaving `Collections` empty on the row. Product-level
metafields come from the same GraphQL node and are fine.

**Where the caller sits decides whether this is viable.**
- *Preventive* (call before module `190` on every line): +1 HTTP call per order line on a
  `sequential: true` scenario, **99.8 % of them useless**, and outside the 02:00–03:00 keepalive window
  every call hits a Render FREE cold start (~30 s). Not viable.
- *Reactive* (a new route on `193` with the inverse filter → mirror → re-call the SP → push): 3–4 new
  modules reconstructing the whole stock branch, to recover one re-level every 24 days.

**Breaks if wrong.** A second write path into `online_products` — a table `docs/shared-database.md`
records as **single-writer**. The invariant survives nominally (same project), but a partially
populated row (empty `Collections`, missing metafields) is read by `Feed-Exporter` and shipped to
Google Shopping and Meta. That is a real downstream break, and a worse outcome than the defect.

**Cost.** ~150–200 lines plus tests in this repo (1–2 sessions), plus an AUTOMATE session for the
caller. The endpoint on its own, with no caller, buys nothing.

---

#### c. More frequent sync — lane **SVILUPPO**

**Shape.** Change the cron of Scheduler job `Shopify-MySQL-Sync` (`0 3 * * *`, `Europe/Rome`, enabled)
and add the matching keepalive window. No application code changes.

**Constraints to respect, all on record.** ~212 s per run. Render FREE spins down after 15 min, so
**the keepalive must precede every trigger** (reversed = cold-start fail) — the current
`*/5 2-3 * * *` window would have to be widened accordingly. `Feed-Exporter` triggers at 05:05 and
Google/Meta fetch inside 05:00–06:55: a sync running across 05:05 lets the feed read a mirror
mid-write (the sync commits per product, and `delete_variants` runs last). **Before committing to this,
Render free-tier instance-hours must be checked against a service kept warm most of the day — not
asserted here, it was not measured.**

**Effect, quantified.** Hourly would cut the mean exposure from 6.6 h to ~0.5 h — a ~13× reduction —
taking the conditional hazard from 22 % to roughly 1–2 %. It never reaches zero: a variant created at
10:58 and sold at 10:59 still loses. It has one genuine secondary benefit: it makes a missed run
self-healing, which directly addresses the 14 missing July nights (§1.7).

**Breaks if wrong.** A wiped or half-written feed inside the 05:00–06:55 fetch window; exhausted free
tier → the service is throttled or suspended and the *daily* sync stops too, turning a 0.21 % defect
into a total outage. 24× the Shopify API load (~1,800 GraphQL calls/day).

---

#### d. Read-only watchdog — lane **SVILUPPO only**

**Shape.** `GET /api/lag-check` in `app.py`: query Shopify for products **updated** in the last ~48 h
(adding an out-of-grid size updates an existing product, so `created_at` alone would miss exactly our
case), keep those ACTIVE and carrying a `VALID_TAGS` tag, compare their variant ids against
`SELECT Variant_id FROM online_products`, return `{"ok": <bool>, "missing": [...], "count": n}`.
Register it as a Scheduler job (`POST /api/jobs`) with a **`response_match`** rule on `$.ok == false`
(`src/lib/alerts/evaluator.ts:68`) — the shape `docs/dependencies-graph.md` prescribes for a 200 whose
body carries the verdict. Hourly during trading hours, excluding the 03:00–03:10 sync window.

**Why it is stronger than (a) despite also being "just an alert".** It watches the **exposure**, not
the **damage**, so it fires *before* the sale. Measured ages at order were 0.35 h, 0.71 h, 3.29 h and
10.15 h: an hourly watchdog would have caught occurrences 2, 3 and 4 in time to act, and only #1
(21 minutes) would have slipped. It also detects a 202-accepted-but-crashed sync, which is precisely
what `dependencies-graph.md` says a new watcher should be built for — and it serves the **already-open
TRACKER item** "monitoring of the daily run" at no extra cost.

**Breaks if wrong.** Nothing is written, so the worst case is noise: false positives if the sync window
is not excluded, or silence if the Shopify query shape is wrong. Both are caught by a test that seeds a
known-unmirrored variant id. A Render FREE cold start on each call is tolerable (gunicorn
`--timeout 120`, the call is a request, not the background thread).

**Cost.** ~80–120 lines plus tests here, one Scheduler job + one alert rule. **1 session, no handoff,
no cross-lane dependency.**

**Remediation loop uses parts that already exist**: the alert names the variant, a human calls the
existing `/api/trigger` (`app.py:89`), 212 s later the mirror is correct.

---

#### e. Shopify webhook `products/update` → targeted mirror — lanes **Shopify-Management** + **SVILUPPO**

The architecturally correct answer: react to the event that creates the exposure instead of polling or
reporting it. Needs a webhook subscription (token and scopes are **Shopify-Management**'s, and
`docs/shared-secrets.md` records the token as shared across projects), a public receiver with HMAC
verification, debouncing (the 2026-08-22 burst would have fired 361 product webhooks in four hours),
and it inherits every second-writer risk of option (b). **3–4 sessions across two lanes** to remove a
defect that fires 15 times a year.

---

#### f. Debounced **full** trigger on variant creation — lane **SVILUPPO** (+ **Shopify-Management** if automated)

The cheap cousin of (e): instead of a targeted mirror, call the **existing** `/api/trigger`
(`app.py:89`, already authenticated, already idempotent, already returns 409 while running). Two
flavours:
- **Manual, zero code**: ops trigger the sync after creating an out-of-grid variant — a one-line
  operational instruction. Costs nothing, relies on a human remembering, and the mechanism is human in
  the first place. Worth stating to Ale because it is free.
- **Automated**: a webhook or a Scheduler job that calls `/api/trigger` when the watchdog of (d)
  reports `ok: false`. That is **(d) with its remediation closed automatically** — the natural second
  step, and only worth building once (d) has shown how often it fires.

**Breaks if wrong.** A full 212 s sync per variant creation, undebounced, would have fired 818 times in
four hours on 2026-08-22. The 409-while-running guard absorbs most of that, but the debounce is not
optional.

---

## 4. Recommendation

**Build (d) — the read-only watchdog — and nothing else yet. Then decide (f-automated) with data.**

Why, in order of weight:

1. **The frequency does not justify an architectural response.** 4 occurrences in 97 days, 0.21 % of
   order lines, ~15 a year. Options (b) and (e) cost 2–4 sessions across two lanes and introduce a
   second writer into a single-writer table whose readers ship to Google Shopping and Meta. That is
   more risk than the defect carries.
2. **But the conditional hazard is 22 %, on the worst possible population.** Doing nothing is not
   right either: the variants that get hit exist *because* a specific customer is about to buy them.
3. **(d) is the only option entirely inside this lane.** Endpoint here, job and alert rule in
   `Scheduler` — both SVILUPPO. No handoff, no Make change, no Shopify scope, no DDL. It can ship
   without waiting on anyone.
4. **(d) warns before the sale, (a) only after it.** On the measured ages, an hourly watchdog catches
   3 of the 4.
5. **(d) has no write risk at all.** The worst failure is noise.
6. **(d) also closes an already-open item.** The TRACKER's next item is "monitoring of the daily run",
   and the measurement turned up 14 July nights with no logged trigger — a 202 that was accepted and
   whose outcome nobody can see. `dependencies-graph.md` says to build a watcher for exactly that and
   for nothing else. One build serves both.
7. **(c) is a running cost for a partial fix.** It shrinks the window ~13× but never closes it, adds
   24× the Shopify load, risks the 05:00–06:55 feed window, and its free-tier headroom is unverified.
   Its one real benefit — self-healing a missed run — is delivered more cheaply by (d) + a manual
   trigger.
8. **(a) is cheap and not wrong.** It is the right *complement* once (d) exists, to catch the residual
   sale inside the watchdog's own interval. It is AUTOMATE's to build, and it should be asked for only
   if the watchdog shows the residual is non-zero — **not before**. Opening an AUTOMATE handoff today
   to add a route to a `sequential: true` scenario, for ~15 events a year, buys less than it risks.

**Sequence proposed**: (d) now → run it 60 days → if it fires and the manual remediation is tolerable,
stop there; if it fires and nobody acts in time, add (f-automated); open the (a) handoff to AUTOMATE
only if a sale still slips through between two watchdog runs.

**Before or alongside (d), one cheap measurement** that changes how urgent all of this is: §3.1 — for
the next occurrence, record whether Shopify's residual `available` was 0 (cosmetic) or > 0 (an oversell
on a made-to-order size). The watchdog can capture it for free.

**Not recommended now**: (b), (c), (e). Not because they are wrong, but because 15 events a year does
not pay for them, and (d) will say within two months whether that judgement holds.

---

## 5. What stays uncovered, per option

| Option | Uncovered |
|---|---|
| **a** | The re-level is never repaired — only reported, after the sale. Blind to a variant unmirrored but not yet sold. Blind to a sync that was triggered and crashed. Adds a module to a `sequential: true` scenario whose last queue block cost 28 hours. |
| **b** | Only covers lines that reach the reactive route; a preventive placement is not viable on Render FREE. Rows written outside the nightly crawl may carry empty `Collections`/metafields and reach the Google/Meta feed. Does not address the 14 missing sync nights. |
| **c** | Never reaches zero — a variant created at 10:58 and sold at 10:59 still loses. Risks the 05:00–06:55 feed window. Free-tier headroom unverified. Does nothing for a run that is triggered and crashes silently. |
| **d** | **Detects, does not repair** — remediation is a human calling `/api/trigger`. Blind to a sale that happens between two runs (occurrence #1, 21 minutes, would have slipped). Depends on the Shopify `updated_at` query shape actually surfacing a variant added to an existing product — **this must be proven by a test, not assumed**. Adds no coverage for class-B (status/tags), which has no instance anyway. |
| **e** | Inherits (b)'s second-writer risk. Depends on a Shopify token and scopes owned by another lane. A missed or replayed webhook has no backstop unless (d) is also present. |
| **f** | Manual flavour depends on a human remembering, and the mechanism is already human. Automated flavour needs debouncing or the 2026-08-22 burst re-fires a 212 s sync hundreds of times; the 409-while-running guard absorbs most but not all of it. |

**Uncovered by every option on the list**, and worth stating plainly: the **cause of the 14 July nights
with no logged sync trigger** (§1.7). It is a Scheduler-side observation, undiagnosed, and it is what
turned occurrence #3's 15.7 h into 51.2 h. Whatever is chosen here, that stays open — it belongs to the
TRACKER's monitoring item.

---

## 6. Handoff disposition

The row is **not closed by this document**. Two legitimate outcomes, and the choice is Ale's at
`gate-1`:

- **Recommendation accepted (d)** → the work is SVILUPPO's. The row stays `in-progress` and is deleted
  by SVILUPPO, in its own commit, only after the watchdog is built and verified.
- **Option (a) chosen instead or in addition** → the row is **re-routed** to **AUTOMATE**, not closed:
  the Ask becomes the alert route on `678434`. Re-routing is the correct move, not a failure to deliver.

Either way `IT/SERVER/infra-hosts.md` §`SP_SHOP_ORDER_IN` — recordset contract should gain one line
correcting *"worst-case 24 h"* to the measured 51.2 h, and the DST refinement of §1.2. That is SERVER's
file; it goes back as knowledge, not as a new obligation.

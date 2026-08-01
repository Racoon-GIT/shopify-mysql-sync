# AUDIT REPORT — shopify-mysql-sync

**Date**: 2026-03-24
**Auditor**: Claude Opus 4.6 (1M context)
**Repo**: shopify-mysql-sync (Racoon-LAB)
**Base commit**: 8aa523d (main)
**Previous audit**: Round 2 (2026-03-24, score 8.8/10, commit 8738692)
**Scope**: Full audit from scratch — verifications independent of the previous report

---

## Scorecard (Round 1)

| # | Dimension | Score | Notes |
|---|-----------|:---------:|------|
| 1 | Code Quality | 8/10 | Fragile throttle detection, wrong sanitize_html lstrip semantics, deprecated VALUES(), status read without lock |
| 2 | Architecture & Patterns | 9/10 | Clean structure. One naming inconsistency in normalized product dict |
| 3 | Code <-> Objectives Alignment | 9/10 | Aligned. CLAUDE.md has stale reference to PUT, ~75 calls is approximate |
| 4 | Documentation <-> Code Coherence | 8/10 | render.yaml name mismatch, CLAUDE.md "PUT" stale |
| 5 | Test Coverage & Reliability | 7/10 | 42 tests OK on pure business logic. Zero tests for reset_variants.py. Zero DB tests |
| 6 | Deploy & Config Readiness | 8/10 | pytest in prod deps, .pytest_cache not in .gitignore, dated API version |

**Composite Score: 8.2/10**

---

## Deficiency List

### Dimension 1: Code Quality

- [x] **D1.1** [MINOR] `src/shopify_client.py:283` — GraphQL throttle detection uses `"THROTTLED" in str(err.get("extensions", {}))`. Converts the whole extensions dict into a string and searches the substring. Works but fragile: any change in Shopify's format could break it.
  - **FIXED**: Replaced with `err.get("extensions", {}).get("code") == "THROTTLED"`. File: `src/shopify_client.py`.

- [x] **D1.2** [MINOR] `shopify_to_mysql.py:47` — `sanitize_html` uses `html.lstrip('\xef\xbb\xbf')` which strips ANY combination of those 3 individual characters from the left side, not the specific 3-character sequence. `lstrip` operates on a SET of characters, not on a substring.
  - **FIXED**: Replaced with `if html.startswith('\xef\xbb\xbf'): html = html[3:]` to remove only the exact sequence. File: `shopify_to_mysql.py`.

- [ ] **D1.3** [MINOR] `src/db.py:321-397` — `VALUES()` in the `ON DUPLICATE KEY UPDATE` clause deprecated in MySQL 8.0.20+. Kept intentionally for compatibility with earlier MySQL versions. No fix required.

- [ ] **D1.4** [MINOR] `app.py:76` — `sync_status` read without lock in `/api/status`. Safe under CPython GIL but formally incorrect. Low priority.

### Dimension 2: Architecture & Patterns

- [x] **D2.1** [MINOR] `src/shopify_client.py:439` — `_normalize_graphql_product` returns `"productType"` (camelCase) while all other fields use snake_case (`body_html`, `compare_at_price`, `product_handle`, etc.). Naming inconsistency in the normalized dict.
  - **FIXED**: Renamed key from `"productType"` to `"product_type"` in normalization. Updated consumer in `shopify_to_mysql.py:151`. Files: `src/shopify_client.py`, `shopify_to_mysql.py`.

### Dimension 3: Code <-> Objectives Alignment

- [ ] **D3.1** [MINOR] `CLAUDE.md:16` — "~75 calls" is an approximation depending on the number of products in the store. Informative value.

- [x] **D3.2** [MINOR] `CLAUDE.md:17` — "Sleep 0.5s between mutating REST calls (POST/PUT/DELETE)" mentions PUT but the `put()` method was removed in the previous audit.
  - **FIXED**: Updated to "(POST/DELETE)". File: `CLAUDE.md`.

### Dimension 4: Documentation <-> Code Coherence

- [ ] **D4.1** [MINOR] `render.yaml:7` — Blueprint service name `shopify-mysql-sync` differs from the actual name on Render `shopify-sync-ws` (confirmed by CLAUDE.md:9). The blueprint name doesn't affect the live service but can confuse.

- [x] **D4.2** [MINOR] `CLAUDE.md:17` — Stale "PUT" reference (duplicate of D3.2, tracked here for documentation dimension).
  - **FIXED**: See D3.2.

### Dimension 5: Test Coverage & Reliability

- [x] **D5.1** [MINOR] No tests for `reset_variants.py`. The `create_variant_from_backup` function has testable pure logic: "perso" filter in the title, variant payload construction.
  - **FIXED**: Added `test_reset.py` with 7 tests: "perso" filter (case-insensitive), variant creation, error handling, full payload check. Total repo tests: 49/49 PASS. File: `test_reset.py`.

- [ ] **D5.2** [MINOR] No tests for `Database` class methods. Would require real MySQL — acceptable for unit tests, but leaves the most critical part (DB interaction, upsert, delete) uncovered.

- [x] **D5.3** [MINOR] `.pytest_cache/` not present in `.gitignore`. It has an internal `.gitignore` but best practice is explicit exclusion.
  - **FIXED**: Added `.pytest_cache/` to `.gitignore`. File: `.gitignore`.

### Dimension 6: Deploy & Config Readiness

- [x] **D6.1** [MINOR] `requirements.txt:15` — `pytest>=7.0.0` installed in production on Render. Wastes build time and disk space on the free tier (256MB).
  - **FIXED**: pytest removed from `requirements.txt`, created `requirements-dev.txt` with `-r requirements.txt` + pytest. Files: `requirements.txt`, `requirements-dev.txt`.

- [ ] **D6.2** [MINOR] `render.yaml:7` — Name mismatch (duplicate of D4.1, tracked for deploy readiness).

- [ ] **D6.3** [MINOR] `src/config.py:103` — `SHOPIFY_API_VERSION` default `2024-04`. Shopify may deprecate this version. Consider updating.

---

**Total deficiencies: 14**
- CRITICAL: 0
- MAJOR: 0
- MINOR: 14

**Fixed deficiencies: 7** (D1.1, D1.2, D2.1, D3.2/D4.2, D5.1, D5.3, D6.1)
**By design / informative: 7** (D1.3, D1.4, D3.1, D4.1, D5.2, D6.2, D6.3)

**Phase 3 complete. Fixed 7/14 deficiencies. Remaining: D1.3, D1.4, D3.1, D4.1, D5.2, D6.2, D6.3 (all MINOR, intentionally kept).**

---

## Re-Audit Round 2

### Verifications performed

- `src/shopify_client.py:283`: Throttle detection now uses `.get("code") == "THROTTLED"` — correct and robust.
- `shopify_to_mysql.py:47-48`: BOM removal now uses `startswith` + slice — removes only the exact sequence.
- `src/shopify_client.py:439`: Normalized key now `"product_type"` (snake_case). Consistent with all other fields.
- `shopify_to_mysql.py:152`: Consumer updated to `product.get("product_type")`.
- `CLAUDE.md:17`: Now says "(POST/DELETE)" without PUT.
- `.gitignore:14`: `.pytest_cache/` added.
- `requirements.txt`: pytest commented out. `requirements-dev.txt` created with `-r requirements.txt` + pytest.
- `test_reset.py`: 7 new tests for `create_variant_from_backup`. All passing.
- **49/49 tests PASS**. Zero regressions.

### New deficiencies found

None.

### Scorecard (Round 2)

| # | Dimension | R1 | R2 | Notes |
|---|-----------|:---:|:---:|------|
| 1 | Code Quality | 8 | **9** | Throttle and sanitize_html fixed. D1.3 (VALUES() compat.) and D1.4 (status read, GIL-safe) remain. |
| 2 | Architecture & Patterns | 9 | **10** | productType inconsistency fixed. Zero residual deficiencies. |
| 3 | Code <-> Objectives Alignment | 9 | **9** | PUT reference fixed. D3.1 remains (~75 calls, informative). |
| 4 | Documentation <-> Code Coherence | 8 | **9** | PUT reference corrected. Only D4.1 remains (blueprint vs live name). |
| 5 | Test Coverage & Reliability | 7 | **8** | 7 new tests for reset_variants. 49 total. D5.2 remains (no DB tests, requires MySQL). |
| 6 | Deploy & Config Readiness | 8 | **9** | pytest out of prod deps. D6.2 (blueprint name) and D6.3 (API version, informative) remain. |

**Composite Score Round 2: 9.0/10**

### Remaining Open Issues (7 MINOR)

| ID | Severity | Description | Reason Not Fixed |
|----|----------|-------------|------------------|
| D1.3 | MINOR | `VALUES()` deprecated MySQL 8.0.20+ | Compatibility with earlier versions. Still works in MySQL 8.x/9.x. |
| D1.4 | MINOR | Status read without lock | GIL guarantees atomicity on dict read in CPython. Zero risk in practice. |
| D3.1 | MINOR | "~75 calls" not fixed | Useful context approximation. Real value depends on catalog. |
| D4.1 | MINOR | render.yaml name vs actual Render | Blueprint name doesn't impact live service. Not verifiable from code. |
| D5.2 | MINOR | No Database class tests | Require real MySQL. Acceptable for unit tests. |
| D6.2 | MINOR | render.yaml name (deploy) | Duplicate of D4.1. |
| D6.3 | MINOR | API version 2024-04 | Works. Update at user's discretion. |

### Assessment

Score 9.0/10 with only 7 residual MINOR deficiencies (5 unique, 2 cross-dimension duplicates). All intentionally unfixed for documented reasons:
- DB compatibility (VALUES())
- Safety guaranteed by GIL (status read)
- Informative accuracy (approximations)
- Infrastructure constraints (render name, API version, MySQL tests)

**AUDIT STATUS: AUDIT COMPLETE**

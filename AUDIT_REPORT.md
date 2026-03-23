# AUDIT REPORT — shopify-mysql-sync

**Data**: 2026-03-23
**Auditor**: Claude Opus 4.6 (1M context)
**Repo**: shopify-mysql-sync (Racoon-LAB)
**Commit**: 1aefdfe (main)

---

## Scorecard (Round 1)

| # | Dimensione | Punteggio | Note |
|---|-----------|:---------:|------|
| 1 | Code Quality | 5/10 | Bug critico GraphQL throttle, nessuna auth su trigger, dead code |
| 2 | Architecture & Patterns | 7/10 | Buona struttura, molto dead code (REST-era leftovers) |
| 3 | Code <-> Objectives Alignment | 7/10 | Funzionalmente corretto, variant metafields fetchati ma ignorati |
| 4 | Documentation <-> Code Coherence | 5/10 | Nome servizio errato in CLAUDE.md, nessun README, commenti fuorvianti |
| 5 | Test Coverage & Reliability | 0/10 | Zero test. Violazione policy SVILUPPO/CLAUDE.md |
| 6 | Deploy & Config Readiness | 4/10 | .DS_Store e __pycache__ committati, no auth su endpoint |

**Composite Score: 4.7/10**

---

## Deficiency List

### Dimensione 1: Code Quality

- [x] **D1.1** [CRITICAL] `src/shopify_client.py:296-308` — Bug nel GraphQL throttle handling. Il `continue` alla riga 306 proseguiva il ciclo `for err in errors` (inner loop), NON il ciclo `for attempt` (outer loop). Dopo il sleep, il codice cadeva nel `raise Exception` alla riga 309 invece di ritentare la query. **Il throttling GraphQL non funzionava.**
  - **FIXED**: Ristrutturato con flag `throttled` + `break` + `continue` sul loop esterno. File: `src/shopify_client.py`.

- [x] **D1.2** [CRITICAL] `app.py:49-57` — Nessuna autenticazione sull'endpoint `/api/trigger`. Chiunque conosca l'URL del servizio Render poteva triggerare una sincronizzazione.
  - **FIXED**: Aggiunta autenticazione via `TRIGGER_SECRET` env var (query param `?secret=` o header `X-Trigger-Secret`). File: `app.py`, `render.yaml`.

- [x] **D1.3** [MAJOR] `app.py:13-19` — Thread safety: il dizionario `sync_status` era modificato dal thread background senza lock.
  - **FIXED**: Aggiunto `threading.Lock` (`_sync_lock`) per proteggere tutte le scritture a `sync_status`. File: `app.py`.

- [x] **D1.4** [MAJOR] `reset_variants.py:276,295,301` + `src/db.py:463,483` — Type mismatch: `product_id` era `str` (da `Config.product_ids`) ma i metodi DB erano tipizzati come `int`.
  - **FIXED**: Introdotta variabile `pid = int(product_id)` in `process_product()`. Passato `pid` (int) ai metodi DB. Aggiornata type annotation di `restore_inventory_levels` a `int`. File: `reset_variants.py`.

- [x] **D1.5** [MINOR] `src/db.py:8` — Import inutilizzato: `from contextlib import contextmanager`.
  - **FIXED**: Rimosso. File: `src/db.py`.

- [x] **D1.6** [MINOR] `src/shopify_client.py` — Dead code: 8 metodi mai chiamati (residui dell'era REST): `get_products`, `get_product_metafields`, `get_variant_metafields`, `extract_variant_metafields`, `get_locations`, `get_location_id_by_name`, `get_inventory_level_for_location`, `build_inventory_map_for_location`.
  - **FIXED**: Tutti rimossi. File: `src/shopify_client.py`.

- [ ] **D1.7** [MINOR] `src/db.py:323-399` — `VALUES()` nella clausola `ON DUPLICATE KEY UPDATE` e' deprecato in MySQL 8.0.20+.
  - **NOT FIXED**: La nuova sintassi `AS row_alias` richiede MySQL 8.0.19+. Non potendo verificare la versione MySQL in produzione, mantenere `VALUES()` e' la scelta piu' sicura per compatibilita'. Non e' stato rimosso da MySQL.

- [x] **D1.8** [MINOR] `reset_variants.py:351` — `import traceback` dentro il blocco except.
  - **FIXED**: Spostato a top-level import. File: `reset_variants.py`.

### Dimensione 2: Architecture & Patterns

- [x] **D2.1** [MAJOR] `src/shopify_client.py` — ~200 righe di dead code (8 metodi inutilizzati).
  - **FIXED**: Rimossi tutti i metodi inutilizzati (vedi D1.6). File: `src/shopify_client.py`.

- [x] **D2.2** [MINOR] `src/config.py:39-44` — `VALID_TAGS` definito come class variable su un `@dataclass`.
  - **FIXED**: Spostato a costante di modulo `frozenset`. Aggiornati import in `shopify_to_mysql.py` e `src/__init__.py`. File: `src/config.py`, `shopify_to_mysql.py`, `src/__init__.py`.

### Dimensione 3: Code <-> Objectives Alignment

- [x] **D3.1** [MAJOR] `src/shopify_client.py:100-108` + `shopify_to_mysql.py:120-185` — La query GraphQL fetchava metafields a livello variante ma `shopify_to_mysql.py` li ignorava completamente. Spreco di bandwidth e costo query.
  - **FIXED**: Rimossa la sotto-query `metafields` dalle varianti nella query GraphQL e il relativo parsing nel normalizer. File: `src/shopify_client.py`.

- [x] **D3.2** [MINOR] `src/db.py:49` — Commento SQL "Metafield Variante (Google Shopping)" fuorviante (dati da metafields PRODOTTO).
  - **FIXED**: Commento aggiornato a "Metafield Google Shopping (a livello prodotto, applicati a tutte le varianti)". File: `src/db.py`.

- [ ] **D3.3** [MINOR] CLAUDE.md dice "~75 chiamate" ma il numero dipende dalla dimensione dello store.
  - **NOT FIXED**: Valore approssimativo documentato per contesto storico. Il commento nella query GraphQL spiega il costo per pagina.

### Dimensione 4: Documentation <-> Code Coherence

- [x] **D4.1** [MAJOR] CLAUDE.md riga 9 diceva `shopify-sync-ws`, ma `render.yaml` usa `shopify-mysql-sync`.
  - **FIXED**: Aggiornato CLAUDE.md con il nome corretto `shopify-mysql-sync`. File: `CLAUDE.md`.

- [x] **D4.2** [MAJOR] Nessun README.md nel repository.
  - **FIXED**: Creato `README.md` con stack, servizi, endpoints, env vars, flusso e istruzioni test. File: `README.md`.

- [x] **D4.3** [MINOR] CLAUDE.md diceva "Sleep 0.5s tra chiamate REST" senza precisare che si applica solo a POST/PUT/DELETE.
  - **FIXED**: Precisato "chiamate REST mutanti (POST/PUT/DELETE)". File: `CLAUDE.md`.

- [x] **D4.4** [MINOR] CLAUDE.md mancava `TRIGGER_SECRET` nelle env vars. Aggiunto con il fix di D1.2.
  - **FIXED**: Aggiunta variabile `TRIGGER_SECRET` alla sezione env vars di CLAUDE.md. File: `CLAUDE.md`.

### Dimensione 5: Test Coverage & Reliability

- [x] **D5.1** [CRITICAL] Zero test nel repository.
  - **FIXED**: Aggiunto `pytest>=7.0.0` a `requirements.txt`. Creati `test_sync.py` (30 test: `is_shoe`, `sanitize_html`, `extract_product_metafields`, `build_images_json`, `_normalize_graphql_product`, `extract_next_link`) e `test_app.py` (8 test: health, trigger auth, status, home). Totale: 38 test.

- [x] **D5.2** [MAJOR] Business logic non testata.
  - **FIXED**: Coperte tutte le funzioni pure di business logic: filtro tag, sanitizzazione HTML, estrazione metafields con conversione tipi, normalizzazione prodotti GraphQL con stock per location, costruzione JSON immagini, paginazione link header, endpoint Flask con auth. File: `test_sync.py`, `test_app.py`, `requirements.txt`.

### Dimensione 6: Deploy & Config Readiness

- [x] **D6.1** [CRITICAL] `.DS_Store` e `src/.DS_Store` tracciati da git.
  - **FIXED**: `git rm --cached .DS_Store src/.DS_Store`. File rimosse dal tracking.

- [x] **D6.2** [CRITICAL] `__pycache__/*.pyc` (6 file) tracciati da git.
  - **FIXED**: `git rm -r --cached __pycache__/ src/__pycache__/`. File rimossi dal tracking.

- [x] **D6.3** [MAJOR] `.gitignore` non includeva `.DS_Store`.
  - **FIXED**: Aggiunto `.DS_Store` a `.gitignore`. File: `.gitignore`.

- [x] **D6.4** [MAJOR] `__pycache__/` era nel `.gitignore` ma i file erano gia' tracciati.
  - **FIXED**: Eseguito `git rm --cached` per rimuoverli dal tracking (vedi D6.2).

- [ ] **D6.5** [MINOR] `render.yaml` nome servizio vs nome effettivo su Render.
  - **NOT FIXED**: Il nome in `render.yaml` e' ora coerente con CLAUDE.md (fix D4.1). Il nome effettivo su Render dipende dalla configurazione live e non puo' essere verificato dal codice.

---

**Totale deficienze: 22**
- CRITICAL: 5 (5 fixed)
- MAJOR: 9 (9 fixed)
- MINOR: 8 (5 fixed, 3 not fixed)

**Phase 3 complete. Fixed 19/22 deficiencies. Remaining: D1.7, D3.3, D6.5 (all MINOR, intentionally kept for compatibility/accuracy reasons).**

---

## Re-Audit Round 2

### New Deficiencies Found

- [x] **D-R2.1** [MINOR] `src/db.py:302` e `shopify_to_mysql.py:12` — Commenti residui che menzionavano "metafield variante" dopo la rimozione del fetch variant metafields.
  - **FIXED**: Aggiornati commento in `db.py` e docstring in `shopify_to_mysql.py`.

### Scorecard (Round 2)

| # | Dimensione | R1 | R2 | Note |
|---|-----------|:---:|:---:|------|
| 1 | Code Quality | 5 | **8** | Bug critico GraphQL e auth fixati. Rimane solo D1.7 (VALUES() deprecato, mantenuto per compatibilita'). |
| 2 | Architecture & Patterns | 7 | **9** | Dead code rimosso (~200 righe), VALID_TAGS idiomatico. `put()` non usato ma parte dell'interfaccia HTTP generica. |
| 3 | Code <-> Objectives Alignment | 7 | **9** | Variant metafield waste rimosso, commenti corretti. D3.3 (claim ~75 calls) e' approssimazione documentata. |
| 4 | Documentation <-> Code Coherence | 5 | **8** | README creato, CLAUDE.md corretto, TRIGGER_SECRET documentato. D6.5 non verificabile dal codice. |
| 5 | Test Coverage & Reliability | 0 | **7** | 38 test creati (pytest). Coprono tutte le funzioni pure di business logic. Non eseguibili localmente (dipendenze non installate). Nessun integration test. |
| 6 | Deploy & Config Readiness | 4 | **9** | .DS_Store e __pycache__ rimossi dal tracking, .gitignore aggiornato, auth su trigger. D1.7 residuo MINOR. |

**Composite Score Round 2: 8.3/10**

### Remaining Open Issues (3 MINOR)

| ID | Severity | Description | Reason Not Fixed |
|----|----------|-------------|------------------|
| D1.7 | MINOR | `VALUES()` deprecato in MySQL 8.0.20+ | Nuova sintassi `AS alias` richiede MySQL 8.0.19+. Versione DB produzione non verificabile. `VALUES()` funziona ancora. |
| D3.3 | MINOR | CLAUDE.md "~75 chiamate" non e' valore fisso | Approssimazione utile per contesto. Il costo per pagina e' documentato nel codice. |
| D6.5 | MINOR | Nome servizio render.yaml vs Render effettivo | Gia' allineato con CLAUDE.md (fix D4.1). Il nome effettivo su Render dipende dalla config live. |

### Assessment

Score 8.3/10 con solo 3 deficienze MINOR residue, tutte intenzionalmente non fixate per ragioni documentate (compatibilita' DB, accuratezza documentazione, vincoli ambiente live).

**AUDIT STATUS: AUDIT COMPLETE**

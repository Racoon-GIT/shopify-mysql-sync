# AUDIT REPORT — shopify-mysql-sync

**Data**: 2026-03-24
**Auditor**: Claude Opus 4.6 (1M context)
**Repo**: shopify-mysql-sync (Racoon-LAB)
**Commit**: 8738692 (main)
**Audit precedente**: a000c8e (2026-03-23, score 8.3/10)

---

## Scorecard (Round 1)

| # | Dimensione | Punteggio | Note |
|---|-----------|:---------:|------|
| 1 | Code Quality | 8/10 | TOCTOU race condition in trigger, dead `put()`, `VALUES()` deprecato |
| 2 | Architecture & Patterns | 9/10 | Dead method `put()`, struttura pulita |
| 3 | Code <-> Objectives Alignment | 9/10 | Tutto allineato, solo approssimazione "~75 chiamate" |
| 4 | Documentation <-> Code Coherence | 8/10 | Nome servizio render.yaml vs Render effettivo, ANALISI_FUNZIONALE con nome vecchio |
| 5 | Test Coverage & Reliability | 7/10 | 46 test buoni su business logic pura, nessun test DB, non verificabili localmente |
| 6 | Deploy & Config Readiness | 8/10 | `.claude/` non in .gitignore (rischio token), render.yaml name mismatch |

**Composite Score: 8.2/10**

---

## Deficiency List

### Dimensione 1: Code Quality

- [x] **D1.1** [MAJOR] `app.py:64-68` — TOCTOU race condition nel trigger endpoint. `sync_status["running"]` viene controllato senza lock, poi il thread viene avviato, ma `running = True` viene settato solo dentro `run_sync()` (nel thread). Due richieste concorrenti possono entrambe passare il check e avviare due sync paralleli.
  - **FIXED**: Check e set `running = True` ora atomici sotto `_sync_lock` nell'endpoint trigger. `run_sync()` non setta piu' `running = True` (gia' settato). File: `app.py`.

- [x] **D1.2** [MINOR] `src/shopify_client.py:227-235` — Metodo `put()` mai chiamato da nessuna parte del codebase. Dead code.
  - **FIXED**: Metodo `put()` rimosso. File: `src/shopify_client.py`.

- [ ] **D1.3** [MINOR] `src/db.py:340-380` — `VALUES()` nella clausola `ON DUPLICATE KEY UPDATE` deprecato in MySQL 8.0.20+. Nota: mantenuto intenzionalmente per compatibilita' con versioni MySQL precedenti (vedi audit precedente D1.7). Non richiede fix.

- [ ] **D1.4** [MINOR] `app.py:75` — `sync_status` letto senza lock in `/api/status`. Sicuro sotto GIL ma formalmente scorretto. Bassa priorita'.

### Dimensione 2: Architecture & Patterns

- [x] **D2.1** [MINOR] `src/shopify_client.py:227-235` — Dead code `put()` (duplicato di D1.2, tracciato qui per completezza architetturale).
  - **FIXED**: Vedi D1.2.

### Dimensione 3: Code <-> Objectives Alignment

- [ ] **D3.1** [MINOR] `CLAUDE.md:16` — "~75 chiamate" e' approssimazione che dipende dal numero di prodotti nello store. Non e' un errore ma puo' confondere. Valore informativo, non richiede fix.

### Dimensione 4: Documentation <-> Code Coherence

- [ ] **D4.1** [MINOR] `render.yaml:7` — Nome servizio `shopify-mysql-sync` diverso dal nome effettivo su Render `shopify-sync-ws` (confermato da commit 8738692 e CLAUDE.md:9). Il blueprint name non influisce sul servizio live ma puo' confondere sviluppatori che usano il blueprint per un nuovo deploy.

- [x] **D4.2** [MINOR] `ANALISI_FUNZIONALE_reset_variants.md:87-91` — Sezione 3.2 mostra `name: shopify-sync` nel render.yaml d'esempio. Ne' il nome blueprint (`shopify-mysql-sync`) ne' il nome effettivo (`shopify-sync-ws`).
  - **FIXED**: Aggiornato esempio render.yaml con `name: reset-variants`, schedule e comandi corretti. File: `ANALISI_FUNZIONALE_reset_variants.md`.

### Dimensione 5: Test Coverage & Reliability

- [ ] **D5.1** [MINOR] Test non verificabili localmente (pytest non installato, per policy non si installano dipendenze). I test devono essere validati al deploy o in CI.

- [ ] **D5.2** [MINOR] Nessun test per i metodi della classe `Database` (upsert, delete_variants, backup/restore). Richiederebbero MySQL — accettabile, ma lascia scoperta la parte piu' critica della logica (interazione DB).

- [x] **D5.3** [MINOR] `test_app.py:45` — `test_trigger_starts_sync` mocka `app.run_sync` ma non verifica che il thread sia stato effettivamente avviato.
  - **FIXED**: Test ora mocka `app.threading.Thread` e verifica `assert_called_once()` e `start.assert_called_once()`. File: `test_app.py`.

### Dimensione 6: Deploy & Config Readiness

- [x] **D6.1** [MAJOR] `.claude/` directory non presente in `.gitignore`. Il file `.claude/settings.local.json` contiene un token Shopify (`shpat_...`) ed e' attualmente NON tracciato da git, ma un `git add -A` lo committerebbe accidentalmente.
  - **FIXED**: Aggiunto `.claude/` a `.gitignore`. File: `.gitignore`.

- [ ] **D6.2** [MINOR] `render.yaml:7` — Nome servizio non corrisponde al nome effettivo su Render (duplicato di D4.1, tracciato qui per deploy readiness).

---

**Totale deficienze: 12**
- CRITICAL: 0
- MAJOR: 2 (D1.1, D6.1)
- MINOR: 10 (D1.2, D1.3, D1.4, D2.1, D3.1, D4.1, D4.2, D5.1, D5.2, D5.3)

**Deficienze fixate: 5** (D1.1, D1.2/D2.1, D4.2, D5.3, D6.1)
**Deficienze informative / by design: 6** (D1.3, D1.4, D3.1, D4.1, D5.1, D5.2, D6.2)

---

**Phase 3 complete. Fixed 5/12 deficiencies (6 unique, D1.2=D2.1). Remaining open: D1.3, D1.4, D3.1, D4.1, D5.1, D5.2, D6.2 (all MINOR, intentionally kept).**

---

## Re-Audit Round 2

### Verifiche effettuate

- `app.py`: TOCTOU fix verificato. Check + set `running = True` atomici sotto lock. `run_sync()` non duplica piu' il set. Lock rilasciato correttamente anche su `return` dentro `with` block.
- `src/shopify_client.py`: `put()` rimosso. Nessun riferimento residuo. `delete()` segue immediatamente `post()`.
- `.gitignore`: `.claude/` aggiunto. Protegge da commit accidentale di `settings.local.json`.
- `test_app.py`: Test trigger mocka `threading.Thread`, verifica creazione e start. Coerente con il nuovo flusso (running settato nell'endpoint).
- `ANALISI_FUNZIONALE_reset_variants.md`: Esempio render.yaml aggiornato con nome e config corretti.

### Nuove deficienze trovate

Nessuna.

### Scorecard (Round 2)

| # | Dimensione | R1 | R2 | Note |
|---|-----------|:---:|:---:|------|
| 1 | Code Quality | 8 | **9** | TOCTOU e dead code fixati. Restano D1.3 (VALUES() compat.) e D1.4 (status read senza lock, GIL-safe). |
| 2 | Architecture & Patterns | 9 | **9** | Invariato. Dead code rimosso. |
| 3 | Code <-> Objectives Alignment | 9 | **9** | Invariato. D3.1 e' approssimazione informativa. |
| 4 | Documentation <-> Code Coherence | 8 | **9** | ANALISI_FUNZIONALE corretta. Resta solo D4.1 (blueprint vs live name). |
| 5 | Test Coverage & Reliability | 7 | **8** | Test trigger migliorato. Restano D5.1 (no pytest locale) e D5.2 (no DB tests). |
| 6 | Deploy & Config Readiness | 8 | **9** | .claude/ protetto. Resta D6.2 (blueprint name). |

**Composite Score Round 2: 8.8/10**

### Remaining Open Issues (6 MINOR)

| ID | Severity | Description | Reason Not Fixed |
|----|----------|-------------|------------------|
| D1.3 | MINOR | `VALUES()` deprecato MySQL 8.0.20+ | Compatibilita' con versioni precedenti. Funziona ancora. |
| D1.4 | MINOR | Status read senza lock | GIL garantisce atomicita' su dict read in CPython. Rischio nullo in pratica. |
| D3.1 | MINOR | "~75 chiamate" non e' fisso | Approssimazione utile per contesto. Valore reale dipende dal catalogo. |
| D4.1 | MINOR | render.yaml name vs Render effettivo | Blueprint name non impatta servizio live. Non verificabile dal codice. |
| D5.1 | MINOR | Test non eseguibili localmente | Per policy no installs locali. Test validabili in CI. |
| D5.2 | MINOR | No test Database class | Richiedono MySQL reale. Accettabile per unit test. |

### Assessment

Score 8.8/10 con solo 6 deficienze MINOR residue. Tutte intenzionalmente non fixate per ragioni documentate (compatibilita' DB, sicurezza GIL, accuratezza documentale, vincoli infrastruttura).

Entrambe le deficienze MAJOR originali (D1.1 TOCTOU, D6.1 .gitignore) sono state risolte.

**AUDIT STATUS: AUDIT COMPLETE**

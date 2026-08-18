# TRACKER — shopify-mysql-sync

## Current state

**Aggiornato**: 2026-08-18

### TLS verso `racoon` — verifica dell'identità dell'host: FATTA e verde

Secondo pilota del rollout `ssl_verify_identity` (il primo è `feed-server`, commit
`77f6e86`). Autorizzato da Ale il 2026-08-18 come pilota **separato** dal primo:
questo servizio è il **writer di `online_products`** e ha un ramo encrypt-only in più.

- **Commit**: `97b653b` — `src/db.py` (`build_ssl_config`) + `test_db.py`.
- **Deploy live**: `dep-da25nbgu01pc73eou9qg`, finished `2026-08-18T13:19:20Z`.
- **Oracolo, entrambe le metà** (mysql-connector-python 9.6.0, la versione pinnata):
  1. host = IP `78.46.244.227` con CA + `ssl_verify_identity=True` → handshake TLS
     passa, la connessione arriva all'auth. L'IP è nel SAN.
  2. host = nome che risolve allo stesso IP ma **assente dal SAN** → `2026 (HY000)
     SSL connection error: certificate verify failed`; causa isolata con OpenSSL
     (`-verify_hostname` → `62 hostname mismatch`, `-verify_ip` → `0 ok`).
  3. Controprova: nel ramo encrypt-only **entrambi** gli host passano l'handshake →
     il rosso di (2) è la verifica, non la rete.
- **End-to-end**: sync completo triggerato sul deploy live → `success`, 213,6 s,
  `last_error: null`.
- `DB_CA_CERT` su Render decodifica nella CA `Racoon Internal CA` (1.956 B,
  sha256 `c4d2a35f…`), identica a `IT/SERVER/racoon-internal-ca.pem`.

Un solo punto di connessione nel repo (`src/db.py`), condiviso da sync e
`reset_variants`: la verifica copre entrambi.

**Non trasferibile ai consumer Node**: su `mysql2`, connettendosi per IP,
`servername` resta `undefined` e Node confronta contro `localhost` — limite noto del
driver, registrato in `../docs/shared-database.md`.

### Chi ha fatto cosa (per non rifare due volte lo stesso lavoro)

Il codice del pilota è stato scritto e committato **da una sessione con cwd
`IT/SVILUPPO`, non da una sessione di questo repo** (`8a0219bf`, commit alle
`2026-08-18T13:18:37Z`), che alle `13:25` ha dichiarato il pilota chiuso. Dal transcript
di quella sessione **non risulta** però il controllo negativo lato driver Python su
questo servizio: la seconda metà dell'oracolo — quella discriminante — è stata
misurata solo il 2026-08-18 sera, nella sessione di questo repo. È il motivo per cui
il lavoro sembrava non fatto: il repo non ne portava traccia.

### Note operative

- L'MCP `render-local` **non si autentica** (OAuth: *"Incompatible auth server: does
  not support dynamic client registration"*, sia da tool sia dal pannello `/mcp`).
  Via praticabile: REST API `api.render.com/v1` con la API key già presente nella
  config MCP del progetto root.

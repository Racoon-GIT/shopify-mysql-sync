"""
Web wrapper per shopify-mysql-sync.
Espone endpoint HTTP per trigger da Scheduler e health check.
"""

import hmac
import os
import threading
import time
from flask import Flask, jsonify, request

from src import lag_check
from src.config import Config

app = Flask(__name__)

TRIGGER_SECRET = os.getenv("TRIGGER_SECRET")

# Stato sync in-memory (protetto da lock)
_sync_lock = threading.Lock()
sync_status = {
    "running": False,
    "last_run": None,
    "last_status": None,
    "last_duration": None,
    "last_error": None,
}


def run_sync():
    """Esegue la sincronizzazione in un thread separato."""
    from shopify_to_mysql import main as sync_main
    from src.config import log

    with _sync_lock:
        sync_status["last_error"] = None
    start = time.time()

    try:
        log("🔄 Sync triggerato via HTTP")
        sync_main()
        with _sync_lock:
            sync_status["last_status"] = "success"
    except Exception as exc:
        with _sync_lock:
            sync_status["last_status"] = "failed"
            sync_status["last_error"] = str(exc)
    finally:
        with _sync_lock:
            sync_status["running"] = False
            sync_status["last_duration"] = round(time.time() - start, 1)
            sync_status["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _bearer_token(auth_header):
    """Estrae il token da 'Authorization: Bearer <token>'. Schema case-insensitive."""
    if not auth_header:
        return None
    parts = auth_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _is_authorized():
    """True se uno dei due trasporti presenta il TRIGGER_SECRET corretto.

    Il trasporto `?secret=` in query string e' stato rimosso il 2026-08-03,
    dopo conferma di SERVER che il record del job Scheduler e' stato ripulito
    (url senza segreto, credenziale in `scheduler_jobs.headers`). Non
    reintrodurlo: un segreto nell'URL finisce in chiaro nei backup MySQL e
    negli access-log di Render.
    """
    if not TRIGGER_SECRET:
        return True
    expected = TRIGGER_SECRET.encode("utf-8")
    candidates = (
        _bearer_token(request.headers.get("Authorization")),
        request.headers.get("X-Trigger-Secret"),
    )
    return any(
        c is not None and hmac.compare_digest(c.encode("utf-8"), expected)
        for c in candidates
    )


@app.route("/health")
def health():
    return "OK", 200


@app.route("/api/trigger", methods=["GET", "POST"])
def trigger():
    if not _is_authorized():
        return jsonify({"error": "unauthorized"}), 401, {"WWW-Authenticate": "Bearer"}

    with _sync_lock:
        if sync_status["running"]:
            return jsonify({"status": "already_running"}), 409
        sync_status["running"] = True

    thread = threading.Thread(target=run_sync, daemon=True)
    thread.start()

    return jsonify({"status": "started", "message": "Sync avviato in background"}), 202


@app.route("/api/status")
def status():
    return jsonify(sync_status)


def _parse_hours_param() -> int:
    """Legge ?hours=, default DEFAULT_WINDOW_HOURS, clamp [MIN, MAX]. Valore
    assente o non numerico -> default (mai un errore per un query param)."""
    raw = request.args.get("hours")
    if raw is None:
        return lag_check.DEFAULT_WINDOW_HOURS
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        return lag_check.DEFAULT_WINDOW_HOURS
    return max(lag_check.MIN_WINDOW_HOURS, min(lag_check.MAX_WINDOW_HOURS, hours))


@app.route("/api/lag-check", methods=["GET"])
def lag_check_route():
    """
    Watchdog di sola lettura: Shopify vs mirror `online_products`
    (docs/sync-lag-plan.md, option (d), gate-1 Ale 2026-09-22). Mai una
    scrittura in nessun ramo. HTTP status resta sempre 200 (anche quando il
    check non e' concludente): il verdetto vive nel body, perche' il consumer
    e' una regola Scheduler `response_match` su `$.ok`. Il segreto non si
    accetta mai in query string — stesso contratto di `/api/trigger`.
    """
    if not _is_authorized():
        return jsonify({"error": "unauthorized"}), 401, {"WWW-Authenticate": "Bearer"}

    hours = _parse_hours_param()

    with _sync_lock:
        running = sync_status["running"]

    if running:
        # Un sync in corso riscrive il mirror: confrontarlo ora produrrebbe
        # solo falsi positivi. Non e' un errore, non si prova nemmeno.
        return jsonify(lag_check.skipped_response(hours)), 200

    try:
        config = Config.from_env(require_product_ids=False)
    except SystemExit:
        # Config.from_env() fa sys.exit(1) su env var mancanti: SystemExit
        # NON e' una Exception, va intercettata qui esplicitamente prima che
        # risalga al worker WSGI.
        return jsonify(lag_check.config_error_response(hours)), 200

    result = lag_check.run_lag_check(config, window_hours=hours)
    return jsonify(result), 200


@app.route("/")
def home():
    return jsonify({
        "service": "shopify-mysql-sync",
        "endpoints": {
            "/health": "Health check",
            "/api/trigger": "Avvia sincronizzazione (GET/POST)",
            "/api/status": "Stato ultima sincronizzazione",
        },
        **sync_status,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)

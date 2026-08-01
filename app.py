"""
Web wrapper per shopify-mysql-sync.
Espone endpoint HTTP per trigger da Scheduler e health check.
"""

import hmac
import os
import threading
import time
from flask import Flask, jsonify, request

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
    """True se uno qualsiasi dei trasporti presenta il TRIGGER_SECRET corretto.

    `Authorization: Bearer` e' il trasporto target (Scheduler). `X-Trigger-Secret`
    e `?secret=` restano per compatibilita' finche' SERVER non aggiorna il record
    del job (handoff 2026-08-01): togliere la query string prima romperebbe in
    silenzio il sync delle 03:00 Rome.
    """
    if not TRIGGER_SECRET:
        return True
    expected = TRIGGER_SECRET.encode("utf-8")
    candidates = (
        _bearer_token(request.headers.get("Authorization")),
        request.headers.get("X-Trigger-Secret"),
        request.args.get("secret"),
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

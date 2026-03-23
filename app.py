"""
Web wrapper per shopify-mysql-sync.
Espone endpoint HTTP per trigger da Scheduler e health check.
"""

import threading
import time
from flask import Flask, jsonify, redirect

app = Flask(__name__)

# Stato sync in-memory
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

    sync_status["running"] = True
    sync_status["last_error"] = None
    start = time.time()

    try:
        log("🔄 Sync triggerato via HTTP")
        sync_main()
        sync_status["last_status"] = "success"
    except Exception as exc:
        sync_status["last_status"] = "failed"
        sync_status["last_error"] = str(exc)
    finally:
        sync_status["running"] = False
        sync_status["last_duration"] = round(time.time() - start, 1)
        sync_status["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@app.route("/health")
def health():
    return "OK", 200


@app.route("/api/trigger", methods=["GET", "POST"])
def trigger():
    if sync_status["running"]:
        return jsonify({"status": "already_running"}), 409

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

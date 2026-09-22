"""
Test per gli endpoint Flask di app.py.
Copre: health check, trigger con/senza auth, status, home.
"""

import os
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def client_no_auth():
    """Client Flask senza TRIGGER_SECRET."""
    os.environ.pop("TRIGGER_SECRET", None)
    import importlib
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def client_with_auth():
    """Client Flask con TRIGGER_SECRET."""
    os.environ["TRIGGER_SECRET"] = "test-secret-123"
    import importlib
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c
    os.environ.pop("TRIGGER_SECRET", None)


class TestHealth:
    def test_health_ok(self, client_no_auth):
        response = client_no_auth.get("/health")
        assert response.status_code == 200
        assert response.data == b"OK"


class TestTriggerNoAuth:
    @patch("app.threading.Thread")
    def test_trigger_starts_sync(self, mock_thread, client_no_auth):
        response = client_no_auth.get("/api/trigger")
        assert response.status_code == 202
        assert response.json["status"] == "started"
        mock_thread.assert_called_once()
        mock_thread.return_value.start.assert_called_once()

    @patch("app.threading.Thread")
    def test_trigger_with_any_bearer_still_202_without_secret(self, mock_thread, client_no_auth):
        """Senza TRIGGER_SECRET in env, un bearer qualunque (anche sbagliato) non blocca."""
        response = client_no_auth.get(
            "/api/trigger",
            headers={"Authorization": "Bearer whatever-wrong-token"}
        )
        assert response.status_code == 202


class TestTriggerWithAuth:
    def test_trigger_without_secret_returns_401(self, client_with_auth):
        response = client_with_auth.get("/api/trigger")
        assert response.status_code == 401

    def test_trigger_with_wrong_secret_returns_401(self, client_with_auth):
        response = client_with_auth.get("/api/trigger?secret=wrong")
        assert response.status_code == 401

    def test_trigger_with_query_param_now_returns_401(self, client_with_auth):
        """Il trasporto ?secret= e' stato rimosso il 2026-08-03: non e' una regressione."""
        response = client_with_auth.get("/api/trigger?secret=test-secret-123")
        assert response.status_code == 401

    @patch("app.run_sync")
    def test_trigger_with_correct_header(self, mock_sync, client_with_auth):
        response = client_with_auth.get(
            "/api/trigger",
            headers={"X-Trigger-Secret": "test-secret-123"}
        )
        assert response.status_code == 202

    @patch("app.run_sync")
    def test_trigger_with_correct_bearer(self, mock_sync, client_with_auth):
        response = client_with_auth.get(
            "/api/trigger",
            headers={"Authorization": "Bearer test-secret-123"}
        )
        assert response.status_code == 202

    @patch("app.run_sync")
    def test_trigger_with_lowercase_bearer_scheme(self, mock_sync, client_with_auth):
        response = client_with_auth.get(
            "/api/trigger",
            headers={"Authorization": "bearer test-secret-123"}
        )
        assert response.status_code == 202

    def test_trigger_with_wrong_bearer_returns_401(self, client_with_auth):
        response = client_with_auth.get(
            "/api/trigger",
            headers={"Authorization": "Bearer wrong"}
        )
        assert response.status_code == 401

    def test_trigger_with_wrong_scheme_returns_401(self, client_with_auth):
        response = client_with_auth.get(
            "/api/trigger",
            headers={"Authorization": "Basic test-secret-123"}
        )
        assert response.status_code == 401

    def test_trigger_with_bearer_no_token_returns_401(self, client_with_auth):
        response = client_with_auth.get(
            "/api/trigger",
            headers={"Authorization": "Bearer"}
        )
        assert response.status_code == 401

    def test_trigger_with_bearer_blank_token_returns_401(self, client_with_auth):
        response = client_with_auth.get(
            "/api/trigger",
            headers={"Authorization": "Bearer   "}
        )
        assert response.status_code == 401

    def test_trigger_with_query_param_no_auth_header_now_returns_401(self, client_with_auth):
        """Il vecchio contratto ?secret= senza Authorization e' stato rimosso il 2026-08-03."""
        response = client_with_auth.get("/api/trigger?secret=test-secret-123")
        assert response.status_code == 401

    def test_trigger_with_correct_query_param_and_wrong_bearer_returns_401(self, client_with_auth):
        """La query string non puo' piu' 'salvare' una richiesta con bearer sbagliato."""
        response = client_with_auth.get(
            "/api/trigger?secret=test-secret-123",
            headers={"Authorization": "Bearer wrong"}
        )
        assert response.status_code == 401

    def test_401_exposes_www_authenticate_bearer_header(self, client_with_auth):
        response = client_with_auth.get("/api/trigger")
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == "Bearer"


class TestStatus:
    def test_status_returns_json(self, client_no_auth):
        response = client_no_auth.get("/api/status")
        assert response.status_code == 200
        data = response.json
        assert "running" in data
        assert "last_run" in data


class TestLagCheckAuth:
    """Caso 16: stesso contratto d'auth di /api/trigger, riusato via _is_authorized()."""

    @patch("app.lag_check.run_lag_check")
    @patch("app.Config.from_env")
    def test_no_secret_set_returns_200(self, mock_from_env, mock_run, client_no_auth):
        mock_from_env.return_value = MagicMock()
        mock_run.return_value = {"ok": True, "status": "clean", "reason": None}
        response = client_no_auth.get("/api/lag-check")
        assert response.status_code == 200
        mock_run.assert_called_once()

    @patch("app.lag_check.run_lag_check")
    @patch("app.Config.from_env")
    def test_wrong_bearer_returns_401(self, mock_from_env, mock_run, client_with_auth):
        response = client_with_auth.get(
            "/api/lag-check",
            headers={"Authorization": "Bearer wrong"}
        )
        assert response.status_code == 401
        mock_run.assert_not_called()

    @patch("app.lag_check.run_lag_check")
    @patch("app.Config.from_env")
    def test_correct_x_trigger_secret_returns_200(self, mock_from_env, mock_run, client_with_auth):
        mock_from_env.return_value = MagicMock()
        mock_run.return_value = {"ok": True, "status": "clean", "reason": None}
        response = client_with_auth.get(
            "/api/lag-check",
            headers={"X-Trigger-Secret": "test-secret-123"}
        )
        assert response.status_code == 200
        mock_run.assert_called_once()

    def test_secret_in_query_string_is_never_accepted(self, client_with_auth):
        response = client_with_auth.get("/api/lag-check?secret=test-secret-123")
        assert response.status_code == 401

    def test_401_exposes_www_authenticate_bearer_header(self, client_with_auth):
        response = client_with_auth.get("/api/lag-check")
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == "Bearer"


class TestLagCheckSkipped:
    """Caso 9: sync in corso -> skipped, ok:true, run_lag_check MAI chiamato
    (non si confronta contro un mirror in fase di riscrittura)."""

    @patch("app.lag_check.run_lag_check")
    def test_running_sync_returns_skipped_without_running_the_check(self, mock_run, client_no_auth):
        import app as app_module

        with app_module._sync_lock:
            app_module.sync_status["running"] = True
        try:
            response = client_no_auth.get("/api/lag-check")
        finally:
            with app_module._sync_lock:
                app_module.sync_status["running"] = False

        assert response.status_code == 200
        data = response.json
        assert data["status"] == "skipped"
        # Amendment 2: skipped e' un non-verdetto, non un GREEN.
        assert data["ok"] is False
        assert data["checked"] is False
        mock_run.assert_not_called()


class TestLagCheckConfigError:
    """Config.from_env() manca env var obbligatorie -> SystemExit intercettata,
    mai lasciata risalire al worker WSGI; il verdetto resta nel body a 200."""

    def test_missing_shopify_env_vars_returns_inconclusive_not_500(self, client_no_auth, monkeypatch):
        for var in ("SHOPIFY_DOMAIN", "SHOPIFY_TOKEN", "DB_HOST", "DB_USER", "DB_PASS", "DB_NAME"):
            monkeypatch.delenv(var, raising=False)

        response = client_no_auth.get("/api/lag-check")

        assert response.status_code == 200
        data = response.json
        assert data["status"] == "inconclusive"
        assert data["ok"] is False
        assert data["checked"] is False
        assert data["reason"] == "config_error"


class TestHome:
    def test_home_returns_service_info(self, client_no_auth):
        response = client_no_auth.get("/")
        assert response.status_code == 200
        data = response.json
        assert data["service"] == "shopify-mysql-sync"
        assert "/health" in data["endpoints"]

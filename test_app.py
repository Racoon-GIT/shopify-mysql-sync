"""
Test per gli endpoint Flask di app.py.
Copre: health check, trigger con/senza auth, status, home.
"""

import os
import pytest
from unittest.mock import patch


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


class TestHome:
    def test_home_returns_service_info(self, client_no_auth):
        response = client_no_auth.get("/")
        assert response.status_code == 200
        data = response.json
        assert data["service"] == "shopify-mysql-sync"
        assert "/health" in data["endpoints"]

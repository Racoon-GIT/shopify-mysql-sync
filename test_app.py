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


class TestTriggerWithAuth:
    def test_trigger_without_secret_returns_401(self, client_with_auth):
        response = client_with_auth.get("/api/trigger")
        assert response.status_code == 401

    def test_trigger_with_wrong_secret_returns_401(self, client_with_auth):
        response = client_with_auth.get("/api/trigger?secret=wrong")
        assert response.status_code == 401

    @patch("app.run_sync")
    def test_trigger_with_correct_query_param(self, mock_sync, client_with_auth):
        response = client_with_auth.get("/api/trigger?secret=test-secret-123")
        assert response.status_code == 202

    @patch("app.run_sync")
    def test_trigger_with_correct_header(self, mock_sync, client_with_auth):
        response = client_with_auth.get(
            "/api/trigger",
            headers={"X-Trigger-Secret": "test-secret-123"}
        )
        assert response.status_code == 202


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

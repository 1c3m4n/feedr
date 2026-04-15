import importlib
import os
import sys


def test_session_cookie_is_secure_for_https_app_url(tmp_path, monkeypatch):
    db_path = tmp_path / "secure_cookie.db"

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("FEEDR_DISABLE_BACKGROUND_FETCHER", "1")
    monkeypatch.setenv("LOCAL_AUTH_ENABLED", "1")
    monkeypatch.setenv("APP_URL", "https://feedr.example.com")
    monkeypatch.setenv("SECRET_KEY", "production-secret")

    sys.modules.pop("main", None)
    module = importlib.import_module("main")

    try:
        from fastapi.testclient import TestClient

        with TestClient(module.app) as client:
            response = client.post(
                "/auth/local",
                data={"username": "secure-user", "password": "password123"},
                follow_redirects=False,
            )
        assert response.status_code == 303
        assert "secure" in response.headers["set-cookie"].lower()
    finally:
        sys.modules.pop("main", None)


def test_default_secret_is_rejected_for_non_local_app_url(tmp_path, monkeypatch):
    db_path = tmp_path / "bad_secret.db"

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("FEEDR_DISABLE_BACKGROUND_FETCHER", "1")
    monkeypatch.setenv("APP_URL", "https://feedr.example.com")
    monkeypatch.delenv("SECRET_KEY", raising=False)

    sys.modules.pop("main", None)

    try:
        try:
            importlib.import_module("main")
        except RuntimeError as exc:
            assert str(exc) == "SECRET_KEY must be set for non-local deployments"
        else:
            raise AssertionError("Expected RuntimeError for missing production secret")
    finally:
        sys.modules.pop("main", None)

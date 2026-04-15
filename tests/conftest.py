import importlib
import os
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def app_module(tmp_path_factory):
    db_dir = tmp_path_factory.mktemp("data")
    db_path = db_dir / "test_feedr.db"

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["FEEDR_DISABLE_BACKGROUND_FETCHER"] = "1"
    os.environ["LOCAL_AUTH_ENABLED"] = "1"
    os.environ.setdefault("SECRET_KEY", "test-secret")

    sys.modules.pop("main", None)
    module = importlib.import_module("main")
    return module


@pytest.fixture()
def db(app_module):
    app_module.Base.metadata.drop_all(bind=app_module.engine)
    app_module.Base.metadata.create_all(bind=app_module.engine)
    app_module.ensure_schema()
    session = app_module.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(app_module, db):
    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture()
def login_local(app_module, client):
    def _login(username: str, password: str = "password123"):
        response = client.post(
            "/auth/local",
            data={"username": username, "password": password},
            follow_redirects=False,
        )
        assert response.status_code == 303
        return response

    return _login

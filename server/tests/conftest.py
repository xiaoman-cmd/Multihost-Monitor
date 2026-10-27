from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "test-only-password-123!"


@pytest.fixture
def database(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'monitor.db'}"
    monkeypatch.setenv("MHM_DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    return url, cfg


@pytest.fixture
def application(database, tmp_path):
    url, _ = database
    static = tmp_path / 'dist'
    static.mkdir()
    (static / 'index.html').write_text('<html>monitor frontend</html>')
    (static / 'app.js').write_text('console.log("monitor")')
    return create_app(Settings(database_url=url, admin_password=PASSWORD, retention_enabled=False, _env_file=None), static_dir=static)


@pytest.fixture
def client(application):
    with TestClient(application) as client:
        yield client


@pytest.fixture
def login(client):
    response = client.post('/api/auth/login', json={'username': 'admin', 'password': PASSWORD},
                           headers={'Origin': 'http://127.0.0.1:8000'})
    assert response.status_code == 200
    return response.json()

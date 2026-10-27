import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text

from app.config import Settings
from app.main import create_app
from app.models import AdminSession, AdminUser, Host, now
from app.security import COOKIE_NAME, digest, verify
from conftest import PASSWORD

ORIGIN = {'Origin': 'http://127.0.0.1:8000'}


def test_health_and_headers(client):
    for path in ['/api/health/live', '/api/health/ready']:
        result = client.get(path)
        assert result.status_code == 200
        assert result.json()['status'] == 'ok'
        assert result.headers['Cache-Control'] == 'no-store'
        assert result.headers['X-Content-Type-Options'] == 'nosniff'
        assert "frame-ancestors 'none'" in result.headers['Content-Security-Policy']


def test_readiness_schema_failure(client, application):
    with application.state.engine.begin() as db:
        db.execute(text("UPDATE alembic_version SET version_num='wrong'"))
    response = client.get('/api/health/ready')
    assert response.status_code == 503
    assert response.json() == {'status': 'unavailable'}
    assert client.get('/api/health/live').status_code == 200


@pytest.mark.parametrize('path', ['/api/hosts', '/api/auth/me'])
def test_auth_required(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize('username,password', [('admin', 'bad'), ('unknown', PASSWORD)])
def test_generic_invalid_credentials(client, username, password):
    response = client.post('/api/auth/login', json={'username': username, 'password': password}, headers=ORIGIN)
    assert response.status_code == 401
    assert response.json()['detail'] == 'Invalid username or password'
    assert COOKIE_NAME not in client.cookies


def test_login_storage_cookie_and_empty_hosts(client, login, application):
    token = client.cookies[COOKIE_NAME]
    with application.state.sessions() as db:
        admin = db.scalar(select(AdminUser))
        assert admin.password_hash != PASSWORD
        assert verify(PASSWORD, admin.password_hash)
        assert db.get(AdminSession, token) is None
        assert db.get(AdminSession, digest(token)) is not None
    assert client.get('/api/auth/me').json() == login
    assert client.get('/api/hosts').json() == {'items': []}
    response = client.post('/api/auth/login', json={'username':'admin', 'password':PASSWORD}, headers=ORIGIN)
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Path=/' in cookie
    assert 'Max-Age=28800' in cookie
    assert client.cookies[COOKIE_NAME] != token
    with application.state.sessions() as db:
        assert db.get(AdminSession, digest(token)) is None
        assert len(db.scalars(select(AdminSession)).all()) == 1


def test_secure_cookie(database):
    app = create_app(Settings(database_url=database[0], admin_password=PASSWORD,
                              cookie_secure=True, origin='https://monitor.test', _env_file=None))
    with TestClient(app, base_url='https://monitor.test') as client:
        response = client.post('/api/auth/login', json={'username':'admin','password':PASSWORD},
                               headers={'Origin':'https://monitor.test'})
        assert 'Secure' in response.headers['set-cookie']
        assert client.get('/api/auth/me').status_code == 200


def test_logout_csrf_revocation(client, login):
    token = client.cookies[COOKIE_NAME]
    assert client.post('/api/auth/logout', headers=ORIGIN).status_code == 403
    assert client.post('/api/auth/logout', headers={**ORIGIN, 'X-CSRF-Token':'wrong'}).status_code == 403
    assert client.post('/api/auth/logout', headers={**ORIGIN, 'X-CSRF-Token':login['csrf_token']}).status_code == 204
    assert COOKIE_NAME not in client.cookies
    client.cookies.set(COOKIE_NAME, token)
    assert client.get('/api/auth/me').status_code == 401


@pytest.mark.parametrize('origin', [None, 'https://evil.test', 'null'])
def test_login_rejects_cross_origin(client, origin):
    response = client.post('/api/auth/login', json={'username':'admin', 'password':PASSWORD},
                           headers={'Origin':origin} if origin else {})
    assert response.status_code == 403


def test_logout_rejects_cross_origin(client, login):
    assert client.post('/api/auth/logout', headers={'Origin':'https://evil.test',
                        'X-CSRF-Token':login['csrf_token']}).status_code == 403


def test_expired_or_forged_session(client, login, application):
    token = client.cookies[COOKIE_NAME]
    with application.state.sessions.begin() as db:
        session = db.get(AdminSession, digest(token))
        session.created_at = now() - 20
        session.expires_at = now() - 10
    assert client.get('/api/auth/me').status_code == 401
    client.cookies.set(COOKIE_NAME, 'forged')
    assert client.get('/api/hosts').status_code == 401


def test_login_throttled(client):
    for _ in range(5):
        assert client.post('/api/auth/login', json={'username':'admin','password':'bad'}, headers=ORIGIN).status_code == 401
    response = client.post('/api/auth/login', json={'username':'admin','password':PASSWORD}, headers=ORIGIN)
    assert response.status_code == 429
    assert response.headers['Retry-After'] == '60'


def test_login_validation(client):
    assert client.post('/api/auth/login', json={'username':'', 'password':'x'}, headers=ORIGIN).status_code == 422
    assert client.post('/api/auth/login', json={'username':'admin','password':'x'*1025}, headers=ORIGIN).status_code == 422


def test_hosts_real_data_no_secret_exposure(client, login, application):
    with application.state.sessions.begin() as db:
        db.add(Host(name='real-host', hostname='host.example'))
    response = client.get('/api/hosts')
    assert response.json()['items'] == [{'id':1, 'name':'real-host', 'hostname':'host.example', 'status':'pending', 'last_seen_at':None}]
    assert 'token' not in response.text


def test_static_spa_and_missing_api(client):
    assert 'monitor frontend' in client.get('/').text
    assert client.get('/dashboard').status_code == 200
    assert client.get('/app.js').status_code == 200
    assert client.get('/api/unknown').status_code == 404
    assert client.get('/missing.js').status_code == 404
    assert client.get('/%2e%2e/%2e%2e/.env').status_code == 404


def test_missing_frontend(database, tmp_path):
    app = create_app(Settings(database_url=database[0], admin_password=PASSWORD, _env_file=None), static_dir=tmp_path / 'missing')
    with TestClient(app) as client:
        assert client.get('/').status_code == 503


def test_startup_requires_migration(tmp_path):
    app = create_app(Settings(database_url=f'sqlite:///{tmp_path / "empty.db"}', admin_password=PASSWORD, _env_file=None))
    with pytest.raises(Exception, match='alembic_version'):
        with TestClient(app):
            pass


@pytest.mark.parametrize('overrides', [dict(admin_password='short'), dict(database_url='postgresql://localhost/db'),
    dict(database_url='sqlite:///:memory:'), dict(origin='https://example.test/'), dict(cookie_secure=True), dict(session_hours=0)])
def test_insecure_configuration_rejected(overrides):
    with pytest.raises(ValidationError):
        Settings(**({'admin_password':PASSWORD} | overrides), _env_file=None)


def test_new_login_invalidates_old_browser(client, login, application):
    old_token = client.cookies[COOKIE_NAME]
    with TestClient(application) as second:
        assert second.post('/api/auth/login', json={'username':'admin','password':PASSWORD}, headers=ORIGIN).status_code == 200
        assert second.get('/api/auth/me').status_code == 200
        assert client.get('/api/auth/me').status_code == 401
    with application.state.sessions() as db:
        assert db.get(AdminSession, digest(old_token)) is None


def test_startup_does_not_reset_password(application, client):
    settings = Settings(database_url=application.state.settings.database_url,
                        admin_password='different-password-123', _env_file=None)
    with pytest.raises(RuntimeError, match='differs from database'):
        with TestClient(create_app(settings)):
            pass
    assert client.post('/api/auth/login', json={'username':'admin','password':PASSWORD}, headers=ORIGIN).status_code == 200


def test_session_survives_application_restart(client, login, application):
    token = client.cookies[COOKIE_NAME]
    restarted = create_app(application.state.settings)
    with TestClient(restarted) as second:
        second.cookies.set(COOKIE_NAME, token)
        assert second.get('/api/auth/me').json() == login


def test_config_validation_does_not_log_password():
    secret = 'private-invalid-password'
    with pytest.raises(ValidationError) as error:
        Settings(admin_password=secret, session_hours=0, _env_file=None)
    assert secret not in str(error.value)

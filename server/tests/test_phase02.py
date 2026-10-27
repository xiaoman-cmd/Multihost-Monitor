import copy
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import func, select, text

from app.agent_api import MAX_BODY
from app.models import AgentCredential, AgentSample, DiskMetric, Host, HostMetric, now
from app.security import digest

ORIGIN = {'Origin': 'http://127.0.0.1:8000'}


@pytest.fixture
def admin_headers(login):
    return {**ORIGIN, 'X-CSRF-Token': login['csrf_token']}


@pytest.fixture
def registered(client, admin_headers):
    response = client.post('/api/v1/hosts', json={'name': 'test-linux', 'hostname': 'linux-a',
        'cloud_provider':'local', 'region':'lab', 'tags':['test'], 'systemd_units':['sshd.service']}, headers=admin_headers)
    assert response.status_code == 201, response.text
    return response.json()


def sample(at=None):
    return {'sample_id': str(uuid4()), 'sampled_at': at or now(),
        'metadata': {'hostname':'linux-a','os':'linux/debian/12','arch':'amd64','agent_version':'1.0.0-phase02'},
        'cpu_percent': 12.5, 'load_1':0.1,'load_5':0.2,'load_15':0.3,
        'memory_total_bytes':1024,'memory_available_bytes':512,'swap_total_bytes':100,'swap_used_bytes':10,
        'uptime_seconds':1000,'disks':[{'mountpoint':'/','total_bytes':1000,'used_bytes':100,'available_bytes':800,'inode_percent':1.0}],
        'networks':[{'interface':'eth0','received_bytes':100,'sent_bytes':200,'receive_rate':None,'send_rate':None}],
        'services':[{'unit':'sshd.service','state':'active'}]}


def upload(client, registered, samples=None, host_id=None, token=None):
    return client.post('/api/v1/agent/metrics', json={'host_id':host_id or registered['host']['id'],
        'samples':samples if samples is not None else [sample()]},
        headers={'Authorization':'Bearer '+(token or registered['agent_token'])})


def count(application, model):
    with application.state.sessions() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_registration_only_once_token(client, registered, application, admin_headers):
    token = registered['agent_token']
    assert len(token) == 64
    assert registered['host']['tags'] == ['test']
    with application.state.sessions() as db:
        credential = db.scalar(select(AgentCredential))
        assert credential.token_hash == digest(token)
        assert credential.token_hash != token
    assert token not in client.get('/api/v1/hosts').text
    assert client.post('/api/v1/hosts', json={'name':'test-linux','hostname':'duplicate'}, headers=admin_headers).status_code == 409
    assert count(application, Host) == 1


@pytest.mark.parametrize('endpoint,method', [('/api/v1/hosts','post'),('/api/v1/hosts/1','put'),
    ('/api/v1/hosts/1/token/rotate','post'),('/api/v1/hosts/1/token/revoke','post')])
def test_admin_mutations_require_csrf(client, registered, endpoint, method):
    assert getattr(client, method)(endpoint, json={'name':'a','hostname':'a'}, headers=ORIGIN).status_code == 403
    assert getattr(client, method)(endpoint, json={'name':'a','hostname':'a'}, headers={'Origin':'https://evil.test'}).status_code == 403


def test_agent_cannot_register_or_query(client, registered):
    client.cookies.clear()
    headers={'Authorization':'Bearer '+registered['agent_token']}
    assert client.post('/api/v1/hosts', json={'name':'new','hostname':'new'}, headers=headers).status_code == 401
    assert client.get('/api/v1/hosts/1/metrics', headers=headers).status_code == 401


def test_rotated_revoked_expired_and_wrong_tokens(client, registered, admin_headers, application):
    original = registered['agent_token']
    response = client.post('/api/v1/hosts/1/token/rotate', headers=admin_headers)
    new = response.json()['agent_token']
    assert new != original
    errors = [upload(client, registered, token=original), upload(client, registered, token='mhm_'+'x'*60)]
    assert upload(client, registered, token=new).status_code == 200
    client.post('/api/v1/hosts/1/token/revoke', headers=admin_headers)
    errors.append(upload(client, registered, token=new))
    for result in errors:
        assert result.status_code == 401
        assert result.json() == {'detail':'Agent authentication failed'}
    response = client.post('/api/v1/hosts/1/token/rotate', headers=admin_headers)
    token = response.json()['agent_token']
    with application.state.sessions.begin() as db:
        cred = db.scalar(select(AgentCredential).where(AgentCredential.token_hash == digest(token)))
        cred.created_at = now()-20
        cred.expires_at = now()-10
    assert upload(client, registered, token=token).status_code == 401


def test_strong_host_binding_all_agent_routes(client, registered, admin_headers, application):
    second = client.post('/api/v1/hosts', json={'name':'other','hostname':'linux-b'}, headers=admin_headers).json()['host']['id']
    assert upload(client, registered, host_id=second).status_code == 401
    headers={'Authorization':'Bearer '+registered['agent_token']}
    assert client.get(f'/api/v1/agent/config?host_id={second}', headers=headers).status_code == 401
    assert client.post('/api/v1/agent/heartbeat', json={'host_id':second,'sampled_at':now(), 'metadata':sample()['metadata']},headers=headers).status_code == 401
    assert count(application, AgentSample) == 0
    with application.state.sessions() as db:
        assert db.get(Host, second).last_seen_at is None


def test_atomic_idempotent_storage_and_query(client, registered, application):
    payload=sample()
    assert upload(client, registered, [payload]).json() == {'accepted':1,'duplicates':0}
    assert upload(client, registered, [payload]).json() == {'accepted':0,'duplicates':1}
    assert count(application, AgentSample) == count(application, HostMetric) == count(application, DiskMetric) == 1
    result=client.get('/api/v1/hosts/1/metrics').json()['items'][0]
    assert result['sample_id'] == payload['sample_id']
    assert result['networks'][0]['receive_rate'] is None
    assert result['disks'][0]['inode_percent'] == 1
    with application.state.sessions() as db:
        assert db.scalar(select(HostMetric)).memory_used_bytes == 512
        host=db.get(Host, 1)
        assert host.status == 'online' and host.last_seen_at is not None
    altered=copy.deepcopy(payload);altered['cpu_percent']=90.0
    assert upload(client, registered, [altered]).status_code == 409
    assert count(application, AgentSample) == 1


def test_whole_batch_rolls_back_on_late_conflict(client, registered, application):
    first=sample(now()-30)
    assert upload(client, registered, [first]).status_code == 200
    new=sample(now())
    conflict=copy.deepcopy(first);conflict['cpu_percent']=80.0
    with application.state.sessions.begin() as db:
        db.get(Host,1).last_seen_at=1
    response=upload(client, registered, [new,conflict])
    assert response.status_code == 409
    assert count(application, AgentSample) == count(application, HostMetric) == count(application, DiskMetric) == 1
    with application.state.sessions() as db:
        assert db.get(Host,1).last_seen_at == 1


@pytest.mark.parametrize('change', [
    {'cpu_percent':101.0}, {'cpu_percent':-1.0}, {'memory_available_bytes':2000}, {'swap_used_bytes':101},
    {'sample_id':'not-a-uuid'}, {'sampled_at':1}, {'sampled_at':9999999999}, {'load_1':-1.0},
    {'uptime_seconds':-1}, {'cpu_percent':'10'}, {'unexpected':'value'},
    {'services':[{'unit':'evil.service','state':'active'}]},
    {'services':[{'unit':'sshd.service;id','state':'active'}]},
    {'networks':[{'interface':'eth0','received_bytes':1,'sent_bytes':2,'receive_rate':-1.0,'send_rate':None}]},
    {'disks':[{'mountpoint':'/../etc','total_bytes':100,'used_bytes':1,'available_bytes':10,'inode_percent':None}]},
])
def test_invalid_batch_has_no_writes(client, registered, application, change):
    good=sample(now()-30);bad=sample();bad.update(change)
    assert upload(client, registered, [good,bad]).status_code == 422
    assert count(application, AgentSample) == count(application, HostMetric) == count(application, DiskMetric) == 0
    with application.state.sessions() as db:
        assert db.get(Host,1).last_seen_at is None


def test_batch_limits_and_duplicates(client, registered):
    assert upload(client, registered, []).status_code == 422
    values=[sample(now()-i*30) for i in range(33)]
    assert upload(client, registered, values).status_code == 422
    assert upload(client, registered, [values[0],values[0]]).status_code == 422
    values[1]['sampled_at']=values[0]['sampled_at']
    assert upload(client, registered, values[:2]).status_code == 422


def test_actual_streamed_body_limit(client, registered):
    headers={'Authorization':'Bearer '+registered['agent_token'],'Content-Type':'application/json'}
    response=client.post('/api/v1/agent/metrics',content=iter([b' '*600000,b' '*600000]),headers=headers)
    assert response.status_code == 413
    assert client.post('/api/v1/agent/metrics',content=b'x'*(MAX_BODY+1),headers=headers).status_code == 413


def test_heartbeat_history_never_regresses_metadata(client, registered, application):
    headers={'Authorization':'Bearer '+registered['agent_token']}
    meta=sample()['metadata']
    response=client.post('/api/v1/agent/heartbeat',json={'host_id':1,'sampled_at':now(),'metadata':meta},headers=headers)
    assert response.status_code == 200
    history=sample(now()-60);history['metadata']['hostname']='old-host'
    assert upload(client, registered, [history]).status_code == 200
    with application.state.sessions() as db:
        host=db.get(Host,1)
        assert host.hostname == 'linux-a'
        assert host.os == meta['os']
    bad=client.post('/api/v1/agent/heartbeat',json={'host_id':1,'sampled_at':1,'metadata':meta},headers=headers)
    assert bad.status_code == 422


def test_concurrent_duplicate_requests(client, registered, application):
    payload=sample()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses=list(pool.map(lambda _:upload(client, registered, [payload]),range(4)))
    assert all(r.status_code == 200 for r in responses)
    assert sum(r.json()['accepted'] for r in responses) == 1
    assert count(application, AgentSample) == 1


def test_config_and_admin_configuration(client, registered, admin_headers):
    headers={'Authorization':'Bearer '+registered['agent_token']}
    assert client.get('/api/v1/agent/config?host_id=1',headers=headers).json()['systemd_units'] == ['sshd.service']
    response=client.put('/api/v1/hosts/1',json={'name':'renamed','hostname':'a','tags':['changed'],'systemd_units':[]},headers=admin_headers)
    assert response.status_code == 200
    assert client.get('/api/v1/agent/config?host_id=1',headers=headers).json()['systemd_units'] == []
    assert upload(client, registered).status_code == 422


def test_disabled_host_and_missing_auth(client, registered, application):
    assert client.get('/api/v1/agent/config?host_id=1').status_code == 401
    with application.state.sessions.begin() as db:
        db.get(Host,1).status='disabled'
    assert upload(client, registered).status_code == 401


def test_query_filters_pagination(client, registered):
    values=[sample(now()-i*30) for i in range(3)]
    assert upload(client, registered, values).status_code == 200
    first=client.get('/api/v1/hosts/1/metrics?limit=2').json()
    next_page=client.get(f"/api/v1/hosts/1/metrics?before_id={first['next_before_id']}&limit=2").json()
    assert len(first['items']) == 2 and len(next_page['items']) == 1
    assert client.get(f"/api/v1/hosts/1/metrics?since={now()-1}").json()['items'][0]['sample_id'] == values[0]['sample_id']
    assert client.get('/api/v1/hosts/1/metrics?limit=101').status_code == 422


def test_upgrade_with_populated_phase01_and_rollback(database):
    url,cfg=database
    from app.db import make_engine
    engine=make_engine(url)
    command.downgrade(cfg,'0001_phase01')
    with engine.begin() as db:
        db.execute(text("INSERT INTO hosts(id,name,hostname,status,created_at) VALUES(1,'legacy','legacy','pending',1)"))
        db.execute(text('INSERT INTO host_metrics(id,host_id,sampled_at,cpu_percent,memory_total_bytes,memory_used_bytes,load_1) VALUES(1,1,1,10,100,50,0)'))
    command.upgrade(cfg,'head')
    with engine.connect() as db:
        assert db.scalar(text('SELECT tags FROM hosts')) == '[]'
        assert db.scalar(text('SELECT cpu_percent FROM host_metrics')) == 10
        assert db.execute(text('PRAGMA foreign_key_check')).all() == []
    command.downgrade(cfg,'0001_phase01')
    with engine.connect() as db:
        assert db.scalar(text('SELECT name FROM hosts')) == 'legacy'
        assert db.scalar(text('SELECT count(*) FROM host_metrics')) == 1
        assert db.execute(text('PRAGMA foreign_key_check')).all() == []
    command.upgrade(cfg,'head')
    engine.dispose()


def test_stream_timeout(monkeypatch):
    import asyncio
    import app.agent_api as module
    from app.agent_api import BodyLimit
    monkeypatch.setattr(module, 'BODY_TIMEOUT', .001)
    messages=[]
    async def downstream(scope, receive, send):
        raise AssertionError('timed out body reached application')
    async def receive():
        await asyncio.sleep(1)
    async def send(message):
        messages.append(message)
    asyncio.run(BodyLimit(downstream)({'type':'http','path':'/api/v1/agent/metrics'},receive,send))
    assert messages[0]['status'] == 408


def test_new_sample_constraints_and_host_cascade(client, registered, application):
    from sqlalchemy.exc import IntegrityError
    assert upload(client, registered).status_code == 200
    with application.state.sessions() as db:
        row=db.scalar(select(AgentSample))
        payload=dict(host_id=row.host_id,sample_id=row.sample_id,sampled_at=row.sampled_at,
                     content_hash=row.content_hash,host_metric_id=row.host_metric_id,payload=row.payload)
    with pytest.raises(IntegrityError):
        with application.state.sessions.begin() as db:
            db.add(AgentSample(**payload))
            db.flush()
    with application.state.sessions.begin() as db:
        db.delete(db.get(Host,1))
    assert count(application, AgentSample) == count(application, HostMetric) == count(application, DiskMetric) == 0

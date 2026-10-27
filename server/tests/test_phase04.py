import asyncio
import json
import os
import socket
import ssl
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from alembic import command
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app import alerts, notifications, outbound, scheduler
from app.config import Settings
from app.db import make_engine, session_factory
from app.models import (AlertEvent, AlertInstance, AlertRule, CheckResult, Host,
                        NotificationChannel, NotificationDelivery, ServiceCheck)
from app.monitoring_api import RuleInput
from app.retention import cleanup_batch
from test_dashboard import STAMP, hosts, store
from test_phase02 import sample


@pytest.fixture
def stamp(monkeypatch):
    for module in [alerts, notifications, scheduler]:
        monkeypatch.setattr(module, 'now', lambda: STAMP)
    monkeypatch.setattr('app.monitoring_api.now', lambda: STAMP)
    return STAMP


@pytest.fixture
def seeded(application, stamp):
    host_id = hosts(application, [{'name': 'isolated-test', 'last_seen_at': STAMP, 'systemd_units': ['ssh.service']}])[0]
    with application.state.sessions.begin() as db:
        channel = NotificationChannel(name='test reference only', kind='webhook', config={'secret_ref': 'test.json'})
        db.add(channel)
        db.flush()
        rule = AlertRule(host_id=host_id, name='test cpu', metric='cpu_percent', operator='gt', threshold=80,
                         recovery_threshold=60, duration_seconds=20, cooldown_seconds=60, channel_ids=[channel.id])
        db.add(rule)
        db.flush()
        return host_id, rule.id, channel.id


def reading(application, host_id, at, **changes):
    value = sample(at)
    value.update(changes)
    store(application, host_id, [value], received_at=at)


def state(application, rule_id, host_id, at=STAMP):
    return alerts.evaluate_one(application.state.sessions, rule_id, host_id, at)


def counts(application):
    with application.state.sessions() as db:
        return [db.scalar(select(func.count()).select_from(model)) for model in [AlertInstance, AlertEvent, NotificationDelivery]]


def test_pending_firing_hysteresis_repeat_recovery_restart(application, seeded):
    host, rule, _ = seeded
    reading(application, host, STAMP, cpu_percent=90)
    assert state(application, rule, host)['state'] == 'pending'
    assert state(application, rule, host, STAMP+19)['state'] == 'pending'
    assert state(application, rule, host, STAMP+20)['state'] == 'firing'
    assert counts(application) == [1, 1, 1]
    state(application, rule, host, STAMP+30)
    assert counts(application) == [1, 1, 1]
    # A fresh SQLAlchemy engine/session factory simulates state reloading after restart.
    engine = make_engine(application.state.settings.database_url)
    sessions = session_factory(engine)
    assert alerts.evaluate_one(sessions, rule, host, STAMP+80)['state'] == 'firing'
    assert counts(application) == [1, 2, 2]
    reading(application, host, STAMP+81, cpu_percent=70)
    assert state(application, rule, host, STAMP+81)['state'] == 'firing'
    reading(application, host, STAMP+82, cpu_percent=60)
    assert state(application, rule, host, STAMP+82)['state'] == 'resolved'
    assert counts(application) == [1, 3, 3]
    engine.dispose()


def test_missing_is_not_normal_and_does_not_resolve(application, seeded):
    host, rule, _ = seeded
    assert state(application, rule, host)['data_status'] == 'missing'
    reading(application, host, STAMP, cpu_percent=90)
    assert state(application, rule, host)['state'] == 'pending'
    assert state(application, rule, host, STAMP+91)['state'] == 'normal'
    reading(application, host, STAMP+92, cpu_percent=90)
    assert state(application, rule, host, STAMP+92)['state'] == 'pending'
    assert state(application, rule, host, STAMP+112)['state'] == 'firing'
    assert state(application, rule, host, STAMP+200)['state'] == 'firing'
    assert state(application, rule, host, STAMP+200)['data_status'] == 'missing'


@pytest.mark.parametrize('metric,operator,threshold,recovery,target,change', [
    ('cpu_percent', 'gt', 80, 60, '', {'cpu_percent': 95}),
    ('memory_percent', 'lt', 20, 30, '', {'memory_total_bytes': 1000, 'memory_available_bytes': 100}),
    ('disk_percent', 'gt', 80, 70, '/', {'disks': [{'mountpoint':'/', 'total_bytes':100, 'used_bytes':90, 'available_bytes':10, 'inode_percent':1.0}]}),
    ('service_down', 'gte', 1, 0, 'ssh.service', {'services': [{'unit':'ssh.service','state':'failed'}]}),
])
def test_resource_and_systemd_rules(application, seeded, metric, operator, threshold, recovery, target, change):
    host, rule_id, _ = seeded
    with application.state.sessions.begin() as db:
        rule = db.get(AlertRule, rule_id)
        rule.metric, rule.operator, rule.threshold, rule.recovery_threshold, rule.target, rule.duration_seconds = metric, operator, threshold, recovery, target, 0
    reading(application, host, STAMP, **change)
    assert state(application, rule_id, host)['state'] == 'firing'


@pytest.mark.parametrize('service_state', ['unknown', 'unavailable'])
def test_unknown_services_are_missing(application, seeded, service_state):
    host, rule_id, _ = seeded
    with application.state.sessions.begin() as db:
        rule = db.get(AlertRule, rule_id)
        rule.metric, rule.target = 'service_down', 'ssh.service'
    reading(application, host, STAMP, services=[{'unit':'ssh.service','state':service_state}])
    assert state(application, rule_id, host)['data_status'] == 'missing'


def test_offline_uses_center_time_and_recovers(application, seeded):
    host_id, rule_id, _ = seeded
    with application.state.sessions.begin() as db:
        rule = db.get(AlertRule, rule_id)
        rule.metric, rule.operator, rule.threshold, rule.recovery_threshold, rule.duration_seconds = 'host_offline', 'gte', 1, 0, 0
    assert state(application, rule_id, host_id, STAMP+90)['state'] == 'normal'
    assert state(application, rule_id, host_id, STAMP+91)['state'] == 'firing'
    with application.state.sessions.begin() as db:
        db.get(Host, host_id).last_seen_at = STAMP+92
    assert state(application, rule_id, host_id, STAMP+92)['state'] == 'resolved'


def test_disabled_and_evaluation_gap_reset_pending(application, seeded):
    host, rule, _ = seeded
    reading(application, host, STAMP, cpu_percent=99)
    state(application, rule, host)
    assert state(application, rule, host, STAMP+40)['state'] == 'pending'
    with application.state.sessions.begin() as db:
        db.get(Host, host).status = 'disabled'
    assert state(application, rule, host, STAMP+41)['state'] == 'normal'
    assert counts(application)[1:] == [0, 0]


def test_rollback_event_outbox_is_atomic(application, seeded, monkeypatch):
    host, rule, _ = seeded
    reading(application, host, STAMP, cpu_percent=99)
    with application.state.sessions.begin() as db:
        db.get(AlertRule, rule).duration_seconds = 0
    original = alerts.emit
    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('test transaction failure')
    monkeypatch.setattr(alerts, 'emit', crash)
    with pytest.raises(RuntimeError):
        state(application, rule, host)
    assert counts(application) == [0, 0, 0]


def test_concurrent_evaluation_deduplicates(application, seeded):
    host, rule, _ = seeded
    reading(application, host, STAMP, cpu_percent=99)
    with application.state.sessions.begin() as db:
        db.get(AlertRule, rule).duration_seconds = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: state(application, rule, host), range(8)))
    assert counts(application) == [1, 1, 1]


def test_ack_silence_and_recovery_notifications(client, login, application, seeded):
    host, rule, _ = seeded
    reading(application, host, STAMP, cpu_percent=99)
    state(application, rule, host)
    result = state(application, rule, host, STAMP+20)
    aid = result['id']
    headers = {'Origin':'http://127.0.0.1:8000','X-CSRF-Token':login['csrf_token']}
    assert client.post(f'/api/v1/alerts/{aid}/ack',headers=headers).status_code == 200
    assert client.post(f'/api/v1/alerts/{aid}/ack',headers=headers).status_code == 200
    assert counts(application) == [1, 2, 1]
    state(application, rule, host, STAMP+80)
    assert counts(application) == [1, 2, 1]
    assert client.post(f'/api/v1/alerts/{aid}/silence',json={'seconds':300},headers=headers).status_code == 200
    reading(application, host, STAMP+81, cpu_percent=20)
    assert state(application, rule, host, STAMP+81)['state'] == 'resolved'
    assert counts(application) == [1, 4, 1]
    assert client.get('/api/v1/alerts?state=resolved').json()['items'][0]['state'] == 'resolved'


@pytest.mark.parametrize('path', ['/checks','/alert-rules','/alerts','/alert-events','/notification-channels','/notification-deliveries'])
def test_monitoring_reads_require_admin(client, path):
    assert client.get('/api/v1'+path).status_code == 401


def test_management_crud_csrf_and_protected_refs(client, login, application, seeded):
    host, _, _ = seeded
    headers = {'Origin':'http://127.0.0.1:8000','X-CSRF-Token':login['csrf_token']}
    check = {'host_id':host,'name':'isolated config only','kind':'http','target':'https://example.invalid'}
    assert client.post('/api/v1/checks',json=check).status_code == 403
    response = client.post('/api/v1/checks',json=check,headers=headers)
    assert response.status_code == 201
    cid = response.json()['id']
    check['enabled'] = False
    assert client.put(f'/api/v1/checks/{cid}',json=check,headers=headers).json()['revision'] == 2
    assert client.get(f'/api/v1/checks/{cid}/results').json()['items'] == []
    channel = {'name':'mail reference','kind':'email','secret_ref':'mail.json'}
    assert client.post('/api/v1/notification-channels',json={**channel,'password':'must-not-be-accepted'},headers=headers).status_code == 422
    assert client.post('/api/v1/notification-channels',json={**channel,'secret_ref':'../escape.json'},headers=headers).status_code == 422
    response = client.post('/api/v1/notification-channels',json=channel,headers=headers)
    assert response.status_code == 201
    channel_id = response.json()['id']
    assert client.post(f'/api/v1/notification-channels/{channel_id}/test',headers=headers).status_code == 202
    assert client.post(f'/api/v1/notification-channels/{channel_id}/test',headers=headers).status_code == 429
    history = client.get('/api/v1/notification-deliveries').json()['items']
    assert len(history)==1 and history[0]['status']=='pending'
    rule = {'host_id':host,'name':'available memory','metric':'memory_percent','operator':'lt','threshold':10.0,'recovery_threshold':20.0,'channel_ids':[channel_id]}
    assert client.post('/api/v1/alert-rules',json=rule,headers=headers).status_code == 201
    assert client.post('/api/v1/alert-rules',json={**rule,'name':'bad hysteresis','recovery_threshold':5.0},headers=headers).status_code == 422
    assert len(client.get('/api/v1/notification-channels').json()['items']) == 2


@pytest.mark.parametrize('data', [
    {'target':'file:///etc/passwd'}, {'target':'https://user:pass@example.invalid'}, {'kind':'process'},
    {'timeout_seconds':0}, {'expected_status_min':400,'expected_status_max':200}, {'target':'http://example.invalid/\r\nX: bad'},
])
def test_invalid_checks(client, login, application, seeded, data):
    headers={'Origin':'http://127.0.0.1:8000','X-CSRF-Token':login['csrf_token']}
    response=client.post('/api/v1/checks',headers=headers,json={'host_id':seeded[0],'name':'bad','kind':'http','target':'https://example.invalid',**data})
    assert response.status_code==422


@pytest.mark.parametrize('address', ['127.0.0.1','::1','10.0.0.1','192.168.1.1','172.16.0.1','169.254.169.254','fe80::1','fc00::1','0.0.0.0','224.0.0.1','::ffff:8.8.8.8','168.63.129.16','100.100.100.200','fd00:ec2::254'])
def test_ssrf_default_rejects(address):
    with pytest.raises(outbound.OutboundError):
        outbound.AddressPolicy().validate(address)


def test_policy_separation_and_metadata_non_overridable():
    assert outbound.AddressPolicy(['127.0.0.0/8']).validate('127.0.0.1')=='127.0.0.1'
    with pytest.raises(outbound.OutboundError):
        outbound.AddressPolicy().validate('127.0.0.1')
    with pytest.raises(outbound.OutboundError):
        outbound.AddressPolicy(['169.254.0.0/16']).validate('169.254.169.254')
    with pytest.raises(outbound.OutboundError):
        outbound.AddressPolicy(['fd00::/8']).validate('fd00:ec2::254')


def test_dns_all_candidates_checked_and_actual_connect_pinned(monkeypatch):
    async def mixed(host,port):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',port)),(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',port))]
    monkeypatch.setattr(outbound,'resolve_records',mixed)
    with pytest.raises(outbound.OutboundError):asyncio.run(outbound.AddressPolicy().resolve('rebinding.invalid',443))
    calls=[]
    async def public(host,port):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',port))]
    async def capture(host,port,**kwargs):calls.append((host,port,kwargs));return object(),object()
    monkeypatch.setattr(outbound,'resolve_records',public)
    monkeypatch.setattr(asyncio,'open_connection',capture)
    asyncio.run(outbound.connect('rebinding.invalid',443,outbound.AddressPolicy(),tls=True))
    assert calls[0][0]=='8.8.8.8' and calls[0][2]['server_hostname']=='rebinding.invalid'


@pytest.fixture
def certificates(tmp_path):
    key=tmp_path/'key.pem';cert=tmp_path/'cert.pem'
    config=tmp_path/'cert.conf';config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=IP:127.0.0.1,DNS:localhost\nbasicConstraints=critical,CA:TRUE\nkeyUsage=digitalSignature,keyEncipherment,keyCertSign\nextendedKeyUsage=serverAuth\n')
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','2','-keyout',str(key),'-out',str(cert),'-config',str(config)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    key.chmod(0o600)
    return key,cert


@pytest.fixture
def http_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.server.received.append((dict(self.headers),self.rfile.read(int(self.headers.get('Content-Length','0')))))
            self.send_response(500 if self.path=='/500' else 200);self.end_headers()
        def do_GET(self):
            if self.path=='/slow':time.sleep(1.2)
            code=500 if self.path=='/500' else 302 if self.path=='/redirect' else 200
            try:self.send_response(code);self.send_header('Location','http://169.254.169.254/');self.end_headers()
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.received=[]
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield server
    server.shutdown();server.server_close();thread.join()


def check(target,kind='http',**kwargs):
    return {'target':target,'kind':kind,'timeout_seconds':1,'expected_status_min':200,'expected_status_max':299,'tls_min_days':14,**kwargs}


@pytest.mark.parametrize('path,status,message',[('/', 'up','ok'),('/500','down','http_status_rejected'),('/redirect','down','redirect_rejected'),('/slow','down','timeout')])
def test_real_local_http(http_server,path,status,message):
    result=asyncio.run(outbound.probe(check(f'http://127.0.0.1:{http_server.server_port}{path}'),outbound.AddressPolicy(['127.0.0.1/32'])))
    assert result['status']==status and result['message']==message and result['latency_ms']>=0


def test_real_local_tcp_success_refused_and_timeout(http_server,monkeypatch):
    policy=outbound.AddressPolicy(['127.0.0.1/32'])
    assert asyncio.run(outbound.probe(check(f'tcp://127.0.0.1:{http_server.server_port}','tcp'),policy))['status']=='up'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));unused=sock.getsockname()[1]
    assert asyncio.run(outbound.probe(check(f'tcp://127.0.0.1:{unused}','tcp'),policy))['message']=='connection_failed'
    async def slow(*args,**kwargs):await asyncio.sleep(5)
    monkeypatch.setattr(outbound,'connect',slow)
    assert asyncio.run(outbound.probe(check('tcp://127.0.0.1:1','tcp'),policy))['message']=='timeout'


def test_real_local_tls_verification_and_expiry(http_server,certificates,monkeypatch):
    key,cert=certificates
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
    http_server.socket=context.wrap_socket(http_server.socket,server_side=True)
    url=f'https://127.0.0.1:{http_server.server_port}/'
    policy=outbound.AddressPolicy(['127.0.0.1/32'])
    assert asyncio.run(outbound.probe(check(url),policy))['status']=='down'  # untrusted CA
    monkeypatch.setenv('SSL_CERT_FILE',str(cert))
    value=asyncio.run(outbound.probe(check(url),policy))
    assert value['message']=='certificate_expiring' and value['tls_expires_at']>time.time()
    assert asyncio.run(outbound.probe(check(url,tls_min_days=0),policy))['status']=='up'
    assert not outbound.certificate_valid(STAMP-1,0,STAMP)


def test_notification_retry_lease_restart_and_idempotency(application,seeded,monkeypatch):
    _,_,channel=seeded
    with alerts.transaction(application.state.sessions) as db:did=notifications.enqueue_test(db,channel,STAMP)
    first=notifications.claim(application.state.sessions)[0]
    assert notifications.claim(application.state.sessions)==[]
    async def failure(*args):raise RuntimeError('DO-NOT-LOG-SECRET')
    monkeypatch.setattr(notifications,'send',failure)
    asyncio.run(notifications.deliver(application.state.sessions,first,application.state.settings))
    with application.state.sessions() as db:
        row=db.get(NotificationDelivery,did)
        assert row.status=='pending' and row.last_error=='delivery_failed' and row.next_attempt_at>STAMP
    monkeypatch.setattr(notifications,'now',lambda:STAMP+100)
    second=notifications.claim(application.state.sessions)[0]
    assert first['key']==second['key']
    async def success(*args):pass
    monkeypatch.setattr(notifications,'send',success)
    asyncio.run(notifications.deliver(application.state.sessions,second,application.state.settings))
    with application.state.sessions() as db:
        assert db.get(NotificationDelivery,did).status=='sent'
    assert notifications.claim(application.state.sessions)==[]


def test_notification_attempt_cap_and_crash_lease_recovery(application,seeded,monkeypatch):
    with alerts.transaction(application.state.sessions) as db:
        did=notifications.enqueue_test(db,seeded[2],STAMP)
    first=notifications.claim(application.state.sessions)[0]
    monkeypatch.setattr(notifications,'now',lambda:STAMP+31)
    second=notifications.claim(application.state.sessions)[0]
    assert second['key']==first['key']
    with alerts.transaction(application.state.sessions) as db:
        row=db.get(NotificationDelivery,did);row.attempts=5;row.lease_until=0
    assert notifications.claim(application.state.sessions)==[]
    with application.state.sessions() as db:assert db.get(NotificationDelivery,did).status=='failed'


def test_protected_secret_file_permissions_symlink_and_size(tmp_path):
    directory=tmp_path/'secrets';directory.mkdir(mode=0o700)
    file=directory/'test.json';file.write_text('{"url":"https://example.invalid","bearer_token":"test-only"}');file.chmod(0o600)
    assert notifications.read_secret(directory,'test.json')['bearer_token']=='test-only'
    file.chmod(0o644)
    with pytest.raises(outbound.OutboundError):notifications.read_secret(directory,'test.json')
    file.chmod(0o600);(directory/'link.json').symlink_to(file)
    with pytest.raises(outbound.OutboundError):notifications.read_secret(directory,'link.json')
    with pytest.raises(outbound.OutboundError):notifications.read_secret(directory,'../test.json')
    file.write_text('x'*17000)
    with pytest.raises(outbound.OutboundError):notifications.read_secret(directory,'test.json')


def test_webhook_uses_independent_policy_and_stable_key(tmp_path,monkeypatch):
    directory=tmp_path/'secrets';directory.mkdir(mode=0o700);file=directory/'test.json'
    file.write_text(json.dumps({'url':'https://127.0.0.1:1/','bearer_token':'test-only'}));file.chmod(0o600)
    settings=Settings(admin_password='test-only-password',notification_secret_dir=str(directory),probe_allowed_cidrs=['127.0.0.1/32'],_env_file=None)
    with pytest.raises(outbound.OutboundError):asyncio.run(notifications.send('webhook','test.json',{'state':'test'},'stable-key',settings))
    observed=[]
    async def fake(url,policy,**kwargs):observed.append(kwargs);return outbound.HTTPResult(200,None)
    monkeypatch.setattr(notifications,'http_request',fake)
    asyncio.run(notifications.send('webhook','test.json',{'state':'test'},'stable-key',settings))
    assert observed[0]['idempotency']=='stable-key' and observed[0]['bearer']=='test-only'


def test_scheduler_single_owner_and_config_revision(application, seeded):
    url=application.state.settings.database_url
    fd=scheduler.scheduler_lock(url)
    with pytest.raises(RuntimeError):scheduler.scheduler_lock(url)
    os.close(fd);fd=scheduler.scheduler_lock(url);os.close(fd)
    with application.state.sessions.begin() as db:
        row=ServiceCheck(host_id=seeded[0],name='test probe',kind='http',target='https://example.invalid',enabled=True)
        db.add(row)
    item=scheduler.claim_checks(application.state.sessions)[0]
    assert scheduler.claim_checks(application.state.sessions)==[]
    with application.state.sessions.begin() as db:db.get(ServiceCheck,item['id']).revision+=1
    scheduler.save_result(application.state.sessions,item,{'status':'up','latency_ms':1.,'message':'ok','http_status':200,'tls_expires_at':None},STAMP)
    with application.state.sessions() as db:assert db.scalar(select(func.count()).select_from(CheckResult))==0


def test_database_busy_retries_on_next_cycle_and_preserves_outbox(application, seeded):
    host,rule,_=seeded
    reading(application,host,STAMP,cpu_percent=99)
    with application.state.engine.connect() as writer:
        writer.exec_driver_sql('BEGIN IMMEDIATE')
        with application.state.engine.connect() as competing:
            competing.exec_driver_sql('PRAGMA busy_timeout=1')
            with pytest.raises(OperationalError):competing.exec_driver_sql('BEGIN IMMEDIATE')
        writer.rollback()
    assert state(application,rule,host)['state']=='pending'
    with application.state.sessions() as db:
        assert db.execute(text('PRAGMA foreign_key_check')).all()==[]
        assert db.scalar(text('PRAGMA journal_mode'))=='wal'


def test_phase04_incremental_migration_preserves_existing_history(database):
    url,cfg=database
    command.downgrade(cfg,'0003_phase03')
    engine=make_engine(url)
    with engine.begin() as db:
        db.execute(text("INSERT INTO hosts(id,name,hostname,status,created_at) VALUES(1,'keep','keep','pending',1)"))
        db.execute(text("INSERT INTO alert_rules(id,name,metric,operator,threshold,duration_seconds,severity,enabled) VALUES(1,'keep','cpu_percent','gt',90,60,'warning',1)"))
        db.execute(text("INSERT INTO alert_instances(id,rule_id,host_id,fingerprint,status,opened_at) VALUES(1,1,1,'cpu','firing',1)"))
        db.execute(text("INSERT INTO alert_events(id,alert_id,kind,created_at,message) VALUES(1,1,'firing',1,'keep')"))
        db.execute(text("INSERT INTO notification_channels(id,name,kind,config,enabled) VALUES(1,'keep','webhook','{}',1)"))
        db.execute(text("INSERT INTO notification_deliveries(id,event_id,channel_id,status,attempts,created_at) VALUES(1,1,1,'pending',0,1)"))
    command.upgrade(cfg,'head');command.check(cfg)
    with engine.connect() as db:
        assert db.scalar(text('SELECT message FROM alert_events'))=='keep'
        assert db.scalar(text('SELECT event_id FROM notification_deliveries'))==1
        assert db.scalar(text('SELECT fired_at FROM alert_instances'))==1
        assert db.execute(text('PRAGMA foreign_key_check')).all()==[]
    command.downgrade(cfg,'0003_phase03');command.upgrade(cfg,'head')
    with engine.connect() as db:assert db.scalar(text('SELECT event_id FROM notification_deliveries'))==1
    engine.dispose()


@pytest.mark.parametrize('mode', ['implicit', 'starttls'])
def test_real_local_smtp_tls_auth_and_message_id(certificates, monkeypatch, mode):
    key, cert = certificates
    monkeypatch.setenv('SSL_CERT_FILE', str(cert))
    async def run():
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        observed=[]
        async def server(reader,writer):
            async def reply(value):writer.write(value.encode()+b'\r\n');await writer.drain()
            try:
                await reply('220 local-test')
                while True:
                    line=await reader.readline()
                    if not line:break
                    if line.startswith(b'EHLO'):await reply('250 local-test')
                    elif line.startswith(b'STARTTLS'):
                        await reply('220 ready')
                        await writer.start_tls(context)
                    elif line.startswith(b'AUTH PLAIN'):
                        # Only a local throwaway credential; no third-party server.
                        await reply('235 authenticated')
                    elif line.startswith((b'MAIL FROM',b'RCPT TO')):await reply('250 ok')
                    elif line.startswith(b'DATA'):
                        await reply('354 continue')
                        data=await reader.readuntil(b'\r\n.\r\n');observed.append(data)
                        await reply('250 accepted')
                    else:break
            finally:
                writer.close()
                await writer.wait_closed()
        service=await asyncio.start_server(server,'127.0.0.1',0,ssl=context if mode=='implicit' else None)
        port=service.sockets[0].getsockname()[1]
        try:
            async with asyncio.timeout(5):
                await notifications.smtp_send({'host':'127.0.0.1','port':port,'tls_mode':mode,'from':'monitor@example.invalid','to':['ops@example.invalid'],'username':'test','password':'throwaway'}, {'state':'firing','rule':'本地告警'}, 'stable-message-id', outbound.AddressPolicy(['127.0.0.1/32']))
            assert len(observed)==1 and b'<stable-message-id@multihost-monitor.invalid>' in observed[0]
            assert observed[0].isascii() and b'quoted-printable' in observed[0]
        finally:
            service.close();await service.wait_closed()
    asyncio.run(run())


def test_smtp_plaintext_mode_rejected():
    config={'host':'127.0.0.1','port':25,'tls_mode':'none','from':'monitor@example.invalid','to':['ops@example.invalid']}
    with pytest.raises(outbound.OutboundError, match='smtp_tls_required'):
        asyncio.run(notifications.smtp_send(config,{},'key',outbound.AddressPolicy()))


def test_probe_concurrency_bound_and_shutdown(application, seeded, monkeypatch):
    sessions=application.state.sessions
    with sessions.begin() as db:
        for i in range(12):db.add(ServiceCheck(host_id=seeded[0],name=f'bounded {i}',kind='http',target='https://example.invalid',enabled=True))
    active=0;maximum=0;cancelled=0
    async def fake(check,policy):
        nonlocal active,maximum,cancelled
        active+=1;maximum=max(maximum,active)
        try:
            await asyncio.sleep(5)
            return {'status':'up','message':'ok','latency_ms':1.,'http_status':200,'tls_expires_at':None}
        except asyncio.CancelledError:
            cancelled+=1;raise
        finally:active-=1
    monkeypatch.setattr(scheduler,'probe',fake)
    async def run():
        task=asyncio.create_task(scheduler.probes(sessions,application.state.settings))
        while maximum<4:await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(run())
    assert maximum==4 and active==0 and cancelled==4


def test_periodic_errors_are_isolated_and_cancelled():
    calls=[]
    async def failing():calls.append('failure');raise OperationalError('test',{},Exception())
    async def successful():calls.append('success')
    async def run():
        tasks=[asyncio.create_task(scheduler.periodic('test-failure',failing,.01)),asyncio.create_task(scheduler.periodic('test-success',successful,.01))]
        await asyncio.sleep(.04)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    asyncio.run(run())
    assert calls.count('failure')>=2 and calls.count('success')>=2


def test_cancellation_waits_for_database_thread():
    finished=threading.Event()
    def operation():time.sleep(.03);finished.set()
    async def run():
        task=asyncio.create_task(scheduler.database_task(operation));await asyncio.sleep(.005);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert finished.is_set()
    asyncio.run(run())


def test_service_probe_rule_and_check_retention(application,seeded,stamp,monkeypatch):
    host,rule_id,_=seeded
    with application.state.sessions.begin() as db:
        check=ServiceCheck(host_id=host,name='availability',kind='tcp',target='tcp://example.invalid:443',enabled=True)
        db.add(check);db.flush()
        rule=db.get(AlertRule,rule_id);rule.metric='service_down';rule.target=f'check:{check.id}';rule.operator='gte';rule.threshold=1;rule.recovery_threshold=0;rule.duration_seconds=0
        db.add(CheckResult(check_id=check.id,host_id=host,checked_at=STAMP,status='down',latency_ms=1.,message='connection_failed'))
        db.add(CheckResult(check_id=check.id,host_id=host,checked_at=STAMP-604801,status='up',latency_ms=1.,message='ok'))
    assert state(application,rule_id,host)['state']=='firing'
    monkeypatch.setattr('app.retention.now',lambda:STAMP)
    report=cleanup_batch(application.state.sessions,apply=True,include_checks=True)
    assert report['deleted']['check_results']==1
    with application.state.sessions() as db:assert db.execute(text('PRAGMA foreign_key_check')).all()==[]


def test_lifespan_scheduler_stops_and_releases_owner(database,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    async def no_network(*args):pass
    monkeypatch.setattr(scheduler,'probes',no_network)
    monkeypatch.setattr(scheduler,'deliveries',no_network)
    settings=Settings(database_url=database[0],admin_password='test-only-password',retention_enabled=False,_env_file=None)
    for _ in range(2):
        with TestClient(create_app(settings)) as client:
            assert client.get('/api/health/ready').status_code==200
            with pytest.raises(RuntimeError):scheduler.scheduler_lock(database[0])
        fd=scheduler.scheduler_lock(database[0]);os.close(fd)


@pytest.mark.parametrize('path,method,body', [
    ('/checks','POST',{}),('/checks/1','PUT',{}),('/alert-rules','POST',{}),('/alert-rules/1','PUT',{}),
    ('/notification-channels','POST',{}),('/notification-channels/1','PUT',{}),('/notification-channels/1/test','POST',None),
    ('/alerts/1/ack','POST',None),('/alerts/1/silence','POST',{'seconds':60}),
])
def test_all_management_writes_require_csrf(client,login,path,method,body):
    response=client.request(method,'/api/v1'+path,json=body,headers={'Origin':'http://127.0.0.1:8000'})
    assert response.status_code==403


def test_pending_notification_never_blocks_agent_ingestion(client,login,application,monkeypatch):
    from test_phase02 import upload
    registered=client.post('/api/v1/hosts',json={'name':'isolated ingest','hostname':'isolated','systemd_units':['sshd.service']},headers={'Origin':'http://127.0.0.1:8000','X-CSRF-Token':login['csrf_token']}).json()
    sessions=application.state.sessions
    with sessions.begin() as db:
        channel=NotificationChannel(name='isolated slow transport',kind='webhook',config={'secret_ref':'test.json'})
        db.add(channel);db.flush();notifications.enqueue_test(db,channel.id,int(time.time()))
    item=notifications.claim(sessions)[0]
    observed=[]
    async def blocked(*args):
        # Prove there is no SQLite writer lock held during network I/O.
        with alerts.transaction(sessions):pass
        response=await asyncio.to_thread(upload,client,registered)
        observed.append(response.status_code)
        raise outbound.OutboundError('test_transport_down')
    monkeypatch.setattr(notifications,'send',blocked)
    asyncio.run(notifications.deliver(sessions,item,application.state.settings))
    assert observed==[200]
    with sessions() as db:assert db.get(NotificationDelivery,item['id']).last_error=='test_transport_down'


def test_probe_history_auth_paging_and_range(client,login,application,seeded):
    with application.state.sessions.begin() as db:
        check=ServiceCheck(host_id=seeded[0],name='paged',kind='tcp',target='tcp://example.invalid:443',enabled=True)
        db.add(check);db.flush();check_id=check.id
        for i in range(3):db.add(CheckResult(check_id=check_id,host_id=seeded[0],checked_at=STAMP-i,status='up',latency_ms=1.))
    first=client.get(f'/api/v1/checks/{check_id}/results?limit=2').json()
    assert len(first['items'])==2
    second=client.get(f"/api/v1/checks/{check_id}/results?limit=2&before_id={first['next_before_id']}").json()
    assert len(second['items'])==1
    assert client.get(f'/api/v1/checks/{check_id}/results?since={STAMP}&until=1').status_code==422


def test_muted_alert_cooldown_does_not_create_event_storm(application,seeded):
    host,rule,_=seeded
    reading(application,host,STAMP,cpu_percent=99)
    state(application,rule,host)
    fired=state(application,rule,host,STAMP+20)
    with application.state.sessions.begin() as db:
        db.get(AlertInstance,fired['id']).silenced_until=STAMP+600
    state(application,rule,host,STAMP+80)
    state(application,rule,host,STAMP+82)
    assert counts(application)==[1,2,1]


def test_hysteresis_boundaries_do_not_overlap():
    with pytest.raises(ValueError):RuleInput(name='no overlap',metric='cpu_percent',operator='gte',threshold=80.,recovery_threshold=80.)


def test_real_local_https_webhook(http_server,certificates,tmp_path,monkeypatch):
    key,cert=certificates
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
    http_server.socket=context.wrap_socket(http_server.socket,server_side=True)
    monkeypatch.setenv('SSL_CERT_FILE',str(cert))
    directory=tmp_path/'secrets';directory.mkdir(mode=0o700);reference=directory/'webhook.json'
    def config(path='/'):
        reference.write_text(json.dumps({'url':f'https://127.0.0.1:{http_server.server_port}{path}','bearer_token':'local-test-only'}));reference.chmod(0o600)
    config()
    settings=Settings(admin_password='isolated-test-only',notification_secret_dir=str(directory),notification_allowed_cidrs=['127.0.0.1/32'],_env_file=None)
    asyncio.run(notifications.send('webhook','webhook.json',{'state':'firing'},'stable-test-key',settings))
    headers,body=http_server.received[0]
    assert headers['Idempotency-Key']=='stable-test-key' and headers['Authorization']=='Bearer local-test-only'
    assert json.loads(body)=={'state':'firing'}
    config('/500')
    with pytest.raises(outbound.OutboundError,match='webhook_rejected'):
        asyncio.run(notifications.send('webhook','webhook.json',{'state':'test'},'another-test-key',settings))


def test_policy_rejection_is_unknown_not_service_failure():
    result=asyncio.run(outbound.probe(check('http://169.254.169.254/'),outbound.AddressPolicy()))
    assert result['status']=='unknown' and result['message']=='address_rejected'


def test_edited_target_does_not_reuse_previous_check_result(application,seeded):
    host,rule_id,_=seeded
    with application.state.sessions.begin() as db:
        check=ServiceCheck(host_id=host,name='changed configuration',kind='http',target='https://example.invalid',enabled=True)
        db.add(check);db.flush()
        db.add(CheckResult(check_id=check.id,host_id=host,checked_at=STAMP,status='down',check_revision=1))
        check.revision=2
        rule=db.get(AlertRule,rule_id);rule.metric='service_down';rule.target=f'check:{check.id}';rule.operator='gte';rule.threshold=1;rule.recovery_threshold=0;rule.duration_seconds=0
    assert state(application,rule_id,host)['data_status']=='missing'


def test_migration_refuses_legacy_plaintext_channel_before_ddl(database):
    url,cfg=database
    command.downgrade(cfg,'0003_phase03')
    engine=make_engine(url)
    with engine.begin() as db:
        db.execute(text("INSERT INTO notification_channels(name,kind,config,enabled) VALUES('legacy','webhook',:config,1)"),{'config':json.dumps({'url':'https://example.invalid','token':'test-only-legacy'})})
    with pytest.raises(RuntimeError,match='protected secret references'):command.upgrade(cfg,'head')
    with engine.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='0003_phase03'
        assert 'next_run_at' not in {row[1] for row in db.execute(text('PRAGMA table_info(service_checks)'))}
    engine.dispose()


@pytest.mark.parametrize('value',['NaN','Infinity','-Infinity'])
def test_nonfinite_monitoring_requests_are_422_without_echo(client,login,value):
    headers={'Origin':'http://127.0.0.1:8000','X-CSRF-Token':login['csrf_token'],'Content-Type':'application/json'}
    body='{"name":"bad","metric":"cpu_percent","threshold":'+value+',"recovery_threshold":60}'
    response=client.post('/api/v1/alert-rules',content=body,headers=headers)
    assert response.status_code==422 and value not in response.text
    response=client.post('/api/v1/notification-channels',json={'password':'DO-NOT-ECHO-TEST-SECRET'},headers=headers)
    assert response.status_code==422 and 'DO-NOT-ECHO-TEST-SECRET' not in response.text

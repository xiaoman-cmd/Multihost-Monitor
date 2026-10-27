import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import make_engine


@pytest.fixture
def connection(database):
    engine = make_engine(database[0])
    with engine.begin() as db:
        db.execute(text("INSERT INTO hosts(id,name,hostname,status,created_at) VALUES (1,'one','one','pending',1),(2,'two','two','pending',1)"))
        db.execute(text("INSERT INTO service_checks(id,host_id,name,kind,target,interval_seconds,timeout_seconds,enabled) VALUES (1,1,'http','http','https://example.test',60,5,1)"))
        db.execute(text("INSERT INTO alert_rules(id,name,metric,operator,threshold,duration_seconds,severity,enabled) VALUES (1,'CPU','cpu_percent','gt',90,60,'warning',1)"))
        db.execute(text("INSERT INTO alert_instances(id,rule_id,host_id,fingerprint,status,opened_at) VALUES (1,1,1,'cpu','firing',1)"))
        db.execute(text("INSERT INTO alert_events(id,alert_id,kind,created_at,message) VALUES (1,1,'firing',1,'CPU high')"))
        db.execute(text("INSERT INTO notification_channels(id,name,kind,config,enabled) VALUES (1,'ops','webhook','{}',1)"))
    with engine.connect() as db:
        yield db
    engine.dispose()


@pytest.mark.parametrize('sql', [
    "INSERT INTO hosts(name,hostname,status,created_at) VALUES ('one','duplicate','pending',1)",
    "INSERT INTO hosts(name,hostname,status,created_at) VALUES ('','host','pending',1)",
    "INSERT INTO hosts(name,hostname,status,created_at) VALUES ('bad','host','bogus',1)",
    "INSERT INTO agent_credentials(host_id,token_hash,created_at) VALUES (999,printf('%064d',1),1)",
    "INSERT INTO agent_credentials(host_id,token_hash,created_at) VALUES (1,'plaintext',1)",
    "INSERT INTO agent_credentials(host_id,token_hash,created_at,expires_at) VALUES (1,printf('%064d',1),2,1)",
    "INSERT INTO host_metrics(host_id,sampled_at,cpu_percent,memory_total_bytes,memory_used_bytes,load_1) VALUES (1,1,101,100,1,0)",
    "INSERT INTO host_metrics(host_id,sampled_at,cpu_percent,memory_total_bytes,memory_used_bytes,load_1) VALUES (1,1,50,100,101,0)",
    "INSERT INTO disk_metrics(host_id,sampled_at,mountpoint,total_bytes,used_bytes) VALUES (1,1,'/',10,-1)",
    "INSERT INTO service_checks(host_id,name,kind,target,interval_seconds,timeout_seconds,enabled) VALUES (1,'bad','udp','x',60,1,1)",
    "INSERT INTO service_checks(host_id,name,kind,target,interval_seconds,timeout_seconds,enabled) VALUES (1,'bad','tcp','x',5,6,1)",
    "INSERT INTO check_results(check_id,host_id,checked_at,status) VALUES (1,2,1,'up')",
    "INSERT INTO check_results(check_id,host_id,checked_at,status,latency_ms) VALUES (1,1,1,'up',-1)",
    "UPDATE alert_rules SET operator = 'bad' WHERE id=1",
    "UPDATE alert_rules SET threshold = 101 WHERE id=1",
    "UPDATE alert_rules SET enabled = 2 WHERE id=1",
    "INSERT INTO alert_instances(rule_id,host_id,fingerprint,status,opened_at) VALUES (1,1,'cpu','firing',2)",
    "UPDATE alert_instances SET status='resolved' WHERE id=1",
    "UPDATE alert_events SET kind='bogus' WHERE id=1",
    "UPDATE notification_channels SET kind='sms' WHERE id=1",
    "INSERT INTO notification_deliveries(event_id,channel_id,status,attempts,created_at) VALUES (1,1,'sent',0,1)",
    "INSERT INTO notification_deliveries(event_id,channel_id,status,attempts,created_at) VALUES (1,1,'pending',-1,1)",
    "INSERT INTO admin_sessions(token_hash,admin_id,csrf_token,created_at,expires_at) VALUES ('hash',999,'csrf',1,2)",
])
def test_invalid_data_rejected(connection, sql):
    with pytest.raises(IntegrityError):
        connection.execute(text(sql))
    connection.rollback()


@pytest.mark.parametrize('table, columns, values', [
    ('host_metrics', 'host_id,sampled_at,cpu_percent,memory_total_bytes,memory_used_bytes,load_1', '1,1,50,100,50,0'),
    ('disk_metrics', 'host_id,sampled_at,mountpoint,total_bytes,used_bytes', "1,1,'/',100,50"),
    ('check_results', 'check_id,host_id,checked_at,status', "1,1,1,'up'"),
    ('notification_deliveries', 'event_id,channel_id,status,attempts,created_at', "1,1,'pending',0,1"),
])
def test_sample_and_delivery_uniqueness(connection, table, columns, values):
    sql = text(f'INSERT INTO {table}({columns}) VALUES ({values})')
    connection.execute(sql)
    with pytest.raises(IntegrityError):
        connection.execute(sql)
    connection.rollback()


def test_resolved_history_and_refiring(connection):
    connection.execute(text("UPDATE alert_instances SET status='resolved', resolved_at=2 WHERE id=1"))
    connection.execute(text("INSERT INTO alert_instances(rule_id,host_id,fingerprint,status,opened_at) VALUES (1,1,'cpu','firing',3)"))
    assert connection.scalar(text('SELECT count(*) FROM alert_instances')) == 2


def test_delete_host_cascades(connection):
    connection.execute(text('DELETE FROM hosts WHERE id=1'))
    for table in ['service_checks', 'alert_instances', 'alert_events']:
        assert connection.scalar(text(f'SELECT count(*) FROM {table}')) == 0
    assert connection.execute(text('PRAGMA foreign_key_check')).all() == []

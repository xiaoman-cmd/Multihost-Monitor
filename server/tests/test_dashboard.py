import json
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import func, inspect, select, text

from app.dashboard_api import RETENTION_SECONDS
from app.models import AgentSample, DiskMetric, Host, HostMetric
from app.retention import cleanup_batch, cleanup_cycle
from app.security import digest
from test_phase02 import sample

STAMP = 1_800_000_000


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr('app.agent_api.now',lambda:STAMP)
    monkeypatch.setattr('app.dashboard_api.now',lambda:STAMP)
    monkeypatch.setattr('app.retention.now',lambda:STAMP)
    return STAMP


def hosts(application, configs):
    with application.state.sessions.begin() as db:
        result=[]
        for config in configs:
            value={'name':str(uuid4()),'hostname':'linux.test',**config}
            h=Host(**value)
            db.add(h);db.flush();result.append(h.id)
        return result


def store(application,host_id,payloads,received_at=STAMP):
    with application.state.sessions.begin() as db:
        for payload in payloads:
            m=HostMetric(host_id=host_id,sampled_at=payload['sampled_at'],cpu_percent=payload['cpu_percent'],
                memory_total_bytes=payload['memory_total_bytes'],memory_used_bytes=payload['memory_total_bytes']-payload['memory_available_bytes'],load_1=payload['load_1'])
            db.add(m);db.flush()
            db.add(AgentSample(host_id=host_id,sample_id=payload['sample_id'],sampled_at=payload['sampled_at'],
                received_at=received_at,content_hash=digest(json.dumps(payload)),host_metric_id=m.id,payload=payload))
            for d in payload['disks']:
                db.add(DiskMetric(host_id=host_id,sampled_at=payload['sampled_at'],mountpoint=d['mountpoint'],total_bytes=d['total_bytes'],used_bytes=d['used_bytes']))


def test_server_received_online_disabled_and_boundary(client,login,application,clock):
    hosts(application,[{'name':'online','last_seen_at':STAMP-90,'status':'offline'},
        {'name':'old','last_seen_at':STAMP-91,'status':'online'}, {'name':'new'},
        {'name':'disabled','status':'disabled','last_seen_at':STAMP}])
    result=client.get('/api/v1/dashboard').json()
    assert result['stats']=={'total':4,'online':1,'offline':2,'disabled':1}
    assert {h['name']:h['status'] for h in result['items']}=={'online':'online','old':'offline','new':'offline','disabled':'disabled'}
    assert all(h['latest'] is None and h['freshness']=='none' for h in result['items'])


def test_dashboard_search_filters_literal_wildcard_and_keyset(client,login,application,clock):
    hosts(application,[{'name':'oracle-a','cloud_provider':'Oracle','region':'us','tags':['api'],'last_seen_at':STAMP},
        {'name':'oracle-b','cloud_provider':'Oracle','status':'disabled'}, {'name':'100% literal','cloud_provider':'Google'}])
    assert client.get('/api/v1/dashboard?search=%25').json()['total']==1
    assert client.get('/api/v1/dashboard?provider=Oracle&status=online&tag=api').json()['total']==1
    first=client.get('/api/v1/dashboard?limit=1').json()
    second=client.get(f"/api/v1/dashboard?limit=1&after_id={first['next_after_id']}").json()
    assert first['items'][0]['id']<second['items'][0]['id']
    assert second['stats']['total']==3
    assert client.get('/api/v1/dashboard?status=disabled').json()['stats']['offline']==1
    assert client.get('/api/v1/dashboard?search=absent').json()['items']==[]


def test_old_backfill_does_not_replace_latest_and_null_rates(client,login,application,clock):
    host_id=hosts(application,[{'name':'test','last_seen_at':STAMP}])[0]
    newest=sample(STAMP-10);newest['cpu_percent']=80.0
    older=sample(STAMP-500);older['cpu_percent']=10.0
    store(application,host_id,[newest,older])
    result=client.get('/api/v1/dashboard').json()['items'][0]
    assert result['latest']['cpu_percent']==80
    assert result['latest']['receive_rate'] is None
    assert result['freshness']=='fresh'
    assert client.get(f'/api/v1/hosts/{host_id}').json()['sample']['sample_id']==newest['sample_id']


def test_agent_online_but_metrics_stale(client,login,application,clock):
    host_id=hosts(application,[{'last_seen_at':STAMP}])[0]
    store(application,host_id,[sample(STAMP-500)],received_at=STAMP)
    result=client.get(f'/api/v1/hosts/{host_id}').json()['host']
    assert result['status']=='online' and result['freshness']=='stale'
    assert result['sample_age_seconds']==500


def test_received_age_independently_stales_metrics(client,login,application,clock):
    host_id=hosts(application,[{'last_seen_at':STAMP}])[0]
    store(application,host_id,[sample(STAMP-10)],received_at=STAMP-100)
    assert client.get(f'/api/v1/hosts/{host_id}').json()['host']['freshness']=='stale'


def test_history_never_aggregates_across_gap_or_fills_unknown_rate(client,login,application,clock):
    host_id=hosts(application,[{}])[0]
    base=(STAMP//1800)*1800-3600
    values=[sample(base+n) for n in [10,40,200,230]]
    for p,cpu,rx,tx in zip(values,[10.,20.,40.,50.],[None,10.,20.,30.],[2.,4.,6.,8.]):
        p['cpu_percent']=cpu;p['networks'][0]['receive_rate']=rx;p['networks'][0]['send_rate']=tx
    store(application,host_id,values)
    result=client.get(f'/api/v1/hosts/{host_id}/history?range=7d').json()
    points=[p for p in result['points'] if p['sample_count']]
    assert len(points)==2
    assert points[0]['bucket_at']==points[1]['bucket_at']
    assert points[0]['segment']!=points[1]['segment']
    assert [p['cpu_percent'] for p in points]==[15.,45.]
    assert points[0]['receive_rate'] is None and points[0]['send_rate']==3
    assert points[1]['receive_rate']==25
    assert any(p['sample_count']==0 and p['cpu_percent'] is None for p in result['points'])
    assert len(result['points'])<=1200


@pytest.mark.parametrize('range,bucket',[('1h',30),('6h',60),('24h',300),('7d',1800)])
def test_empty_history_ranges_are_null_not_zero(client,login,application,clock,range,bucket):
    host_id=hosts(application,[{}])[0]
    response=client.get(f'/api/v1/hosts/{host_id}/history?range={range}')
    assert response.status_code==200
    data=response.json()
    assert data['bucket_seconds']==bucket
    assert all(p['cpu_percent'] is None and p['receive_rate'] is None and p['sample_count']==0 for p in data['points'])


def test_raw_pagination_snapshot_ignores_new_backfill(client,login,application,clock):
    host_id=hosts(application,[{}])[0]
    values=[sample(STAMP-60),sample(STAMP-30)]
    store(application,host_id,values)
    url=f'/api/v1/hosts/{host_id}/samples?since={STAMP-3600}&until={STAMP}&limit=1'
    first=client.get(url).json();cursor=first['next_cursor']
    store(application,host_id,[sample(STAMP-45)])
    next_url=url+f"&snapshot_id={first['snapshot_id']}&after_time={cursor['after_time']}&after_id={cursor['after_id']}"
    second=client.get(next_url).json()
    assert second['items'][0]['sample_id']==values[1]['sample_id']
    assert second['next_cursor'] is None


@pytest.mark.parametrize('suffix',[f'?since={STAMP-604801}&until={STAMP}',f'?since={STAMP}&until={STAMP-1}',
    f'?since={STAMP-100}&until={STAMP}&after_id=1',f'?since={STAMP-100}&until={STAMP}&limit=201',
    f'?since={STAMP-100}&until={STAMP+121}'])
def test_raw_limits(client,login,application,clock,suffix):
    host_id=hosts(application,[{}])[0]
    assert client.get(f'/api/v1/hosts/{host_id}/samples'+suffix).status_code==422


@pytest.mark.parametrize('path',['/api/v1/dashboard','/api/v1/hosts/1','/api/v1/hosts/1/history',
    f'/api/v1/hosts/1/samples?since={STAMP-10}&until={STAMP}'])
def test_dashboard_authentication(client,path):
    assert client.get(path).status_code==401


def test_disable_enable_requires_csrf_and_updates_statistics(client,login,application,clock):
    host_id=hosts(application,[{'last_seen_at':STAMP}])[0]
    url=f'/api/v1/hosts/{host_id}/state'
    assert client.patch(url,json={'disabled':True}).status_code==403
    headers={'Origin':'http://127.0.0.1:8000','X-CSRF-Token':login['csrf_token']}
    assert client.patch(url,json={'disabled':True},headers=headers).json()['disabled'] is True
    assert client.get('/api/v1/dashboard').json()['stats']=={'total':1,'online':0,'offline':0,'disabled':1}
    assert client.patch(url,json={'disabled':False},headers=headers).status_code==200
    assert client.get('/api/v1/dashboard').json()['stats']['online']==1
    assert client.patch(url,json={'disabled':'true'},headers=headers).status_code==422


def test_old_rows_excluded_before_cleanup(client,login,application,clock):
    host_id=hosts(application,[{}])[0]
    store(application,host_id,[sample(STAMP-RETENTION_SECONDS-1)])
    assert client.get(f'/api/v1/hosts/{host_id}').json()['sample'] is None
    assert client.get(f'/api/v1/hosts/{host_id}/metrics').json()['items']==[]
    raw=client.get(f'/api/v1/hosts/{host_id}/samples?since=0&until=1').json()
    assert raw['items']==[]


def test_retention_dry_run_boundaries_batches_and_foreign_keys(client,login,application,clock):
    host_id=hosts(application,[{}])[0]
    cutoff=STAMP-RETENTION_SECONDS
    store(application,host_id,[sample(cutoff-2),sample(cutoff-1),sample(cutoff)])
    report=cleanup_batch(application.state.sessions)
    assert report['applied'] is False and report['remaining']['host_metrics']==2
    report=cleanup_batch(application.state.sessions,apply=True,batch_size=1)
    assert report['deleted']=={'agent_samples':1,'disk_metrics':1,'host_metrics':1}
    report=cleanup_cycle(application.state.sessions)
    assert not any(report['remaining'].values())
    with application.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(HostMetric))==1
        assert db.scalar(select(func.count()).select_from(DiskMetric))==1
        assert db.scalar(select(func.count()).select_from(AgentSample))==1
        assert db.execute(text('PRAGMA foreign_key_check')).all()==[]
        assert db.get(Host,host_id) is not None
        assert db.execute(text('PRAGMA journal_mode')).scalar()=='wal'


def test_retention_cannot_shorten_seven_days(client,login,application,clock):
    host_id=hosts(application,[{}])[0]
    store(application,host_id,[sample(STAMP-100)])
    report=cleanup_batch(application.state.sessions,cutoff=STAMP,apply=True)
    assert not any(report['deleted'].values())
    with pytest.raises(ValueError):
        cleanup_batch(application.state.sessions,batch_size=1001)


def test_phase03_index_migration_preserves_data_and_rolls_back(database):
    from types import SimpleNamespace
    from app.db import make_engine, session_factory
    url,cfg=database
    engine=make_engine(url)
    with engine.begin() as db:
        db.execute(text("INSERT INTO hosts(name,hostname,status,created_at) VALUES('keep','keep','pending',1)"))
    payload=sample(STAMP)
    store(SimpleNamespace(state=SimpleNamespace(sessions=session_factory(engine))),1,[payload])
    command.downgrade(cfg,'0002_phase02')
    with engine.connect() as db:
        assert db.scalar(text('SELECT name FROM hosts'))=='keep'
        assert json.loads(db.scalar(text('SELECT payload FROM agent_samples')))==payload
        assert 'ix_host_metrics_retention' not in {i['name'] for i in inspect(db).get_indexes('host_metrics')}
    command.upgrade(cfg,'head')
    with engine.connect() as db:
        assert 'ix_host_metrics_retention' in {i['name'] for i in inspect(db).get_indexes('host_metrics')}
        assert json.loads(db.scalar(text('SELECT payload FROM agent_samples')))==payload
        assert db.execute(text('PRAGMA foreign_key_check')).all()==[]
    engine.dispose()


def test_sample_ids_are_never_reused_after_retention(client,login,application,clock):
    host_id=hosts(application,[{}])[0]
    store(application,host_id,[sample(STAMP-RETENTION_SECONDS-1)])
    with application.state.sessions() as db:
        before=db.scalar(select(AgentSample.id))
    cleanup_batch(application.state.sessions,apply=True)
    store(application,host_id,[sample(STAMP)])
    with application.state.sessions() as db:
        assert db.scalar(select(AgentSample.id))>before


def test_history_too_many_segments_is_explicit_not_silently_truncated(client,login,application,clock):
    host_id=hosts(application,[{}])[0]
    values=[]
    for i in range(1201):
        value=sample(STAMP-i*120)
        value['disks']=[]
        values.append(value)
    store(application,host_id,values)
    response=client.get(f'/api/v1/hosts/{host_id}/history?range=7d')
    assert response.status_code==422
    assert 'shorter range' in response.json()['detail']


def test_raw_continuation_requires_snapshot(client,login,application,clock):
    host_id=hosts(application,[{}])[0]
    assert client.get(f'/api/v1/hosts/{host_id}/samples?since={STAMP-3600}&until={STAMP}&after_time={STAMP-100}&after_id=1').status_code==422

"""Seven-day synthetic performance data, always in a disposable isolated database."""
import json
import os
import platform
import secrets
import statistics
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import insert, text

from app.config import Settings
from app.db import make_engine
from app.main import create_app
from app.models import AgentSample, DiskMetric, Host, HostMetric
from app.retention import cleanup_batch
from app.security import digest


def run():
    with tempfile.TemporaryDirectory(prefix='mhm-history-bench-') as directory:
        url=f'sqlite:///{directory}/bench.db'
        os.environ['MHM_DATABASE_URL']=url
        command.upgrade(Config('alembic.ini'),'head')
        engine=make_engine(url)
        stamp=int(time.time())
        template={'metadata':{'hostname':'synthetic','os':'linux/test','arch':'amd64','agent_version':'benchmark'},
            'cpu_percent':25.0,'load_1':.1,'load_5':.2,'load_15':.3,'memory_total_bytes':1024,
            'memory_available_bytes':512,'swap_total_bytes':0,'swap_used_bytes':0,'uptime_seconds':100,
            'disks':[],'networks':[{'interface':'test0','received_bytes':100,'sent_bytes':200,
                                  'receive_rate':100.0,'send_rate':200.0}],'services':[]}
        with engine.begin() as db:
            db.execute(insert(Host).values(id=1,name='synthetic benchmark only',hostname='synthetic',last_seen_at=stamp))
            for start in range(0,20160,1000):
                metrics=[];samples=[]
                for i in range(start,min(start+1000,20160)):
                    at=stamp-604800+(i+1)*30
                    payload={**template,'sample_id':str(uuid4()),'sampled_at':at}
                    metrics.append({'id':i+1,'host_id':1,'sampled_at':at,'cpu_percent':25.,'memory_total_bytes':1024,'memory_used_bytes':512,'load_1':.1})
                    samples.append({'id':i+1,'host_id':1,'sample_id':payload['sample_id'],'sampled_at':at,
                                    'received_at':stamp,'content_hash':digest(json.dumps(payload)),'host_metric_id':i+1,'payload':payload})
                db.execute(insert(HostMetric),metrics)
                db.execute(insert(AgentSample),samples)
        password=secrets.token_urlsafe(32)
        app=create_app(Settings(database_url=url,admin_password=password,retention_enabled=False,scheduler_enabled=False,_env_file=None))
        timings=[]
        with TestClient(app) as client:
            login=client.post('/api/auth/login',json={'username':'admin','password':password},headers={'Origin':'http://127.0.0.1:8000'})
            assert login.status_code==200
            for _ in range(10):
                begin=time.perf_counter()
                response=client.get('/api/v1/hosts/1/history?range=7d')
                timings.append((time.perf_counter()-begin)*1000)
                assert response.status_code==200,response.status_code
            data=response.json()
            with engine.begin() as db:
                old_metrics=[];old_samples=[];old_disks=[]
                for i in range(500):
                    at=stamp-604800-30*(i+1)
                    payload={**template,'sample_id':str(uuid4()),'sampled_at':at}
                    old_metrics.append({'id':20161+i,'host_id':1,'sampled_at':at,'cpu_percent':25.,'memory_total_bytes':1024,'memory_used_bytes':512,'load_1':.1})
                    old_samples.append({'id':20161+i,'host_id':1,'sample_id':payload['sample_id'],'sampled_at':at,'received_at':stamp,'content_hash':digest(json.dumps(payload)),'host_metric_id':20161+i,'payload':payload})
                    old_disks.append({'host_id':1,'sampled_at':at,'mountpoint':'/','total_bytes':1000,'used_bytes':100})
                db.execute(insert(HostMetric),old_metrics)
                db.execute(insert(AgentSample),old_samples)
                db.execute(insert(DiskMetric),old_disks)
            begin=time.perf_counter()
            cleanup=cleanup_batch(app.state.sessions,apply=True)
            cleanup_ms=(time.perf_counter()-begin)*1000
            with engine.connect() as db:
                fk=db.execute(text('PRAGMA foreign_key_check')).all()
                plan=[list(row) for row in db.execute(text('EXPLAIN QUERY PLAN SELECT id FROM agent_samples WHERE host_id=1 AND sampled_at>=:since ORDER BY sampled_at'),{'since':stamp-604800})]
            result={'kind':'synthetic isolated SQLite + TestClient; no production data; no network/TLS',
                'platform':platform.platform(),'python':platform.python_version(),'rows':20160,'period_days':7,
                'interval_seconds':30,'query_runs':10,'bucket_seconds':data['bucket_seconds'],
                'points_returned':len(data['points']),'samples_aggregated':sum(p['sample_count'] for p in data['points']),
                'response_bytes':len(response.content),'query_ms_median':statistics.median(timings),
                'query_ms_p95_nearest_rank':sorted(timings)[9],'query_ms_each':timings,
                'cleanup_batch_ms':cleanup_ms,'cleanup_deleted':cleanup['deleted'],'foreign_key_errors':len(fk),
                'query_plan':plan}
        engine.dispose()
    Path('docs/evidence/phase03/history-performance.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    run()

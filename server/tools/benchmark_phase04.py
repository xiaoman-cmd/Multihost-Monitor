"""Isolated synthetic SQLite alert/outbox benchmark; no network or production DB."""
import json
import os
import platform
import statistics
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text

from app.alerts import evaluate_batch
from app.db import make_engine, session_factory
from app.models import AgentSample, AlertEvent, AlertInstance, AlertRule, Host, HostMetric, NotificationChannel, NotificationDelivery
from app.notifications import claim
from app.security import digest


def main():
    with tempfile.TemporaryDirectory(prefix='mhm-phase04-benchmark-') as directory:
        os.environ['MHM_DATABASE_URL']='sqlite:///'+directory+'/benchmark.db'
        command.upgrade(Config('alembic.ini'),'head')
        engine=make_engine(os.environ['MHM_DATABASE_URL']);sessions=session_factory(engine)
        stamp=int(time.time())
        with sessions.begin() as db:
            channels=[NotificationChannel(name=f'SYNTHETIC-{i}',kind='webhook',config={'secret_ref':'not-installed.json'}) for i in range(2)]
            db.add_all(channels);db.flush()
            ids=[c.id for c in channels]
            db.add_all([AlertRule(name='SYNTHETIC CPU',metric='cpu_percent',operator='gt',threshold=80,recovery_threshold=60,duration_seconds=0,channel_ids=ids),AlertRule(name='SYNTHETIC available memory',metric='memory_percent',operator='lt',threshold=20,recovery_threshold=30,duration_seconds=0,channel_ids=ids)])
            for i in range(50):
                host=Host(name=f'SYNTHETIC host {i}',hostname='benchmark.invalid',last_seen_at=stamp);db.add(host);db.flush()
                metric=HostMetric(host_id=host.id,sampled_at=stamp,cpu_percent=90.,memory_total_bytes=1024,memory_used_bytes=924,load_1=1.);db.add(metric);db.flush()
                payload={'cpu_percent':90.,'memory_total_bytes':1024,'memory_available_bytes':100,'disks':[],'services':[]}
                db.add(AgentSample(host_id=host.id,sample_id=str(uuid4()),sampled_at=stamp,received_at=stamp,content_hash=digest(json.dumps(payload)),host_metric_id=metric.id,payload=payload))
        before=time.perf_counter();evaluate_batch(sessions);initial_ms=(time.perf_counter()-before)*1000
        runs=[]
        for _ in range(5):
            before=time.perf_counter();evaluate_batch(sessions);runs.append((time.perf_counter()-before)*1000)
        before=time.perf_counter();claimed=claim(sessions);claim_ms=(time.perf_counter()-before)*1000
        with sessions() as db:
            counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [Host,AlertInstance,AlertEvent,NotificationDelivery]}
            fk=db.execute(text('PRAGMA foreign_key_check')).all()
            wal=db.scalar(text('PRAGMA journal_mode'))
        result={'kind':'synthetic temporary file SQLite; no network; not production capacity acceptance','platform':platform.platform(),'python':platform.python_version(),'pairs':100,'initial_evaluation_and_atomic_outbox_ms':initial_ms,'steady_batch_ms_each':runs,'steady_batch_ms_median':statistics.median(runs),'claim_count':len(claimed),'claim_ms':claim_ms,'counts':counts,'foreign_key_errors':len(fk),'journal_mode':wal}
        engine.dispose()
    path=Path('docs/evidence/phase04/performance.json');path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()

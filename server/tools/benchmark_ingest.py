"""Run a disposable in-process SQLite ingestion benchmark; never contacts real hosts."""
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

from app.config import Settings
from app.main import create_app


def run():
    with tempfile.TemporaryDirectory(prefix='mhm-benchmark-') as folder:
        url = f'sqlite:///{folder}/bench.db'
        os.environ['MHM_DATABASE_URL'] = url
        command.upgrade(Config('alembic.ini'), 'head')
        password = secrets.token_urlsafe(32)
        app = create_app(Settings(database_url=url, admin_password=password, _env_file=None))
        with TestClient(app) as client:
            login = client.post('/api/auth/login', json={'username':'admin','password':password},
                                headers={'Origin':'http://127.0.0.1:8000'}).json()
            host = client.post('/api/v1/hosts', json={'name':'benchmark','hostname':'synthetic'},
                headers={'Origin':'http://127.0.0.1:8000','X-CSRF-Token':login['csrf_token']}).json()
            timings = []
            sizes = []
            stamp = int(time.time())
            for batch in range(20):
                samples = []
                for i in range(32):
                    samples.append({'sample_id':str(uuid4()),'sampled_at':stamp-(batch*32+i)*30,
                        'metadata':{'hostname':'synthetic','os':'linux/test','arch':'amd64','agent_version':'bench'},
                        'cpu_percent':10.0,'load_1':0.1,'load_5':0.2,'load_15':0.3,'memory_total_bytes':1024,
                        'memory_available_bytes':512,'swap_total_bytes':0,'swap_used_bytes':0,'uptime_seconds':100,
                        'disks':[{'mountpoint':'/','total_bytes':1000,'used_bytes':100,'available_bytes':800,'inode_percent':1.0}],
                        'networks':[{'interface':'eth0','received_bytes':100,'sent_bytes':100,'receive_rate':None,'send_rate':None}],
                        'services':[]})
                body={'host_id':host['host']['id'],'samples':samples}
                sizes.append(len(json.dumps(body).encode()))
                start=time.perf_counter()
                response=client.post('/api/v1/agent/metrics',json=body,headers={'Authorization':'Bearer '+host['agent_token']})
                timings.append((time.perf_counter()-start)*1000)
                assert response.status_code == 200, response.status_code
                assert response.json()['accepted'] == 32
            ordered=sorted(timings)
            result={'kind':'synthetic TestClient + file SQLite WAL; no socket/TLS; not real-host acceptance',
                'platform':platform.platform(),'python':platform.python_version(),'batches':20,'samples_per_batch':32,
                'total_samples':640,'batch_bytes_mean':statistics.mean(sizes),'batch_ms_median':statistics.median(timings),
                'batch_ms_p95':ordered[18],'total_request_seconds':sum(timings)/1000,
                'samples_per_request_second':640/(sum(timings)/1000)}
    Path('docs/phase-02-performance.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    run()

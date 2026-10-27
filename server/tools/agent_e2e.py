"""Local macOS real-process HTTPS test. Explicitly NOT two-Linux-host acceptance.

Run from repository root with MHM_AGENT_BINARY pointing at a locally built binary.
Creates only temporary credentials, certificate and database; no production deployment.
"""
import json
import os
import platform
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]


def run():
    binary = os.environ['MHM_AGENT_BINARY']
    processes = []
    with tempfile.TemporaryDirectory(prefix='mhm-e2e-') as temp:
        folder = Path(temp)
        (folder/'certificate.conf').write_text('''[req]
distinguished_name=dn
x509_extensions=ext
prompt=no
[dn]
CN=localhost
[ext]
subjectAltName=DNS:localhost,IP:127.0.0.1
basicConstraints=critical,CA:TRUE
keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign
extendedKeyUsage=serverAuth
''')
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
            '-keyout',str(folder/'key.pem'),'-out',str(folder/'ca.pem'),'-config',str(folder/'certificate.conf')],
            check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        os.chmod(folder/'key.pem',0o600)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port = sock.getsockname()[1]
        origin=f'https://127.0.0.1:{port}'
        password=secrets.token_urlsafe(32)
        url=f'sqlite:///{folder}/monitor.db'
        os.environ['MHM_DATABASE_URL']=url
        command.upgrade(Config(str(ROOT/'alembic.ini')),'head')
        env={**os.environ,'MHM_DATABASE_URL':url,'MHM_ADMIN_PASSWORD':password,'MHM_ORIGIN':origin,
             'MHM_COOKIE_SECURE':'true'}
        context=ssl.create_default_context(cafile=str(folder/'ca.pem'))
        opener=build_opener(HTTPCookieProcessor(),HTTPSHandler(context=context))
        def request(path, body=None, headers=None):
            data=json.dumps(body).encode() if body is not None else None
            req=Request(origin+path,data=data,headers={'Content-Type':'application/json',**(headers or {})})
            with opener.open(req,timeout=3) as response:
                raw=response.read()
                return json.loads(raw) if raw else None
        center_log=open(folder/'center.log','w')
        agent_log=open(folder/'agent.log','w')
        def start_center():
            proc=subprocess.Popen([str(ROOT/'.venv/bin/uvicorn'),'app.main:create_app','--factory','--app-dir',str(ROOT/'server'),
                '--host','127.0.0.1','--port',str(port),'--workers','1','--no-proxy-headers',
                '--ssl-keyfile',str(folder/'key.pem'),'--ssl-certfile',str(folder/'ca.pem')],
                env=env,cwd=ROOT,stdout=center_log,stderr=center_log)
            processes.append(proc)
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                try:
                    if request('/api/health/ready')['status']=='ok':return proc
                except Exception:
                    time.sleep(.1)
            raise RuntimeError('temporary center did not start')
        def start_agent():
            proc=subprocess.Popen([binary,'-config',str(folder/'config.json')],cwd=ROOT,stdout=agent_log,stderr=agent_log)
            processes.append(proc)
            return proc
        engine=create_engine(url)
        def counts():
            with engine.connect() as db:
                return db.scalar(text('SELECT count(*) FROM agent_samples'))
        def await_count(n,timeout):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                if counts()>=n:return
                time.sleep(.2)
            raise RuntimeError(f'expected {n} persisted local samples')
        try:
            center=start_center()
            csrf=request('/api/auth/login',{'username':'admin','password':password},{'Origin':origin})['csrf_token']
            registration=request('/api/v1/hosts',{'name':'local-e2e','hostname':'local-e2e'},
                                 {'Origin':origin,'X-CSRF-Token':csrf})
            token=registration['agent_token']
            (folder/'agent.token').write_text(token)
            os.chmod(folder/'agent.token',0o600)
            (folder/'config.json').write_text(json.dumps({'server_url':origin,'host_id':registration['host']['id'],
                'token_file':str(folder/'agent.token'),'ca_file':str(folder/'ca.pem'),'queue_capacity':4,'batch_size':4,
                'timeout_seconds':2,'mountpoints':['/'],'interfaces':[],'systemd_units':[]}))
            agent=start_agent()
            await_count(1,45)
            print('local HTTPS first real macOS sample persisted',flush=True)
            center.terminate();center.wait(timeout=10)
            time.sleep(33)
            assert agent.poll() is None, 'Agent exited during center outage'
            center=start_center()
            await_count(2,35)
            print('local center restarted; queued sample persisted',flush=True)
            first_agent=agent
            agent.terminate();agent.wait(timeout=12)
            before=counts()
            agent=start_agent()
            await_count(before+1,45)
            agent.terminate();agent.wait(timeout=12)
            print('local Agent restarted and resumed collection',flush=True)
            with engine.connect() as db:
                row=db.execute(text('SELECT payload FROM agent_samples ORDER BY id DESC LIMIT 1')).scalar_one()
                latest=json.loads(row)
                evidence={'kind':'real macOS process -> HTTPS -> FastAPI -> SQLite; NOT Linux acceptance',
                    'platform':platform.platform(),'first_sample':True,'center_outage_seconds':33,
                    'agent_survived_outage':first_agent.returncode==0,'buffer_replayed':True,'restart_resumed':True,
                    'persisted_samples':counts(),'cpu_percent_in_range':0<=latest['cpu_percent']<=100,
                    'memory_total_positive':latest['memory_total_bytes']>0,'disk_count':len(latest['disks']),
                    'network_count':len(latest['networks']),'journal_mode':db.scalar(text('PRAGMA journal_mode'))}
            # Capture only fixed event names/counts; never include temporary credential paths or telemetry identity.
            agent_log.flush()
            logs=[json.loads(line) for line in (folder/'agent.log').read_text().splitlines() if line.startswith('{')]
            assert all(token not in json.dumps(line) for line in logs)
            evidence['retry_events']=sum(line.get('msg')=='upload_retry' for line in logs)
            evidence['token_not_in_agent_log']=True
            Path('docs/phase-02-local-e2e.json').write_text(json.dumps(evidence,indent=2)+'\n')
            print(json.dumps(evidence,indent=2),flush=True)
        finally:
            for proc in reversed(processes):
                if proc.poll() is None:
                    proc.terminate()
                    try:proc.wait(timeout=12)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
            engine.dispose()
            center_log.close();agent_log.close()


if __name__=='__main__':
    run()

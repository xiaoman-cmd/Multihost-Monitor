"""Disposable local UI acceptance: real loopback HTTP probes, no synthetic metrics.

Touch/remove <private directory>/unavailable to change the actual test endpoint.
The scheduler probes its real response; this helper never writes check results.
"""
import json
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from alembic import command
from alembic.config import Config

ROOT=Path(__file__).resolve().parents[2]
PASSWORD='phase04-local-preview-only!'


def main():
    directory=Path(tempfile.mkdtemp(prefix='mhm-phase04-preview-'));directory.chmod(0o700)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            self.send_response(503 if (directory/'unavailable').exists() else 200)
            self.end_headers()
    endpoint=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=endpoint.serve_forever,daemon=True);thread.start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    url='sqlite:///'+str(directory/'preview.db');os.environ['MHM_DATABASE_URL']=url
    command.upgrade(Config('alembic.ini'),'head')
    env={**os.environ,'MHM_ADMIN_PASSWORD':PASSWORD,'MHM_ORIGIN':origin,'MHM_COOKIE_SECURE':'false','MHM_SCHEDULER_ENABLED':'true','MHM_PROBE_ALLOWED_CIDRS':'["127.0.0.1/32"]','MHM_NOTIFICATION_ALLOWED_CIDRS':'[]'}
    log=(directory/'process.log').open('w')
    center=subprocess.Popen([str(ROOT/'.venv/bin/uvicorn'),'app.main:create_app','--factory','--app-dir','server','--host','127.0.0.1','--port',str(port),'--workers','1','--no-proxy-headers'],cwd=ROOT,env=env,stdout=log,stderr=log)
    opener=build_opener(HTTPCookieProcessor())
    def request(path,body=None,headers=None,method=None):
        with opener.open(Request(origin+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Content-Type':'application/json',**(headers or {})}),timeout=10) as response:return json.loads(response.read())
    try:
        for _ in range(100):
            try:
                if request('/api/health/ready')['status']=='ok':break
            except (URLError,OSError):time.sleep(.1)
        else:raise RuntimeError('preview did not start')
        csrf=request('/api/auth/login',{'username':'admin','password':PASSWORD},{'Origin':origin})['csrf_token']
        headers={'Origin':origin,'X-CSRF-Token':csrf}
        host=request('/api/v1/hosts',{'name':'本地探测验收目标','hostname':'localhost','cloud_provider':'隔离测试','region':'本机回环'},headers)['host']
        check={'host_id':host['id'],'name':'本地 HTTP 健康检查','kind':'http','target':f'http://127.0.0.1:{endpoint.server_port}/health','interval_seconds':30,'timeout_seconds':5}
        created=request('/api/v1/checks',check,headers)
        request('/api/v1/alert-rules',{'host_id':host['id'],'name':'本地 HTTP 可用性','metric':'service_down','operator':'gte','threshold':1.0,'recovery_threshold':0.0,'duration_seconds':0,'target':f"check:{created['id']}"},headers)
        Path('/private/tmp/mhm-phase04-preview.json').write_text(json.dumps({'origin':origin,'directory':str(directory),'pid':os.getpid()}))
        print(json.dumps({'origin':origin,'directory':str(directory),'kind':'real local loopback endpoint only; no metrics seeded'}),flush=True)
        while True:
            time.sleep(1)
    finally:
        center.terminate()
        try:center.wait(timeout=15)
        except subprocess.TimeoutExpired:center.kill();center.wait()
        endpoint.shutdown();endpoint.server_close();thread.join();log.close()


if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:main()
    except KeyboardInterrupt:pass

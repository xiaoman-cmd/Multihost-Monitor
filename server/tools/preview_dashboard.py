"""Private localhost visual QA with REAL local Agent data; never seeds telemetry.

Waits for a private start-agent marker so the empty view can be inspected first.
Only the disposable preview credentials are fixed; never use this for deployment.
"""
import json
import os
import signal
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener

from alembic import command
from alembic.config import Config

ROOT=Path(__file__).resolve().parents[2]
PASSWORD='phase03-local-preview-only!'


def port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1',0))
        return connection.getsockname()[1]


def main():
    directory=Path(tempfile.mkdtemp(prefix='mhm-phase03-preview-'))
    os.chmod(directory,0o700)
    tls_port=port();browser_port=port()
    browser_origin=f'http://127.0.0.1:{browser_port}'
    backend_origin=f'https://127.0.0.1:{tls_port}'
    (directory/'cert.conf').write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=IP:127.0.0.1,DNS:localhost\nbasicConstraints=critical,CA:TRUE\nkeyUsage=digitalSignature,keyEncipherment,keyCertSign\nextendedKeyUsage=serverAuth\n')
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',str(directory/'key.pem'),'-out',str(directory/'ca.pem'),'-config',str(directory/'cert.conf')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    os.chmod(directory/'key.pem',0o600)
    url=f'sqlite:///{directory}/preview.db'
    os.environ['MHM_DATABASE_URL']=url
    command.upgrade(Config('alembic.ini'),'head')
    env={**os.environ,'MHM_ADMIN_PASSWORD':PASSWORD,'MHM_ORIGIN':browser_origin,'MHM_COOKIE_SECURE':'false'}
    log=open(directory/'process.log','w')
    center=subprocess.Popen([str(ROOT/'.venv/bin/uvicorn'),'app.main:create_app','--factory','--app-dir','server','--host','127.0.0.1','--port',str(tls_port),'--workers','1','--no-proxy-headers','--ssl-keyfile',str(directory/'key.pem'),'--ssl-certfile',str(directory/'ca.pem')],cwd=ROOT,env=env,stdout=log,stderr=log)
    context=ssl.create_default_context(cafile=str(directory/'ca.pem'))
    opener=build_opener(HTTPCookieProcessor(),HTTPSHandler(context=context))
    def request(path,body=None,headers=None):
        data=json.dumps(body).encode() if body is not None else None
        with opener.open(Request(backend_origin+path,data=data,headers={'Content-Type':'application/json',**(headers or {})}),timeout=10) as response:
            return json.loads(response.read())
    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):self.forward()
        def do_POST(self):self.forward()
        def do_PUT(self):self.forward()
        def do_PATCH(self):self.forward()
        def log_message(self,*args):pass
        def forward(self):
            count=int(self.headers.get('Content-Length','0'))
            if count>1048576:self.send_error(413);return
            body=self.rfile.read(count) if count else None
            req=Request(backend_origin+self.path,data=body,method=self.command,headers={k:v for k,v in self.headers.items() if k.lower() not in {'host','connection','content-length'}})
            try:response=build_opener(HTTPSHandler(context=context)).open(req,timeout=20)
            except HTTPError as error:response=error
            with response:
                raw=response.read();self.send_response(response.status)
                for key,value in response.headers.items():
                    if key.lower() not in {'content-length','connection','transfer-encoding'}:self.send_header(key,value)
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    proxy=ThreadingHTTPServer(('127.0.0.1',browser_port),Proxy)
    agent=None
    try:
        for _ in range(100):
            try:
                if request('/api/health/ready')['status']=='ok':break
            except Exception:time.sleep(.1)
        else:raise RuntimeError('preview center unavailable')
        threading.Thread(target=proxy.serve_forever,daemon=True).start()
        Path('/private/tmp/mhm-phase03-preview.json').write_text(json.dumps({'browser_origin':browser_origin,'directory':str(directory),'pid':os.getpid()}))
        print(json.dumps({'browser_origin':browser_origin,'directory':str(directory),'state':'empty workspace; real Agent waits for marker'}),flush=True)
        while not (directory/'start-agent').exists():time.sleep(.2)
        csrf=request('/api/auth/login',{'username':'admin','password':PASSWORD},{'Origin':browser_origin})['csrf_token']
        # This private helper session invalidates old preview browser sessions by design.
        registration=request('/api/v1/hosts',{'name':'本机开发工作站','hostname':'local-development','cloud_provider':'本地开发','region':'上海','tags':['真实采集','开发环境'],'systemd_units':[]},{'Origin':browser_origin,'X-CSRF-Token':csrf})
        (directory/'agent.token').write_text(registration['agent_token']);os.chmod(directory/'agent.token',0o600)
        (directory/'agent.json').write_text(json.dumps({'server_url':backend_origin,'host_id':registration['host']['id'],'token_file':str(directory/'agent.token'),'ca_file':str(directory/'ca.pem'),'queue_capacity':240,'batch_size':16,'timeout_seconds':5,'mountpoints':['/'],'interfaces':[],'systemd_units':[]}));os.chmod(directory/'agent.json',0o600)
        agent=subprocess.Popen([os.environ['MHM_AGENT_BINARY'],'-config',str(directory/'agent.json')],stdout=log,stderr=log)
        print('Real local macOS Agent started; no synthetic metrics; re-login preview browser after helper session.',flush=True)
        while True:time.sleep(1)
    finally:
        proxy.shutdown()
        for process in [agent,center]:
            if process and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=12)
                except subprocess.TimeoutExpired:process.kill();process.wait()
        log.close()


if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    main()

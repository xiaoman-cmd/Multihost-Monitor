import asyncio
import json
import secrets
from contextlib import contextmanager

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.responses import JSONResponse

from .agent_schema import Batch, Heartbeat, HostCreate
from .models import AgentCredential, AgentSample, DiskMetric, Host, HostMetric, now
from .security import digest

MAX_BODY = 1024 * 1024
BODY_TIMEOUT = 15


class BodyLimit:
    """Bound actual streamed bytes before parsing, including absent/forged Content-Length."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/v1/'):
            return await self.app(scope, receive, send)
        body = bytearray()
        try:
            async with asyncio.timeout(BODY_TIMEOUT):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    chunk = message.get('body', b'')
                    if len(body) + len(chunk) > MAX_BODY:
                        return await JSONResponse({'detail': 'Request too large'}, status_code=413)(scope, receive, send)
                    body.extend(chunk)
                    if not message.get('more_body', False):
                        break
        except TimeoutError:
            return await JSONResponse({'detail': 'Request read timed out'}, status_code=408)(scope, receive, send)
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
        await self.app(scope, bounded_receive, send)


def host_view(h):
    return {key: getattr(h, key) for key in ['id', 'name', 'hostname', 'cloud_provider', 'region',
        'tags', 'systemd_units', 'status', 'last_seen_at', 'metadata_at', 'os', 'arch', 'agent_version']}


def install_routes(app, sessions, authenticated, same_origin):

    @contextmanager
    def write():
        # Serialize auth+revocation+ingestion under the SQLite writer lock. This also
        # prevents duplicate request races from passing the idempotency precheck.
        with sessions.begin() as db:
            db.execute(text('BEGIN IMMEDIATE'))
            yield db

    @app.exception_handler(OperationalError)
    async def unavailable(request, exception):
        return JSONResponse({'detail': 'Storage temporarily unavailable'}, status_code=503,
                            headers={'Retry-After': '5', 'Cache-Control': 'no-store'})

    def admin_write(request: Request, auth=Depends(authenticated), origin=Depends(same_origin)):
        if not secrets.compare_digest(request.headers.get('x-csrf-token', ''), auth[0].csrf_token):
            raise HTTPException(403, 'CSRF token rejected')
        return auth

    def agent(db, request, host_id=None):
        header = request.headers.get('authorization', '')
        scheme, _, token = header.partition(' ')
        credential = None
        if scheme.lower() == 'bearer' and len(token) == 64 and token.startswith('mhm_'):
            credential = db.scalar(select(AgentCredential).where(AgentCredential.token_hash == digest(token)))
        host = db.get(Host, credential.host_id) if credential else None
        if (credential is None or credential.revoked_at is not None or
                (credential.expires_at is not None and credential.expires_at <= now()) or
                host is None or host.status == 'disabled' or (host_id is not None and host.id != host_id)):
            raise HTTPException(401, 'Agent authentication failed', headers={'WWW-Authenticate': 'Bearer'})
        return host

    def issue(db, host_id):
        token = 'mhm_' + secrets.token_urlsafe(45)  # 360 random bits, exactly 64 ASCII characters.
        db.add(AgentCredential(host_id=host_id, token_hash=digest(token)))
        return token

    def require_host(db, host_id):
        host = db.get(Host, host_id)
        if host is None:
            raise HTTPException(404, 'Host not found')
        return host

    def timestamp(value):
        if not now() - 7 * 86400 <= value <= now() + 120:
            raise HTTPException(422, 'Sample time outside accepted window')

    def seen(host, sampled_at, metadata):
        host.last_seen_at = max(host.last_seen_at or 0, now())
        if host.metadata_at is None or sampled_at > host.metadata_at:
            host.metadata_at = sampled_at
            host.hostname = metadata.hostname
            host.os, host.arch, host.agent_version = metadata.os, metadata.arch, metadata.agent_version
        host.status = 'online'

    @app.post('/api/v1/hosts', status_code=201, dependencies=[Depends(admin_write)])
    def create_host(payload: HostCreate):
        try:
            with write() as db:
                host = Host(**payload.model_dump())
                db.add(host)
                db.flush()
                token = issue(db, host.id)
                result = {'host': host_view(host), 'agent_token': token}
            return result
        except IntegrityError:
            raise HTTPException(409, 'Host name already exists') from None

    @app.get('/api/v1/hosts', dependencies=[Depends(authenticated)])
    def list_hosts(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
        with sessions() as db:
            return {'items': [host_view(h) for h in db.scalars(select(Host).order_by(Host.id).offset(offset).limit(limit))]}

    @app.put('/api/v1/hosts/{host_id}', dependencies=[Depends(admin_write)])
    def configure_host(host_id: int, payload: HostCreate):
        try:
            with write() as db:
                host = require_host(db, host_id)
                for key, value in payload.model_dump().items():
                    setattr(host, key, value)
                db.flush()
                return host_view(host)
        except IntegrityError:
            raise HTTPException(409, 'Host name already exists') from None

    @app.post('/api/v1/hosts/{host_id}/token/rotate', dependencies=[Depends(admin_write)])
    def rotate(host_id: int):
        with write() as db:
            require_host(db, host_id)
            db.execute(update(AgentCredential).where(AgentCredential.host_id == host_id,
                AgentCredential.revoked_at.is_(None)).values(revoked_at=now()))
            token = issue(db, host_id)
        return {'agent_token': token}

    @app.post('/api/v1/hosts/{host_id}/token/revoke', status_code=204, dependencies=[Depends(admin_write)])
    def revoke(host_id: int):
        with write() as db:
            require_host(db, host_id)
            db.execute(update(AgentCredential).where(AgentCredential.host_id == host_id,
                AgentCredential.revoked_at.is_(None)).values(revoked_at=now()))

    @app.get('/api/v1/agent/config')
    def config(request: Request, host_id: int = Query(gt=0)):
        with sessions() as db:
            host = agent(db, request, host_id)
            return {'host_id': host.id, 'interval_seconds': 30, 'max_batch': 32,
                    'systemd_units': host.systemd_units}

    @app.post('/api/v1/agent/heartbeat')
    def heartbeat(payload: Heartbeat, request: Request):
        with write() as db:
            host = agent(db, request, payload.host_id)
            timestamp(payload.sampled_at)
            seen(host, payload.sampled_at, payload.metadata)
        return {'status': 'accepted'}

    @app.post('/api/v1/agent/metrics')
    def metrics(payload: Batch, request: Request):
        accepted, duplicates = 0, 0
        with write() as db:
            host = agent(db, request, payload.host_id)
            for sample in payload.samples:
                timestamp(sample.sampled_at)
                if any(s.unit not in host.systemd_units for s in sample.services):
                    raise HTTPException(422, 'Collection target is not permitted')
                value = sample.model_dump()
                content_hash = digest(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False))
                previous = db.scalar(select(AgentSample).where(AgentSample.host_id == host.id,
                                                              AgentSample.sample_id == sample.sample_id))
                if previous is not None:
                    if previous.content_hash != content_hash:
                        raise HTTPException(409, 'Sample ID content conflict')
                    duplicates += 1
                    continue
                if db.scalar(select(HostMetric.id).where(HostMetric.host_id == host.id,
                                                         HostMetric.sampled_at == sample.sampled_at)):
                    raise HTTPException(409, 'Sample timestamp conflict')
                metric = HostMetric(host_id=host.id, sampled_at=sample.sampled_at,
                    cpu_percent=sample.cpu_percent, memory_total_bytes=sample.memory_total_bytes,
                    memory_used_bytes=sample.memory_total_bytes - sample.memory_available_bytes, load_1=sample.load_1)
                db.add(metric)
                db.flush()
                db.add(AgentSample(host_id=host.id, sample_id=sample.sample_id, sampled_at=sample.sampled_at,
                                   host_metric_id=metric.id, content_hash=content_hash, payload=value))
                for disk in sample.disks:
                    db.add(DiskMetric(host_id=host.id, sampled_at=sample.sampled_at, mountpoint=disk.mountpoint,
                                      total_bytes=disk.total_bytes, used_bytes=disk.used_bytes))
                db.flush()
                accepted += 1
            newest = max(payload.samples, key=lambda s: s.sampled_at)
            seen(host, newest.sampled_at, newest.metadata)
        return {'accepted': accepted, 'duplicates': duplicates}

    @app.get('/api/v1/hosts/{host_id}/metrics', dependencies=[Depends(authenticated)])
    def query_metrics(host_id: int, since: int = Query(0, ge=0), until: int = Query(2**63-1, ge=0),
                      limit: int = Query(100, ge=1, le=100), before_id: int | None = Query(None, gt=0)):
        with sessions() as db:
            require_host(db, host_id)
            statement = select(AgentSample).where(AgentSample.host_id == host_id,
                AgentSample.sampled_at >= max(since, now() - 7 * 86400), AgentSample.sampled_at <= until)
            if before_id is not None:
                statement = statement.where(AgentSample.id < before_id)
            rows = db.scalars(statement.order_by(AgentSample.id.desc()).limit(limit)).all()
            return {'items': [{'id': s.id, 'received_at': s.received_at, **s.payload} for s in rows],
                    'next_before_id': rows[-1].id if len(rows) == limit else None}

    from .dashboard_api import install_dashboard_routes
    install_dashboard_routes(app, sessions, authenticated, admin_write, write)

    from .monitoring_api import install_monitoring_routes
    install_monitoring_routes(app, sessions, authenticated, admin_write, write)

"""Bounded read models over Phase 02 telemetry; never manufacture samples."""
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, case, func, or_, select, text

from .agent_api import host_view
from .models import AgentSample, Host, now

OFFLINE_SECONDS = 90
RETENTION_SECONDS = 7 * 86400
RANGES = {'1h': (3600, 30), '6h': (21600, 60), '24h': (86400, 300), '7d': (604800, 1800)}


def effective_status(stamp):
    return case((Host.status == 'disabled', 'disabled'),
                (Host.last_seen_at >= stamp - OFFLINE_SECONDS, 'online'), else_='offline')


def network_sum(networks, key):
    if not networks or any(n.get(key) is None for n in networks):
        return None
    return sum(n[key] for n in networks)


def overview(host, sample, stamp):
    result = host_view(host)
    result['status'] = ('disabled' if host.status == 'disabled' else
                        'online' if host.last_seen_at is not None and host.last_seen_at >= stamp - 90 else 'offline')
    result['latest'] = None
    result['freshness'] = 'none'
    result['sample_age_seconds'] = None
    if sample is not None:
        value = sample.payload
        memory_total = value['memory_total_bytes']
        memory_used = memory_total - value['memory_available_bytes']
        disks = value['disks']
        age = max(0, stamp - sample.sampled_at)
        result['freshness'] = 'stale' if age > 90 or stamp - sample.received_at > 90 else 'fresh'
        result['sample_age_seconds'] = age
        result['latest'] = {'sampled_at': sample.sampled_at, 'received_at': sample.received_at,
            'cpu_percent': value['cpu_percent'], 'memory_percent': 100 * memory_used / memory_total,
            'memory_used_bytes': memory_used, 'memory_total_bytes': memory_total,
            'disk_percent': max((100 * d['used_bytes'] / d['total_bytes'] for d in disks), default=None),
            'receive_rate': network_sum(value['networks'], 'receive_rate'),
            'send_rate': network_sum(value['networks'], 'send_rate')}
    return result


def latest_statement(stamp):
    latest_id = (select(AgentSample.id).where(AgentSample.host_id == Host.id,
        AgentSample.sampled_at >= stamp - RETENTION_SECONDS).order_by(AgentSample.sampled_at.desc())
        .limit(1).correlate(Host).scalar_subquery())
    return select(Host, AgentSample).outerjoin(AgentSample, AgentSample.id == latest_id)


# Scan the bounded indexed time range once. Segment before aggregation so a gap
# inside a large bucket cannot be averaged across or become a connecting line.
HISTORY_SQL = text('''
WITH source AS MATERIALIZED (
 SELECT s.id,s.sampled_at,m.cpu_percent AS cpu,
 100.0*m.memory_used_bytes/nullif(m.memory_total_bytes,0) AS memory,
 m.memory_used_bytes AS memory_used,
 CASE WHEN json_extract(s.payload,'$.swap_total_bytes')=0 THEN 0 ELSE
 100.0*json_extract(s.payload,'$.swap_used_bytes')/nullif(json_extract(s.payload,'$.swap_total_bytes'),0) END AS swap,
 json_extract(s.payload,'$.swap_used_bytes') AS swap_used,
 CASE WHEN json_array_length(s.payload,'$.networks')=0 OR EXISTS
 (SELECT 1 FROM json_each(s.payload,'$.networks') n WHERE json_extract(n.value,'$.receive_rate') IS NULL)
 THEN NULL ELSE (SELECT SUM(json_extract(n.value,'$.receive_rate')) FROM json_each(s.payload,'$.networks') n) END AS rx,
 CASE WHEN json_array_length(s.payload,'$.networks')=0 OR EXISTS
 (SELECT 1 FROM json_each(s.payload,'$.networks') n WHERE json_extract(n.value,'$.send_rate') IS NULL)
 THEN NULL ELSE (SELECT SUM(json_extract(n.value,'$.send_rate')) FROM json_each(s.payload,'$.networks') n) END AS tx
 FROM agent_samples s JOIN host_metrics m ON m.id=s.host_metric_id
 WHERE s.host_id=:host_id AND s.sampled_at>=:since AND s.sampled_at<=:until
), ordered AS (
 SELECT *,LAG(sampled_at) OVER (ORDER BY sampled_at,id) AS previous_at FROM source
), segmented AS (
 SELECT *,SUM(CASE WHEN previous_at IS NULL OR sampled_at-previous_at>90 THEN 1 ELSE 0 END)
 OVER (ORDER BY sampled_at) AS segment,
 CAST(sampled_at/:bucket AS INTEGER)*:bucket AS bucket_at FROM ordered
)
SELECT segment,bucket_at,MIN(sampled_at) AS first_sample_at,MAX(sampled_at) AS last_sample_at,
 COUNT(*) AS sample_count,AVG(cpu) AS cpu_percent,AVG(memory) AS memory_percent,
 AVG(swap) AS swap_percent,AVG(memory_used) AS memory_used_bytes,AVG(swap_used) AS swap_used_bytes,
 CASE WHEN COUNT(rx)=COUNT(*) THEN AVG(rx) ELSE NULL END AS receive_rate,
 CASE WHEN COUNT(tx)=COUNT(*) THEN AVG(tx) ELSE NULL END AS send_rate
FROM segmented GROUP BY segment,bucket_at ORDER BY first_sample_at LIMIT 1201
''')


class StateInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    disabled: bool


def install_dashboard_routes(app, sessions, authenticated, admin_write, write):
    def require_host(db, host_id):
        host = db.get(Host, host_id)
        if host is None:
            raise HTTPException(404, 'Host not found')
        return host

    @app.get('/api/v1/dashboard', dependencies=[Depends(authenticated)])
    def dashboard(search: str = Query('', max_length=128),
                  status: Literal['all','online','offline','disabled'] = 'all',
                  provider: str = Query('', max_length=64), tag: str = Query('', max_length=64),
                  after_id: int = Query(0, ge=0), limit: int = Query(12, ge=1, le=50)):
        stamp = now()
        with sessions() as db:
            state = effective_status(stamp)
            stats = {'total': 0, 'online': 0, 'offline': 0, 'disabled': 0}
            for key, count in db.execute(select(state, func.count()).group_by(state)):
                stats[key] = count
                stats['total'] += count
            filters = []
            if search.strip():
                filters.append(or_(*(column.icontains(search.strip(), autoescape=True) for column in
                                     [Host.name, Host.hostname, Host.cloud_provider, Host.region])))
            if status != 'all':
                filters.append(state == status)
            if provider:
                filters.append(Host.cloud_provider == provider)
            if tag:
                items = func.json_each(Host.tags).table_valued('value')
                filters.append(select(1).select_from(items).where(items.c.value == tag).exists())
            total = db.scalar(select(func.count()).select_from(Host).where(*filters))
            rows = db.execute(latest_statement(stamp).where(*filters, Host.id > after_id)
                              .order_by(Host.id).limit(limit + 1)).all()
            has_more = len(rows) > limit
            rows = rows[:limit]
            providers = db.scalars(select(Host.cloud_provider).where(Host.cloud_provider != '')
                                   .distinct().order_by(Host.cloud_provider).limit(100)).all()
            return {'server_time': stamp, 'offline_after_seconds': 90, 'stats': stats, 'total': total,
                    'items': [overview(h, s, stamp) for h, s in rows],
                    'next_after_id': rows[-1][0].id if has_more else None, 'providers': list(providers)}

    @app.get('/api/v1/hosts/{host_id}', dependencies=[Depends(authenticated)])
    def detail(host_id: int):
        stamp = now()
        with sessions() as db:
            row = db.execute(latest_statement(stamp).where(Host.id == host_id)).first()
            if row is None:
                raise HTTPException(404, 'Host not found')
            host, sample = row
            return {'server_time': stamp, 'host': overview(host, sample, stamp),
                    'sample': sample.payload if sample else None}

    @app.patch('/api/v1/hosts/{host_id}/state', dependencies=[Depends(admin_write)])
    def state(host_id: int, payload: StateInput):
        with write() as db:
            host = require_host(db, host_id)
            host.status = 'disabled' if payload.disabled else 'pending'
        return {'id': host_id, 'disabled': payload.disabled}

    @app.get('/api/v1/hosts/{host_id}/history', dependencies=[Depends(authenticated)])
    def history(host_id: int, time_range: Literal['1h','6h','24h','7d'] = Query('1h', alias='range')):
        stamp = now()
        seconds, bucket = RANGES[time_range]
        since = stamp - seconds
        with sessions() as db:
            require_host(db, host_id)
            points = [dict(row) for row in db.execute(HISTORY_SQL,
                {'host_id': host_id, 'since': since, 'until': stamp, 'bucket': bucket}).mappings()]
            if len(points) > 1200:
                raise HTTPException(422, 'Too many separated history segments; select a shorter range')
        occupied = {point['bucket_at'] for point in points}
        for bucket_at in range((since // bucket) * bucket, (stamp // bucket) * bucket + 1, bucket):
            if bucket_at not in occupied:
                points.append({'segment': None, 'bucket_at': bucket_at, 'first_sample_at': None,
                    'last_sample_at': None, 'sample_count': 0, 'cpu_percent': None,
                    'memory_percent': None, 'swap_percent': None, 'memory_used_bytes': None,
                    'swap_used_bytes': None, 'receive_rate': None, 'send_rate': None})
        for point in points:
            point['at'] = point['last_sample_at'] if point['last_sample_at'] is not None else max(since, point['bucket_at'])
        points.sort(key=lambda point: point['at'])
        if len(points) > 1200:
            raise HTTPException(422, 'Too many separated history segments; select a shorter range')
        return {'server_time': stamp, 'from': since, 'until': stamp, 'bucket_seconds': bucket,
                'gap_after_seconds': 90, 'points': points}

    @app.get('/api/v1/hosts/{host_id}/samples', dependencies=[Depends(authenticated)])
    def raw_samples(host_id: int, since: int = Query(ge=0), until: int = Query(ge=0),
                    limit: int = Query(100, ge=1, le=200), snapshot_id: int | None = Query(None, ge=0),
                    after_time: int | None = Query(None, ge=0), after_id: int | None = Query(None, ge=0)):
        stamp = now()
        if until < since or until - since > RETENTION_SECONDS or until > stamp + 120:
            raise HTTPException(422, 'Invalid UTC time range (maximum 7 days)')
        if after_time is not None and snapshot_id is None:
            raise HTTPException(422, 'snapshot_id is required when continuing a page')
        if (after_time is None) != (after_id is None):
            raise HTTPException(422, 'Both cursor fields are required')
        with sessions() as db:
            require_host(db, host_id)
            if snapshot_id is None:
                snapshot_id = db.scalar(select(func.max(AgentSample.id)).where(AgentSample.host_id == host_id)) or 0
            statement = select(AgentSample).where(AgentSample.host_id == host_id,
                AgentSample.sampled_at >= max(since, stamp - RETENTION_SECONDS),
                AgentSample.sampled_at <= min(until, stamp), AgentSample.id <= snapshot_id)
            if after_time is not None:
                statement = statement.where(or_(AgentSample.sampled_at > after_time,
                    and_(AgentSample.sampled_at == after_time, AgentSample.id > after_id)))
            rows = db.scalars(statement.order_by(AgentSample.sampled_at, AgentSample.id).limit(limit + 1)).all()
            has_more = len(rows) > limit
            rows = rows[:limit]
            return {'server_time': stamp, 'snapshot_id': snapshot_id,
                    'items': [{'id': row.id, 'received_at': row.received_at, **row.payload} for row in rows],
                    'next_cursor': {'after_time': rows[-1].sampled_at, 'after_id': rows[-1].id}
                                   if has_more else None}

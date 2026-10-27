"""Session/CSRF-protected monitoring administration; never accepts actual secrets."""
import re
from typing import Literal

from fastapi import Depends, HTTPException, Query
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from starlette.responses import JSONResponse

from .alerts import emit, instance_view
from .models import (AlertEvent, AlertInstance, AlertRule, CheckResult, Host,
                     NotificationChannel, NotificationDelivery, ServiceCheck, now)
from .notifications import enqueue_test
from .outbound import OutboundError, target


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class CheckInput(Input):
    host_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    kind: Literal['http', 'tcp']
    target: str = Field(max_length=2048)
    interval_seconds: int = Field(default=60, ge=30, le=3600)
    timeout_seconds: int = Field(default=5, ge=1, le=15)
    expected_status_min: int = Field(default=200, ge=100, le=599)
    expected_status_max: int = Field(default=299, ge=100, le=599)
    tls_min_days: int = Field(default=14, ge=0, le=365)
    enabled: bool = True

    @model_validator(mode='after')
    def valid(self):
        if not self.name.strip() or self.timeout_seconds > self.interval_seconds or self.expected_status_max < self.expected_status_min:
            raise ValueError('Invalid check timing or status range')
        try:
            target(self.target, self.kind)
        except OutboundError:
            raise ValueError('Invalid target; use http(s)://host/path or tcp://host:port') from None
        return self


class RuleInput(Input):
    host_id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=128)
    metric: Literal['cpu_percent', 'memory_percent', 'disk_percent', 'service_down', 'host_offline']
    operator: Literal['gt', 'gte', 'lt', 'lte'] = 'gt'
    threshold: float = Field(ge=0, le=100)
    recovery_threshold: float = Field(ge=0, le=100)
    duration_seconds: int = Field(default=60, ge=0, le=86400)
    cooldown_seconds: int = Field(default=3600, ge=60, le=604800)
    severity: Literal['info', 'warning', 'critical'] = 'warning'
    target: str = Field(default='', max_length=128)
    channel_ids: list[int] = Field(default_factory=list, max_length=16)
    enabled: bool = True

    @model_validator(mode='after')
    def valid(self):
        if not self.name.strip() or len(set(self.channel_ids)) != len(self.channel_ids) or any(i <= 0 for i in self.channel_ids):
            raise ValueError('Invalid name or channel IDs')
        if self.operator in {'gt', 'gte'} and self.recovery_threshold >= self.threshold:
            raise ValueError('Recovery must be below the firing threshold')
        if self.operator in {'lt', 'lte'} and self.recovery_threshold <= self.threshold:
            raise ValueError('Recovery must be above the firing threshold')
        if self.metric in {'service_down', 'host_offline'} and (self.operator != 'gte' or self.threshold != 1 or self.recovery_threshold != 0):
            raise ValueError('Binary rules require gte 1, recovery 0')
        if self.metric == 'service_down' and not re.fullmatch(r'(?:check:[1-9][0-9]*|[A-Za-z0-9_:@.\-]{1,120}\.service)', self.target):
            raise ValueError('Select a check:<id> or whitelisted .service unit')
        if self.metric in {'cpu_percent', 'memory_percent', 'host_offline'} and self.target:
            raise ValueError('This metric does not accept a target')
        return self


class ChannelInput(Input):
    name: str = Field(min_length=1, max_length=128)
    kind: Literal['webhook', 'email']
    secret_ref: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}\.json$')
    enabled: bool = True

    @model_validator(mode='after')
    def valid(self):
        if not self.name.strip():
            raise ValueError('Name must not be blank')
        return self


class SilenceInput(Input):
    seconds: int = Field(ge=0, le=604800)


def view(row, keys):
    return {key: getattr(row, key) for key in keys}


CHECK_FIELDS = ['id', *CheckInput.model_fields, 'next_run_at', 'revision']
RULE_FIELDS = ['id', *RuleInput.model_fields]
RESULT_FIELDS = ['id', 'check_id', 'host_id', 'checked_at', 'status', 'latency_ms', 'message', 'http_status', 'tls_expires_at', 'check_revision']


def install_monitoring_routes(app, sessions, authenticated, admin_write, write):
    @app.exception_handler(RequestValidationError)
    async def validation(request, error):
        roots = ('checks', 'alert-rules', 'alerts', 'alert-events', 'notification-channels', 'notification-deliveries')
        if any(request.url.path == '/api/v1/' + root or request.url.path.startswith('/api/v1/' + root + '/') for root in roots):
            # Never echo rejected secret fields or serialize non-finite raw inputs.
            return JSONResponse({'detail': 'Invalid monitoring fields or query limits'}, status_code=422)
        return await request_validation_exception_handler(request, error)

    @app.exception_handler(IntegrityError)
    async def conflict(request, error):
        return JSONResponse({'detail': 'Configuration conflicts with an existing record'}, status_code=409)

    def require(db, model, row_id):
        row = db.get(model, row_id)
        if row is None:
            raise HTTPException(404, 'Record not found')
        return row

    def capacity(db, model, maximum):
        if db.scalar(select(func.count()).select_from(model)) >= maximum:
            raise HTTPException(422, 'Configuration capacity reached')

    def validate_rule(db, payload):
        if payload.host_id is not None:
            host = require(db, Host, payload.host_id)
            if payload.metric == 'service_down' and not payload.target.startswith('check:') and payload.target not in host.systemd_units:
                raise HTTPException(422, 'Service must be in the host whitelist')
        if payload.target.startswith('check:'):
            check = require(db, ServiceCheck, int(payload.target[6:]))
            if payload.host_id != check.host_id:
                raise HTTPException(422, 'Service check rules require the matching host')
        for channel_id in payload.channel_ids:
            require(db, NotificationChannel, channel_id)

    @app.get('/api/v1/checks', dependencies=[Depends(authenticated)])
    def checks(after_id: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        with sessions() as db:
            rows = db.scalars(select(ServiceCheck).where(ServiceCheck.id > after_id).order_by(ServiceCheck.id).limit(limit+1)).all()
            items = []
            for row in rows[:limit]:
                latest = db.scalar(select(CheckResult).where(CheckResult.check_id == row.id, CheckResult.check_revision == row.revision).order_by(CheckResult.checked_at.desc()).limit(1))
                items.append({**view(row, CHECK_FIELDS), 'latest': view(latest, RESULT_FIELDS) if latest else None})
            return {'items': items, 'next_after_id': rows[limit-1].id if len(rows)>limit else None, 'server_time': now()}

    def save_check(payload, row_id=None):
        with write() as db:
            require(db, Host, payload.host_id)
            if row_id is None:
                capacity(db, ServiceCheck, 500)
                row = ServiceCheck(**payload.model_dump())
                db.add(row)
            else:
                row = require(db, ServiceCheck, row_id)
                if row.host_id != payload.host_id:
                    raise HTTPException(422, 'Check host cannot change')
                for key, value in payload.model_dump().items():
                    setattr(row, key, value)
                row.revision += 1
            row.next_run_at = now()
            db.flush()
            return view(row, CHECK_FIELDS)

    @app.post('/api/v1/checks', status_code=201, dependencies=[Depends(admin_write)])
    def create_check(payload: CheckInput):
        return save_check(payload)

    @app.put('/api/v1/checks/{check_id}', dependencies=[Depends(admin_write)])
    def update_check(check_id: int, payload: CheckInput):
        return save_check(payload, check_id)

    @app.get('/api/v1/checks/{check_id}/results', dependencies=[Depends(authenticated)])
    def results(check_id: int, before_id: int | None = Query(None, gt=0), since: int = Query(0, ge=0),
                until: int | None = Query(None, ge=0), limit: int = Query(50, ge=1, le=100)):
        stamp = now()
        if until is not None and until < since:
            raise HTTPException(422, 'Invalid UTC range')
        with sessions() as db:
            check = require(db, ServiceCheck, check_id)
            filters = [CheckResult.check_id == check_id, CheckResult.checked_at >= max(since, stamp-604800), CheckResult.checked_at <= min(until or stamp, stamp)]
            if before_id:
                filters.append(CheckResult.id < before_id)
            rows = db.scalars(select(CheckResult).where(*filters).order_by(CheckResult.id.desc()).limit(limit+1)).all()
            return {'check': view(check, CHECK_FIELDS), 'items': [view(row, RESULT_FIELDS) for row in rows[:limit]],
                'next_before_id': rows[limit-1].id if len(rows)>limit else None, 'server_time': stamp}

    @app.get('/api/v1/alert-rules', dependencies=[Depends(authenticated)])
    def rules():
        with sessions() as db:
            return {'items': [view(r, RULE_FIELDS) for r in db.scalars(select(AlertRule).order_by(AlertRule.id).limit(200))]}

    def save_rule(payload, row_id=None):
        with write() as db:
            validate_rule(db, payload)
            if row_id is None:
                capacity(db, AlertRule, 200)
                row = AlertRule(**payload.model_dump())
                db.add(row)
            else:
                row = require(db, AlertRule, row_id)
                has_history = db.scalar(select(AlertInstance.id).where(AlertInstance.rule_id == row_id).limit(1))
                if has_history and any(getattr(row, key) != getattr(payload, key) for key in ['host_id', 'metric', 'target']):
                    raise HTTPException(409, 'Create a new rule when changing an evaluated rule target')
                for key, value in payload.model_dump().items():
                    setattr(row, key, value)
                for instance in db.scalars(select(AlertInstance).where(AlertInstance.rule_id == row_id)):
                    instance.pending_since = None
            db.flush()
            return view(row, RULE_FIELDS)

    @app.post('/api/v1/alert-rules', status_code=201, dependencies=[Depends(admin_write)])
    def create_rule(payload: RuleInput):
        return save_rule(payload)

    @app.put('/api/v1/alert-rules/{rule_id}', dependencies=[Depends(admin_write)])
    def update_rule(rule_id: int, payload: RuleInput):
        return save_rule(payload, rule_id)

    @app.get('/api/v1/alerts', dependencies=[Depends(authenticated)])
    def alerts(state: Literal['all', 'normal', 'pending', 'firing', 'resolved'] = 'all',
               before_id: int | None = Query(None, gt=0), limit: int = Query(50, ge=1, le=100)):
        with sessions() as db:
            filters = []
            if before_id:
                filters.append(AlertInstance.id < before_id)
            if state == 'firing':
                filters.append(AlertInstance.status == 'firing')
            elif state == 'pending':
                filters += [AlertInstance.status != 'firing', AlertInstance.pending_since.is_not(None)]
            elif state in {'normal', 'resolved'}:
                filters += [AlertInstance.status != 'firing', AlertInstance.pending_since.is_(None),
                            AlertInstance.fired_at.is_(None) if state == 'normal' else AlertInstance.fired_at.is_not(None)]
            rows = db.scalars(select(AlertInstance).where(*filters).order_by(AlertInstance.id.desc()).limit(limit+1)).all()
            return {'items': [instance_view(r) for r in rows[:limit]], 'next_before_id': rows[limit-1].id if len(rows)>limit else None, 'server_time': now()}

    @app.post('/api/v1/alerts/{alert_id}/ack', dependencies=[Depends(admin_write)])
    def acknowledge(alert_id: int):
        with write() as db:
            instance = require(db, AlertInstance, alert_id)
            if instance.status != 'firing':
                raise HTTPException(409, 'Only firing alerts may be acknowledged')
            if instance.acknowledged_at is None:
                instance.acknowledged_at = now()
                emit(db, instance, db.get(AlertRule, instance.rule_id), 'acknowledged', now(), 'acknowledged_by_admin', notify=False)
            return instance_view(instance)

    @app.post('/api/v1/alerts/{alert_id}/silence', dependencies=[Depends(admin_write)])
    def silence(alert_id: int, payload: SilenceInput):
        with write() as db:
            instance = require(db, AlertInstance, alert_id)
            instance.silenced_until = now() + payload.seconds if payload.seconds else None
            emit(db, instance, db.get(AlertRule, instance.rule_id), 'acknowledged', now(), f'silenced_for_seconds:{payload.seconds}', notify=False)
            return instance_view(instance)

    @app.get('/api/v1/alert-events', dependencies=[Depends(authenticated)])
    def events(alert_id: int | None = Query(None, gt=0), before_id: int | None = Query(None, gt=0), limit: int = Query(50, ge=1, le=100)):
        with sessions() as db:
            filters = []
            if alert_id:
                filters.append(AlertEvent.alert_id == alert_id)
            if before_id:
                filters.append(AlertEvent.id < before_id)
            rows = db.scalars(select(AlertEvent).where(*filters).order_by(AlertEvent.id.desc()).limit(limit+1)).all()
            return {'items': [view(r, ['id', 'alert_id', 'kind', 'created_at', 'message']) for r in rows[:limit]],
                'next_before_id': rows[limit-1].id if len(rows)>limit else None}

    def channel_view(row):
        return {**view(row, ['id', 'name', 'kind', 'enabled']), 'secret_ref': row.config.get('secret_ref')}

    @app.get('/api/v1/notification-channels', dependencies=[Depends(authenticated)])
    def channels():
        with sessions() as db:
            return {'items': [channel_view(row) for row in db.scalars(select(NotificationChannel).order_by(NotificationChannel.id).limit(50))]}

    def save_channel(payload, row_id=None):
        with write() as db:
            if row_id is None:
                capacity(db, NotificationChannel, 50)
                row = NotificationChannel(name=payload.name, kind=payload.kind, enabled=payload.enabled, config={'secret_ref': payload.secret_ref})
                db.add(row)
            else:
                row = require(db, NotificationChannel, row_id)
                row.name, row.kind, row.enabled, row.config = payload.name, payload.kind, payload.enabled, {'secret_ref': payload.secret_ref}
            db.flush()
            return channel_view(row)

    @app.post('/api/v1/notification-channels', status_code=201, dependencies=[Depends(admin_write)])
    def create_channel(payload: ChannelInput):
        return save_channel(payload)

    @app.put('/api/v1/notification-channels/{channel_id}', dependencies=[Depends(admin_write)])
    def update_channel(channel_id: int, payload: ChannelInput):
        return save_channel(payload, channel_id)

    @app.post('/api/v1/notification-channels/{channel_id}/test', status_code=202, dependencies=[Depends(admin_write)])
    def test_channel(channel_id: int):
        with write() as db:
            channel = require(db, NotificationChannel, channel_id)
            if not channel.enabled:
                raise HTTPException(409, 'Channel disabled')
            if db.scalar(select(NotificationDelivery.id).where(NotificationDelivery.channel_id == channel_id,
                NotificationDelivery.event_id.is_(None), NotificationDelivery.created_at > now()-60).limit(1)):
                raise HTTPException(429, 'Test notification cooldown is 60 seconds')
            return {'delivery_id': enqueue_test(db, channel_id, now()), 'status': 'pending'}

    @app.get('/api/v1/notification-deliveries', dependencies=[Depends(authenticated)])
    def delivery_history(before_id: int | None = Query(None, gt=0), limit: int = Query(50, ge=1, le=100)):
        with sessions() as db:
            query = select(NotificationDelivery)
            if before_id:
                query = query.where(NotificationDelivery.id < before_id)
            rows = db.scalars(query.order_by(NotificationDelivery.id.desc()).limit(limit+1)).all()
            return {'items': [view(r, ['id', 'event_id', 'channel_id', 'status', 'attempts', 'created_at', 'delivered_at', 'last_error', 'next_attempt_at', 'idempotency_key']) for r in rows[:limit]],
                'next_before_id': rows[limit-1].id if len(rows)>limit else None}

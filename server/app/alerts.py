"""Persisted alert evaluation, isolated from ingestion and all network I/O."""
import time
from uuid import uuid4
from contextlib import contextmanager

from sqlalchemy import and_, or_, select, text

from .models import (AgentSample, AlertEvent, AlertInstance, AlertRule, CheckResult,
                     Host, NotificationChannel, NotificationDelivery, ServiceCheck, now)


@contextmanager
def transaction(sessions):
    with sessions.begin() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        yield db


def instance_view(instance):
    keys = ['id', 'rule_id', 'host_id', 'fingerprint', 'opened_at', 'resolved_at', 'pending_since',
            'last_evaluated_at', 'data_status', 'last_value', 'acknowledged_at', 'silenced_until']
    return {**{k: getattr(instance, k) for k in keys}, 'state': instance.state}


def emit(db, instance, rule, kind, stamp, message, *, notify=True):
    event = AlertEvent(alert_id=instance.id, kind=kind, created_at=stamp, message=message)
    db.add(event)
    db.flush()
    if notify:
        # Cooldown also applies to a suppressed notification decision.
        instance.last_notified_at = stamp
    if not notify or (instance.silenced_until or 0) > stamp:
        return event
    payload = {'event_id': event.id, 'alert_id': instance.id, 'host_id': instance.host_id,
               'rule': rule.name, 'severity': rule.severity, 'state': kind, 'message': message, 'at': stamp}
    for channel_id in rule.channel_ids:
        channel = db.get(NotificationChannel, channel_id)
        if channel and channel.enabled:
            db.add(NotificationDelivery(event_id=event.id, channel_id=channel_id,
                idempotency_key=uuid4().hex,
                payload=payload, created_at=stamp, next_attempt_at=stamp))
    return event


def observation(db, rule, host, stamp):
    if host.status == 'disabled' or not rule.enabled:
        return None
    if rule.metric == 'host_offline':
        return float(host.last_seen_at is None or stamp - host.last_seen_at > 90)
    if rule.metric == 'service_down' and rule.target.startswith('check:'):
        check_id = int(rule.target[6:])
        check = db.get(ServiceCheck, check_id)
        if not check or check.host_id != host.id or not check.enabled:
            return None
        result = db.scalar(select(CheckResult).where(CheckResult.check_id == check_id, CheckResult.check_revision == check.revision)
                           .order_by(CheckResult.checked_at.desc()).limit(1))
        if not result or stamp - result.checked_at > max(90, check.interval_seconds * 2) or result.status == 'unknown':
            return None
        return float(result.status == 'down')
    sample = db.scalar(select(AgentSample).where(AgentSample.host_id == host.id)
                       .order_by(AgentSample.sampled_at.desc()).limit(1))
    if not sample or stamp - sample.sampled_at > 90 or stamp - sample.received_at > 90:
        return None
    value = sample.payload
    if rule.metric == 'cpu_percent':
        return value['cpu_percent']
    if rule.metric == 'memory_percent':
        return 100 * value['memory_available_bytes'] / value['memory_total_bytes']
    if rule.metric == 'disk_percent':
        disks = [d for d in value['disks'] if not rule.target or d['mountpoint'] == rule.target]
        return max((100 * d['used_bytes'] / d['total_bytes'] for d in disks), default=None)
    if rule.metric == 'service_down':
        if rule.target not in host.systemd_units:
            return None
        service = next((s for s in value['services'] if s['unit'] == rule.target), None)
        if not service or service['state'] in {'unavailable', 'unknown'}:
            return None
        return float(service['state'] != 'active')
    return None


def bad(rule, value):
    return {'gt': value > rule.threshold, 'gte': value >= rule.threshold,
            'lt': value < rule.threshold, 'lte': value <= rule.threshold,
            'eq': value == rule.threshold}[rule.operator]


def recovered(rule, value):
    threshold = rule.recovery_threshold
    if threshold is None:
        return not bad(rule, value)
    if rule.operator in {'gt', 'gte'}:
        return value <= threshold
    if rule.operator in {'lt', 'lte'}:
        return value >= threshold
    return value != rule.threshold


def evaluate_one(sessions, rule_id, host_id, stamp=None):
    stamp = now() if stamp is None else stamp
    with transaction(sessions) as db:
        rule, host = db.get(AlertRule, rule_id), db.get(Host, host_id)
        if not rule or not host or (rule.host_id is not None and rule.host_id != host_id):
            return
        fingerprint = rule.metric + ':' + rule.target
        instance = db.scalar(select(AlertInstance).where(AlertInstance.rule_id == rule_id,
            AlertInstance.host_id == host_id, AlertInstance.fingerprint == fingerprint)
            .order_by(AlertInstance.id.desc()).limit(1))
        if instance is None:
            instance = AlertInstance(rule_id=rule_id, host_id=host_id, fingerprint=fingerprint,
                status='resolved', opened_at=stamp, resolved_at=stamp)
            db.add(instance)
            db.flush()
        value = observation(db, rule, host, stamp)
        previous_at = instance.last_evaluated_at
        instance.last_evaluated_at = stamp
        instance.last_value = value
        instance.data_status = 'missing' if value is None else 'present'
        if value is None:
            instance.pending_since = None
            return instance_view(instance)
        if instance.status == 'firing':
            if recovered(rule, value):
                instance.status = 'resolved'
                instance.resolved_at = max(stamp, instance.opened_at)
                instance.pending_since = None
                emit(db, instance, rule, 'resolved', stamp, 'recovered')
            elif not instance.acknowledged_at and stamp - (instance.last_notified_at or instance.opened_at) >= rule.cooldown_seconds:
                emit(db, instance, rule, 'firing', stamp, 'repeat')
        elif bad(rule, value):
            if instance.pending_since is None or (previous_at is not None and stamp - previous_at > 30):
                instance.pending_since = stamp
                instance.acknowledged_at = None
            if stamp - instance.pending_since >= rule.duration_seconds:
                instance.status = 'firing'
                instance.opened_at = stamp
                instance.fired_at = stamp
                instance.resolved_at = None
                instance.pending_since = None
                emit(db, instance, rule, 'firing', stamp, 'threshold_exceeded')
        else:
            instance.pending_since = None
        return instance_view(instance)


def evaluate_batch(sessions, cursor=(0, 0), limit=100):
    with sessions() as db:
        rows = db.execute(select(AlertRule.id, Host.id).select_from(AlertRule).join(Host,
            or_(AlertRule.host_id.is_(None), AlertRule.host_id == Host.id)).where(
            or_(AlertRule.id > cursor[0], and_(AlertRule.id == cursor[0], Host.id > cursor[1])))
            .order_by(AlertRule.id, Host.id).limit(limit)).all()
    started = time.monotonic()
    processed = []
    for rule_id, host_id in rows:
        evaluate_one(sessions, rule_id, host_id)
        processed.append((rule_id, host_id))
        if time.monotonic() - started >= 2:
            break
    return processed[-1] if processed and (len(rows) == limit or len(processed) < len(rows)) else (0, 0)

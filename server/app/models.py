"""Monitoring schema. All timestamps are UTC Unix seconds; secrets are never API fields."""
import time

from sqlalchemy import (Boolean, CheckConstraint, Float, ForeignKey, ForeignKeyConstraint,
                        Index, Integer, JSON, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> int:
    return int(time.time())


class Host(Base):
    __tablename__ = "hosts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    hostname: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    created_at: Mapped[int] = mapped_column(default=now)
    last_seen_at: Mapped[int | None]
    cloud_provider: Mapped[str] = mapped_column(String(64), default="", server_default="")
    region: Mapped[str] = mapped_column(String(64), default="", server_default="")
    tags: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    systemd_units: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    metadata_at: Mapped[int | None]
    os: Mapped[str | None] = mapped_column(String(128))
    arch: Mapped[str | None] = mapped_column(String(32))
    agent_version: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 128", name="ck_host_name"),
        CheckConstraint("length(trim(hostname)) BETWEEN 1 AND 255", name="ck_host_hostname"),
        CheckConstraint("status IN ('pending','online','offline','disabled')", name="ck_host_status"),
    )


class AgentCredential(Base):
    __tablename__ = "agent_credentials"
    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[int] = mapped_column(default=now)
    expires_at: Mapped[int | None]
    revoked_at: Mapped[int | None]
    __table_args__ = (
        CheckConstraint("length(token_hash) = 64", name="ck_agent_token_hash"),
        CheckConstraint("expires_at IS NULL OR expires_at > created_at", name="ck_agent_expiry"),
    )


class HostMetric(Base):
    __tablename__ = "host_metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"))
    sampled_at: Mapped[int]
    cpu_percent: Mapped[float] = mapped_column(Float)
    memory_total_bytes: Mapped[int]
    memory_used_bytes: Mapped[int]
    load_1: Mapped[float] = mapped_column(Float)
    __table_args__ = (
        UniqueConstraint("host_id", "sampled_at", name="uq_host_metric_sample"),
        Index("ix_host_metrics_retention", "sampled_at", "id"),
        CheckConstraint("cpu_percent BETWEEN 0 AND 100", name="ck_cpu_percent"),
        CheckConstraint("memory_total_bytes > 0 AND memory_used_bytes BETWEEN 0 AND memory_total_bytes", name="ck_memory_bytes"),
        CheckConstraint("load_1 >= 0", name="ck_load_1"),
        CheckConstraint("sampled_at > 0", name="ck_host_sample_time"),
    )


class DiskMetric(Base):
    __tablename__ = "disk_metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"))
    sampled_at: Mapped[int]
    mountpoint: Mapped[str] = mapped_column(String(512))
    total_bytes: Mapped[int]
    used_bytes: Mapped[int]
    __table_args__ = (
        UniqueConstraint("host_id", "mountpoint", "sampled_at", name="uq_disk_sample"),
        Index("ix_disk_metrics_retention", "sampled_at", "id"),
        CheckConstraint("total_bytes > 0 AND used_bytes BETWEEN 0 AND total_bytes", name="ck_disk_bytes"),
        CheckConstraint("length(mountpoint) BETWEEN 1 AND 512", name="ck_disk_mount"),
        CheckConstraint("sampled_at > 0", name="ck_disk_sample_time"),
    )


class ServiceCheck(Base):
    __tablename__ = "service_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    target: Mapped[str] = mapped_column(String(2048))
    interval_seconds: Mapped[int] = mapped_column(default=60, server_default="60")
    timeout_seconds: Mapped[int] = mapped_column(default=5, server_default="5")
    enabled: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="ck_check_enabled"), default=True)
    __table_args__ = (
        UniqueConstraint("host_id", "name", name="uq_host_check_name"),
        UniqueConstraint("id", "host_id", name="uq_check_host"),
        CheckConstraint("kind IN ('http','tcp','process')", name="ck_check_kind"),
        CheckConstraint("interval_seconds >= 5 AND timeout_seconds > 0 AND timeout_seconds <= interval_seconds", name="ck_check_timing"),
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 128 AND length(target) BETWEEN 1 AND 2048", name="ck_check_text"),
    )


class CheckResult(Base):
    __tablename__ = "check_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int]
    host_id: Mapped[int]
    checked_at: Mapped[int]
    status: Mapped[str] = mapped_column(String(16))
    latency_ms: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(["check_id", "host_id"], ["service_checks.id", "service_checks.host_id"], ondelete="CASCADE"),
        UniqueConstraint("check_id", "checked_at", name="uq_check_result_sample"),
        Index("ix_check_result_host_time", "host_id", "checked_at"),
        CheckConstraint("status IN ('up','down','unknown')", name="ck_result_status"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_result_latency"),
        CheckConstraint("checked_at > 0", name="ck_result_time"),
    )


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int | None] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    metric: Mapped[str] = mapped_column(String(32))
    operator: Mapped[str] = mapped_column(String(8))
    threshold: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[int] = mapped_column(default=60)
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    enabled: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="ck_rule_enabled"), default=True)
    __table_args__ = (
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 128", name="ck_rule_name"),
        CheckConstraint("metric IN ('cpu_percent','memory_percent','disk_percent','service_down','host_offline')", name="ck_rule_metric"),
        CheckConstraint("operator IN ('gt','gte','lt','lte','eq')", name="ck_rule_operator"),
        CheckConstraint("duration_seconds >= 0", name="ck_rule_duration"),
        CheckConstraint("severity IN ('info','warning','critical')", name="ck_rule_severity"),
        CheckConstraint("threshold BETWEEN 0 AND 100", name="ck_rule_threshold"),
    )


class AlertInstance(Base):
    __tablename__ = "alert_instances"
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"), index=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="firing")
    opened_at: Mapped[int] = mapped_column(default=now)
    resolved_at: Mapped[int | None]
    __table_args__ = (
        CheckConstraint("status IN ('firing','resolved')", name="ck_alert_status"),
        CheckConstraint("length(fingerprint) BETWEEN 1 AND 255", name="ck_alert_fingerprint"),
        CheckConstraint("(status = 'firing' AND resolved_at IS NULL) OR (status = 'resolved' AND resolved_at IS NOT NULL AND resolved_at >= opened_at)", name="ck_alert_resolution"),
        Index("uq_active_alert", "rule_id", "host_id", "fingerprint", unique=True, sqlite_where=(status == "firing")),
    )


class AlertEvent(Base):
    __tablename__ = "alert_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alert_instances.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[int] = mapped_column(default=now)
    message: Mapped[str] = mapped_column(Text)
    __table_args__ = (CheckConstraint("kind IN ('firing','resolved','acknowledged')", name="ck_event_kind"),)


class NotificationChannel(Base):
    __tablename__ = "notification_channels"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="ck_channel_enabled"), default=True)
    __table_args__ = (
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 128", name="ck_channel_name"),
        CheckConstraint("kind IN ('webhook','email')", name="ck_channel_kind"),
    )


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("alert_events.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("notification_channels.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[int] = mapped_column(default=now)
    delivered_at: Mapped[int | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("event_id", "channel_id", name="uq_event_channel"),
        CheckConstraint("status IN ('pending','sent','failed')", name="ck_delivery_status"),
        CheckConstraint("attempts >= 0", name="ck_delivery_attempts"),
        CheckConstraint("(status = 'sent' AND delivered_at IS NOT NULL AND delivered_at >= created_at) OR (status != 'sent' AND delivered_at IS NULL)", name="ck_delivery_time"),
    )


class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[int] = mapped_column(default=now)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[int] = mapped_column(default=now)
    expires_at: Mapped[int] = mapped_column(index=True)
    __table_args__ = (CheckConstraint("expires_at > created_at", name="ck_session_expiry"),)


class AgentSample(Base):
    __tablename__ = "agent_samples"
    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"))
    sample_id: Mapped[str] = mapped_column(String(36))
    sampled_at: Mapped[int]
    received_at: Mapped[int] = mapped_column(default=now)
    content_hash: Mapped[str] = mapped_column(String(64))
    host_metric_id: Mapped[int] = mapped_column(ForeignKey("host_metrics.id", ondelete="CASCADE"), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint("host_id", "sample_id", name="uq_agent_sample_id"),
        Index("ix_agent_samples_retention", "sampled_at", "id"),
        UniqueConstraint("host_id", "sampled_at", name="uq_agent_sample_time"),
        CheckConstraint("length(sample_id) = 36", name="ck_agent_sample_id"),
        CheckConstraint("length(content_hash) = 64", name="ck_agent_sample_hash"),
        CheckConstraint("sampled_at > 0 AND received_at > 0", name="ck_agent_sample_times"),
        {"sqlite_autoincrement": True},
    )

"""Phase 01 schema

Revision ID: 0001_phase01
Revises: none
"""
from alembic import op
import sqlalchemy as sa

revision = '0001_phase01'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('admin_users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('hosts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('hostname', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
    sa.Column('created_at', sa.Integer(), nullable=False),
    sa.Column('last_seen_at', sa.Integer(), nullable=True),
    sa.CheckConstraint("status IN ('pending','online','offline','disabled')", name='ck_host_status'),
    sa.CheckConstraint('length(trim(hostname)) BETWEEN 1 AND 255', name='ck_host_hostname'),
    sa.CheckConstraint('length(trim(name)) BETWEEN 1 AND 128', name='ck_host_name'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('notification_channels',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('enabled', sa.Boolean(create_constraint=True, name='ck_channel_enabled'), nullable=False),
    sa.CheckConstraint("kind IN ('webhook','email')", name='ck_channel_kind'),
    sa.CheckConstraint('length(trim(name)) BETWEEN 1 AND 128', name='ck_channel_name'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('admin_sessions',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('admin_id', sa.Integer(), nullable=False),
    sa.Column('csrf_token', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.Integer(), nullable=False),
    sa.CheckConstraint('expires_at > created_at', name='ck_session_expiry'),
    sa.ForeignKeyConstraint(['admin_id'], ['admin_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('token_hash')
    )
    with op.batch_alter_table('admin_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_admin_sessions_admin_id'), ['admin_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_admin_sessions_expires_at'), ['expires_at'], unique=False)

    op.create_table('agent_credentials',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('host_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.Integer(), nullable=True),
    sa.Column('revoked_at', sa.Integer(), nullable=True),
    sa.CheckConstraint('expires_at IS NULL OR expires_at > created_at', name='ck_agent_expiry'),
    sa.CheckConstraint('length(token_hash) = 64', name='ck_agent_token_hash'),
    sa.ForeignKeyConstraint(['host_id'], ['hosts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    with op.batch_alter_table('agent_credentials', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_credentials_host_id'), ['host_id'], unique=False)

    op.create_table('alert_rules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('host_id', sa.Integer(), nullable=True),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('metric', sa.String(length=32), nullable=False),
    sa.Column('operator', sa.String(length=8), nullable=False),
    sa.Column('threshold', sa.Float(), nullable=False),
    sa.Column('duration_seconds', sa.Integer(), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('enabled', sa.Boolean(create_constraint=True, name='ck_rule_enabled'), nullable=False),
    sa.CheckConstraint("metric IN ('cpu_percent','memory_percent','disk_percent','service_down','host_offline')", name='ck_rule_metric'),
    sa.CheckConstraint("operator IN ('gt','gte','lt','lte','eq')", name='ck_rule_operator'),
    sa.CheckConstraint("severity IN ('info','warning','critical')", name='ck_rule_severity'),
    sa.CheckConstraint('duration_seconds >= 0', name='ck_rule_duration'),
    sa.CheckConstraint('length(trim(name)) BETWEEN 1 AND 128', name='ck_rule_name'),
    sa.CheckConstraint('threshold BETWEEN 0 AND 100', name='ck_rule_threshold'),
    sa.ForeignKeyConstraint(['host_id'], ['hosts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    with op.batch_alter_table('alert_rules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alert_rules_host_id'), ['host_id'], unique=False)

    op.create_table('disk_metrics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('host_id', sa.Integer(), nullable=False),
    sa.Column('sampled_at', sa.Integer(), nullable=False),
    sa.Column('mountpoint', sa.String(length=512), nullable=False),
    sa.Column('total_bytes', sa.Integer(), nullable=False),
    sa.Column('used_bytes', sa.Integer(), nullable=False),
    sa.CheckConstraint('length(mountpoint) BETWEEN 1 AND 512', name='ck_disk_mount'),
    sa.CheckConstraint('sampled_at > 0', name='ck_disk_sample_time'),
    sa.CheckConstraint('total_bytes > 0 AND used_bytes BETWEEN 0 AND total_bytes', name='ck_disk_bytes'),
    sa.ForeignKeyConstraint(['host_id'], ['hosts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('host_id', 'mountpoint', 'sampled_at', name='uq_disk_sample')
    )
    op.create_table('host_metrics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('host_id', sa.Integer(), nullable=False),
    sa.Column('sampled_at', sa.Integer(), nullable=False),
    sa.Column('cpu_percent', sa.Float(), nullable=False),
    sa.Column('memory_total_bytes', sa.Integer(), nullable=False),
    sa.Column('memory_used_bytes', sa.Integer(), nullable=False),
    sa.Column('load_1', sa.Float(), nullable=False),
    sa.CheckConstraint('cpu_percent BETWEEN 0 AND 100', name='ck_cpu_percent'),
    sa.CheckConstraint('load_1 >= 0', name='ck_load_1'),
    sa.CheckConstraint('memory_total_bytes > 0 AND memory_used_bytes BETWEEN 0 AND memory_total_bytes', name='ck_memory_bytes'),
    sa.CheckConstraint('sampled_at > 0', name='ck_host_sample_time'),
    sa.ForeignKeyConstraint(['host_id'], ['hosts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('host_id', 'sampled_at', name='uq_host_metric_sample')
    )
    op.create_table('service_checks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('host_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('target', sa.String(length=2048), nullable=False),
    sa.Column('interval_seconds', sa.Integer(), server_default='60', nullable=False),
    sa.Column('timeout_seconds', sa.Integer(), server_default='5', nullable=False),
    sa.Column('enabled', sa.Boolean(create_constraint=True, name='ck_check_enabled'), nullable=False),
    sa.CheckConstraint("kind IN ('http','tcp','process')", name='ck_check_kind'),
    sa.CheckConstraint('interval_seconds >= 5 AND timeout_seconds > 0 AND timeout_seconds <= interval_seconds', name='ck_check_timing'),
    sa.CheckConstraint('length(trim(name)) BETWEEN 1 AND 128 AND length(target) BETWEEN 1 AND 2048', name='ck_check_text'),
    sa.ForeignKeyConstraint(['host_id'], ['hosts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('host_id', 'name', name='uq_host_check_name'),
    sa.UniqueConstraint('id', 'host_id', name='uq_check_host')
    )
    op.create_table('alert_instances',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('rule_id', sa.Integer(), nullable=False),
    sa.Column('host_id', sa.Integer(), nullable=False),
    sa.Column('fingerprint', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('opened_at', sa.Integer(), nullable=False),
    sa.Column('resolved_at', sa.Integer(), nullable=True),
    sa.CheckConstraint("(status = 'firing' AND resolved_at IS NULL) OR (status = 'resolved' AND resolved_at IS NOT NULL AND resolved_at >= opened_at)", name='ck_alert_resolution'),
    sa.CheckConstraint("status IN ('firing','resolved')", name='ck_alert_status'),
    sa.CheckConstraint('length(fingerprint) BETWEEN 1 AND 255', name='ck_alert_fingerprint'),
    sa.ForeignKeyConstraint(['host_id'], ['hosts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['rule_id'], ['alert_rules.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('alert_instances', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alert_instances_host_id'), ['host_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_alert_instances_rule_id'), ['rule_id'], unique=False)
        batch_op.create_index('uq_active_alert', ['rule_id', 'host_id', 'fingerprint'], unique=True, sqlite_where=sa.text("status = 'firing'"))

    op.create_table('check_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('check_id', sa.Integer(), nullable=False),
    sa.Column('host_id', sa.Integer(), nullable=False),
    sa.Column('checked_at', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('latency_ms', sa.Float(), nullable=True),
    sa.Column('message', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('up','down','unknown')", name='ck_result_status'),
    sa.CheckConstraint('checked_at > 0', name='ck_result_time'),
    sa.CheckConstraint('latency_ms IS NULL OR latency_ms >= 0', name='ck_result_latency'),
    sa.ForeignKeyConstraint(['check_id', 'host_id'], ['service_checks.id', 'service_checks.host_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('check_id', 'checked_at', name='uq_check_result_sample')
    )
    with op.batch_alter_table('check_results', schema=None) as batch_op:
        batch_op.create_index('ix_check_result_host_time', ['host_id', 'checked_at'], unique=False)

    op.create_table('alert_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('alert_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.Integer(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.CheckConstraint("kind IN ('firing','resolved','acknowledged')", name='ck_event_kind'),
    sa.ForeignKeyConstraint(['alert_id'], ['alert_instances.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('alert_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alert_events_alert_id'), ['alert_id'], unique=False)

    op.create_table('notification_deliveries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.Integer(), nullable=False),
    sa.Column('delivered_at', sa.Integer(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.CheckConstraint("(status = 'sent' AND delivered_at IS NOT NULL AND delivered_at >= created_at) OR (status != 'sent' AND delivered_at IS NULL)", name='ck_delivery_time'),
    sa.CheckConstraint("status IN ('pending','sent','failed')", name='ck_delivery_status'),
    sa.CheckConstraint('attempts >= 0', name='ck_delivery_attempts'),
    sa.ForeignKeyConstraint(['channel_id'], ['notification_channels.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['event_id'], ['alert_events.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_id', 'channel_id', name='uq_event_channel')
    )
    with op.batch_alter_table('notification_deliveries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notification_deliveries_channel_id'), ['channel_id'], unique=False)



def downgrade():
    with op.batch_alter_table('notification_deliveries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notification_deliveries_channel_id'))

    op.drop_table('notification_deliveries')
    with op.batch_alter_table('alert_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_alert_events_alert_id'))

    op.drop_table('alert_events')
    with op.batch_alter_table('check_results', schema=None) as batch_op:
        batch_op.drop_index('ix_check_result_host_time')

    op.drop_table('check_results')
    with op.batch_alter_table('alert_instances', schema=None) as batch_op:
        batch_op.drop_index('uq_active_alert', sqlite_where=sa.text("status = 'firing'"))
        batch_op.drop_index(batch_op.f('ix_alert_instances_rule_id'))
        batch_op.drop_index(batch_op.f('ix_alert_instances_host_id'))

    op.drop_table('alert_instances')
    op.drop_table('service_checks')
    op.drop_table('host_metrics')
    op.drop_table('disk_metrics')
    with op.batch_alter_table('alert_rules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_alert_rules_host_id'))

    op.drop_table('alert_rules')
    with op.batch_alter_table('agent_credentials', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agent_credentials_host_id'))

    op.drop_table('agent_credentials')
    with op.batch_alter_table('admin_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_admin_sessions_expires_at'))
        batch_op.drop_index(batch_op.f('ix_admin_sessions_admin_id'))

    op.drop_table('admin_sessions')
    op.drop_table('notification_channels')
    op.drop_table('hosts')
    op.drop_table('admin_users')

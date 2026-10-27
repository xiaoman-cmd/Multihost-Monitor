"""Phase 04 additive monitoring state and database outbox.

Revision ID: 0004_phase04
Revises: 0003_phase03
"""
import json
import re

from alembic import op
import sqlalchemy as sa

revision = '0004_phase04'
down_revision = '0003_phase03'
branch_labels = None
depends_on = None


def upgrade():
    # Fail before any DDL if an operator previously stored plaintext channel
    # credentials. Move those to protected references in a maintenance window.
    for (raw,) in op.get_bind().execute(sa.text('SELECT config FROM notification_channels')):
        try:
            config = json.loads(raw) if isinstance(raw, str) else raw
            valid = config == {} or (isinstance(config, dict) and set(config) == {'secret_ref'}
                and isinstance(config['secret_ref'], str)
                and re.fullmatch(r'[A-Za-z0-9_-]{1,80}\.json', config['secret_ref']))
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise RuntimeError('Move legacy channel configuration to protected secret references before Phase 04 upgrade')
    op.add_column('service_checks', sa.Column('expected_status_min', sa.Integer(), nullable=False, server_default='200'))
    op.add_column('service_checks', sa.Column('expected_status_max', sa.Integer(), nullable=False, server_default='299'))
    op.add_column('service_checks', sa.Column('tls_min_days', sa.Integer(), sa.CheckConstraint('expected_status_min BETWEEN 100 AND 599 AND expected_status_max BETWEEN expected_status_min AND 599 AND tls_min_days BETWEEN 0 AND 365', name='ck_probe_expectation'), nullable=False, server_default='14'))
    op.add_column('service_checks', sa.Column('next_run_at', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('service_checks', sa.Column('revision', sa.Integer(), nullable=False, server_default='1'))
    op.create_index('ix_service_checks_next_run_at', 'service_checks', ['next_run_at'])
    op.add_column('check_results', sa.Column('check_revision', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('check_results', sa.Column('http_status', sa.Integer(), nullable=True))
    op.add_column('check_results', sa.Column('tls_expires_at', sa.Integer(), nullable=True))
    op.add_column('alert_rules', sa.Column('recovery_threshold', sa.Float(), nullable=True))
    op.add_column('alert_rules', sa.Column('cooldown_seconds', sa.Integer(), sa.CheckConstraint('cooldown_seconds BETWEEN 60 AND 604800 AND (recovery_threshold IS NULL OR recovery_threshold BETWEEN 0 AND 100)', name='ck_rule_recovery'), nullable=False, server_default='3600'))
    op.add_column('alert_rules', sa.Column('target', sa.String(128), nullable=False, server_default=''))
    op.add_column('alert_rules', sa.Column('channel_ids', sa.JSON(), nullable=False, server_default='[]'))
    for name in ['pending_since', 'fired_at', 'last_evaluated_at', 'acknowledged_at', 'silenced_until', 'last_notified_at']:
        op.add_column('alert_instances', sa.Column(name, sa.Integer(), nullable=True))
    op.add_column('alert_instances', sa.Column('last_value', sa.Float(), nullable=True))
    op.add_column('alert_instances', sa.Column('data_status', sa.String(16), nullable=False, server_default='missing'))
    op.execute("UPDATE alert_instances SET fired_at=opened_at")
    # Leaf table only: no table refers to deliveries. Preserve existing rows/FKs.
    with op.batch_alter_table('notification_deliveries', recreate='always') as batch:
        batch.alter_column('event_id', existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column('next_attempt_at', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('lease_until', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('idempotency_key', sa.String(64), nullable=True))
        batch.add_column(sa.Column('payload', sa.JSON(), nullable=False, server_default='{}'))
        batch.create_unique_constraint('uq_delivery_idempotency', ['idempotency_key'])
        batch.create_index('ix_notification_deliveries_next_attempt_at', ['next_attempt_at'])


def downgrade():
    # Test-only outbox rows have no event; downgrade deliberately drops these only.
    op.execute('DELETE FROM notification_deliveries WHERE event_id IS NULL')
    with op.batch_alter_table('notification_deliveries', recreate='always') as batch:
        batch.drop_index('ix_notification_deliveries_next_attempt_at')
        batch.drop_constraint('uq_delivery_idempotency', type_='unique')
        for name in ['payload', 'idempotency_key', 'lease_until', 'next_attempt_at']:
            batch.drop_column(name)
        batch.alter_column('event_id', existing_type=sa.Integer(), nullable=False)
    for name in ['data_status', 'last_value', 'last_notified_at', 'silenced_until', 'acknowledged_at', 'last_evaluated_at', 'fired_at', 'pending_since']:
        op.drop_column('alert_instances', name)
    for name in ['channel_ids', 'target', 'cooldown_seconds', 'recovery_threshold']:
        op.drop_column('alert_rules', name)
    op.drop_column('check_results', 'tls_expires_at')
    op.drop_column('check_results', 'http_status')
    op.drop_column('check_results', 'check_revision')
    op.drop_index('ix_service_checks_next_run_at', table_name='service_checks')
    for name in ['revision', 'next_run_at', 'tls_min_days', 'expected_status_max', 'expected_status_min']:
        op.drop_column('service_checks', name)

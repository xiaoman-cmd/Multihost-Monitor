"""Agent host configuration and atomic samples

Revision ID: 0002_phase02
Revises: 0001_phase01
"""
from alembic import op
import sqlalchemy as sa

revision = '0002_phase02'
down_revision = '0001_phase01'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_samples',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('host_id', sa.Integer(), nullable=False),
    sa.Column('sample_id', sa.String(length=36), nullable=False),
    sa.Column('sampled_at', sa.Integer(), nullable=False),
    sa.Column('received_at', sa.Integer(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('host_metric_id', sa.Integer(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.CheckConstraint('length(content_hash) = 64', name='ck_agent_sample_hash'),
    sa.CheckConstraint('length(sample_id) = 36', name='ck_agent_sample_id'),
    sa.CheckConstraint('sampled_at > 0 AND received_at > 0', name='ck_agent_sample_times'),
    sa.ForeignKeyConstraint(['host_id'], ['hosts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['host_metric_id'], ['host_metrics.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('host_id', 'sample_id', name='uq_agent_sample_id'),
    sa.UniqueConstraint('host_id', 'sampled_at', name='uq_agent_sample_time'),
    sa.UniqueConstraint('host_metric_id')
    )
    op.add_column('hosts', sa.Column('cloud_provider', sa.String(length=64), server_default='', nullable=False))
    op.add_column('hosts', sa.Column('region', sa.String(length=64), server_default='', nullable=False))
    op.add_column('hosts', sa.Column('tags', sa.JSON(), server_default='[]', nullable=False))
    op.add_column('hosts', sa.Column('systemd_units', sa.JSON(), server_default='[]', nullable=False))
    op.add_column('hosts', sa.Column('metadata_at', sa.Integer(), nullable=True))
    op.add_column('hosts', sa.Column('os', sa.String(length=128), nullable=True))
    op.add_column('hosts', sa.Column('arch', sa.String(length=32), nullable=True))
    op.add_column('hosts', sa.Column('agent_version', sa.String(length=64), nullable=True))



def downgrade():
    # SQLite >= 3.35 supports DROP COLUMN without rebuilding the parent hosts table.
    op.drop_table('agent_samples')
    op.drop_column('hosts', 'agent_version')
    op.drop_column('hosts', 'arch')
    op.drop_column('hosts', 'os')
    op.drop_column('hosts', 'metadata_at')
    op.drop_column('hosts', 'systemd_units')
    op.drop_column('hosts', 'tags')
    op.drop_column('hosts', 'region')
    op.drop_column('hosts', 'cloud_provider')


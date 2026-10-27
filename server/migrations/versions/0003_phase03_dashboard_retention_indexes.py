"""Dashboard retention indexes

Revision ID: 0003_phase03
Revises: 0002_phase02
"""
from alembic import op

revision = '0003_phase03'
down_revision = '0002_phase02'
branch_labels = None
depends_on = None


def upgrade():
    # No table references agent_samples; recreate only this leaf table to make
    # pagination IDs non-reusable after retention deletes. Existing rows survive.
    with op.batch_alter_table('agent_samples', schema=None, recreate='always',
                              table_kwargs={'sqlite_autoincrement': True}) as batch_op:
        batch_op.create_index('ix_agent_samples_retention', ['sampled_at', 'id'], unique=False)

    with op.batch_alter_table('disk_metrics', schema=None) as batch_op:
        batch_op.create_index('ix_disk_metrics_retention', ['sampled_at', 'id'], unique=False)

    with op.batch_alter_table('host_metrics', schema=None) as batch_op:
        batch_op.create_index('ix_host_metrics_retention', ['sampled_at', 'id'], unique=False)



def downgrade():
    with op.batch_alter_table('host_metrics', schema=None) as batch_op:
        batch_op.drop_index('ix_host_metrics_retention')

    with op.batch_alter_table('disk_metrics', schema=None) as batch_op:
        batch_op.drop_index('ix_disk_metrics_retention')

    with op.batch_alter_table('agent_samples', schema=None, recreate='always',
                              table_kwargs={'sqlite_autoincrement': False}) as batch_op:
        batch_op.drop_index('ix_agent_samples_retention')

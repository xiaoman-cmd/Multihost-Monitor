from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from app.db import Base, make_engine
from app.models import Host

BUSINESS_TABLES = {'hosts', 'agent_credentials', 'host_metrics', 'disk_metrics', 'service_checks',
                  'check_results', 'alert_rules', 'alert_instances', 'alert_events',
                  'notification_channels', 'notification_deliveries'}


def test_upgrade_schema_pragmas_and_no_drift(database):
    url, cfg = database
    engine = make_engine(url)
    with engine.connect() as db:
        assert set(inspect(db).get_table_names()) == BUSINESS_TABLES | {'alembic_version', 'admin_users', 'admin_sessions'}
        assert db.scalar(text('PRAGMA foreign_keys')) == 1
        assert db.scalar(text('PRAGMA busy_timeout')) == 5000
        assert db.scalar(text('PRAGMA journal_mode')) == 'wal'
        assert db.scalar(text('PRAGMA integrity_check')) == 'ok'
        assert db.execute(text('PRAGMA foreign_key_check')).all() == []
        assert compare_metadata(MigrationContext.configure(db, opts={'compare_type': True}), Base.metadata) == []
        for table in BUSINESS_TABLES:
            assert db.scalar(text(f'SELECT count(*) FROM {table}')) == 0
    command.upgrade(cfg, 'head')
    engine.dispose()


def test_downgrade_and_reupgrade(database):
    url, cfg = database
    command.downgrade(cfg, 'base')
    engine = make_engine(url)
    assert set(inspect(engine).get_table_names()) == {'alembic_version'}
    command.upgrade(cfg, 'head')
    assert 'hosts' in inspect(engine).get_table_names()
    engine.dispose()


def test_repeat_upgrade_preserves_data(database):
    url, cfg = database
    engine = make_engine(url)
    with engine.begin() as db:
        db.execute(Host.__table__.insert().values(name='real-host', hostname='host.example'))
    command.upgrade(cfg, 'head')
    with engine.connect() as db:
        assert db.scalar(text('SELECT name FROM hosts')) == 'real-host'
    engine.dispose()

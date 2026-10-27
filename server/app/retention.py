"""Fixed-scope retention, bounded transactions, no public deletion endpoint."""
import argparse
import asyncio
import json
import logging
import time

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import SQLAlchemyError

from .models import AgentSample, CheckResult, DiskMetric, HostMetric, now

TABLES = (AgentSample, DiskMetric, HostMetric)
DAYS = 7 * 86400


def cleanup_batch(sessions, *, cutoff=None, batch_size=500, apply=False, include_checks=False):
    if not 1 <= batch_size <= 1000:
        raise ValueError('batch_size must be between 1 and 1000')
    # The clock determines the oldest eligible boundary; callers cannot shorten retention.
    cutoff = min(cutoff if cutoff is not None else now() - DAYS, now() - DAYS)
    result = {'cutoff': cutoff, 'applied': apply, 'deleted': {}, 'remaining': {}}
    with sessions.begin() as db:
        if apply:
            db.execute(text('BEGIN IMMEDIATE'))
        for model in (*TABLES, CheckResult) if include_checks else TABLES:
            timestamp = model.checked_at if model is CheckResult else model.sampled_at
            removed = 0
            if apply:
                ids = select(model.id).where(timestamp < cutoff).order_by(timestamp, model.id).limit(batch_size)
                removed = db.execute(delete(model).where(model.id.in_(ids))).rowcount
            result['deleted'][model.__tablename__] = removed
            result['remaining'][model.__tablename__] = db.scalar(
                select(func.count()).select_from(model).where(timestamp < cutoff))
    return result


def cleanup_cycle(sessions, *, budget_seconds=5, stop=None):
    started = time.monotonic()
    cutoff = now() - DAYS
    report = None
    while time.monotonic() - started < budget_seconds and not (stop and stop.is_set()):
        report = cleanup_batch(sessions, cutoff=cutoff, apply=True, include_checks=True)
        if not any(report['remaining'].values()):
            break
    return report


async def retention_loop(sessions, stop, state):
    while not stop.is_set():
        try:
            state.retention_last_run = await asyncio.to_thread(cleanup_cycle, sessions, stop=stop)
        except SQLAlchemyError:
            logging.getLogger(__name__).warning('retention_batch_failed')
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except TimeoutError:
            pass


def main():
    from .config import Settings
    from .db import make_engine, session_factory
    parser = argparse.ArgumentParser(description='7-day retention; defaults to dry-run, at most 1000 rows per table')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--batch-size', type=int, default=500)
    args = parser.parse_args()
    settings = Settings()
    engine = make_engine(settings.database_url)
    try:
        print(json.dumps(cleanup_batch(session_factory(engine), batch_size=args.batch_size, apply=args.apply)))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()

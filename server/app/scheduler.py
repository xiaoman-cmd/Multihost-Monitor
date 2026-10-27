"""Single-process lifecycle scheduler; bounded tasks, no network inside SQLite writes."""
import asyncio
import fcntl
import logging
import os
import stat
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url

from .alerts import evaluate_batch, transaction
from .models import CheckResult, Host, ServiceCheck, now
from .notifications import claim, deliver
from .outbound import AddressPolicy, probe


async def database_task(function, *args):
    # Cancellation cannot kill a running SQLite thread. Join it before releasing
    # scheduler ownership/disposal, so a restart never overlaps the old writer.
    job = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(job)
    except asyncio.CancelledError:
        await job
        raise


def scheduler_lock(database_url):
    path = Path(make_url(database_url).database).resolve()
    fd = os.open(str(path) + '.scheduler.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise OSError("unsafe lock file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise RuntimeError('Only one monitoring scheduler may own this database') from None
    return fd


def claim_checks(sessions):
    stamp = now()
    with transaction(sessions) as db:
        checks = db.scalars(select(ServiceCheck).join(Host).where(ServiceCheck.enabled.is_(True),
            ServiceCheck.kind.in_(['http', 'tcp']), Host.status != 'disabled', ServiceCheck.next_run_at <= stamp)
            .order_by(ServiceCheck.next_run_at, ServiceCheck.id).limit(32)).all()
        result = []
        for check in checks:
            check.next_run_at = stamp + check.interval_seconds
            result.append({key: getattr(check, key) for key in ['id', 'host_id', 'kind', 'target', 'timeout_seconds',
                'expected_status_min', 'expected_status_max', 'tls_min_days', 'revision']})
        return result


def save_result(sessions, check, result, stamp):
    with transaction(sessions) as db:
        current = db.get(ServiceCheck, check['id'])
        if not current or not current.enabled or current.revision != check['revision']:
            return
        # Unique checked_at protects duplicate completion; preserve first persisted result.
        exists = db.scalar(select(CheckResult.id).where(CheckResult.check_id == check['id'], CheckResult.checked_at == stamp))
        if not exists:
            db.add(CheckResult(check_id=check['id'], host_id=check['host_id'], checked_at=stamp, check_revision=check['revision'], **result))


async def probes(sessions, settings):
    checks = await database_task(claim_checks, sessions)
    semaphore = asyncio.Semaphore(4)
    async def run(check):
        async with semaphore:
            result = await probe(check, AddressPolicy(settings.probe_allowed_cidrs))
            await database_task(save_result, sessions, check, result, now())
    results = await asyncio.gather(*(run(check) for check in checks), return_exceptions=True)
    if any(isinstance(result, Exception) for result in results):
        logging.getLogger(__name__).warning('probe_completion_failed')


async def deliveries(sessions, settings):
    items = await database_task(claim, sessions)
    semaphore = asyncio.Semaphore(2)
    async def run(item):
        async with semaphore:
            await deliver(sessions, item, settings)
    results = await asyncio.gather(*(run(item) for item in items), return_exceptions=True)
    if any(isinstance(result, Exception) for result in results):
        logging.getLogger(__name__).warning('delivery_completion_failed')


async def periodic(name, action, seconds):
    while True:
        try:
            await action()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).warning('monitoring_task_failed task=%s', name)
        await asyncio.sleep(seconds)


async def start_tasks(sessions, settings):
    cursor = (0, 0)
    async def evaluate():
        nonlocal cursor
        cursor = await database_task(evaluate_batch, sessions, cursor)
    return [asyncio.create_task(periodic('probe', lambda: probes(sessions, settings), 5)),
            asyncio.create_task(periodic('alert', evaluate, 2)),
            asyncio.create_task(periodic('notification', lambda: deliveries(sessions, settings), 2))]

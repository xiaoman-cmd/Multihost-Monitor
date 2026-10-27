"""Protected external secrets and bounded, leased database Outbox delivery."""
import asyncio
import base64
import json
import os
import re
import secrets
import ssl
import stat
from email.message import EmailMessage
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from .alerts import transaction
from .models import NotificationChannel, NotificationDelivery, now
from .outbound import AddressPolicy, OutboundError, connect, http_request


def read_secret(directory, reference):
    if not isinstance(reference, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}\.json', reference):
        raise OutboundError('secret_reference_invalid')
    base = Path(directory)
    try:
        info = base.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise OutboundError('secret_permissions')
        # O_NOFOLLOW for the directory and final component; never follow ref symlinks.
        directory_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(directory_fd)
            if opened.st_uid != os.getuid() or stat.S_IMODE(opened.st_mode) != 0o700 or opened.st_ino != info.st_ino or opened.st_dev != info.st_dev:
                raise OutboundError('secret_permissions')
            fd = os.open(reference, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            with os.fdopen(fd, 'rb') as file:
                info = os.fstat(file.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 16384:
                    raise OutboundError('secret_permissions')
                data = json.loads(file.read(16385))
        finally:
            os.close(directory_fd)
        if not isinstance(data, dict):
            raise OutboundError('secret_invalid')
        return data
    except (OSError, ValueError):
        raise OutboundError('secret_unavailable') from None


async def smtp_send(config, payload, key, policy):
    host = config['host']
    port = config.get('port', 465)
    if not isinstance(host, str) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise OutboundError('smtp_config_invalid')
    sender = config['from']
    recipients = config['to']
    email_pattern = r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+'
    if not isinstance(recipients, list) or not 1 <= len(recipients) <= 10 or any(not isinstance(address, str) or not re.fullmatch(email_pattern, address) or len(address) > 254 for address in [sender, *recipients]):
        raise OutboundError('smtp_config_invalid')
    mode = config.get('tls_mode', 'implicit' if port == 465 else 'starttls')
    if mode not in {'implicit', 'starttls'}:
        raise OutboundError('smtp_tls_required')
    reader, writer = await connect(host, port, policy, tls=mode == 'implicit')
    async def reply(expected):
        for _ in range(32):
            line = await reader.readline()
            if len(line) > 2048 or not re.match(rb'\d{3}[- ]', line):
                raise OutboundError('smtp_protocol')
            if line[3:4] == b' ':
                if int(line[:3]) not in expected:
                    raise OutboundError('smtp_rejected')
                return
        raise OutboundError('smtp_protocol')
    async def command(value, expected):
        writer.write(value.encode('ascii') + b'\r\n')
        await writer.drain()
        await reply(expected)
    try:
        await reply({220})
        await command('EHLO multihost-monitor', {250})
        if mode == 'starttls':
            await command('STARTTLS', {220})
            await writer.start_tls(ssl.create_default_context(), server_hostname=host)
            await command('EHLO multihost-monitor', {250})
        username = config.get('username', '')
        password = config.get('password', '')
        if username:
            auth = base64.b64encode(('\0' + username + '\0' + password).encode()).decode('ascii')
            await command('AUTH PLAIN ' + auth, {235})
        await command('MAIL FROM:<' + sender + '>', {250})
        for address in recipients:
            await command('RCPT TO:<' + address + '>', {250, 251})
        await command('DATA', {354})
        message = EmailMessage()
        message['From'] = sender
        message['To'] = ', '.join(recipients)
        message['Subject'] = 'MultiHost Monitor: ' + str(payload.get('state', 'test'))
        message['Message-ID'] = f'<{key}@multihost-monitor.invalid>'
        message.set_content(json.dumps(payload, ensure_ascii=False), cte="quoted-printable")
        lines = message.as_bytes().replace(b'\r\n', b'\n').split(b'\n')
        writer.write(b'\r\n'.join(b'.' + line if line.startswith(b'.') else line for line in lines) + b'\r\n.\r\n')
        await writer.drain()
        await reply({250})
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def send(kind, reference, payload, key, settings):
    config = await asyncio.to_thread(read_secret, settings.notification_secret_dir, reference)
    policy = AddressPolicy(settings.notification_allowed_cidrs)
    async with asyncio.timeout(10):
        if kind == 'webhook':
            if not config['url'].startswith('https://'):
                raise OutboundError('webhook_https_required')
            result = await http_request(config['url'], policy, method='POST',
                body=json.dumps(payload, ensure_ascii=False).encode(), bearer=config.get('bearer_token'), idempotency=key)
            if not 200 <= result.status < 300:
                raise OutboundError('webhook_rejected')
        elif kind == 'email':
            await smtp_send(config, payload, key, policy)
        else:
            raise OutboundError('channel_kind_invalid')


def enqueue_test(db, channel_id, stamp):
    delivery = NotificationDelivery(channel_id=channel_id, event_id=None, created_at=stamp,
        next_attempt_at=stamp, idempotency_key=uuid4().hex, payload={'state': 'test', 'at': stamp, 'message': 'Administrator requested channel test'})
    db.add(delivery)
    db.flush()
    return delivery.id


def claim(sessions, limit=16):
    stamp = now()
    with transaction(sessions) as db:
        rows = db.scalars(select(NotificationDelivery).where(NotificationDelivery.status == 'pending',
            NotificationDelivery.next_attempt_at <= stamp, NotificationDelivery.lease_until <= stamp)
            .order_by(NotificationDelivery.next_attempt_at, NotificationDelivery.id).limit(limit)).all()
        result = []
        for row in rows:
            channel = db.get(NotificationChannel, row.channel_id)
            if not channel or not channel.enabled:
                row.status = 'failed'
                row.last_error = 'channel_disabled'
                continue
            if row.attempts >= 5:
                row.status = 'failed'
                row.last_error = 'attempts_exhausted'
                continue
            row.attempts += 1
            row.lease_until = stamp + 30
            if row.idempotency_key is None:  # Old Phase 01 pending deliveries.
                row.idempotency_key = uuid4().hex
            result.append({'id': row.id, 'lease': row.lease_until, 'kind': channel.kind,
                'reference': channel.config.get('secret_ref'), 'payload': row.payload, 'key': row.idempotency_key})
        return result


async def deliver(sessions, item, settings):
    error = None
    try:
        await send(item['kind'], item['reference'], item['payload'], item['key'], settings)
    except asyncio.CancelledError:
        # Leave the durable lease intact: retry after lease expiry on restart.
        raise
    except TimeoutError:
        error = 'timeout'
    except OutboundError as failure:
        error = str(failure)
    except Exception:
        error = 'delivery_failed'  # Never store exception text, URL or SMTP credentials.
    from .scheduler import database_task
    await database_task(finish, sessions, item, error)


def finish(sessions, item, error):
    with transaction(sessions) as db:
        row = db.get(NotificationDelivery, item['id'])
        if not row or row.status != 'pending' or row.lease_until != item['lease']:
            return
        row.lease_until = 0
        row.last_error = error
        if error is None:
            row.status = 'sent'
            row.delivered_at = max(now(), row.created_at)
        else:
            row.status = 'failed' if row.attempts >= 5 else 'pending'
            row.next_attempt_at = now() + min(900, 5 * 2 ** min(row.attempts - 1, 8)) + secrets.randbelow(5)

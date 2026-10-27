"""Pinned-address outbound I/O. No environment proxies, redirects or secret logging."""
import asyncio
import ipaddress
import re
import socket
import ssl
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit


DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="monitor-dns")
DNS_SLOTS = threading.BoundedSemaphore(4)


async def resolve_records(host, port):
    if not DNS_SLOTS.acquire(blocking=False):
        raise OutboundError("dns_busy")
    try:
        future = DNS_POOL.submit(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except Exception:
        DNS_SLOTS.release()
        raise
    future.add_done_callback(lambda _: DNS_SLOTS.release())
    return await asyncio.wrap_future(future)


class OutboundError(Exception):
    pass


def target(value, kind='http'):
    try:
        parsed = urlsplit(value)
        allowed = {'http', 'https'} if kind == 'http' else {'tcp'}
        if parsed.scheme not in allowed or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError()
        host = parsed.hostname.encode('idna').decode('ascii')
        if len(host) > 253 or '%' in host or any(ord(c) <= 32 or ord(c) == 127 for c in value):
            raise ValueError()
        port = parsed.port or (443 if parsed.scheme == 'https' else 80 if parsed.scheme == 'http' else 0)
        if not 1 <= port <= 65535 or (kind == 'tcp' and (parsed.path or parsed.query)):
            raise ValueError()
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        path.encode('ascii')
        return parsed, host, port, path
    except (ValueError, UnicodeError):
        raise OutboundError('invalid_target') from None


class AddressPolicy:
    def __init__(self, allowed_cidrs=()):
        self.allowed = tuple(ipaddress.ip_network(c, strict=True) for c in allowed_cidrs)

    def validate(self, value):
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            raise OutboundError('address_rejected') from None
        if ip.is_unspecified or ip.is_multicast or ip.is_link_local or getattr(ip, 'ipv4_mapped', None) or getattr(ip, 'teredo', None) or getattr(ip, 'sixtofour', None):
            raise OutboundError('address_rejected')
        if not ip.is_global and not any(ip.version == net.version and ip in net for net in self.allowed):
            raise OutboundError('address_rejected')
        # Azure platform virtual IP is public-looking but is a metadata/control target.
        if str(ip) in {'168.63.129.16', '100.100.100.200', 'fd00:ec2::254'}:
            raise OutboundError('address_rejected')
        return str(ip)

    async def resolve(self, host, port):
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            values = await resolve_records(host, port)
            if not values or len(values) > 16:
                raise OutboundError('dns_rejected')
            # Validate EVERY answer before selecting an address, not just the first.
            return list(dict.fromkeys(self.validate(v[4][0]) for v in values))
        return [self.validate(str(literal))]


@dataclass
class HTTPResult:
    status: int
    tls_expires_at: int | None


async def connect(host, port, policy, *, tls=False):
    addresses = await policy.resolve(host, port)
    context = ssl.create_default_context() if tls else None
    # Only numeric validated addresses go to the socket; original hostname is TLS SNI.
    for address in addresses:
        try:
            return await asyncio.open_connection(address, port, ssl=context,
                server_hostname=host if tls else None, limit=32768)
        except (OSError, ssl.SSLError):
            continue
    raise OutboundError('connection_failed')


async def http_request(url, policy, *, method='GET', body=b'', bearer=None, idempotency=None):
    parsed, host, port, path = target(url)
    if bearer is not None and (not isinstance(bearer, str) or re.search(r'[\r\n\x00]', bearer)):
        raise OutboundError('secret_invalid')
    reader, writer = await connect(host, port, policy, tls=parsed.scheme == 'https')
    try:
        authority = f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
        headers = [f'{method} {path} HTTP/1.1', f'Host: {authority}', 'Connection: close', 'User-Agent: MultiHost-Monitor/1.0']
        if method == 'POST':
            headers += ['Content-Type: application/json', f'Content-Length: {len(body)}']
        if bearer:
            headers += [f'Authorization: Bearer {bearer}']
        if idempotency:
            headers += [f'Idempotency-Key: {idempotency}']
        writer.write(('\r\n'.join(headers) + '\r\n\r\n').encode('ascii') + body)
        await writer.drain()
        head = await reader.readuntil(b'\r\n\r\n')
        if len(head) > 16384:
            raise OutboundError('response_headers_too_large')
        first = head.split(b'\r\n', 1)[0]
        match = re.fullmatch(rb'HTTP/1\.[01] ([1-5][0-9]{2})(?: .*|)', first)
        if not match:
            raise OutboundError('invalid_http_response')
        code = int(match[1])
        if 300 <= code <= 399:
            raise OutboundError('redirect_rejected')
        peer = writer.get_extra_info('ssl_object')
        expires = peer.getpeercert().get('notAfter') if peer else None
        return HTTPResult(code, int(ssl.cert_time_to_seconds(expires)) if expires else None)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ssl.SSLError):
            pass


def certificate_valid(expires_at, min_days, stamp):
    return expires_at is not None and expires_at - stamp >= min_days * 86400


async def probe(check, policy):
    started = time.monotonic()
    result = {'status': 'down', 'http_status': None, 'tls_expires_at': None, 'message': 'connection_failed'}
    try:
        async with asyncio.timeout(check['timeout_seconds']):
            if check['kind'] == 'http':
                response = await http_request(check['target'], policy)
                result.update(http_status=response.status, tls_expires_at=response.tls_expires_at)
                ok = check['expected_status_min'] <= response.status <= check['expected_status_max']
                if check['target'].startswith('https:') and not certificate_valid(response.tls_expires_at, check['tls_min_days'], int(time.time())):
                    result['message'] = 'certificate_expiring'
                else:
                    result.update(status='up' if ok else 'down', message='ok' if ok else 'http_status_rejected')
            elif check['kind'] == 'tcp':
                _, host, port, _ = target(check['target'], 'tcp')
                _, writer = await connect(host, port, policy)
                writer.close()
                await writer.wait_closed()
                result.update(status='up', message='ok')
            else:
                result.update(status='unknown', message='unsupported_check_kind')
    except TimeoutError:
        result['message'] = 'timeout'
    except OutboundError as error:
        result['message'] = str(error)
        if str(error) in {'address_rejected', 'dns_rejected', 'dns_busy', 'invalid_target'}:
            result['status'] = 'unknown'
    except (OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        result['message'] = 'connection_failed'
    result['latency_ms'] = (time.monotonic() - started) * 1000
    return result

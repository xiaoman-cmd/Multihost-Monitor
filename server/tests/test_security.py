from concurrent.futures import ThreadPoolExecutor

from app.security import LoginLimiter, digest


def test_limiter_thread_safe_and_bounded():
    limiter = LoginLimiter()
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(lambda _: limiter.allow('same-host'), range(40))) == 5
    for i in range(200):
        limiter.allow(f'host-{i}')
    assert len(limiter.global_entries) == 100
    assert len(limiter.entries) <= 100


def test_limiter_window_expires(monkeypatch):
    import app.security as security
    stamp = [100.0]
    monkeypatch.setattr(security, 'monotonic', lambda: stamp[0])
    limiter = LoginLimiter()
    for _ in range(5):
        assert limiter.allow('host')
    assert not limiter.allow('host')
    stamp[0] = 161.0
    assert limiter.allow('host')
    assert len(limiter.global_entries) == 1


def test_digest_is_deterministic_and_not_plaintext():
    assert digest('token') == digest('token')
    assert len(digest('token')) == 64
    assert digest('token') != digest('other')

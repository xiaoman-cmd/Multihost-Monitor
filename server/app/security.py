import hashlib
import secrets
from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))
COOKIE_NAME = "mhm_session"


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify(password: str, hashed: str) -> bool:
    try:
        return hasher.verify(hashed, password)
    except VerificationError:
        return False


class LoginLimiter:
    """Single-process bounded limiter; remote address must come from the trusted socket."""
    def __init__(self):
        self.entries = defaultdict(deque)
        self.global_entries = deque()
        self.lock = Lock()

    def allow(self, address: str) -> bool:
        with self.lock:
            cutoff = monotonic() - 60
            for key in list(self.entries):
                queue = self.entries[key]
                while queue and queue[0] <= cutoff:
                    queue.popleft()
                if not queue:
                    del self.entries[key]
            while self.global_entries and self.global_entries[0] <= cutoff:
                self.global_entries.popleft()
            if len(self.global_entries) >= 100 or len(self.entries[address]) >= 5:
                return False
            stamp = monotonic()
            self.entries[address].append(stamp)
            self.global_entries.append(stamp)
            return True

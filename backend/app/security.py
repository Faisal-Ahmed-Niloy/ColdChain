"""Web authentication: bcrypt password hashes, JWT access tokens, role permissions.

Permissions are enforced on the server (brief p.1): every API route declares the
permission it needs and `require()` rejects tokens whose role lacks it.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

import jwt  # PyJWT

from .config import settings

ROLES = ("admin", "pharmacist", "inspector")

# permission -> roles allowed. Inspector is read-only (auditor).
PERMISSIONS: dict[str, set[str]] = {
    "dashboard.view":     {"admin", "pharmacist", "inspector"},
    "inventory.view":     {"admin", "pharmacist", "inspector"},
    "inventory.edit":     {"admin", "pharmacist"},
    "batch.move":         {"admin", "pharmacist"},
    "relocation.confirm": {"admin", "pharmacist"},
    "alert.ack":          {"admin", "pharmacist"},
    "logs.view":          {"admin", "pharmacist", "inspector"},
    "logs.verify":        {"admin", "pharmacist", "inspector"},
    "security.view":      {"admin", "inspector"},
    "ai.view":            {"admin", "pharmacist", "inspector"},
    "device.reset_led":   {"admin"},
    "device.recalibrate": {"admin"},
    "users.manage":       {"admin"},
    "alert.test":         {"admin"},
}


def can(role: str, permission: str) -> bool:
    return role in PERMISSIONS.get(permission, set())


def permissions_for(role: str) -> list[str]:
    return sorted(p for p, roles in PERMISSIONS.items() if role in roles)


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    import bcrypt  # imported lazily so the core can be unit-tested without it
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def check_password(password: str, hashed: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def password_problem(password: str) -> str | None:
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if password.isalpha() or password.isdigit():
        return "Password must mix letters with digits or symbols"
    return None


# ---------------------------------------------------------------- tokens
def issue_token(user_id: int, username: str, role: str, now: float | None = None) -> str:
    now = now or time.time()
    payload = {"sub": str(user_id), "usr": username, "role": role,
               "iat": int(now), "exp": int(now + settings.jwt_expire_min * 60)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    """Raises jwt.PyJWTError on any problem (bad signature, expired, malformed)."""
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})


# ---------------------------------------------------------------- brute force
class LoginThrottle:
    """Max 5 failed logins per username per 5 minutes."""

    def __init__(self, limit: int = 5, window_s: int = 300) -> None:
        self.limit, self.window = limit, window_s
        self._fails: dict[str, deque] = defaultdict(deque)

    def blocked(self, username: str, now: float) -> bool:
        q = self._fails[username.lower()]
        while q and now - q[0] > self.window:
            q.popleft()
        return len(q) >= self.limit

    def fail(self, username: str, now: float) -> None:
        self._fails[username.lower()].append(now)

    def reset(self, username: str) -> None:
        self._fails.pop(username.lower(), None)


throttle = LoginThrottle()

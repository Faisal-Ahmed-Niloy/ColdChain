"""FastAPI dependencies: current user from the Bearer JWT, and permission checks.
Every protected route uses `Depends(require("<permission>"))`, so permissions are
enforced on the server no matter what the browser shows or hides."""
from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db
from .security import can, decode_token

_bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired or invalid",
                            headers={"WWW-Authenticate": "Bearer"})
    # Re-check the account on every request: a deactivated user or changed role takes effect at once.
    with db.read() as conn:
        u = db.one(conn.execute("SELECT id, username, role, active FROM users WHERE id=?", (int(claims["sub"]),)))
    if not u or not u["active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled")
    return u


def require(permission: str):
    def checker(user: dict = Depends(current_user)) -> dict:
        if not can(user["role"], permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Your role ({user['role']}) cannot do this")
        return user
    return checker

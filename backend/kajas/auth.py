"""Local-admin authentication.

V1 supports a single passphrase. We use argon2id for hashing and
``itsdangerous`` for signing the session cookie. The session cookie is
HttpOnly and SameSite=Lax; ``Secure`` is also set whenever the request
was made over HTTPS so the same code works on ``http://localhost`` and
through Tailscale.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

SESSION_COOKIE = "kajas_session"
# 8 days feels right for a local dev tool. ``TimestampSigner`` enforces it.
SESSION_MAX_AGE = 60 * 60 * 24 * 8

_PH: PasswordHasher | None = None


def _hasher() -> PasswordHasher:
    global _PH
    if _PH is None:
        _PH = PasswordHasher()
    return _PH


def hash_passphrase(passphrase: str) -> str:
    return _hasher().hash(passphrase)


def verify_passphrase(stored: str, candidate: str) -> bool:
    if not stored:
        return False
    try:
        return _hasher().verify(stored, candidate)
    except (VerifyMismatchError, InvalidHashError):
        return False


def generate_session_secret() -> str:
    return secrets.token_urlsafe(48)


@dataclass(frozen=True)
class SessionUser:
    name: str = "admin"


def _signer(secret: str) -> TimestampSigner:
    return TimestampSigner(secret, salt="kajas-session-v1")


def issue_session(secret: str, user: SessionUser = SessionUser()) -> str:
    return _signer(secret).sign(user.name.encode("utf-8")).decode("ascii")


def read_session(secret: str, token: str | None) -> SessionUser | None:
    if not token:
        return None
    try:
        value = _signer(secret).unsign(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return SessionUser(name=value.decode("utf-8"))


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def require_user(
    request: Request,
    kajas_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> SessionUser:
    from .config import load_global_config  # avoid import cycle at module load

    cfg = load_global_config()
    if not cfg.auth.enabled:
        # No auth required; treat as admin.
        return SessionUser()
    if not cfg.auth.session_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth not bootstrapped: missing session_secret",
        )
    user = read_session(cfg.auth.session_secret, kajas_session)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user


def is_https(request: Request) -> bool:
    # Trust ``X-Forwarded-Proto`` only when the configured trusted_hosts
    # includes the request host. The full host allow-list is enforced
    # in the server module.
    return request.url.scheme == "https"


def cookie_secure(request: Request, trusted_hosts: list[str]) -> bool:
    if is_https(request):
        return True
    # On plain HTTP we still want the cookie scoped to local use; never
    # set ``Secure`` in that case so localhost works.
    return False


__all__ = [
    "SESSION_COOKIE",
    "SESSION_MAX_AGE",
    "SessionUser",
    "cookie_secure",
    "generate_session_secret",
    "hash_passphrase",
    "issue_session",
    "read_session",
    "require_user",
    "verify_passphrase",
]

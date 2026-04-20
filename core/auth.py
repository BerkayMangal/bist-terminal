# ================================================================
# BISTBULL TERMINAL -- AUTHENTICATION (Phase 1)
# core/auth.py
#
# argon2id password hashing, JWT minting/verification, email &
# password policy, FastAPI dependencies for auth-required and
# auth-or-session endpoints.
# ================================================================

from __future__ import annotations

import os
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from pathlib import Path

import jwt as _jwt  # PyJWT
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import HTTPException, Request, status

log = logging.getLogger("bistbull.auth")

JWT_ALGORITHM = "HS256"
JWT_TTL_DAYS = 7
PASSWORD_MIN_LENGTH = 12

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def require_jwt_secret() -> str:
    """Return JWT_SECRET from env or raise RuntimeError.

    Called by the FastAPI lifespan at startup so the app refuses to
    boot without a secret. Minimum length 32 chars. Rejects known
    placeholder strings to catch copy-paste mistakes from .env.example.
    """
    secret = os.environ.get("JWT_SECRET", "")
    if not secret or len(secret) < 32:
        raise RuntimeError(
            "JWT_SECRET env variable required, min 32 chars. "
            "Generate via: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
        )
    lowered = secret.lower()
    if "change-me" in lowered or "your-secret" in lowered:
        raise RuntimeError(
            "JWT_SECRET is still the placeholder value -- set a real random secret."
        )
    return secret


_hasher = PasswordHasher()  # argon2-cffi default = argon2id


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    except Exception as e:  # pragma: no cover
        log.warning(f"verify_password unexpected error: {e}")
        return False


_COMMON_PASSWORDS: Optional[set[str]] = None


def _load_common_passwords() -> set[str]:
    global _COMMON_PASSWORDS
    if _COMMON_PASSWORDS is not None:
        return _COMMON_PASSWORDS
    path = Path(__file__).parent.parent / "data" / "common_passwords.txt"
    try:
        with open(path, encoding="utf-8") as f:
            _COMMON_PASSWORDS = {
                line.strip().lower()
                for line in f
                if line.strip() and not line.lstrip().startswith("#")
            }
    except FileNotFoundError:
        log.warning(f"Common passwords list not found at {path} -- skipping check")
        _COMMON_PASSWORDS = set()
    return _COMMON_PASSWORDS


def check_password_policy(password: str) -> Optional[str]:
    """Return an error message if the password is bad, else None.

    Policy:
      - >= 12 characters
      - not in the common-passwords list
    Length over complexity, per modern consensus (no upper/lower/digit/special).
    """
    if not isinstance(password, str):
        return "password must be a string"
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"password must be at least {PASSWORD_MIN_LENGTH} characters"
    if password.lower() in _load_common_passwords():
        return "password is too common; please choose another"
    return None


def check_email(email: str) -> bool:
    return bool(isinstance(email, str) and _EMAIL_RE.match(email))


def create_jwt(user_id: str, ttl_days: int = JWT_TTL_DAYS) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=ttl_days)).timestamp()),
    }
    return _jwt.encode(payload, require_jwt_secret(), algorithm=JWT_ALGORITHM)


def verify_jwt(token: str) -> Optional[str]:
    if not token:
        return None
    try:
        payload = _jwt.decode(token, require_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except _jwt.ExpiredSignatureError:
        return None
    except _jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) and sub else None


def extract_bearer_token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        tok = auth[7:].strip()
        return tok or None
    return None


def get_current_user_id(request: Request) -> str:
    """Strict: return user_id from a valid JWT, else 401."""
    token = extract_bearer_token(request)
    user_id = verify_jwt(token) if token else None
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


def get_current_user_or_session(request: Request) -> str:
    """JWT user_id if present, else bb_session id.

    Backward-compatible: existing anonymous flows keep working, while
    JWT-bearing requests upgrade to a persistent user_id. Raises 401
    only if neither is available -- should not happen normally since
    ses_mw always sets request.state.user_id from the cookie.
    """
    token = extract_bearer_token(request)
    user_id = verify_jwt(token) if token else None
    if user_id:
        return user_id
    sid = getattr(request.state, "user_id", None)
    if not sid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No session or bearer token",
        )
    return sid

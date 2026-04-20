# ================================================================
# BISTBULL TERMINAL -- AUTH ENDPOINTS (Phase 1)
# api/auth.py
#
# POST /api/auth/register -- create user; optional session_id migration
# POST /api/auth/login    -- email+password -> JWT (IP-limited 5/15min)
# POST /api/auth/logout   -- stateless 200
# GET  /api/auth/me       -- requires Bearer token
# ================================================================

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from core.auth import (
    hash_password, verify_password, check_password_policy,
    check_email, create_jwt, get_current_user_id,
)
from core.rate_limiter import check_rate_limit
from core.response_envelope import success
from infra.storage import (
    user_create, user_get_by_email, user_get, user_update_last_login,
    session_migrate_to_user,
)

log = logging.getLogger("bistbull.api.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: str
    password: str
    # Optional: migrate anonymous watchlist/alerts from this bb_session
    # cookie id to the new user (FAZ 1.5.5).
    session_id: Optional[str] = None


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/register")
async def register(request: Request, body: RegisterIn):
    # Input shape first (no DB / crypto work on malformed input)
    if not check_email(body.email):
        raise HTTPException(status_code=400, detail="Invalid email format")
    pwd_err = check_password_policy(body.password)
    if pwd_err:
        raise HTTPException(status_code=400, detail=pwd_err)

    email = body.email.lower().strip()
    if user_get_by_email(email):
        raise HTTPException(status_code=409, detail="Email already registered")

    user_id = user_create(email=email, password_hash=hash_password(body.password))

    migrated = None
    if body.session_id:
        migrated = session_migrate_to_user(body.session_id, user_id)

    return success({
        "user_id": user_id,
        "email": email,
        "token": create_jwt(user_id),
        "migrated": migrated,  # {"watchlist": n, "alerts": n, "snapshots": n} or None
    })


@router.post("/login")
async def login(request: Request, body: LoginIn):
    # IP-throttled via rate_limiter config 'auth_login' (5 / 900s).
    check_rate_limit(request, "auth_login")

    if not check_email(body.email):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user = user_get_by_email(body.email.lower().strip())
    # Intentionally indistinguishable from user-not-found -- no email enumeration.
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.get("is_active", 1):
        raise HTTPException(status_code=403, detail="Account is inactive")

    user_update_last_login(user["user_id"])
    return success({
        "user_id": user["user_id"],
        "email": user["email"],
        "token": create_jwt(user["user_id"]),
    })


@router.post("/logout")
async def logout(request: Request):
    # Stateless: tokens are not server-revocable in v1.
    return success({"ok": True})


@router.get("/me")
async def me(request: Request):
    user_id = get_current_user_id(request)
    u = user_get(user_id)
    if not u:
        raise HTTPException(status_code=401, detail="User not found")
    return success({
        "user_id": u["user_id"],
        "email": u["email"],
        "created_at": u["created_at"],
        "last_login_at": u["last_login_at"],
    })

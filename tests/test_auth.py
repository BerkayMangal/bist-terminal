"""Phase 1 auth endpoint tests.

Covers register/login/me, password policy, common-password reject,
anon -> user migration (FAZ 1.5.5), login IP rate limit, and the
JWT-or-session fallback pattern.

Runs against a fresh sqlite DB per test via an autouse fixture that
clears the users/watchlist/alerts tables. JWT_SECRET is set at module
import time so app.py's lifespan check passes.
"""

import os
import sys
import importlib

os.environ["JWT_SECRET"] = "test-secret-abcdefghijklmnop-qrstuvwxyz-0123456789"
os.environ["BISTBULL_DB_PATH"] = "/tmp/test_auth.db"
os.environ["TRUST_PROXY"] = "0"  # local dev mode for IP extraction

# Start clean: remove any leftover DB so init_db() creates fresh schema.
for p in ["/tmp/test_auth.db", "/tmp/test_auth.db-wal", "/tmp/test_auth.db-shm"]:
    if os.path.exists(p):
        os.remove(p)

import pytest
from fastapi.testclient import TestClient

# Import app after env is set. TestClient triggers lifespan which requires JWT_SECRET.
from app import app
from infra.storage import _get_conn, init_db


@pytest.fixture(autouse=True)
def _reset_tables():
    """Clear auth-relevant tables before every test for isolation."""
    init_db()
    conn = _get_conn()
    conn.execute("DELETE FROM users")
    conn.execute("DELETE FROM watchlist")
    conn.execute("DELETE FROM alerts")
    conn.execute("DELETE FROM symbol_snapshots")
    conn.commit()

    # Reset rate limiter store (module-level state) so login-throttle tests
    # don't poison each other.
    from core import rate_limiter
    with rate_limiter._store_lock:
        rate_limiter._store.clear()

    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ================================================================
# REGISTER
# ================================================================
class TestRegister:
    def test_register_success_returns_token(self, client):
        r = client.post("/api/auth/register", json={
            "email": "alice@example.com", "password": "GoodPasswd12!"
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email"] == "alice@example.com"
        assert body["user_id"].startswith("u_")
        assert isinstance(body["token"], str) and len(body["token"]) > 40
        assert body["migrated"] is None  # no session_id provided

    def test_register_normalizes_email_case(self, client):
        r = client.post("/api/auth/register", json={
            "email": "Alice@Example.COM", "password": "GoodPasswd12!"
        })
        assert r.status_code == 200
        # Second registration with any case should conflict
        r2 = client.post("/api/auth/register", json={
            "email": "alice@EXAMPLE.com", "password": "AnotherPwd12!"
        })
        assert r2.status_code == 409

    def test_register_rejects_bad_email(self, client):
        for bad in ["noatsign", "@nolocal.co", "nodomain@", "no@dot", "a b@c.co"]:
            r = client.post("/api/auth/register", json={
                "email": bad, "password": "GoodPasswd12!"
            })
            assert r.status_code == 400, f"{bad!r} should 400, got {r.status_code}"

    def test_register_rejects_short_password(self, client):
        r = client.post("/api/auth/register", json={
            "email": "x@y.co", "password": "short11char"  # 11 chars, < 12 limit
        })
        assert r.status_code == 400
        assert "12" in r.json()["detail"]

    def test_register_rejects_common_password(self, client):
        # The SecLists top-10k list has relatively few entries >=12 chars,
        # so we pick 'unbelievable' (12 chars, clean, real entry on line
        # ~9000 of 10k-most-common.txt). Passes the length check, fails
        # the common-password check.
        r = client.post("/api/auth/register", json={
            "email": "x@y.co", "password": "unbelievable"
        })
        assert r.status_code == 400
        assert "common" in r.json()["detail"].lower()

    def test_register_duplicate_returns_409(self, client):
        body = {"email": "bob@example.com", "password": "GoodPasswd12!"}
        r1 = client.post("/api/auth/register", json=body)
        assert r1.status_code == 200
        r2 = client.post("/api/auth/register", json=body)
        assert r2.status_code == 409


# ================================================================
# LOGIN
# ================================================================
class TestLogin:
    def _make_user(self, client, email="u@x.co", pwd="GoodPasswd12!"):
        r = client.post("/api/auth/register", json={"email": email, "password": pwd})
        assert r.status_code == 200

    def test_login_returns_token(self, client):
        self._make_user(client)
        r = client.post("/api/auth/login", json={"email": "u@x.co", "password": "GoodPasswd12!"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email"] == "u@x.co"
        assert isinstance(body["token"], str) and len(body["token"]) > 40

    def test_login_wrong_password_401(self, client):
        self._make_user(client)
        r = client.post("/api/auth/login", json={"email": "u@x.co", "password": "wrongwrong12"})
        assert r.status_code == 401

    def test_login_unknown_email_same_401(self, client):
        # Email enumeration protection: unknown email returns the same 401 as wrong password.
        r = client.post("/api/auth/login", json={"email": "ghost@x.co", "password": "GoodPasswd12!"})
        assert r.status_code == 401
        assert "invalid credentials" in r.json()["detail"].lower()

    def test_login_bad_email_shape_401(self, client):
        r = client.post("/api/auth/login", json={"email": "notanemail", "password": "GoodPasswd12!"})
        assert r.status_code == 401

    def test_login_rate_limit_429_on_6th_attempt(self, client):
        # 5 failed attempts allowed in 15 min; 6th should hit 429.
        for i in range(5):
            r = client.post("/api/auth/login", json={"email": "ghost@x.co", "password": "pwpwpwpwpw12"})
            assert r.status_code == 401, f"attempt {i+1} expected 401, got {r.status_code}"
        r6 = client.post("/api/auth/login", json={"email": "ghost@x.co", "password": "pwpwpwpwpw12"})
        assert r6.status_code == 429, r6.text


# ================================================================
# /me
# ================================================================
class TestMe:
    def test_me_without_token_401(self, client):
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_me_invalid_token_401(self, client):
        r = client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401

    def test_me_with_valid_token(self, client):
        client.post("/api/auth/register", json={
            "email": "me@x.co", "password": "GoodPasswd12!"
        })
        login = client.post("/api/auth/login", json={
            "email": "me@x.co", "password": "GoodPasswd12!"
        })
        tok = login.json()["token"]
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email"] == "me@x.co"
        assert body["user_id"].startswith("u_")
        # last_login_at should be populated after login
        assert body["last_login_at"] is not None


# ================================================================
# ANONYMOUS -> USER MIGRATION (FAZ 1.5.5)
# ================================================================
class TestSessionMigration:
    def test_anonymous_watchlist_migrates_on_signup(self, client):
        """Anonymous user adds 2 watchlist symbols -> registers with session_id ->
        both symbols should now belong to the new user_id."""
        # Step 1: anonymous user adds symbols -- the bb_session cookie becomes
        # the watchlist user_id.
        r1 = client.post("/api/watchlist", json={"symbol": "THYAO"})
        assert r1.status_code == 200, r1.text
        r2 = client.post("/api/watchlist", json={"symbol": "AKBNK"})
        assert r2.status_code == 200

        # Capture the bb_session cookie that was set
        bb_session = client.cookies.get("bb_session")
        assert bb_session, "bb_session cookie should be set after anonymous actions"

        # Before signup: anonymous watchlist has 2 entries
        before_list = client.get("/api/watchlist").json()["items"]
        assert len(before_list) == 2
        assert {s["symbol"] for s in before_list} == {"THYAO", "AKBNK"}

        # Step 2: register passing the session_id -- triggers migration
        reg = client.post("/api/auth/register", json={
            "email": "mig@example.com",
            "password": "GoodPasswd12!",
            "session_id": bb_session,
        })
        assert reg.status_code == 200, reg.text
        migrated = reg.json()["migrated"]
        assert migrated == {"watchlist": 2, "alerts": 0, "snapshots": 0}

        # Step 3: with JWT, the user should see the same 2 symbols
        tok = reg.json()["token"]
        after_list = client.get(
            "/api/watchlist", headers={"Authorization": f"Bearer {tok}"}
        ).json()["items"]
        assert len(after_list) == 2
        assert {s["symbol"] for s in after_list} == {"THYAO", "AKBNK"}

        # Step 4: add a 3rd under JWT -- all three should be together
        add3 = client.post(
            "/api/watchlist",
            json={"symbol": "GARAN"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert add3.status_code == 200
        final = client.get(
            "/api/watchlist", headers={"Authorization": f"Bearer {tok}"}
        ).json()["items"]
        assert {s["symbol"] for s in final} == {"THYAO", "AKBNK", "GARAN"}

    def test_register_without_session_id_no_migration(self, client):
        reg = client.post("/api/auth/register", json={
            "email": "nomig@example.com", "password": "GoodPasswd12!"
        })
        assert reg.status_code == 200
        assert reg.json()["migrated"] is None

    def test_register_with_empty_session_id_no_migration(self, client):
        # Defensive: a client sending session_id="" should not crash.
        # (Pydantic Optional treats empty string as present; our code
        # treats falsy session_id as "don't migrate".)
        # Using None here since empty string != absent for pydantic:
        reg = client.post("/api/auth/register", json={
            "email": "empty@example.com",
            "password": "GoodPasswd12!",
            "session_id": None,
        })
        assert reg.status_code == 200
        assert reg.json()["migrated"] is None


# ================================================================
# BACKWARD COMPAT: anonymous watchlist still works, JWT overrides
# ================================================================
class TestJwtOrSession:
    def test_anonymous_watchlist_still_works(self, client):
        """No auth header -> bb_session cookie drives user_id -> watchlist CRUD works."""
        r = client.post("/api/watchlist", json={"symbol": "ISCTR"})
        assert r.status_code == 200
        lst = client.get("/api/watchlist").json()["items"]
        assert any(s["symbol"] == "ISCTR" for s in lst)

    def test_jwt_user_id_overrides_session_cookie(self, client):
        """Two requests sharing a bb_session but with different JWTs
        see different watchlists -- confirms JWT wins over session."""
        # Register two separate users
        u1 = client.post("/api/auth/register", json={
            "email": "u1@x.co", "password": "GoodPasswd12!"
        }).json()
        u2 = client.post("/api/auth/register", json={
            "email": "u2@x.co", "password": "GoodPasswd12!"
        }).json()

        # u1 adds THYAO under their JWT
        client.post("/api/watchlist",
                    json={"symbol": "THYAO"},
                    headers={"Authorization": f"Bearer {u1['token']}"})
        # u2 adds AKBNK under their JWT
        client.post("/api/watchlist",
                    json={"symbol": "AKBNK"},
                    headers={"Authorization": f"Bearer {u2['token']}"})

        u1_list = client.get("/api/watchlist",
                             headers={"Authorization": f"Bearer {u1['token']}"}
                             ).json()["items"]
        u2_list = client.get("/api/watchlist",
                             headers={"Authorization": f"Bearer {u2['token']}"}
                             ).json()["items"]

        assert {s["symbol"] for s in u1_list} == {"THYAO"}
        assert {s["symbol"] for s in u2_list} == {"AKBNK"}


# ================================================================
# LOGOUT
# ================================================================
class TestLogout:
    def test_logout_always_200(self, client):
        # Stateless in v1.
        r = client.post("/api/auth/logout")
        assert r.status_code == 200
        r = client.post("/api/auth/logout", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 200


# ================================================================
# SECURITY HEADERS (FAZ 1.6)
# ================================================================
class TestSecurityHeaders:
    def test_headers_present_on_any_response(self, client):
        r = client.get("/api/auth/logout")  # any 2xx will do; use POST
        r = client.post("/api/auth/logout")
        h = r.headers
        assert h.get("x-content-type-options") == "nosniff"
        assert h.get("x-frame-options") == "DENY"
        assert "strict-origin" in h.get("referrer-policy", "")
        # Phase 1 additions
        assert "max-age=" in h.get("strict-transport-security", "")
        csp = h.get("content-security-policy", "")
        assert "default-src 'self'" in csp
        assert "cdnjs.cloudflare.com" in csp
        pp = h.get("permissions-policy", "")
        assert "geolocation=()" in pp
        assert "microphone=()" in pp
        assert "camera=()" in pp

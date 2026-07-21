"""
test_auth.py — gated Clerk authentication (src/api/auth.py).

Auth is GATED: it is a no-op unless Clerk is configured (CLERK_ISSUER /
CLERK_JWKS_URL present). These tests never touch the network, Clerk, or a real
DB — they run against the in-memory `client` fixture and monkeypatch the three
module-level seams on `src.api.auth`, which `require_user` looks up at call time:

  * is_auth_enabled() -> bool     (whether the gate is on)
  * verify_token(token) -> claims (JWT verification)
  * resolve_email(claims) -> email

Protected route used for assertions: GET /api/transactions (behind require_user).
Open routes: GET /api/health (health check — never protected).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api import auth


@pytest.fixture(autouse=True)
def _clean_auth_state(monkeypatch):
    """Every case starts from a known-clean auth state: no Clerk/allowlist env
    leaking in, and both module caches cleared. monkeypatch auto-undoes env."""
    for var in ("CLERK_ISSUER", "CLERK_JWKS_URL", "CLERK_SECRET_KEY",
                "ALLOWED_EMAILS", "PLAID_SYNC_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    auth._jwk_clients.clear()
    auth._email_cache.clear()
    yield
    auth._jwk_clients.clear()
    auth._email_cache.clear()


def _enable(monkeypatch):
    """Turn the gate ON without any real Clerk config."""
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)


# ── 1. Disabled by default (no Clerk env) ──────────────────────────────────────

class TestAuthDisabled:
    """With no Clerk env, require_user is a no-op — this is why the existing
    447-test suite stays green. No Authorization header is required."""

    def test_protected_route_open_when_disabled(self, client):
        # Sanity: gate really is off by default.
        assert auth.is_auth_enabled() is False
        resp = client.get("/api/transactions")
        assert resp.status_code == 200

    def test_health_open_when_disabled(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ── 2. Enabled (gate on) ────────────────────────────────────────────────────────

class TestAuthEnabled:
    def test_missing_authorization_header_401(self, client, monkeypatch):
        _enable(monkeypatch)
        resp = client.get("/api/transactions")
        assert resp.status_code == 401

    def test_malformed_authorization_header_401(self, client, monkeypatch):
        _enable(monkeypatch)
        resp = client.get("/api/transactions", headers={"Authorization": "Token abc"})
        assert resp.status_code == 401

    def test_empty_bearer_token_401(self, client, monkeypatch):
        _enable(monkeypatch)
        resp = client.get("/api/transactions", headers={"Authorization": "Bearer "})
        assert resp.status_code == 401

    def test_bad_token_verify_failure_401(self, client, monkeypatch):
        _enable(monkeypatch)

        def _boom(token):
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        monkeypatch.setattr(auth, "verify_token", _boom)
        resp = client.get(
            "/api/transactions", headers={"Authorization": "Bearer bad.token.here"}
        )
        assert resp.status_code == 401

    def test_valid_token_no_allowlist_allows(self, client, monkeypatch):
        """Valid token + no ALLOWED_EMAILS => any authenticated user allowed."""
        _enable(monkeypatch)
        monkeypatch.setattr(auth, "verify_token", lambda t: {"sub": "user_1"})
        monkeypatch.setattr(auth, "resolve_email", lambda c: "me@example.com")
        monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
        resp = client.get(
            "/api/transactions", headers={"Authorization": "Bearer good.token"}
        )
        assert resp.status_code == 200

    def test_valid_token_email_in_allowlist_allows(self, client, monkeypatch):
        _enable(monkeypatch)
        monkeypatch.setattr(auth, "verify_token", lambda t: {"sub": "user_1"})
        monkeypatch.setattr(auth, "resolve_email", lambda c: "me@example.com")
        monkeypatch.setenv("ALLOWED_EMAILS", "me@example.com")
        resp = client.get(
            "/api/transactions", headers={"Authorization": "Bearer good.token"}
        )
        assert resp.status_code == 200

    def test_valid_token_email_not_in_allowlist_403(self, client, monkeypatch):
        _enable(monkeypatch)
        monkeypatch.setattr(auth, "verify_token", lambda t: {"sub": "user_1"})
        monkeypatch.setattr(auth, "resolve_email", lambda c: "me@example.com")
        monkeypatch.setenv("ALLOWED_EMAILS", "other@example.com")
        resp = client.get(
            "/api/transactions", headers={"Authorization": "Bearer good.token"}
        )
        assert resp.status_code == 403

    def test_unresolved_email_with_allowlist_403(self, client, monkeypatch):
        """Cannot resolve an email while an allowlist is enforced => 403."""
        _enable(monkeypatch)
        monkeypatch.setattr(auth, "verify_token", lambda t: {"sub": "user_1"})
        monkeypatch.setattr(auth, "resolve_email", lambda c: None)
        monkeypatch.setenv("ALLOWED_EMAILS", "me@example.com")
        resp = client.get(
            "/api/transactions", headers={"Authorization": "Bearer good.token"}
        )
        assert resp.status_code == 403

    def test_health_open_even_when_enabled(self, client, monkeypatch):
        """/api/health stays open (no token) even with the gate on."""
        _enable(monkeypatch)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_plaid_sync_token_exempt(self, client, monkeypatch):
        """A matching X-Plaid-Sync-Token exempts Clerk auth ONLY on the cron
        sync-all route — the shared secret is never a master key for other
        routes. With no Bearer header:
          * a normal protected route (GET /api/transactions) => 401
          * POST /api/plaid/sync-all passes the auth layer (NOT 401)."""
        _enable(monkeypatch)
        monkeypatch.setenv("PLAID_SYNC_TOKEN", "cron-secret-123")

        # Security guarantee: the sync token does NOT exempt other routes.
        resp_other = client.get(
            "/api/transactions", headers={"X-Plaid-Sync-Token": "cron-secret-123"}
        )
        assert resp_other.status_code == 401

        # The exemption applies on the sync-all route: the request clears the
        # Clerk auth layer. Plaid is unconfigured in tests, so the handler
        # itself returns 503 (or 200 if mocked) — anything but 401 proves the
        # exemption fired.
        resp_sync = client.post(
            "/api/plaid/sync-all", headers={"X-Plaid-Sync-Token": "cron-secret-123"}
        )
        assert resp_sync.status_code != 401

    def test_wrong_plaid_sync_token_still_401(self, client, monkeypatch):
        """A non-matching X-Plaid-Sync-Token is NOT exempt: falls through to the
        normal Bearer requirement => 401 when no Authorization header is sent."""
        _enable(monkeypatch)
        monkeypatch.setenv("PLAID_SYNC_TOKEN", "cron-secret-123")
        resp = client.get(
            "/api/transactions", headers={"X-Plaid-Sync-Token": "wrong"}
        )
        assert resp.status_code == 401

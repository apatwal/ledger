"""
Clerk-based authentication (gated).

Mirrors the "gated by env var" pattern used by ai.py / plaid_client.py: the app
boots and every existing test passes with NO Clerk keys set. When Clerk is NOT
configured, authentication is DISABLED and every request is allowed exactly as
before. Auth turns ON only when configured.

Config (environment):
  CLERK_ISSUER      e.g. https://<subdomain>.clerk.accounts.dev — presence of this
                    (or CLERK_JWKS_URL) is what ENABLES auth.
  CLERK_JWKS_URL    defaults to ${CLERK_ISSUER}/.well-known/jwks.json when only
                    the issuer is given.
  CLERK_SECRET_KEY  optional; enables a server-side user-email lookup fallback
                    (GET https://api.clerk.com/v1/users/{sub}).
  ALLOWED_EMAILS    comma-separated allowlist (case-insensitive). Empty = allow
                    any authenticated user.
  PLAID_SYNC_TOKEN  shared secret for the cron sync-all route — a matching
                    X-Plaid-Sync-Token header is exempt from Clerk auth.

Verification (require_user), when enabled:
  * Require `Authorization: Bearer <token>` — missing/malformed -> 401.
  * Verify the JWT with PyJWT (RS256) using a cached PyJWKClient(CLERK_JWKS_URL):
    signature, exp/nbf, and iss == CLERK_ISSUER. Invalid/expired -> 401.
  * Resolve the user's email (email claim, else Clerk API fallback if
    CLERK_SECRET_KEY set). Cannot resolve + ALLOWED_EMAILS set -> 403.
  * ALLOWED_EMAILS non-empty: email must be in it -> else 403.
  * Fails closed: network/verify errors -> 401 (but the whole thing is bypassed
    when auth is disabled).
"""
from __future__ import annotations

import hmac
import json
import os
import urllib.request
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request

# ── Module-level caches ────────────────────────────────────────────────────────
# Cached PyJWKClient (keyed by JWKS URL) so we don't refetch signing keys every
# request, and a sub -> email cache to avoid a Clerk API lookup per request.
_jwk_clients: dict = {}
_email_cache: dict = {}


# ── Config helpers ─────────────────────────────────────────────────────────────

def _issuer() -> str:
    return (os.environ.get("CLERK_ISSUER") or "").strip().rstrip("/")


def get_jwks_url() -> str:
    """Explicit CLERK_JWKS_URL, or derived from CLERK_ISSUER."""
    explicit = (os.environ.get("CLERK_JWKS_URL") or "").strip()
    if explicit:
        return explicit
    issuer = _issuer()
    return f"{issuer}/.well-known/jwks.json" if issuer else ""


def is_auth_enabled() -> bool:
    """True when Clerk is configured — i.e. CLERK_ISSUER or CLERK_JWKS_URL is set.
    When False, require_user allows every request (no checks)."""
    return bool(_issuer()) or bool((os.environ.get("CLERK_JWKS_URL") or "").strip())


def get_allowed_emails() -> set[str]:
    """ALLOWED_EMAILS as a lower-cased set (empty = allow any authenticated user)."""
    raw = os.environ.get("ALLOWED_EMAILS") or ""
    return {tok.strip().lower() for tok in raw.split(",") if tok.strip()}


# ── JWT verification (seam: verify_token) ───────────────────────────────────────

def _get_jwk_client(jwks_url: str):
    """Return a cached PyJWKClient for the JWKS URL (imported lazily so the module
    imports cleanly even if PyJWT were absent, and always without keys)."""
    client = _jwk_clients.get(jwks_url)
    if client is None:
        from jwt import PyJWKClient

        client = PyJWKClient(jwks_url)
        _jwk_clients[jwks_url] = client
    return client


def verify_token(token: str) -> dict:
    """Verify a Clerk session JWT and return its claims. Raises HTTPException(401)
    on any failure (fails closed). Seam for tests to monkeypatch."""
    import jwt

    jwks_url = get_jwks_url()
    issuer = _issuer()
    try:
        signing_key = _get_jwk_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer or None,
            options={
                # Validate signature + exp/nbf; iss only when we know the issuer.
                "require": ["exp"],
                "verify_iss": bool(issuer),
                "verify_aud": False,  # Clerk session tokens use azp, not aud.
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # jwt errors, network fetching JWKS, malformed token
        raise HTTPException(status_code=401, detail=f"Invalid or expired token: {e}")
    return claims


# ── Email resolution (seam: resolve_email) ──────────────────────────────────────

def _email_from_claims(claims: dict) -> Optional[str]:
    """Pull an email from common Clerk claim shapes, if present."""
    email = claims.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip()
    # Some Clerk JWT templates nest it.
    for key in ("email_address", "primary_email_address"):
        val = claims.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _lookup_email_via_clerk_api(sub: str) -> Optional[str]:
    """Fallback: GET https://api.clerk.com/v1/users/{sub} with CLERK_SECRET_KEY and
    read the primary email. Cached per-sub. Returns None on any failure."""
    if sub in _email_cache:
        return _email_cache[sub]
    secret = (os.environ.get("CLERK_SECRET_KEY") or "").strip()
    if not secret:
        return None
    url = f"https://api.clerk.com/v1/users/{sub}"
    try:
        # A real User-Agent is required: Clerk's API is behind Cloudflare, which
        # 403s the default "Python-urllib" UA.
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {secret}",
                "User-Agent": "expense-tracker/1.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 (trusted host)
            data = json.loads(resp.read().decode("utf-8"))
        email = _primary_email_from_user(data)
    except Exception:
        # Transient failure (network/timeout/403): do NOT cache. Caching None here
        # would 403 the user until process restart, so leave the cache untouched so
        # the next request retries the lookup.
        return None
    # Cache only a SUCCESSFUL resolution (including a legitimate "no email" -> None).
    _email_cache[sub] = email
    return email


def _primary_email_from_user(user: dict) -> Optional[str]:
    """Extract the primary email from a Clerk user object."""
    if not isinstance(user, dict):
        return None
    primary_id = user.get("primary_email_address_id")
    addresses = user.get("email_addresses") or []
    if isinstance(addresses, list):
        # Prefer the primary email address.
        for addr in addresses:
            if isinstance(addr, dict) and addr.get("id") == primary_id:
                email = addr.get("email_address")
                if isinstance(email, str) and email.strip():
                    return email.strip()
        # Fall back to the first address with an email.
        for addr in addresses:
            if isinstance(addr, dict):
                email = addr.get("email_address")
                if isinstance(email, str) and email.strip():
                    return email.strip()
    return None


def resolve_email(claims: dict) -> Optional[str]:
    """Resolve the user's email: prefer an email claim, else the Clerk API lookup
    fallback (when CLERK_SECRET_KEY is set). Seam for tests to monkeypatch."""
    email = _email_from_claims(claims)
    if email:
        return email
    sub = claims.get("sub")
    if sub:
        return _lookup_email_via_clerk_api(sub)
    return None


# ── FastAPI dependency ───────────────────────────────────────────────────────

def require_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_plaid_sync_token: Optional[str] = Header(default=None),
) -> Optional[dict]:
    """FastAPI dependency protecting /api routes.

    * Auth disabled (no Clerk env) -> return None, allow everything (keeps local
      dev + the existing test suite green).
    * A valid X-Plaid-Sync-Token matching PLAID_SYNC_TOKEN is exempt ONLY on the
      cron sync-all route (/plaid/sync-all) — so the shared secret is never a
      master key for other routes.
    * Otherwise: verify the Bearer JWT, resolve the email, enforce the allowlist.
    """
    if not is_auth_enabled():
        return None

    # Exemption: the Plaid cron sync-all shared secret. Scoped to ONLY the
    # sync-all route so a leaked token can't unlock any other /api endpoint.
    if request.url.path.endswith("/plaid/sync-all"):
        sync_token = (os.environ.get("PLAID_SYNC_TOKEN") or "").strip()
        if (
            sync_token
            and x_plaid_sync_token
            and hmac.compare_digest(x_plaid_sync_token, sync_token)
        ):
            return {"sub": None, "email": None, "via": "plaid_sync_token"}

    if not authorization or not authorization.strip().lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    token = authorization.strip()[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    claims = verify_token(token)
    sub = claims.get("sub")

    allowed = get_allowed_emails()
    email = resolve_email(claims)

    if allowed:
        if not email:
            raise HTTPException(status_code=403, detail="Could not resolve user email for allowlist check")
        if email.lower() not in allowed:
            raise HTTPException(status_code=403, detail="User is not allowed")

    return {"sub": sub, "email": email}


# Convenience for router registration.
require_user_dep = Depends(require_user)

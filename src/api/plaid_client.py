"""
Plaid client + configuration (v8).

Mirrors the AI layer's "gated by env var" pattern (see ai.py): the app boots and
every existing test passes with NO Plaid keys set. `is_configured()` gates all
network-touching endpoints; when it's False the routes return 503.

Config (environment):
  PLAID_CLIENT_ID   required for any Plaid call (absent -> feature disabled / 503)
  PLAID_SECRET      required for any Plaid call
  PLAID_ENV         sandbox (default) | development | production -> selects host
  PLAID_PRODUCTS    comma list, default "transactions,investments"
  PLAID_COUNTRY_CODES  comma list, default "US"
  PLAID_REDIRECT_URI   optional OAuth redirect URI for Link
  PLAID_SYNC_TOKEN     optional shared secret protecting POST /api/plaid/sync-all
  PLAID_AUTOSYNC_INTERVAL_MINUTES  optional; when set AND configured, run the
                                   in-process scheduler on that interval

The plaid SDK is imported lazily inside get_client() so the module (and the whole
app) imports cleanly even if plaid-python were absent, and always without keys.
"""
from __future__ import annotations

import os

# Plaid host per environment. plaid-python 40.x's Environment enum only exposes
# Sandbox/Production (Plaid retired the shared "development" env), so we resolve
# hosts by name here to still honour PLAID_ENV=development for older accounts.
_PLAID_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}

DEFAULT_PRODUCTS = "transactions,investments"
DEFAULT_COUNTRY_CODES = "US"


class PlaidNotConfigured(RuntimeError):
    """Raised when a Plaid call is attempted without PLAID_CLIENT_ID/PLAID_SECRET."""


def is_configured() -> bool:
    """True only when BOTH the client id and secret are present."""
    return bool(os.environ.get("PLAID_CLIENT_ID")) and bool(os.environ.get("PLAID_SECRET"))


def get_env() -> str:
    """Normalized PLAID_ENV (sandbox | development | production); defaults to sandbox."""
    env = (os.environ.get("PLAID_ENV") or "sandbox").strip().lower()
    return env if env in _PLAID_HOSTS else "sandbox"


def _host() -> str:
    return _PLAID_HOSTS[get_env()]


def _csv_env(name: str, default: str) -> list[str]:
    """Read a comma-separated env var into a list of trimmed, non-empty tokens."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        raw = default
    return [tok.strip() for tok in raw.split(",") if tok.strip()]


def get_products() -> list[str]:
    """PLAID_PRODUCTS as a list, e.g. ["transactions", "investments"]."""
    return _csv_env("PLAID_PRODUCTS", DEFAULT_PRODUCTS)


def get_country_codes() -> list[str]:
    """PLAID_COUNTRY_CODES as a list, e.g. ["US"]."""
    return _csv_env("PLAID_COUNTRY_CODES", DEFAULT_COUNTRY_CODES)


def get_redirect_uri() -> str | None:
    uri = (os.environ.get("PLAID_REDIRECT_URI") or "").strip()
    return uri or None


def get_client():
    """Return a configured plaid_api.PlaidApi, or raise PlaidNotConfigured.

    The plaid SDK is imported lazily so the app boots without the library/keys.
    """
    client_id = os.environ.get("PLAID_CLIENT_ID")
    secret = os.environ.get("PLAID_SECRET")
    if not client_id or not secret:
        raise PlaidNotConfigured("PLAID_CLIENT_ID / PLAID_SECRET are not set")

    import plaid
    from plaid.api import plaid_api

    configuration = plaid.Configuration(
        host=_host(),
        api_key={"clientId": client_id, "secret": secret},
    )
    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)

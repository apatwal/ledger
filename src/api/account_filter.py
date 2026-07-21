"""Shared multi-account filtering helper (v9).

Users can deselect some connected cards, so list/stats/duplicates endpoints accept
an `accounts` query param (comma-separated list) alongside the legacy single
`account` param. Semantics:
  * `accounts` (when it has ≥1 non-empty token) wins over single `account`.
  * A filter of `Transaction.account IN (list)` is applied.
  * An absent/empty `accounts` AND absent `account` means "all accounts" (no filter).
Kept in one place so every endpoint filters identically.
"""
from __future__ import annotations

from typing import Optional

from .models import Transaction


def parse_accounts(accounts: Optional[str]) -> Optional[list[str]]:
    """Parse a comma-separated `accounts` param into a list of trimmed, non-empty
    tokens. Returns None when the param is absent/blank (→ no multi-account filter)."""
    if not accounts:
        return None
    tokens = [tok.strip() for tok in accounts.split(",") if tok.strip()]
    return tokens or None


def account_filter_condition(account: Optional[str], accounts: Optional[str]):
    """Return a SQLAlchemy condition for account selection, or None for "all".

    `accounts` (comma list) wins over the single `account` param.
    """
    parsed = parse_accounts(accounts)
    if parsed is not None:
        return Transaction.account.in_(parsed)
    if account:
        return Transaction.account == account
    return None

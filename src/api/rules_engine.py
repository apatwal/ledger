"""
Rules engine (v5) — pure classification layer. No DB, no HTTP, no network.

apply_rules() takes a list of rule-like objects (anything with the rule
attributes: enabled, priority, id, match_field, match_op, match_value,
amount_min, amount_max, set_type, set_category, set_account) and a transaction's
fields, and returns the actions of the FIRST enabled rule that matches
(ordered by priority asc, then id asc). Returns None when nothing matches.

Also exposes AMBIGUOUS_TOKENS + is_ambiguous() for the needs-review heuristic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

# Tokens that commonly indicate a transaction whose true category/direction is
# unclear from a bank/checking export — flag these for human/AI review when no
# rule has already classified them. Matched case-insensitively as substrings of
# the description. Easy to extend.
AMBIGUOUS_TOKENS = [
    "venmo",
    "zelle",
    "cash app",
    "cashapp",
    "atm",
    "withdrawal",
    "check ",      # trailing space avoids matching "checkout", "checkers", etc.
    "e-check",
    "echeck",
    "cash deposit",
    "wire transfer",
    "money transfer",
    "p2p",
]

# v5.4: peer-to-peer pass-through tokens. These DEFAULT to `transfer` (a
# reimbursement pass-through, excluded from income/spend) while still flagged
# needs_review so the user can reclassify a genuine income/expense one.
P2P_TOKENS = [
    "venmo",
    "zelle",
    "cash app",
    "cashapp",
]


def is_p2p(description: Optional[str]) -> Optional[str]:
    """Return the first peer-to-peer pass-through token in the description, else None."""
    if not description:
        return None
    desc = description.lower()
    for token in P2P_TOKENS:
        if token in desc:
            return token.strip()
    return None


@dataclass
class RuleHit:
    """The non-null actions of the matched rule. Any of these may be None
    (meaning 'keep whatever was inferred for that field')."""
    rule_id: int
    set_type: Optional[str] = None
    set_category: Optional[str] = None
    set_account: Optional[str] = None


def is_ambiguous(description: Optional[str]) -> Optional[str]:
    """Return the first ambiguous token found in the description, else None."""
    if not description:
        return None
    desc = description.lower()
    for token in AMBIGUOUS_TOKENS:
        if token in desc:
            return token.strip()
    return None


# Brokerage / investment platforms (v5.1). A deposit from checking into one of
# these is ambiguous: it could be "savings" (count toward savings rate) or a
# neutral "transfer". We default to transfer (safe) and flag for the user to
# decide once. Matched case-insensitively as substrings of the description.
BROKERAGE_TOKENS = [
    "robinhood",
    "fidelity",
    "vanguard",
    "schwab",
    "charles schwab",
    "e*trade",
    "etrade",
    "coinbase",
    "wealthfront",
    "betterment",
    "merrill",
    "sofi invest",
    "acorns",
    "td ameritrade",
    "webull",
]


def is_brokerage(description: Optional[str]) -> Optional[str]:
    """Return the first brokerage token found in the description, else None."""
    if not description:
        return None
    desc = description.lower()
    for token in BROKERAGE_TOKENS:
        if token in desc:
            return token.strip()
    return None


# Bank-side credit-card payment patterns (v5.3). When a checking/savings export
# shows a payment TO a credit card, classify it as `transfer` — otherwise it
# would count as spend AND double-count the card's own purchases. These are the
# distinctive tokens BofA/Wells/etc. use for card payments. Extensible list.
# NOTE: genuinely ambiguous rows (Venmo, Zelle, Discover CONA net/mobile moves,
# cash) are deliberately NOT here — they stay in needs-review.
CARD_PAYMENT_TOKENS = [
    "des:e-payment",
    "des:epay",
    "des:ccpymt",
    "credit crd",
    "wells fargo card",
    "bilt card des:pmt",
    "card des:pmt",
    "online banking payment to crd",
    "e-payment",
]


def is_card_payment(description: Optional[str]) -> Optional[str]:
    """Return the first bank-side card-payment token in the description, else None."""
    if not description:
        return None
    desc = description.lower()
    for token in CARD_PAYMENT_TOKENS:
        if token in desc:
            return token.strip()
    return None


def _field_value(match_field: str, description, category, account) -> list[str]:
    """Return the candidate string(s) to test for a given match_field."""
    description = description or ""
    category = category or ""
    account = account or ""
    if match_field == "description":
        return [description]
    if match_field == "category":
        return [category]
    if match_field == "account":
        return [account]
    if match_field == "any":
        return [description, category, account]
    return [description]


def _op_matches(op: str, candidate: str, value: str) -> bool:
    c = candidate or ""
    v = value or ""
    if op == "contains":
        return v.lower() in c.lower()
    if op == "equals":
        return c.strip().lower() == v.strip().lower()
    if op == "regex":
        try:
            return re.search(v, c, flags=re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def _amount_in_range(amount, amount_min, amount_max) -> bool:
    if amount is None:
        # if a rule constrains amount but we have no amount, treat as no-match
        return amount_min is None and amount_max is None
    if amount_min is not None and amount < amount_min:
        return False
    if amount_max is not None and amount > amount_max:
        return False
    return True


def rule_matches(rule, *, description, category, account, amount) -> bool:
    """Whether a single rule matches the given transaction fields (ignores enabled)."""
    if not _amount_in_range(amount, rule.amount_min, rule.amount_max):
        return False
    candidates = _field_value(rule.match_field, description, category, account)
    return any(_op_matches(rule.match_op, cand, rule.match_value) for cand in candidates)


def apply_rules(
    rules: Sequence,
    *,
    description: Optional[str] = None,
    category: Optional[str] = None,
    account: Optional[str] = None,
    amount: Optional[float] = None,
) -> Optional[RuleHit]:
    """First enabled rule (priority asc, then id asc) that matches wins."""
    ordered = sorted(
        (r for r in rules if getattr(r, "enabled", True)),
        key=lambda r: (r.priority if r.priority is not None else 100, r.id or 0),
    )
    for rule in ordered:
        if rule_matches(
            rule, description=description, category=category, account=account, amount=amount
        ):
            return RuleHit(
                rule_id=rule.id,
                set_type=rule.set_type,
                set_category=rule.set_category,
                set_account=rule.set_account,
            )
    return None

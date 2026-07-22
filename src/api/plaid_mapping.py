"""
Pure Plaid -> Transaction mapping (v8). NO network, NO DB, NO plaid client.

QA can unit-test `map_transaction` and `map_investment_transaction` offline with
plain dicts. Both functions accept a dict OR a plaid model object (models expose
`.to_dict()` / attributes) and normalize to a dict first.

Plaid sign convention: `amount` is POSITIVE when money LEAVES the account
(a purchase) and NEGATIVE when money comes IN (a credit/refund). We store the
absolute value and derive `type` separately so the app's income/expense/transfer
stats stay correct. Trusting Plaid's `personal_finance_category` means Plaid rows
skip the rules engine / needs-review / AI entirely.
"""
from __future__ import annotations

from datetime import date as date_type, datetime
from typing import Any, Optional

# Plaid personal_finance_category.primary (PFC) -> friendly category label.
PFC_CATEGORY_LABELS: dict[str, str] = {
    "FOOD_AND_DRINK": "Food & Drink",
    "GENERAL_MERCHANDISE": "Shopping",
    "TRANSPORTATION": "Transportation",
    "TRAVEL": "Travel",
    "RENT_AND_UTILITIES": "Bills & Utilities",
    "ENTERTAINMENT": "Entertainment",
    "MEDICAL": "Health",
    "PERSONAL_CARE": "Personal Care",
    "GENERAL_SERVICES": "Services",
    "GOVERNMENT_AND_NON_PROFIT": "Government",
    "TRANSFER_IN": "Transfer",
    "TRANSFER_OUT": "Transfer",
    "LOAN_PAYMENTS": "Payments & Credits",
    "BANK_FEES": "Fees",
    "INCOME": "Income",
}

# PFC primaries that represent money moving between the user's own accounts (or
# credit-card payments). Classifying these as `transfer` keeps them out of
# income/expense stats -> the credit-card-payment double-count stays solved.
TRANSFER_PFCS = {"TRANSFER_IN", "TRANSFER_OUT", "LOAN_PAYMENTS"}

FALLBACK_CATEGORY = "Uncategorized"

# Canonical category vocabulary — the Plaid-derived single source of truth used
# across the app (built from PFC_CATEGORY_LABELS.values() + "Investment" from the
# investment mapping + the "Uncategorized" fallback). Defined as an explicit
# ordered literal: naive dedup of PFC_CATEGORY_LABELS.values() would put Income
# last, but canonical order puts Income before Transfer and appends
# Investment + Uncategorized.
CANONICAL_CATEGORIES: list[str] = [
    "Food & Drink",
    "Shopping",
    "Transportation",
    "Travel",
    "Bills & Utilities",
    "Entertainment",
    "Health",
    "Personal Care",
    "Services",
    "Government",
    "Income",
    "Transfer",
    "Payments & Credits",
    "Fees",
    "Investment",
    "Uncategorized",
]

# Drift guard: every mapped label + Investment + fallback must be canonical.
assert set(CANONICAL_CATEGORIES) >= set(PFC_CATEGORY_LABELS.values()) | {
    "Investment",
    FALLBACK_CATEGORY,
}


def _to_dict(obj: Any) -> dict:
    """Normalize a plaid model object OR a dict to a plain dict."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict()
        except Exception:
            pass
    # Last resort: shallow copy of public attributes.
    return {k: v for k, v in vars(obj).items() if not k.startswith("_")}


def _get(d: dict, key: str, default: Any = None) -> Any:
    val = d.get(key, default)
    return default if val is None else val


def _norm_date(value: Any) -> Optional[date_type]:
    """Coerce a plaid date field (date | datetime | ISO string) to a date."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_type):
        return value
    try:
        return date_type.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _pfc_primary(txn: dict) -> str:
    """Extract personal_finance_category.primary, robust to dict/model/missing."""
    pfc = _to_dict(txn.get("personal_finance_category"))
    return str(pfc.get("primary") or "").strip().upper()


def _logo_url(txn: dict) -> Optional[str]:
    """Prefer the transaction's own logo_url; fall back to the first
    counterparty's logo_url (robust to dict/model/missing); else None."""
    logo = _get(txn, "logo_url")
    if logo:
        return str(logo)
    counterparties = txn.get("counterparties") or []
    if isinstance(counterparties, (list, tuple)) and counterparties:
        first = _to_dict(counterparties[0])
        cp_logo = first.get("logo_url")
        if cp_logo:
            return str(cp_logo)
    return None


def map_transaction(plaid_txn: Any, account_label: str) -> dict:
    """Map one Plaid /transactions/sync transaction to Transaction fields.

    Returns a dict with: date, amount, type, category, description, account,
    plaid_transaction_id, plaid_account_id, plus v9 enrichment: merchant_name,
    logo_url, pending, pending_transaction_id, category_icon_url.
    """
    txn = _to_dict(plaid_txn)

    raw_amount = _get(txn, "amount", 0.0)
    try:
        raw_amount = float(raw_amount)
    except (TypeError, ValueError):
        raw_amount = 0.0
    amount = abs(raw_amount)

    pfc = _pfc_primary(txn)

    # Derive type from PFC + sign. Plaid amount > 0 => money OUT, < 0 => money IN.
    if pfc == "INCOME":
        tx_type = "income"
    elif pfc in TRANSFER_PFCS:
        tx_type = "transfer"
    elif raw_amount < 0:
        tx_type = "refund"
    else:
        tx_type = "expense"

    category = PFC_CATEGORY_LABELS.get(pfc, FALLBACK_CATEGORY)

    # description: merchant_name preferred, else name.
    description = _get(txn, "merchant_name") or _get(txn, "name") or None
    if description is not None:
        description = str(description).strip() or None

    # date: authorized_date preferred, else date.
    tx_date = _norm_date(txn.get("authorized_date")) or _norm_date(txn.get("date"))

    # v9 enrichment fields.
    merchant_name = _get(txn, "merchant_name")
    if merchant_name is not None:
        merchant_name = str(merchant_name).strip() or None

    return {
        "date": tx_date,
        "amount": amount,
        "type": tx_type,
        "category": category,
        "description": description,
        "account": account_label,
        "plaid_transaction_id": _get(txn, "transaction_id"),
        "plaid_account_id": _get(txn, "account_id"),
        "merchant_name": merchant_name,
        "logo_url": _logo_url(txn),
        "pending": bool(_get(txn, "pending", False)),
        "pending_transaction_id": _get(txn, "pending_transaction_id"),
        "category_icon_url": _get(txn, "personal_finance_category_icon_url"),
    }


def map_investment_transaction(inv_txn: Any, account_label: str) -> dict:
    """Map one Plaid /investments/transactions/get transaction to Transaction
    fields. category is always "Investment"; type derives from subtype/type.

    Returns a dict with: date, amount, type, category, description, account,
    plaid_transaction_id, plaid_account_id.
    """
    txn = _to_dict(inv_txn)

    raw_amount = _get(txn, "amount", 0.0)
    try:
        raw_amount = float(raw_amount)
    except (TypeError, ValueError):
        raw_amount = 0.0
    amount = abs(raw_amount)

    # Investment activity must NOT inflate gross income/expense (IRA/brokerage
    # buy-sell churn was blowing totals up). We NEUTRALIZE portfolio moves as
    # `transfer` (excluded from stats) and only surface REAL cash flow:
    #   - dividends/interest received -> income
    #   - contributions/deposits      -> expense (counts toward savings, since
    #                                     savings = expense rows in Savings/Investment)
    # Decide from `type`, then refine `cash` rows by `subtype`.
    inv_type = str(_get(txn, "type") or "").strip().lower()
    inv_subtype = str(_get(txn, "subtype") or "").strip().lower()

    if inv_type in ("buy", "sell", "transfer", "cancel"):
        tx_type = "transfer"            # portfolio churn — neutral
    elif inv_type == "fee":
        tx_type = "expense"             # real cost
    elif inv_type == "cash":
        if "dividend" in inv_subtype or "interest" in inv_subtype:
            tx_type = "income"          # real income received
        elif "contribution" in inv_subtype or "deposit" in inv_subtype:
            tx_type = "expense"         # money in -> counts toward savings
        elif "withdrawal" in inv_subtype or "distribution" in inv_subtype:
            tx_type = "transfer"        # neutral — don't inflate income
        else:
            tx_type = "transfer"        # unknown cash movement — neutral
    else:
        tx_type = "transfer"            # unknown/absent type — neutral

    description = _get(txn, "name") or None
    if description is not None:
        description = str(description).strip() or None

    tx_date = _norm_date(txn.get("date"))

    return {
        "date": tx_date,
        "amount": amount,
        "type": tx_type,
        "category": "Investment",
        "description": description,
        "account": account_label,
        "plaid_transaction_id": _get(txn, "investment_transaction_id"),
        "plaid_account_id": _get(txn, "account_id"),
    }

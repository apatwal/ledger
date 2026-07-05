import csv
import io
import re
from datetime import date as date_type, datetime
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Transaction, Rule, ImportBatch
from ..schemas import CSVImportResponse, CSVErrorRow
from .. import rules_engine

router = APIRouter(prefix="/transactions", tags=["csv"])

TEMPLATE_HEADERS = "date,amount,type,category,description\n"
TEMPLATE_EXAMPLE = "2024-03-15,2500.00,income,Salary,March salary\n2024-03-16,120.00,expense,Groceries,Weekly shop\n"

VALID_TYPES = {"income", "expense", "transfer", "refund"}

# ── Header alias map (v3, bank-agnostic) ──────────────────────────────────────
# Headers are normalized (lowercase, strip whitespace/quotes, remove "." and "_",
# collapse inner whitespace) before matching. We match by EXACT normalized
# equality first across ALL fields, then fall back to `contains`. Within a field,
# the FIRST alias (in list order) that resolves wins — so order matters:
# trans-date aliases come before post-date aliases.
COLUMN_ALIASES = {
    "date": [
        "date", "transaction date", "trans date", "transaction posted date",
        "posted date", "post date", "posting date", "date posted",
        "effective date", "booking date", "value date", "process date",
        "activity date", "completed date", "run date",
    ],
    "amount": ["amount", "amt", "transaction amount", "value", "net amount"],
    "debit": [
        "debit", "debit amount", "withdrawal", "withdrawals", "money out",
        "paid out", "outflow", "payments",
    ],
    "credit": [
        "credit", "credit amount", "deposit", "deposits", "money in",
        "paid in", "inflow",
    ],
    "description": [
        "description", "transaction description", "original description",
        "extended description", "memo", "payee", "name", "merchant",
        "details", "narration", "particulars", "reference", "notes",
    ],
    "category": ["category", "transaction category", "classification"],
    "type": ["type", "transaction type", "dr/cr", "debit/credit", "cr/dr", "direction"],
}

# Columns that must NEVER be used as amount or date.
IGNORE_HEADERS = {
    "balance", "running balance", "running bal", "available balance",
    "card no", "card number", "check or slip #", "status",
}

# ── Transfer auto-classification tokens ───────────────────────────────────────
# Two module-level lists so they're easy to extend.
TRANSFER_CATEGORIES = [
    "payments and credits",
    "payments & credits",
    "payment",
    "transfer",
    "credit card payment",
]
TRANSFER_DESCRIPTION_TOKENS = [
    "internet payment",
    "thank you",
    "autopay",
    "payment - thank you",
    "payment thank you",
]

# ── Bank `Type`/direction VALUE mapping ───────────────────────────────────────
TYPE_VALUE_MAP = {
    "debit": "expense", "dr": "expense", "withdrawal": "expense",
    "sale": "expense", "purchase": "expense",
    "credit": "income", "cr": "income", "deposit": "income",
    "payment": "transfer", "xfer": "transfer", "transfer": "transfer",
    "acct_xfer": "transfer", "acct xfer": "transfer",
    # v5.4: returns/refunds/reversals NET against category spend (negative expense),
    # so they map to `refund` (was `transfer` in v5.1). Chase `Return` rows and
    # labeled refunds reduce their category's spend rather than being excluded.
    "return": "refund", "refund": "refund", "reversal": "refund",
    # our literal values pass through:
    "income": "income", "expense": "expense", "transfer": "transfer",
}


def _normalize_header(h: str) -> str:
    """lowercase, strip whitespace + surrounding quotes, remove '.' and '_', collapse whitespace."""
    h = (h or "").strip().strip('"').strip("'").lower()
    h = h.replace(".", "").replace("_", "")
    return re.sub(r"\s+", " ", h).strip()


def classify_transfer(category: str, description: str) -> bool:
    """Return True if the (category, description) pair looks like an account transfer."""
    cat = _normalize_header(category)
    if cat in TRANSFER_CATEGORIES:
        return True
    desc = (description or "").strip().lower()
    return any(token in desc for token in TRANSFER_DESCRIPTION_TOKENS)


def _parse_date(raw: str) -> date_type:
    """Accept MM/DD/YYYY (Discover) and ISO YYYY-MM-DD. Raises ValueError otherwise."""
    raw = raw.strip()
    try:
        return date_type.fromisoformat(raw)
    except ValueError:
        pass
    return datetime.strptime(raw, "%m/%d/%Y").date()


def _resolve_columns(fieldnames: list[str]) -> dict:
    """
    Map each logical field -> the original header string.
    Match by EXACT normalized equality across all field aliases first; if a field
    is still unresolved, fall back to `contains` matching. First alias (in list
    order) that resolves wins. Ignore-list headers are never used for amount/date.
    Returns a dict with keys among: date, amount, debit, credit, description,
    category, type.
    """
    # normalized -> original header (keep first occurrence)
    norm_to_orig: dict[str, str] = {}
    for orig in fieldnames:
        norm = _normalize_header(orig)
        if norm in IGNORE_HEADERS:
            continue
        norm_to_orig.setdefault(norm, orig)

    norms = list(norm_to_orig.keys())
    resolved: dict[str, str] = {}

    for field, aliases in COLUMN_ALIASES.items():
        chosen = None
        # pass 1: exact normalized equality
        for alias in aliases:
            if alias in norm_to_orig:
                chosen = norm_to_orig[alias]
                break
        # pass 2: contains fallback
        if chosen is None:
            for alias in aliases:
                for norm in norms:
                    if alias in norm and norm_to_orig[norm] not in resolved.values():
                        chosen = norm_to_orig[norm]
                        break
                if chosen:
                    break
        if chosen is not None:
            resolved[field] = chosen

    return resolved


def _to_float(s: str) -> float:
    """Parse a money string: strip $/commas, handle parenthesised negatives."""
    s = (s or "").strip().replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    val = float(s)
    return -val if neg else val


def _explicit_type(row, cols) -> Optional[str]:
    """Return the mapped type if the row has a recognized Type/direction column
    VALUE, else None. Used to decide whether P2P/brokerage defaults may apply
    (an explicit Type column always wins)."""
    if "type" not in cols:
        return None
    raw_type_val = (row.get(cols.get("type", ""), "") or "").strip().lower()
    norm_type_val = re.sub(r"\s+", " ", raw_type_val.replace("_", " ")).strip()
    mapped = TYPE_VALUE_MAP.get(raw_type_val) or TYPE_VALUE_MAP.get(norm_type_val)
    return mapped if mapped in VALID_TYPES else None


def _infer_type(
    row, cols, category, description, signed_amount, debit_credit_hint,
    statement_type: str = "card",
) -> str:
    """Built-in type inference (used when no user rule sets the type). Priority:
      1. Explicit/recognized `type`/direction column VALUE wins over sign inference.
         Banks use OPPOSITE sign conventions per-value, so the labelled direction is
         authoritative: Chase `Sale` (negative amount) => expense (abs);
         `Payment`/`Return`/`Refund` (positive amount) => transfer, NEVER income.
      2. Transfer auto-classification (category/description tokens); v5.3 also
         bank-side credit-card-payment tokens => transfer.
      3. Split debit/credit hint: debit->expense, credit->income.
      4. Single signed amount, by statement_type:
         - card (credit card, e.g. Discover): positive->expense, negative->transfer.
         - bank (checking/savings): negative->expense (outflow), positive->income (inflow).
    """
    raw_type_val = (row.get(cols.get("type", ""), "") or "").strip().lower() if "type" in cols else ""
    norm_type_val = re.sub(r"\s+", " ", raw_type_val.replace("_", " ")).strip()
    mapped_type = TYPE_VALUE_MAP.get(raw_type_val) or TYPE_VALUE_MAP.get(norm_type_val)

    if mapped_type in VALID_TYPES:
        return mapped_type
    # v5.3: bank-side credit-card payments are transfers (don't count as spend).
    if rules_engine.is_card_payment(description or ""):
        return "transfer"
    if classify_transfer(category, description or ""):
        return "transfer"
    if debit_credit_hint == "debit":
        return "expense"
    if debit_credit_hint == "credit":
        return "income"
    # single signed amount — sign convention depends on statement type
    if statement_type == "bank":
        # checking/savings: negative = outflow (expense), positive = inflow (income)
        return "expense" if signed_amount < 0 else "income"
    # card (default): positive = purchase (expense), negative = payment/credit (transfer)
    if signed_amount < 0:
        return "transfer"
    return "expense"


@router.get("/csv/template")
def download_template():
    content = TEMPLATE_HEADERS + TEMPLATE_EXAMPLE
    return StreamingResponse(
        io.BytesIO(content.encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions_template.csv"},
    )


def _body_from(all_rows: list[list[str]], idx: int) -> str:
    """Re-serialize CSV rows from `idx` onward (safe to feed to csv.DictReader)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    for r in all_rows[idx:]:
        writer.writerow(r)
    return buf.getvalue()


def _find_header_and_body(text: str):
    """v5.3: scan the leading CSV rows to locate the REAL header, skipping any
    summary preamble/blank lines before it. Returns a dict:
      { "found": bool, "header": list[str]|None, "body": str|None,
        "partial": list[str]|None, "first": list[str] }
    - "found"/header/body: the FIRST row where BOTH a date alias AND an
      amount/debit/credit alias resolve (the header we use).
    - "partial": the first row that resolves a date alias but NO amount/debit/
      credit — lets the caller emit the specific "amount column missing" error.
    - "first": the first non-empty row — for the generic "no date" error.
    """
    all_rows = list(csv.reader(io.StringIO(text)))
    first: list[str] = []
    partial: list[str] | None = None
    for idx, cells in enumerate(all_rows):
        if not any((c or "").strip() for c in cells):
            continue  # blank line
        if not first:
            first = cells
        cols = _resolve_columns(cells)
        has_date = "date" in cols
        has_amount = any(k in cols for k in ("amount", "debit", "credit"))
        if has_date and has_amount:
            return {"found": True, "header": cells, "body": _body_from(all_rows, idx),
                    "partial": partial, "first": first}
        if has_date and partial is None:
            partial = cells  # remember a date-only header for the specific error
    return {"found": False, "header": None, "body": None, "partial": partial, "first": first}


@router.post("/csv", response_model=CSVImportResponse)
async def import_csv(
    file: UploadFile = File(...),
    account: str = Form(None),
    statement_type: str = Form("card"),  # v5.3: card (default) | bank
    db: Session = Depends(get_db),
):
    # v4: optional `account` form field — tag every imported row with this card.
    account_tag = account.strip() if account and account.strip() else None
    # v5.3: normalize statement_type; anything other than "bank" is treated as "card".
    statement_type = (statement_type or "card").strip().lower()
    if statement_type not in ("card", "bank"):
        statement_type = "card"

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")  # handle BOM
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    # v5.3: find the REAL header row, skipping any summary preamble before it.
    scan = _find_header_and_body(text)
    if not scan["found"]:
        # No row had BOTH a date and an amount/debit/credit column.
        if scan["partial"] is not None:
            # We DID find a date column but no amount/debit/credit — specific error.
            raise HTTPException(
                status_code=400,
                detail=(
                    "Could not resolve a required 'amount' (or 'debit'/'credit') column. "
                    f"Headers seen: {scan['partial']}."
                ),
            )
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not resolve a required 'date' column. "
                f"Headers seen: {scan['first']}. "
                f"Add the header to the date alias list if needed."
            ),
        )

    # Re-read starting at the detected header so DictReader keys off the real header.
    reader = csv.DictReader(io.StringIO(scan["body"]))
    seen_headers = list(reader.fieldnames or scan["header"])
    cols = _resolve_columns(seen_headers)

    imported = 0
    skipped = 0
    transfers = 0
    needs_review_count = 0
    errors: list[CSVErrorRow] = []
    created_rows: list[Transaction] = []  # v5.2: tag with batch_id after counting

    has_single_amount = "amount" in cols

    # v5: load enabled rules once; they run BEFORE built-in inference and override it.
    enabled_rules = list(
        db.execute(
            select(Rule).where(Rule.enabled == True).order_by(Rule.priority.asc(), Rule.id.asc())  # noqa: E712
        ).scalars().all()
    )

    for row_num, row in enumerate(reader, start=2):  # row 1 is header
        # --- date ---
        raw_date = (row.get(cols["date"], "") or "").strip()
        if not raw_date:
            errors.append(CSVErrorRow(row=row_num, reason="Missing date"))
            skipped += 1
            continue
        try:
            parsed_date = _parse_date(raw_date)
        except ValueError:
            errors.append(CSVErrorRow(row=row_num, reason=f"Invalid date: '{raw_date}'"))
            skipped += 1
            continue

        # --- amount + side hint from debit/credit ---
        # signed_amount: positive = charge/expense side, negative = credit/inflow side
        # debit_credit_hint: "debit" | "credit" | None  (only when split columns used)
        debit_credit_hint = None
        try:
            if has_single_amount:
                cell = row.get(cols["amount"], "")
                if not str(cell).strip():
                    raise ValueError("empty amount")
                signed_amount = _to_float(cell)
            else:
                debit_cell = row.get(cols.get("debit", ""), "") if "debit" in cols else ""
                credit_cell = row.get(cols.get("credit", ""), "") if "credit" in cols else ""
                debit_val = _to_float(debit_cell) if str(debit_cell).strip() else 0.0
                credit_val = _to_float(credit_cell) if str(credit_cell).strip() else 0.0
                if debit_val == 0.0 and credit_val == 0.0:
                    raise ValueError("empty amount")
                # debit = money out (expense, positive), credit = money in (negative)
                if debit_val != 0.0:
                    signed_amount = abs(debit_val)
                    debit_credit_hint = "debit"
                else:
                    signed_amount = -abs(credit_val)
                    debit_credit_hint = "credit"
            if signed_amount == 0:
                raise ValueError("zero amount")
        except ValueError as e:
            errors.append(CSVErrorRow(row=row_num, reason=f"Invalid amount ({e})"))
            skipped += 1
            continue
        amount = abs(signed_amount)

        # --- category / description ---
        category = (row.get(cols.get("category", ""), "") or "").strip() if "category" in cols else ""
        description = (row.get(cols.get("description", ""), "") or "").strip() if "description" in cols else ""
        if not category:
            category = "Uncategorized"
        description = description or None

        # --- classification: user rules first (override), else built-in inference ---
        row_account = account_tag

        # v5: USER RULES run first and override built-in inference.
        rule_hit = rules_engine.apply_rules(
            enabled_rules,
            description=description,
            category=category,
            account=row_account,
            amount=amount,
        )

        if rule_hit is not None:
            # A matching rule's non-null actions override; unset actions keep inference.
            if rule_hit.set_category:
                category = rule_hit.set_category
            if rule_hit.set_account:
                row_account = rule_hit.set_account
            if rule_hit.set_type:
                tx_type = rule_hit.set_type
            else:
                tx_type = _infer_type(
                    row, cols, category, description, signed_amount, debit_credit_hint,
                    statement_type,
                )
        else:
            tx_type = _infer_type(
                row, cols, category, description, signed_amount, debit_credit_hint,
                statement_type,
            )

        # v5 / v5.1 / v5.4: flag needs_review when NO rule matched. Precedence:
        #   1. P2P pass-through (venmo/zelle/cash app) — DEFAULT type=transfer
        #      (excluded), review to reclassify. Only when no explicit Type value.
        #   2. Brokerage deposit (savings-vs-transfer) — also forces type=transfer.
        #   3. Ambiguous token (atm/check/wire/...).
        #   4. Uncategorized.
        # A matching user rule overrides entirely (so once the user picks, future
        # rows are auto-handled and NOT re-flagged).
        needs_review = False
        review_reason = None
        if rule_hit is None:
            p2p = rules_engine.is_p2p(description)
            brokerage = rules_engine.is_brokerage(description)
            explicit = _explicit_type(row, cols)
            if p2p and explicit is None:
                # v5.4: peer-to-peer pass-through defaults to transfer (excluded),
                # but flagged so the user can reclassify a real income/expense.
                tx_type = "transfer"
                needs_review = True
                review_reason = (
                    f"Assumed pass-through transfer ({p2p}) — reclassify if income/expense"
                )
            elif brokerage:
                # ambiguous savings-vs-transfer: default safe (transfer), let user decide once
                tx_type = "transfer"
                needs_review = True
                review_reason = (
                    f"Brokerage: count as savings or keep as transfer? ({brokerage})"
                )
            else:
                ambiguous = rules_engine.is_ambiguous(description)
                if ambiguous:
                    # the ambiguous token is the more actionable reason — prefer it
                    needs_review = True
                    review_reason = f"Ambiguous: {ambiguous}"
                elif category == "Uncategorized":
                    needs_review = True
                    review_reason = "Uncategorized"

        obj = Transaction(
            date=parsed_date,
            amount=amount,
            type=tx_type,
            category=category,
            description=description,
            account=row_account,
            needs_review=needs_review,
            review_reason=review_reason,
            source="csv",
        )
        db.add(obj)
        created_rows.append(obj)
        imported += 1
        # `transfers` = number of imported rows classified as transfer (any path).
        if tx_type == "transfer":
            transfers += 1
        if needs_review:
            needs_review_count += 1

    # v5.2: record this import as a batch and tag every created row with its id.
    batch = ImportBatch(
        filename=(file.filename or "import.csv"),
        account=account_tag,
        statement_type=statement_type,
        imported=imported,
        skipped=skipped,
        transfers=transfers,
        needs_review=needs_review_count,
    )
    db.add(batch)
    db.flush()  # assign batch.id
    for obj in created_rows:
        obj.batch_id = batch.id

    db.commit()
    return CSVImportResponse(
        imported=imported,
        skipped=skipped,
        transfers=transfers,
        needs_review=needs_review_count,
        batch_id=batch.id,
        errors=errors,
    )

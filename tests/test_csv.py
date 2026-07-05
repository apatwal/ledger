"""
test_csv.py — Tests for CSV import and the template endpoint.

Coverage
--------
* GET  /api/transactions/csv/template — returns valid CSV with expected headers
* POST /api/transactions/csv          — valid file imports correctly
* POST /api/transactions/csv          — partial success (good rows + bad rows)
* POST /api/transactions/csv          — fully malformed file
* POST /api/transactions/csv          — empty file / missing rows
* POST /api/transactions/csv          — missing required columns
* POST /api/transactions/csv          — non-CSV content type / bad data
* POST /api/transactions/csv          — duplicate / edge-case rows
* Imported rows appear in GET /api/transactions with source=csv
"""

from __future__ import annotations

import io
import os
import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _fixture(name: str) -> dict:
    """files dict for uploading a real fixture CSV from tests/fixtures/."""
    with open(os.path.join(FIXTURES_DIR, name), "rb") as fh:
        data = fh.read()
    return {"file": (name, io.BytesIO(data), "text/csv")}


def _make_csv(content: str, filename: str = "test.csv") -> dict:
    """Build a files dict for multipart CSV upload."""
    return {
        "file": (filename, io.BytesIO(content.encode("utf-8")), "text/csv")
    }


VALID_CSV = """\
date,amount,type,category,description
2024-03-01,100.00,expense,Groceries,Weekly shop
2024-03-05,5000.00,income,Salary,March salary
2024-03-10,50.00,expense,Transport,Bus pass
"""

VALID_CSV_NO_DESCRIPTION = """\
date,amount,type,category
2024-04-01,200.00,expense,Rent
2024-04-15,4500.00,income,Salary
"""

# Note: the backend stores abs(amount), so a negative amount row is IMPORTED
# (as its positive magnitude), NOT rejected. Bad-date, missing-date and
# non-numeric-amount rows are the genuine error rows here.
# Rows (data row numbering, header = row 1):
#   row 2: Groceries  -> imported
#   row 3: BADDATE     -> error (invalid date)
#   row 4: -50.00      -> imported as 50.00 (abs)
#   row 5: Bonus       -> imported
#   row 6: missing date-> error
#   row 7: notanumber  -> error (invalid amount)
# => imported = 3, skipped = 3
MIXED_CSV = """\
date,amount,type,category,description
2024-05-01,100.00,expense,Groceries,Good row
BADDATE,200.00,expense,Rent,Bad date
2024-05-03,-50.00,expense,Dining,Negative amount (stored as abs)
2024-05-04,75.00,income,Bonus,Good row
,100.00,expense,Rent,Missing date
2024-05-06,notanumber,expense,Dining,Bad amount format
"""

CASE_INSENSITIVE_HEADERS_CSV = """\
DATE,AMOUNT,TYPE,CATEGORY,DESCRIPTION
2024-06-01,300.00,expense,Groceries,Case test
"""

CSV_WITH_EXTRA_WHITESPACE = """\
date , amount , type , category , description
2024-07-01 , 100.00 , expense , Groceries , Whitespace test
"""


# ---------------------------------------------------------------------------
# GET /api/transactions/csv/template
# ---------------------------------------------------------------------------

class TestCSVTemplate:

    def test_template_returns_200(self, client):
        """Template endpoint returns 200."""
        resp = client.get("/api/transactions/csv/template")
        assert resp.status_code == 200

    def test_template_content_type(self, client):
        """Template returns text/csv content type."""
        resp = client.get("/api/transactions/csv/template")
        assert "text/csv" in resp.headers.get("content-type", "").lower()

    def test_template_has_required_headers(self, client):
        """Template CSV contains all required column headers."""
        resp = client.get("/api/transactions/csv/template")
        content = resp.text.lower()
        for col in ("date", "amount", "type", "category"):
            assert col in content, f"Expected column '{col}' in template"

    def test_template_is_downloadable(self, client):
        """Template response has content-disposition or is valid CSV text."""
        resp = client.get("/api/transactions/csv/template")
        # Either a download header or text that parses as CSV
        content_disposition = resp.headers.get("content-disposition", "")
        is_download = "attachment" in content_disposition or "filename" in content_disposition
        is_text_csv = resp.text.strip() != ""
        assert is_download or is_text_csv


# ---------------------------------------------------------------------------
# POST /api/transactions/csv — valid uploads
# ---------------------------------------------------------------------------

class TestCSVImportValid:

    def test_import_valid_csv(self, client):
        """All valid rows imported, no errors."""
        resp = client.post("/api/transactions/csv", files=_make_csv(VALID_CSV))
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 3
        assert data["skipped"] == 0
        assert data["errors"] == []

    def test_import_sets_source_csv(self, client):
        """Imported rows have source='csv' in the database."""
        client.post("/api/transactions/csv", files=_make_csv(VALID_CSV))
        transactions = client.get("/api/transactions").json()
        assert all(t["source"] == "csv" for t in transactions)

    def test_import_csv_no_description_column(self, client):
        """CSV without description column imports successfully."""
        resp = client.post(
            "/api/transactions/csv", files=_make_csv(VALID_CSV_NO_DESCRIPTION)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2
        assert data["errors"] == []

    def test_import_rows_appear_in_list(self, client):
        """Imported transactions are retrievable via GET /api/transactions."""
        client.post("/api/transactions/csv", files=_make_csv(VALID_CSV))
        transactions = client.get("/api/transactions").json()
        assert len(transactions) == 3
        categories = {t["category"] for t in transactions}
        assert "Groceries" in categories
        assert "Salary" in categories
        assert "Transport" in categories

    def test_import_case_insensitive_headers(self, client):
        """CSV with uppercase headers is handled correctly."""
        resp = client.post(
            "/api/transactions/csv",
            files=_make_csv(CASE_INSENSITIVE_HEADERS_CSV),
        )
        assert resp.status_code == 200
        assert resp.json()["imported"] >= 1

    def test_import_response_schema(self, client):
        """Response has keys: imported, skipped, transfers (v3), errors."""
        resp = client.post("/api/transactions/csv", files=_make_csv(VALID_CSV))
        data = resp.json()
        assert "imported" in data
        assert "skipped" in data
        assert "transfers" in data        # v3 addition
        assert "errors" in data
        assert isinstance(data["imported"], int)
        assert isinstance(data["skipped"], int)
        assert isinstance(data["transfers"], int)
        assert isinstance(data["errors"], list)

    def test_import_amounts_preserved(self, client):
        """Amount values from CSV are stored correctly."""
        client.post("/api/transactions/csv", files=_make_csv(VALID_CSV))
        transactions = client.get("/api/transactions").json()
        amounts = sorted(t["amount"] for t in transactions)
        assert amounts == pytest.approx(sorted([100.0, 5000.0, 50.0]))

    def test_import_types_preserved(self, client):
        """Income vs expense types from CSV are stored correctly."""
        client.post("/api/transactions/csv", files=_make_csv(VALID_CSV))
        transactions = client.get("/api/transactions").json()
        income_rows = [t for t in transactions if t["type"] == "income"]
        expense_rows = [t for t in transactions if t["type"] == "expense"]
        assert len(income_rows) == 1
        assert len(expense_rows) == 2


# ---------------------------------------------------------------------------
# POST /api/transactions/csv — error handling / partial import
# ---------------------------------------------------------------------------

class TestCSVImportErrors:

    def test_mixed_rows_partial_import(self, client):
        """Valid rows are imported; bad rows are reported in errors.

        3 importable rows (incl. the negative-amount row stored as abs) and
        3 genuinely bad rows (bad date, missing date, non-numeric amount).
        """
        resp = client.post("/api/transactions/csv", files=_make_csv(MIXED_CSV))
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 3
        assert data["skipped"] == 3
        assert len(data["errors"]) == 3

    def test_error_rows_have_row_number_and_reason(self, client):
        """Each error entry has 'row' (int) and 'reason' (str)."""
        resp = client.post("/api/transactions/csv", files=_make_csv(MIXED_CSV))
        data = resp.json()
        for err in data["errors"]:
            assert "row" in err, "Error entry missing 'row' field"
            assert "reason" in err, "Error entry missing 'reason' field"
            assert isinstance(err["row"], int)
            assert isinstance(err["reason"], str)
            assert len(err["reason"]) > 0

    def test_bad_date_reported(self, client):
        """Row with bad date format is reported as an error."""
        bad_date_csv = "date,amount,type,category\nBADDATE,100.00,expense,Groceries\n"
        resp = client.post("/api/transactions/csv", files=_make_csv(bad_date_csv))
        data = resp.json()
        assert data["imported"] == 0
        assert len(data["errors"]) == 1

    def test_negative_amount_stored_as_magnitude(self, client):
        """Row with negative amount is imported as its positive magnitude.

        Per the contract, amount is 'always positive magnitude'; the backend
        applies abs() to CSV amounts rather than rejecting negatives. So a
        -100.00 row imports successfully and is stored as 100.00.
        """
        neg_amount_csv = "date,amount,type,category\n2024-01-01,-100.00,expense,Rent\n"
        resp = client.post("/api/transactions/csv", files=_make_csv(neg_amount_csv))
        data = resp.json()
        assert data["imported"] == 1
        assert len(data["errors"]) == 0
        # Verify it was stored as the positive magnitude
        txs = client.get("/api/transactions").json()
        assert txs[0]["amount"] == pytest.approx(100.0)

    def test_non_numeric_amount_reported(self, client):
        """Row with non-numeric amount is reported as an error."""
        bad_amount_csv = "date,amount,type,category\n2024-01-01,abc,expense,Rent\n"
        resp = client.post("/api/transactions/csv", files=_make_csv(bad_amount_csv))
        data = resp.json()
        assert data["imported"] == 0
        assert len(data["errors"]) >= 1

    def test_missing_date_reported(self, client):
        """Row with empty date is reported as an error."""
        missing_date_csv = "date,amount,type,category\n,100.00,expense,Rent\n"
        resp = client.post("/api/transactions/csv", files=_make_csv(missing_date_csv))
        data = resp.json()
        assert data["imported"] == 0
        assert len(data["errors"]) >= 1

    def test_unrecognized_type_value_falls_back_to_sign(self, client):
        """An UNRECOGNIZED type-column value falls back to sign inference.

        Per the v3 contract, a recognized direction value wins; an unrecognized
        one does NOT error — it falls back to sign/token inference. A positive
        single-amount row with an unknown type value => expense.
        """
        bad_type_csv = "date,amount,type,category\n2024-01-01,100.00,frobnicate,Rent\n"
        resp = client.post("/api/transactions/csv", files=_make_csv(bad_type_csv))
        data = resp.json()
        assert data["imported"] == 1
        txs = client.get("/api/transactions").json()
        assert txs[0]["type"] == "expense"

    def test_explicit_transfer_type_value_passes_through(self, client):
        """A recognized 'transfer' type-column value is honored over sign."""
        csv_content = "date,amount,type,category\n2024-01-01,100.00,transfer,Moving money\n"
        resp = client.post("/api/transactions/csv", files=_make_csv(csv_content))
        assert resp.json()["imported"] == 1
        txs = client.get("/api/transactions").json()
        assert txs[0]["type"] == "transfer"

    def test_empty_csv_body(self, client):
        """Uploading an empty file is rejected gracefully.

        An empty file has no header row, so the backend returns 400
        ('CSV has no headers') rather than silently importing nothing.
        It must not 500 and must not import anything.
        """
        resp = client.post("/api/transactions/csv", files=_make_csv(""))
        assert resp.status_code == 400
        # Nothing should have been imported
        assert client.get("/api/transactions").json() == []

    def test_headers_only_csv(self, client):
        """CSV with only headers and no data rows: 0 imported."""
        headers_only = "date,amount,type,category,description\n"
        resp = client.post(
            "/api/transactions/csv", files=_make_csv(headers_only)
        )
        assert resp.status_code == 200
        assert resp.json()["imported"] == 0

    def test_missing_category_column_defaults_uncategorized(self, client):
        """v3: 'category' is OPTIONAL — a missing category column imports fine.

        Only date + amount (or debit/credit) are required. With no category
        column, rows import with category 'Uncategorized'.
        """
        no_category_csv = "date,amount,type\n2024-01-01,100.00,expense\n"
        resp = client.post(
            "/api/transactions/csv", files=_make_csv(no_category_csv)
        )
        assert resp.status_code == 200
        assert resp.json()["imported"] == 1
        txs = client.get("/api/transactions").json()
        assert txs[0]["category"] == "Uncategorized"

    def test_missing_required_column_amount(self, client):
        """CSV missing the required 'amount'/'debit'/'credit' column → 400 fail-fast."""
        no_amount_csv = "date,type,category\n2024-01-01,expense,Groceries\n"
        resp = client.post(
            "/api/transactions/csv", files=_make_csv(no_amount_csv)
        )
        assert resp.status_code in (400, 422)
        assert client.get("/api/transactions").json() == []

    def test_import_does_not_corrupt_existing_data(self, seeded_client):
        """CSV import does not delete or modify existing manual transactions."""
        client, created = seeded_client
        original_count = len(created)
        client.post("/api/transactions/csv", files=_make_csv(VALID_CSV))
        all_txs = client.get("/api/transactions").json()
        manual_txs = [t for t in all_txs if t["source"] == "manual"]
        assert len(manual_txs) == original_count

    def test_row_numbers_in_errors_are_accurate(self, client):
        """Error row numbers match spreadsheet row positions (header = row 1).

        The backend numbers rows starting at 2 for the first DATA row (the
        header occupies row 1), matching how a spreadsheet displays the file.
        So the 2nd data line below is reported as row 3.
        """
        csv_content = (
            "date,amount,type,category\n"          # row 1 (header)
            "2024-01-01,100.00,expense,Groceries\n"  # row 2 (valid)
            "BADDATE,200.00,expense,Rent\n"          # row 3 (bad date)
        )
        resp = client.post("/api/transactions/csv", files=_make_csv(csv_content))
        data = resp.json()
        assert data["imported"] == 1
        error_rows = [e["row"] for e in data["errors"]]
        # The bad row is the 2nd data line => spreadsheet row 3
        assert 3 in error_rows


# ---------------------------------------------------------------------------
# v3 — Bank-agnostic CSV import
# ---------------------------------------------------------------------------

class TestDiscoverFixtureImport:
    """The real Discover export fixture (tests/fixtures/discover_sample.csv)."""

    def test_discover_import_counts(self, client):
        """imported:15, skipped:0, transfers:2 per the contract reference."""
        resp = client.post("/api/transactions/csv", files=_fixture("discover_sample.csv"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 15
        assert data["skipped"] == 0
        assert data["transfers"] == 2
        assert data["errors"] == []

    def test_discover_exactly_two_transfers_stored(self, client):
        """Exactly 2 rows are stored as transfer — the INTERNET PAYMENT rows."""
        client.post("/api/transactions/csv", files=_fixture("discover_sample.csv"))
        transfers = client.get("/api/transactions?type=transfer").json()
        assert len(transfers) == 2
        for t in transfers:
            assert "INTERNET PAYMENT" in (t["description"] or "").upper()
        # And 13 expenses
        expenses = client.get("/api/transactions?type=expense").json()
        assert len(expenses) == 13

    def test_discover_dates_parsed_to_iso(self, client):
        """MM/DD/YYYY Discover dates are stored as ISO YYYY-MM-DD."""
        client.post("/api/transactions/csv", files=_fixture("discover_sample.csv"))
        txs = client.get("/api/transactions").json()
        for t in txs:
            # ISO format: YYYY-MM-DD
            assert len(t["date"]) == 10 and t["date"][4] == "-" and t["date"][7] == "-"

    def test_discover_by_category_excludes_payments(self, client):
        """by-category after import has Restaurants/Merchandise, NOT 'Payments and Credits'."""
        client.post("/api/transactions/csv", files=_fixture("discover_sample.csv"))
        items = client.get("/api/stats/by-category").json()
        cats = {i["category"] for i in items}
        assert "Restaurants" in cats
        assert "Merchandise" in cats
        assert "Payments and Credits" not in cats

    def test_discover_payments_dont_affect_expense_total(self, client):
        """The two −payment rows (233.99 + 69.77) are excluded from expense totals."""
        client.post("/api/transactions/csv", files=_fixture("discover_sample.csv"))
        summary = client.get("/api/stats/summary").json()
        # Sum of the 13 expense magnitudes in the fixture:
        # 16.04+10.22+19.76+19.53+5.59+11.93+3.57+13.08+92.81+31.61+21.66+4.23+46.87
        expected_expense = (
            16.04 + 10.22 + 19.76 + 19.53 + 5.59 + 11.93 + 3.57
            + 13.08 + 92.81 + 31.61 + 21.66 + 4.23 + 46.87
        )
        assert summary["total_expense"] == pytest.approx(expected_expense, abs=0.01)
        # Payments must NOT show up as income either
        assert summary["total_income"] == pytest.approx(0.0)
        # by-category expense total equals summary expense total (no payment leakage)
        cat_total = sum(i["total"] for i in client.get("/api/stats/by-category").json())
        assert cat_total == pytest.approx(expected_expense, abs=0.01)


class TestBankFormatVariants:
    """Inline fixtures exercising the bank-agnostic header/amount/type handling."""

    def test_trans_date_alias_single_signed_amount(self, client):
        """`Trans. Date` header (date alias) + single signed Amount column.

        Credit-card convention: positive => expense, negative => transfer (credit).
        """
        csv_content = (
            "Trans. Date,Description,Amount\n"
            "01/05/2024,COFFEE SHOP,4.50\n"        # positive => expense
            "01/06/2024,REFUND CREDIT,-12.00\n"     # negative, non-payment => transfer
        )
        resp = client.post("/api/transactions/csv", files=_make_csv(csv_content))
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2
        assert data["transfers"] == 1   # the negative refund row
        txs = client.get("/api/transactions").json()
        by_amt = {round(t["amount"], 2): t for t in txs}
        assert by_amt[4.50]["type"] == "expense"
        assert by_amt[12.00]["type"] == "transfer"   # stored as positive magnitude

    def test_split_debit_credit_columns(self, client):
        """Capital One style: separate Debit / Credit columns.

        Debit (money out) => expense; Credit (money in) => income.
        """
        csv_content = (
            "Transaction Date,Description,Debit,Credit\n"
            "2024-01-10,GROCERY STORE,53.20,\n"     # debit => expense
            "2024-01-12,PAYROLL DEPOSIT,,2500.00\n"  # credit => income
        )
        resp = client.post("/api/transactions/csv", files=_make_csv(csv_content))
        assert resp.status_code == 200
        assert resp.json()["imported"] == 2
        txs = client.get("/api/transactions").json()
        by_amt = {round(t["amount"], 2): t for t in txs}
        assert by_amt[53.20]["type"] == "expense"
        assert by_amt[2500.00]["type"] == "income"

    def test_bank_type_column_dr_cr_payment(self, client):
        """Bank `Type` column values DEBIT/CREDIT/PAYMENT map to expense/income/transfer."""
        csv_content = (
            "Date,Description,Amount,Type\n"
            "2024-02-01,STORE PURCHASE,40.00,DEBIT\n"      # => expense
            "2024-02-02,SALARY,3000.00,CREDIT\n"           # => income
            "2024-02-03,CARD PAYMENT,500.00,PAYMENT\n"     # => transfer
        )
        resp = client.post("/api/transactions/csv", files=_make_csv(csv_content))
        assert resp.status_code == 200
        assert resp.json()["imported"] == 3
        txs = client.get("/api/transactions").json()
        type_by_amt = {round(t["amount"], 2): t["type"] for t in txs}
        assert type_by_amt[40.00] == "expense"
        assert type_by_amt[3000.00] == "income"
        assert type_by_amt[500.00] == "transfer"

    def test_type_column_value_wins_over_sign(self, client):
        """An explicit recognized direction value beats sign inference."""
        # Positive amount that would be 'expense' by sign, but Type=CREDIT => income
        csv_content = (
            "Date,Description,Amount,Type\n"
            "2024-02-01,WEIRD CASE,100.00,CREDIT\n"
        )
        resp = client.post("/api/transactions/csv", files=_make_csv(csv_content))
        assert resp.json()["imported"] == 1
        assert client.get("/api/transactions").json()[0]["type"] == "income"


class TestFailFastMissingColumns:
    """Fail-fast 400 when a required date/amount column can't be resolved."""

    def test_no_recognizable_date_column(self, client):
        """No date alias resolves => 400 naming the missing field + headers seen."""
        csv_content = (
            "Memo,Amount\n"            # no date-like header at all
            "COFFEE,4.50\n"
        )
        resp = client.post("/api/transactions/csv", files=_make_csv(csv_content))
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "date" in detail.lower()
        # lists the headers actually seen so the user knows what to add
        assert "Memo" in detail and "Amount" in detail
        assert client.get("/api/transactions").json() == []

    def test_no_recognizable_amount_column(self, client):
        """A date but no amount/debit/credit => 400 naming amount + headers seen."""
        csv_content = (
            "Date,Description\n"
            "2024-01-01,COFFEE\n"
        )
        resp = client.post("/api/transactions/csv", files=_make_csv(csv_content))
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "amount" in detail.lower()
        assert "Date" in detail and "Description" in detail
        assert client.get("/api/transactions").json() == []


# ---------------------------------------------------------------------------
# v5 — needs_review flagging (debit_sample.csv regression guard)
# ---------------------------------------------------------------------------

class TestNeedsReviewImport:
    """v5/v5.1: ambiguous + brokerage rows are flagged needs_review; clean ones are not.

    v5.1: the ROBINHOOD brokerage-deposit row is ALSO flagged (defaulted to
    transfer with a distinct "Brokerage:" review_reason), so the flagged count
    is 7 (6 ambiguous-token rows + 1 brokerage), and unflagged is 14 - 7 = 7.
    """

    # Ambiguous-token rows (venmo/zelle/atm/check/cash-deposit) flagged with a
    # token-based review_reason.
    AMBIGUOUS_DESCRIPTIONS = {
        "VENMO PAYMENT JOHN DOE",
        "ZELLE TRANSFER TO JANE",
        "ATM WITHDRAWAL MAIN ST",
        "CASH DEPOSIT BRANCH 14",
        "CHECK 1042",
        "VENMO CASHOUT TO BANK",
    }
    # v5.1 brokerage row — flagged with a distinct "Brokerage:" reason.
    BROKERAGE_DESCRIPTION = "ROBINHOOD BROKERAGE DEPOSIT"
    # All rows expected to be flagged needs_review.
    FLAGGED_DESCRIPTIONS = AMBIGUOUS_DESCRIPTIONS | {BROKERAGE_DESCRIPTION}
    # Clean rows (NOT flagged).
    CLEAN_DESCRIPTIONS = {
        "EMPLOYER DIRECT DEPOSIT PAYROLL",
        "NATIONAL GRID UTILITIES BILL",
        "COMCAST INTERNET",
        "WHOLE FOODS MARKET",
        "ACME RENT MANAGEMENT",
    }

    def test_import_response_has_needs_review(self, client):
        """The import response includes a needs_review count (v5.1: 7)."""
        resp = client.post("/api/transactions/csv", files=_fixture("debit_sample.csv"))
        assert resp.status_code == 200
        data = resp.json()
        assert "needs_review" in data
        assert isinstance(data["needs_review"], int)
        assert data["imported"] == 14
        # v5.1: 6 ambiguous-token rows + 1 brokerage (ROBINHOOD) = 7
        assert data["needs_review"] == 7

    def test_ambiguous_rows_flagged(self, client):
        """Venmo/Zelle/ATM/check/cash-deposit rows are flagged with a reason."""
        client.post("/api/transactions/csv", files=_fixture("debit_sample.csv"))
        txs = client.get("/api/transactions?limit=100").json()
        by_desc = {t["description"]: t for t in txs}
        for desc in self.AMBIGUOUS_DESCRIPTIONS:
            assert by_desc[desc]["needs_review"] is True, f"{desc} should be flagged"
            assert by_desc[desc]["review_reason"], f"{desc} should have a review_reason"

    def test_brokerage_row_flagged(self, client):
        """v5.1: the ROBINHOOD brokerage row is flagged with a 'Brokerage:' reason
        and defaulted to transfer (safe — never inflates spend)."""
        client.post("/api/transactions/csv", files=_fixture("debit_sample.csv"))
        txs = client.get("/api/transactions?limit=100").json()
        rob = next(t for t in txs if t["description"] == self.BROKERAGE_DESCRIPTION)
        assert rob["needs_review"] is True
        assert rob["type"] == "transfer"
        assert rob["review_reason"].startswith("Brokerage:")

    def test_unambiguous_rows_not_flagged(self, client):
        """Paycheck/utilities/groceries/rent are NOT flagged."""
        client.post("/api/transactions/csv", files=_fixture("debit_sample.csv"))
        txs = client.get("/api/transactions?limit=100").json()
        by_desc = {t["description"]: t for t in txs}
        for desc in self.CLEAN_DESCRIPTIONS:
            assert by_desc[desc]["needs_review"] is False, f"{desc} should NOT be flagged"
            assert by_desc[desc]["review_reason"] is None

    def test_review_reason_names_token(self, client):
        """review_reason references the ambiguous token (e.g. 'venmo', 'atm')."""
        client.post("/api/transactions/csv", files=_fixture("debit_sample.csv"))
        txs = client.get("/api/transactions?limit=100").json()
        by_desc = {t["description"]: t for t in txs}
        assert "venmo" in by_desc["VENMO PAYMENT JOHN DOE"]["review_reason"].lower()
        assert "atm" in by_desc["ATM WITHDRAWAL MAIN ST"]["review_reason"].lower()

    def test_filter_needs_review_true(self, client):
        """GET /api/transactions?needs_review=true returns exactly the flagged rows."""
        client.post("/api/transactions/csv", files=_fixture("debit_sample.csv"))
        flagged = client.get("/api/transactions?needs_review=true&limit=100").json()
        assert len(flagged) == 7      # v5.1: 6 ambiguous + 1 brokerage
        assert all(t["needs_review"] is True for t in flagged)
        assert {t["description"] for t in flagged} == self.FLAGGED_DESCRIPTIONS

    def test_filter_needs_review_false(self, client):
        """needs_review=false returns the unflagged rows (14 - 7 = 7)."""
        client.post("/api/transactions/csv", files=_fixture("debit_sample.csv"))
        unflagged = client.get("/api/transactions?needs_review=false&limit=100").json()
        assert len(unflagged) == 7
        assert all(t["needs_review"] is False for t in unflagged)

    def test_debit_sample_all_imported(self, client):
        """The debit fixture imports cleanly with no skipped/error rows."""
        resp = client.post("/api/transactions/csv", files=_fixture("debit_sample.csv"))
        data = resp.json()
        assert data["imported"] == 14
        assert data["skipped"] == 0
        assert data["errors"] == []

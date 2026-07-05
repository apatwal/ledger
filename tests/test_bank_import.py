"""
test_bank_import.py — v5.3 bank/checking statement support.

Covers:
  * Preamble skipping — the BofA summary block is skipped and the real header
    (Date,Description,Amount,Running Bal.) is used (no 400).
  * statement_type=bank sign convention — − => expense, + => income (paychecks
    import as income, not expense); bank-side card payments => transfer; SCHWAB
    => transfer w/ "Brokerage:" review; Venmo/Zelle => needs_review.
  * statement_type=card (default) is unchanged — the credit-card fixtures still
    classify as before.
  * Preamble scanner still yields the specific 'amount'/'date' 400 errors.
  * GET /api/imports records statement_type for a bank import.
"""

from __future__ import annotations

import io
import pytest

from .conftest import fixture_csv_file


def _import(client, name, *, statement_type=None, account=None):
    data = {}
    if statement_type is not None:
        data["statement_type"] = statement_type
    if account is not None:
        data["account"] = account
    resp = client.post("/api/transactions/csv", files=fixture_csv_file(name),
                       data=data or None)
    return resp


def _by_desc(client):
    txs = client.get("/api/transactions?limit=200").json()
    return {t["description"]: t for t in txs}


def _find(client, needle):
    txs = client.get("/api/transactions?limit=200").json()
    return next(t for t in txs if needle in t["description"])


def _csv(content: str, filename="bank.csv") -> dict:
    return {"file": (filename, io.BytesIO(content.encode("utf-8")), "text/csv")}


# ---------------------------------------------------------------------------
# Preamble skipping
# ---------------------------------------------------------------------------

class TestPreambleSkip:

    def test_bofa_import_succeeds_no_400(self, client):
        """The summary preamble is skipped and the real header is used."""
        resp = _import(client, "bofa_checking_sample.csv", statement_type="bank")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["imported"] == 12
        assert data["skipped"] == 0
        assert data["errors"] == []

    def test_preamble_rows_not_imported(self, client):
        """No 'Beginning balance'/'Total credits' summary rows become transactions."""
        _import(client, "bofa_checking_sample.csv", statement_type="bank")
        descs = [t["description"] or "" for t in client.get("/api/transactions?limit=200").json()]
        assert not any("Beginning balance" in d for d in descs)
        assert not any("Total credits" in d for d in descs)
        assert not any("Summary Amt" in d for d in descs)

    def test_inline_preamble_then_real_header(self, client):
        """A generic summary block before the real header is skipped."""
        content = (
            "Account Summary,,\n"
            "Beginning balance,,100.00\n"
            "\n"
            "Date,Description,Amount,Running Bal.\n"
            "2026-01-05,COFFEE SHOP,-4.50,95.50\n"
        )
        resp = client.post("/api/transactions/csv",
                           files=_csv(content), data={"statement_type": "bank"})
        assert resp.status_code == 200
        assert resp.json()["imported"] == 1


# ---------------------------------------------------------------------------
# statement_type = bank : sign + classification
# ---------------------------------------------------------------------------

class TestBankStatementClassification:

    def _import_bank(self, client):
        resp = _import(client, "bofa_checking_sample.csv", statement_type="bank",
                       account="BofA Checking")
        assert resp.status_code == 200
        return resp.json()

    def test_payroll_is_income_not_expense(self, client):
        """A positive PAYROLL deposit imports as income (the v5.3 fix)."""
        self._import_bank(client)
        payroll = [t for t in client.get("/api/transactions?limit=200").json()
                   if "PAYROLL" in t["description"]]
        assert len(payroll) == 2
        assert all(t["type"] == "income" for t in payroll)

    def test_utilities_groceries_gas_are_expense(self, client):
        """Negative outflows import as expense under bank convention."""
        self._import_bank(client)
        for needle in ("EVERSOURCE", "COMCAST", "STOP AND SHOP", "SHELL GAS", "NATIONAL GRID"):
            row = _find(client, needle)
            assert row["type"] == "expense", f"{needle} should be expense, got {row['type']}"

    def test_bank_side_card_payments_are_transfer(self, client):
        """DISCOVER E-PAYMENT and CHASE CREDIT CRD (paying a card) => transfer."""
        self._import_bank(client)
        discover = _find(client, "DISCOVER DES:E-PAYMENT")
        chase = _find(client, "CHASE CREDIT CRD")
        assert discover["type"] == "transfer"
        assert chase["type"] == "transfer"

    def test_schwab_is_transfer_with_brokerage_review(self, client):
        """SCHWAB brokerage move => transfer, flagged with a 'Brokerage:' reason."""
        self._import_bank(client)
        schwab = _find(client, "SCHWAB")
        assert schwab["type"] == "transfer"
        assert schwab["needs_review"] is True
        assert schwab["review_reason"].startswith("Brokerage:")

    def test_venmo_zelle_pass_through_transfer_and_review(self, client):
        """v5.4: Venmo / Zelle DEFAULT to transfer (pass-through) but stay flagged."""
        self._import_bank(client)
        venmo = _find(client, "VENMO")
        zelle = _find(client, "ZELLE")
        # v5.4: P2P defaults to transfer (excluded), still needs_review to reclassify
        assert venmo["type"] == "transfer"
        assert venmo["needs_review"] is True
        assert venmo["review_reason"].startswith("Assumed pass-through transfer")
        assert "venmo" in venmo["review_reason"].lower()
        assert zelle["type"] == "transfer"
        assert zelle["needs_review"] is True
        assert zelle["review_reason"].startswith("Assumed pass-through transfer")
        assert "zelle" in zelle["review_reason"].lower()

    def test_card_payments_excluded_from_spending(self, client):
        """Card payments AND P2P pass-throughs (transfers) don't inflate spend."""
        self._import_bank(client)
        summary = client.get("/api/stats/summary").json()
        # v5.4 expense = utilities(142.55+89.99+108.30) + groceries 76.40 + gas 41.20
        #   = 458.44. Zelle 120 is now a pass-through transfer (excluded), not expense.
        # Excluded transfers: 233.99 + 450.00 card payments, 600 SCHWAB, 120 Zelle, 320 Venmo.
        assert summary["total_expense"] == pytest.approx(458.44, abs=0.01)
        # income = 3200 + 100 payroll = 3300 (Venmo cashout 320 is now transfer)
        assert summary["total_income"] == pytest.approx(3300.0, abs=0.01)

    def test_bank_transfers_count(self, client):
        """v5.4: 5 transfers auto-classified — 2 card payments + 1 brokerage (SCHWAB)
        + 2 P2P pass-throughs (Venmo + Zelle now default to transfer)."""
        data = self._import_bank(client)
        assert data["transfers"] == 5


# ---------------------------------------------------------------------------
# statement_type = card (default) — unchanged behavior
# ---------------------------------------------------------------------------

class TestCardStatementUnchanged:

    def test_discover_card_default(self, client):
        """Default (no statement_type) — Discover fixture unchanged: 15/2."""
        resp = _import(client, "discover_sample.csv", account="Discover")
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 15
        assert data["transfers"] == 2

    def test_discover_explicit_card(self, client):
        """Explicit statement_type=card matches the default."""
        resp = _import(client, "discover_sample.csv", statement_type="card")
        assert resp.status_code == 200
        assert resp.json()["imported"] == 15
        assert resp.json()["transfers"] == 2

    def test_chase_card_default(self, client):
        """Chase under card convention: 157 imported, 13 transfers (v5.4).

        v5.4: the 11 Chase `Return` rows are now `refund` (net against spend),
        no longer counted as transfers, so transfers drop 24 → 13.
        """
        resp = _import(client, "chase_sample.csv")
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 157
        assert data["transfers"] == 13

    def test_card_positive_is_expense(self, client):
        """Under card convention a positive single amount is an expense (not income)."""
        content = "Date,Description,Amount\n2026-01-01,STORE PURCHASE,25.00\n"
        client.post("/api/transactions/csv", files=_csv(content), data={"statement_type": "card"})
        tx = client.get("/api/transactions").json()[0]
        assert tx["type"] == "expense"

    def test_bank_positive_is_income(self, client):
        """Same positive row under bank convention is income — the sign flip."""
        content = "Date,Description,Amount\n2026-01-01,DIRECT DEPOSIT,25.00\n"
        client.post("/api/transactions/csv", files=_csv(content), data={"statement_type": "bank"})
        tx = client.get("/api/transactions").json()[0]
        assert tx["type"] == "income"


# ---------------------------------------------------------------------------
# Error cases — specific 'amount' / 'date' 400s survive the preamble scanner
# ---------------------------------------------------------------------------

class TestErrorsAfterPreambleScan:

    def test_date_but_no_amount_400(self, client):
        """A date column but no amount/debit/credit => specific 'amount' 400."""
        content = "Date,Description\n2026-01-01,COFFEE\n"
        resp = client.post("/api/transactions/csv", files=_csv(content),
                           data={"statement_type": "bank"})
        assert resp.status_code == 400
        assert "amount" in resp.json()["detail"].lower()
        assert client.get("/api/transactions").json() == []

    def test_neither_date_nor_amount_400(self, client):
        """No date alias anywhere => the 'date' 400."""
        content = "Memo,Notes\nCOFFEE,nice\n"
        resp = client.post("/api/transactions/csv", files=_csv(content),
                           data={"statement_type": "bank"})
        assert resp.status_code == 400
        assert "date" in resp.json()["detail"].lower()
        assert client.get("/api/transactions").json() == []

    def test_preamble_only_no_real_header_400(self, client):
        """A file that is ALL preamble (no real header) fails as before."""
        content = (
            "Description,,Summary Amt.\n"
            "Beginning balance,,100.00\n"
            "Total credits,,50.00\n"
        )
        resp = client.post("/api/transactions/csv", files=_csv(content),
                           data={"statement_type": "bank"})
        assert resp.status_code == 400
        assert client.get("/api/transactions").json() == []


# ---------------------------------------------------------------------------
# statement_type recorded on the import batch
# ---------------------------------------------------------------------------

class TestImportBatchStatementType:

    def test_bank_import_records_statement_type(self, client):
        """GET /api/imports includes statement_type='bank' for a bank import."""
        _import(client, "bofa_checking_sample.csv", statement_type="bank",
                account="BofA Checking")
        batches = client.get("/api/imports").json()
        assert len(batches) == 1
        assert batches[0]["statement_type"] == "bank"
        assert batches[0]["filename"] == "bofa_checking_sample.csv"
        assert batches[0]["account"] == "BofA Checking"

    def test_card_import_records_statement_type(self, client):
        """A default/card import records statement_type='card'."""
        _import(client, "discover_sample.csv", account="Discover")
        batch = client.get("/api/imports").json()[0]
        assert batch["statement_type"] == "card"

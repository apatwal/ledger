"""
test_rules.py — v5 user rules engine: CRUD, validation, apply ordering,
override-during-import, /rules/apply, /rules/preview.

Contract: a user rule runs BEFORE built-in inference and overrides it. The first
matching ENABLED rule (priority asc, then id asc) wins. Non-null rule actions
(set_type/set_category/set_account) override; null actions keep the inference.
"""

from __future__ import annotations

import io
import pytest


def _rule(**kwargs):
    """Build a RuleCreate body with sensible defaults."""
    body = {
        "match_field": "description",
        "match_op": "contains",
        "match_value": "x",
    }
    body.update(kwargs)
    return body


def _post_rule(client, **kwargs):
    r = client.post("/api/rules", json=_rule(**kwargs))
    assert r.status_code == 201, r.text
    return r.json()


def _post_tx(client, **kwargs):
    defaults = {"source": "manual"}
    defaults.update(kwargs)
    r = client.post("/api/transactions", json=defaults)
    assert r.status_code == 201, r.text
    return r.json()


def _csv(content: str, filename: str = "rules.csv") -> dict:
    return {"file": (filename, io.BytesIO(content.encode("utf-8")), "text/csv")}


# ---------------------------------------------------------------------------
# Rule CRUD + validation
# ---------------------------------------------------------------------------

class TestRuleCRUD:

    def test_create_rule(self, client):
        rule = _post_rule(client, name="Robinhood->transfer", match_value="ROBINHOOD",
                          set_type="transfer", priority=10)
        assert rule["id"] is not None
        assert rule["match_value"] == "ROBINHOOD"
        assert rule["set_type"] == "transfer"
        assert rule["priority"] == 10
        assert rule["enabled"] is True            # default

    def test_create_rule_defaults(self, client):
        """priority defaults to 100, enabled to true."""
        rule = _post_rule(client, match_value="VENMO")
        assert rule["priority"] == 100
        assert rule["enabled"] is True
        assert rule["set_type"] is None           # null actions allowed

    def test_get_rule(self, client):
        created = _post_rule(client, match_value="ZELLE")
        resp = client.get(f"/api/rules/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["match_value"] == "ZELLE"

    def test_get_rule_404(self, client):
        assert client.get("/api/rules/999999").status_code == 404

    def test_list_rules_priority_asc(self, client):
        _post_rule(client, match_value="A", priority=50)
        _post_rule(client, match_value="B", priority=10)
        _post_rule(client, match_value="C", priority=30)
        rules = client.get("/api/rules").json()
        priorities = [r["priority"] for r in rules]
        assert priorities == sorted(priorities)   # ascending

    def test_list_rules_enabled_filter(self, client):
        _post_rule(client, match_value="ON", enabled=True)
        _post_rule(client, match_value="OFF", enabled=False)
        enabled = client.get("/api/rules?enabled=true").json()
        assert all(r["enabled"] for r in enabled)
        disabled = client.get("/api/rules?enabled=false").json()
        assert all(not r["enabled"] for r in disabled)

    def test_update_rule(self, client):
        created = _post_rule(client, match_value="OLD", priority=100)
        resp = client.put(f"/api/rules/{created['id']}", json={
            "match_value": "NEW", "priority": 5, "set_category": "Investment",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["match_value"] == "NEW"
        assert data["priority"] == 5
        assert data["set_category"] == "Investment"

    def test_update_rule_partial_keeps_other_fields(self, client):
        created = _post_rule(client, match_value="KEEP", set_type="transfer", priority=20)
        # only flip enabled; match_value/set_type/priority must persist
        resp = client.put(f"/api/rules/{created['id']}", json={"enabled": False})
        data = resp.json()
        assert data["enabled"] is False
        assert data["match_value"] == "KEEP"
        assert data["set_type"] == "transfer"
        assert data["priority"] == 20

    def test_update_rule_404(self, client):
        assert client.put("/api/rules/999999", json={"enabled": False}).status_code == 404

    def test_delete_rule(self, client):
        created = _post_rule(client, match_value="DELME")
        assert client.delete(f"/api/rules/{created['id']}").status_code == 204
        assert client.get(f"/api/rules/{created['id']}").status_code == 404

    def test_delete_rule_404(self, client):
        assert client.delete("/api/rules/999999").status_code == 404

    # --- validation (422) ---

    def test_create_empty_match_value(self, client):
        resp = client.post("/api/rules", json=_rule(match_value="   "))
        assert resp.status_code == 422

    def test_create_invalid_match_field(self, client):
        resp = client.post("/api/rules", json=_rule(match_field="memo"))
        assert resp.status_code == 422

    def test_create_invalid_match_op(self, client):
        resp = client.post("/api/rules", json=_rule(match_op="startswith"))
        assert resp.status_code == 422

    def test_create_invalid_set_type(self, client):
        resp = client.post("/api/rules", json=_rule(set_type="savings"))
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# apply_rules ordering — first match by priority (lower wins), then id
# ---------------------------------------------------------------------------

class TestRuleOrdering:

    def test_lower_priority_wins(self, client):
        """Two rules match the same row; the lower-priority one applies."""
        _post_rule(client, match_value="COFFEE", set_category="Dining", priority=50)
        _post_rule(client, match_value="COFFEE", set_category="Treats", priority=10)  # wins
        tx = _post_tx(client, date="2026-01-01", amount=5.0, type="expense",
                      category="Uncategorized", description="STARBUCKS COFFEE")
        client.post("/api/rules/apply", json={})
        updated = client.get(f"/api/transactions/{tx['id']}").json()
        assert updated["category"] == "Treats"        # priority 10 beat priority 50

    def test_disabled_rule_skipped(self, client):
        """A disabled higher-priority rule is skipped; the enabled one wins."""
        _post_rule(client, match_value="GYM", set_category="ShouldNotApply",
                   priority=1, enabled=False)
        _post_rule(client, match_value="GYM", set_category="Health", priority=50)
        tx = _post_tx(client, date="2026-01-01", amount=30.0, type="expense",
                      category="Uncategorized", description="PLANET GYM")
        client.post("/api/rules/apply", json={})
        updated = client.get(f"/api/transactions/{tx['id']}").json()
        assert updated["category"] == "Health"

    def test_tie_priority_lower_id_wins(self, client):
        """Same priority -> the earlier-created (lower id) rule wins."""
        first = _post_rule(client, match_value="TAXI", set_category="First", priority=100)
        _post_rule(client, match_value="TAXI", set_category="Second", priority=100)
        tx = _post_tx(client, date="2026-01-01", amount=15.0, type="expense",
                      category="Uncategorized", description="YELLOW TAXI")
        client.post("/api/rules/apply", json={})
        updated = client.get(f"/api/transactions/{tx['id']}").json()
        assert updated["category"] == "First"
        assert first["id"] < client.get("/api/rules").json()[-1]["id"]


# ---------------------------------------------------------------------------
# Rule overriding built-in inference DURING CSV import
# ---------------------------------------------------------------------------

class TestRuleOverridesImport:

    def test_rule_makes_positive_row_a_transfer(self, client):
        """A ROBINHOOD->set_type=transfer rule overrides positive-amount expense inference."""
        _post_rule(client, name="brokerage", match_field="description",
                   match_op="contains", match_value="ROBINHOOD",
                   set_type="transfer", priority=10)
        # Single signed amount, positive => built-in would infer EXPENSE.
        csv_content = (
            "Date,Description,Amount\n"
            "2026-03-01,ROBINHOOD BROKERAGE DEPOSIT,500.00\n"
            "2026-03-02,WHOLE FOODS,80.00\n"
        )
        resp = client.post("/api/transactions/csv", files=_csv(csv_content))
        assert resp.status_code == 200
        txs = client.get("/api/transactions").json()
        by_desc = {t["description"]: t for t in txs}
        # rule overrode inference: positive amount is now a transfer
        assert by_desc["ROBINHOOD BROKERAGE DEPOSIT"]["type"] == "transfer"
        # the other row still follows built-in inference (positive => expense)
        assert by_desc["WHOLE FOODS"]["type"] == "expense"

    def test_rule_match_clears_needs_review_on_import(self, client):
        """A row that a rule classifies is NOT flagged needs_review (rule matched)."""
        _post_rule(client, match_field="description", match_op="contains",
                   match_value="VENMO", set_type="transfer", set_category="Transfers",
                   priority=10)
        csv_content = (
            "Date,Description,Amount\n"
            "2026-03-01,VENMO PAYMENT TO BOB,60.00\n"   # would be ambiguous w/o a rule
        )
        resp = client.post("/api/transactions/csv", files=_csv(csv_content))
        data = resp.json()
        # rule matched => not counted as needs_review
        assert data["needs_review"] == 0
        tx = client.get("/api/transactions").json()[0]
        assert tx["needs_review"] is False
        assert tx["type"] == "transfer"
        assert tx["category"] == "Transfers"

    def test_rule_set_category_overrides_keeps_inferred_type(self, client):
        """A rule with only set_category overrides category but keeps inferred type."""
        _post_rule(client, match_field="description", match_op="contains",
                   match_value="SHELL", set_category="Fuel", priority=10)
        csv_content = "Date,Description,Amount\n2026-03-01,SHELL GAS,40.00\n"
        client.post("/api/transactions/csv", files=_csv(csv_content))
        tx = client.get("/api/transactions").json()[0]
        assert tx["category"] == "Fuel"       # overridden
        assert tx["type"] == "expense"        # inferred (positive single amount)


# ---------------------------------------------------------------------------
# POST /api/rules/apply  &  POST /api/rules/preview
# ---------------------------------------------------------------------------

class TestApplyAndPreview:

    def _seed_three_robinhood(self, client):
        a = _post_tx(client, date="2026-01-01", amount=100.0, type="expense",
                     category="Uncategorized", description="ROBINHOOD DEPOSIT 1")
        b = _post_tx(client, date="2026-01-02", amount=200.0, type="expense",
                     category="Uncategorized", description="ROBINHOOD DEPOSIT 2")
        c = _post_tx(client, date="2026-01-03", amount=50.0, type="expense",
                     category="Groceries", description="TRADER JOES")
        return a, b, c

    def test_apply_returns_updated_count(self, client):
        """/rules/apply re-applies enabled rules and returns {updated: N}."""
        self._seed_three_robinhood(client)
        _post_rule(client, match_value="ROBINHOOD", set_type="transfer",
                   set_category="Brokerage", priority=10)
        resp = client.post("/api/rules/apply", json={})
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2     # the two ROBINHOOD rows
        # verify they were reclassified
        transfers = client.get("/api/transactions?type=transfer").json()
        assert len(transfers) == 2
        assert all(t["category"] == "Brokerage" for t in transfers)

    def test_apply_no_rules_updates_nothing(self, client):
        self._seed_three_robinhood(client)
        resp = client.post("/api/rules/apply", json={})
        assert resp.json()["updated"] == 0

    def test_apply_account_scope(self, client):
        """apply with account scope only touches that account's rows."""
        _post_tx(client, date="2026-01-01", amount=100.0, type="expense",
                 category="Uncategorized", description="ROBINHOOD", account="Chase")
        _post_tx(client, date="2026-01-02", amount=100.0, type="expense",
                 category="Uncategorized", description="ROBINHOOD", account="Schwab")
        _post_rule(client, match_value="ROBINHOOD", set_type="transfer", priority=10)
        resp = client.post("/api/rules/apply", json={"account": "Chase"})
        assert resp.json()["updated"] == 1
        chase = client.get("/api/transactions?account=Chase").json()[0]
        schwab = client.get("/api/transactions?account=Schwab").json()[0]
        assert chase["type"] == "transfer"
        assert schwab["type"] == "expense"      # untouched

    def test_apply_only_review_scope(self, client):
        """apply with only_review=true only touches needs_review rows."""
        # import an ambiguous row (gets needs_review) + a clean one
        csv_content = (
            "Date,Description,Category,Amount\n"
            "2026-03-01,ATM WITHDRAWAL,,-40.00\n"        # ambiguous => needs_review
            "2026-03-02,WHOLE FOODS,Groceries,-50.00\n"   # clean
        )
        client.post("/api/transactions/csv", files=_csv(csv_content))
        _post_rule(client, match_value="WHOLE FOODS", set_category="Food", priority=10)
        # only_review=true => the WHOLE FOODS row (not in review) is skipped
        resp = client.post("/api/rules/apply", json={"only_review": True})
        assert resp.json()["updated"] == 0     # WHOLE FOODS not in review set; ATM rule doesn't match

    def test_apply_clears_needs_review_when_rule_matches(self, client):
        """A rule that matches a needs_review row clears the flag."""
        csv_content = "Date,Description,Category,Amount\n2026-03-01,ZELLE TO SAM,,-30.00\n"
        client.post("/api/transactions/csv", files=_csv(csv_content))
        before = client.get("/api/transactions?needs_review=true").json()
        assert len(before) == 1
        _post_rule(client, match_value="ZELLE", set_type="transfer",
                   set_category="Transfers", priority=10)
        resp = client.post("/api/rules/apply", json={})
        assert resp.json()["updated"] == 1
        after = client.get("/api/transactions?needs_review=true").json()
        assert after == []                      # flag cleared

    def test_preview_returns_match_count(self, client):
        """/rules/preview counts existing txns a not-yet-saved rule would hit."""
        self._seed_three_robinhood(client)
        resp = client.post("/api/rules/preview", json=_rule(
            match_value="ROBINHOOD", set_type="transfer"))
        assert resp.status_code == 200
        assert resp.json()["matches"] == 2     # two ROBINHOOD rows

    def test_preview_does_not_mutate(self, client):
        """preview is read-only — it must not change any transaction."""
        a, b, c = self._seed_three_robinhood(client)
        client.post("/api/rules/preview", json=_rule(match_value="ROBINHOOD", set_type="transfer"))
        # all still expenses, no rule persisted
        assert client.get("/api/transactions?type=transfer").json() == []
        assert client.get("/api/rules").json() == []

    def test_preview_amount_range(self, client):
        """preview honors amount_min/amount_max."""
        self._seed_three_robinhood(client)   # amounts 100, 200, 50
        resp = client.post("/api/rules/preview", json=_rule(
            match_field="any", match_op="contains", match_value="ROBINHOOD",
            amount_min=150.0))
        # only the 200 row qualifies (100 is below min, TRADER JOES doesn't match text)
        assert resp.json()["matches"] == 1

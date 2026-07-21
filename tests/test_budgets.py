"""
test_budgets.py — v9 budgets: category spending limits, savings goals, and the
Assistant budget-creation path.

Discipline
----------
  * All DB work uses the per-test in-memory engine (`client` / `test_session`,
    which share `test_engine`). No network, no real DB.
  * Category-budget "spent" is the CURRENT CALENDAR MONTH net expense, so dates
    are computed relative to `date.today()` (prior-month rows must not count).
  * The Assistant path (`POST /api/assistant/budget`) mocks the Gemini seam:
    `ai.is_enabled` -> True and `ai.plan_budget` -> a canned plan. Gating (503)
    is verified by deleting GEMINI_API_KEY, exactly like test_assistant.py.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from src.api.models import CategoryBudget, SavingsGoal, PlaidItem
from src.api.routes.budgets import (
    create_category_budget,
    create_savings_goal,
    _goal_out,
    _account_balance,
)


# ── Date helpers (relative to the current calendar month) ─────────────────────

def _this_month_day(day: int = 15) -> str:
    """A date string in the CURRENT calendar month (day defaults to the 15th,
    always valid)."""
    return date.today().replace(day=day).isoformat()


def _prior_month_day() -> str:
    """Last day of the PREVIOUS calendar month."""
    first_of_this = date.today().replace(day=1)
    return (first_of_this - timedelta(days=1)).isoformat()


def _post_tx(client, **kwargs) -> dict:
    defaults = {"source": "manual"}
    defaults.update(kwargs)
    resp = client.post("/api/transactions", json=defaults)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_item_with_balance(session, app_account, current, item_id="item-1"):
    item = PlaidItem(
        item_id=item_id,
        access_token="access-sandbox-1",
        institution_name="Test Bank",
        accounts_json=json.dumps({"a1": {"app_account": app_account, "current": current}}),
        status="active",
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


# ═══════════════════════════════════════════════════════════════════════════
# 1. create_category_budget — upsert semantics (direct helper)
# ═══════════════════════════════════════════════════════════════════════════

class TestCreateCategoryBudget:

    def test_creates_row(self, test_session):
        b = create_category_budget(test_session, "Food & Drink", 400.0)
        assert b.id is not None
        assert b.category == "Food & Drink"
        assert b.limit_amount == pytest.approx(400.0)
        assert b.period == "monthly"

    def test_second_call_same_category_updates_not_duplicates(self, test_session):
        first = create_category_budget(test_session, "Food & Drink", 400.0)
        second = create_category_budget(test_session, "Food & Drink", 250.0)
        assert second.id == first.id                       # same row
        assert second.limit_amount == pytest.approx(250.0)  # updated
        assert test_session.query(CategoryBudget).count() == 1

    def test_distinct_categories_are_separate_rows(self, test_session):
        create_category_budget(test_session, "Food & Drink", 400.0)
        create_category_budget(test_session, "Travel", 1000.0)
        assert test_session.query(CategoryBudget).count() == 2


# ═══════════════════════════════════════════════════════════════════════════
# 2. GET /api/budgets/categories — computed spent/remaining/pct/over
# ═══════════════════════════════════════════════════════════════════════════

class TestCategoryBudgetProgress:

    def test_spent_is_net_of_refunds_current_month_only(self, client):
        # Limit 400 for Dining.
        assert client.post("/api/budgets/categories",
                           json={"category": "Dining", "limit_amount": 400.0}).status_code == 201
        # Current month: expense 300, refund 50 -> net spent 250.
        _post_tx(client, date=_this_month_day(), amount=300.0, type="expense", category="Dining")
        _post_tx(client, date=_this_month_day(), amount=50.0,  type="refund",  category="Dining")
        # Prior month expense 999 -> must NOT count.
        _post_tx(client, date=_prior_month_day(), amount=999.0, type="expense", category="Dining")

        rows = client.get("/api/budgets/categories").json()
        dining = next(r for r in rows if r["category"] == "Dining")
        assert dining["spent"] == pytest.approx(250.0)          # 300 - 50, prior month excluded
        assert dining["remaining"] == pytest.approx(150.0)      # 400 - 250
        assert dining["pct"] == pytest.approx(62.5)             # 250/400*100
        assert dining["over"] is False

    def test_over_budget_flags_and_negative_remaining(self, client):
        assert client.post("/api/budgets/categories",
                           json={"category": "Travel", "limit_amount": 100.0}).status_code == 201
        _post_tx(client, date=_this_month_day(), amount=150.0, type="expense", category="Travel")

        rows = client.get("/api/budgets/categories").json()
        travel = next(r for r in rows if r["category"] == "Travel")
        assert travel["spent"] == pytest.approx(150.0)
        assert travel["remaining"] == pytest.approx(-50.0)
        assert travel["pct"] == pytest.approx(150.0)
        assert travel["over"] is True

    def test_zero_spend(self, client):
        assert client.post("/api/budgets/categories",
                           json={"category": "Groceries", "limit_amount": 200.0}).status_code == 201
        rows = client.get("/api/budgets/categories").json()
        g = next(r for r in rows if r["category"] == "Groceries")
        assert g["spent"] == pytest.approx(0.0)
        assert g["remaining"] == pytest.approx(200.0)
        assert g["pct"] == pytest.approx(0.0)
        assert g["over"] is False


# ═══════════════════════════════════════════════════════════════════════════
# 3. Category-budget CRUD via the REST routes
# ═══════════════════════════════════════════════════════════════════════════

class TestCategoryBudgetCrud:

    def test_post_201(self, client):
        resp = client.post("/api/budgets/categories",
                           json={"category": "Dining", "limit_amount": 300.0})
        assert resp.status_code == 201
        assert resp.json()["category"] == "Dining"

    def test_put_updates(self, client):
        bid = client.post("/api/budgets/categories",
                          json={"category": "Dining", "limit_amount": 300.0}).json()["id"]
        resp = client.put(f"/api/budgets/categories/{bid}", json={"limit_amount": 500.0})
        assert resp.status_code == 200
        assert resp.json()["limit_amount"] == pytest.approx(500.0)

    def test_put_missing_404(self, client):
        assert client.put("/api/budgets/categories/99999",
                          json={"limit_amount": 1.0}).status_code == 404

    def test_delete_204(self, client):
        bid = client.post("/api/budgets/categories",
                          json={"category": "Dining", "limit_amount": 300.0}).json()["id"]
        assert client.delete(f"/api/budgets/categories/{bid}").status_code == 204
        assert client.get("/api/budgets/categories").json() == []

    def test_delete_missing_404(self, client):
        assert client.delete("/api/budgets/categories/99999").status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# 4. create_savings_goal — starting_balance capture (direct helper)
# ═══════════════════════════════════════════════════════════════════════════

class TestCreateSavingsGoal:

    def test_captures_starting_balance_from_account(self, test_session):
        _seed_item_with_balance(test_session, "Vault", 1000.0)
        g = create_savings_goal(test_session, "Japan", 2000.0, account="Vault")
        assert g.starting_balance == pytest.approx(1000.0)

    def test_account_balance_helper_matches_label(self, test_session):
        _seed_item_with_balance(test_session, "Vault", 1234.5)
        assert _account_balance(test_session, "Vault") == pytest.approx(1234.5)
        assert _account_balance(test_session, "Nonexistent") is None
        assert _account_balance(test_session, None) is None

    def test_no_account_starting_zero(self, test_session):
        g = create_savings_goal(test_session, "Rainy Day", 500.0, account=None)
        assert g.starting_balance == pytest.approx(0.0)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Savings-goal progress (direct _goal_out + GET route)
# ═══════════════════════════════════════════════════════════════════════════

class TestSavingsGoalProgress:

    def test_saved_pct_remaining_after_balance_growth(self, test_session):
        item = _seed_item_with_balance(test_session, "Vault", 1000.0)
        target_date = date.today() + timedelta(days=180)
        g = create_savings_goal(test_session, "Japan", 2000.0, target_date=target_date, account="Vault")
        # Balance grows 1000 -> 1500 (edit accounts_json).
        item.accounts_json = json.dumps({"a1": {"app_account": "Vault", "current": 1500.0}})
        test_session.commit()

        out = _goal_out(test_session, g)
        assert out.current_balance == pytest.approx(1500.0)
        assert out.saved == pytest.approx(500.0)             # 1500 - 1000
        assert out.pct == pytest.approx(25.0)                # 500/2000*100
        assert out.remaining == pytest.approx(1500.0)        # 2000 - 500
        assert out.monthly_needed is not None                # target_date set -> computed
        assert out.monthly_needed > 0

    def test_no_account_zero_progress(self, test_session):
        g = create_savings_goal(test_session, "Rainy Day", 500.0, account=None)
        out = _goal_out(test_session, g)
        assert out.starting_balance == pytest.approx(0.0)
        assert out.saved == pytest.approx(0.0)
        assert out.current_balance is None

    def test_goal_visible_via_route(self, client, test_session):
        _seed_item_with_balance(test_session, "Vault", 1000.0)
        create_savings_goal(test_session, "Japan", 2000.0, account="Vault")
        # Bump the balance so the route computes non-zero progress.
        item = test_session.query(PlaidItem).first()
        item.accounts_json = json.dumps({"a1": {"app_account": "Vault", "current": 1800.0}})
        test_session.commit()

        goals = client.get("/api/budgets/goals").json()
        assert len(goals) == 1
        assert goals[0]["name"] == "Japan"
        assert goals[0]["saved"] == pytest.approx(800.0)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Savings-goal CRUD via the REST routes
# ═══════════════════════════════════════════════════════════════════════════

class TestSavingsGoalCrud:

    def test_post_201(self, client):
        resp = client.post("/api/budgets/goals",
                           json={"name": "Japan", "target_amount": 2000.0})
        assert resp.status_code == 201
        assert resp.json()["name"] == "Japan"
        assert resp.json()["starting_balance"] == pytest.approx(0.0)

    def test_put_updates(self, client):
        gid = client.post("/api/budgets/goals",
                          json={"name": "Japan", "target_amount": 2000.0}).json()["id"]
        resp = client.put(f"/api/budgets/goals/{gid}", json={"target_amount": 3000.0})
        assert resp.status_code == 200
        assert resp.json()["target_amount"] == pytest.approx(3000.0)

    def test_put_missing_404(self, client):
        assert client.put("/api/budgets/goals/99999",
                          json={"target_amount": 1.0}).status_code == 404

    def test_delete_204(self, client):
        gid = client.post("/api/budgets/goals",
                          json={"name": "Japan", "target_amount": 2000.0}).json()["id"]
        assert client.delete(f"/api/budgets/goals/{gid}").status_code == 204
        assert client.get("/api/budgets/goals").json() == []

    def test_delete_missing_404(self, client):
        assert client.delete("/api/budgets/goals/99999").status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# 7. Assistant budget-creation — POST /api/assistant/budget
# ═══════════════════════════════════════════════════════════════════════════

class TestAssistantBudget:

    _PLAN = {
        "actions": [
            {"kind": "goal", "name": "Japan", "target_amount": 2000.0,
             "target_date": "2026-12-01", "account": None},
            {"kind": "category_limit", "category": "Food & Drink", "limit_amount": 400.0},
        ],
        "reply": "Created a Japan goal and a Food & Drink limit.",
    }

    def _enable(self, monkeypatch, plan):
        """Patch the Gemini seam: ai.is_enabled -> True, ai.plan_budget -> plan.
        The assistant route calls both via the `ai` module ref, so patching the
        module attributes is sufficient (mirrors test_assistant.py)."""
        from src.api import ai as ai_mod
        monkeypatch.setattr(ai_mod, "is_enabled", lambda: True)
        monkeypatch.setattr(
            ai_mod, "plan_budget",
            lambda messages, known_categories=None, account_labels=None: dict(plan),
        )

    def test_503_when_key_unset(self, client, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        resp = client.post("/api/assistant/budget",
                           json={"messages": [{"role": "user", "content": "save for japan"}]})
        assert resp.status_code == 503

    def test_persists_goal_and_category_and_returns_confirmation(self, client, monkeypatch):
        self._enable(monkeypatch, self._PLAN)

        resp = client.post(
            "/api/assistant/budget",
            json={"messages": [{"role": "user",
                                "content": "Save $2000 for Japan by December and cap dining at $400"}]},
        )
        assert resp.status_code == 200
        body = resp.json()

        # Response shape: {reply, created:{goals:[...], category_limits:[...]}}
        assert body["reply"] == "Created a Japan goal and a Food & Drink limit."
        assert len(body["created"]["goals"]) == 1
        assert len(body["created"]["category_limits"]) == 1
        assert body["created"]["goals"][0]["name"] == "Japan"
        assert body["created"]["goals"][0]["target_amount"] == pytest.approx(2000.0)
        assert str(body["created"]["goals"][0]["target_date"]) == "2026-12-01"
        assert body["created"]["category_limits"][0]["category"] == "Food & Drink"
        assert body["created"]["category_limits"][0]["limit_amount"] == pytest.approx(400.0)

        # BOTH persisted — visible via the budgets routes.
        goals = client.get("/api/budgets/goals").json()
        cats = client.get("/api/budgets/categories").json()
        assert [g["name"] for g in goals] == ["Japan"]
        assert [c["category"] for c in cats] == ["Food & Drink"]

    def test_persisted_rows_exist_in_db(self, client, test_session, monkeypatch):
        self._enable(monkeypatch, self._PLAN)
        client.post("/api/assistant/budget",
                    json={"messages": [{"role": "user", "content": "x"}]})
        assert test_session.query(SavingsGoal).count() == 1
        assert test_session.query(CategoryBudget).count() == 1

    def test_empty_actions_creates_nothing(self, client, monkeypatch):
        self._enable(monkeypatch, {"actions": [], "reply": "No budget intent detected."})
        resp = client.post("/api/assistant/budget",
                           json={"messages": [{"role": "user", "content": "hello"}]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "No budget intent detected."
        assert body["created"]["goals"] == []
        assert body["created"]["category_limits"] == []
        assert client.get("/api/budgets/goals").json() == []
        assert client.get("/api/budgets/categories").json() == []

"""
Unit tests for the Assistant AGENT creation tools (src/api/ai.execute_tool).

These exercise the tool dispatch DIRECTLY against a per-test in-memory DB — no
live Gemini call is made. They verify that the three creation tools persist the
right rows, that creating a rule re-labels existing matching transactions, and
that invalid arguments return {"error": ...} without creating anything.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import sessionmaker

from src.api import ai
from src.api.models import Transaction, Rule, SavingsGoal, CategoryBudget


def _session(test_engine):
    """A Session bound to the per-test in-memory engine."""
    return sessionmaker(autocommit=False, autoflush=False, bind=test_engine)()


def _add_txn(db, **kwargs):
    defaults = dict(
        date=date(2024, 1, 15),
        amount=100.0,
        type="expense",
        category="Uncategorized",
        description="",
        account=None,
    )
    defaults.update(kwargs)
    t = Transaction(**defaults)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


# ── create_categorization_rule ────────────────────────────────────────────────

def test_create_rule_creates_and_relabels_existing(test_engine):
    db = _session(test_engine)
    try:
        match = _add_txn(db, description="Transfer to my savings account", type="expense")
        other = _add_txn(db, description="Coffee shop", type="expense")

        result = ai.execute_tool(
            db,
            "create_categorization_rule",
            {
                "match_field": "description",
                "match_op": "contains",
                "match_value": "savings",
                "set_type": "transfer",
            },
        )

        # A Rule row was created.
        rules = db.query(Rule).all()
        assert len(rules) == 1
        assert rules[0].set_type == "transfer"

        # The matching transaction flipped to transfer; the unrelated one is unchanged.
        db.refresh(match)
        db.refresh(other)
        assert match.type == "transfer"
        assert other.type == "expense"

        assert "rule" in result
        assert result["updated_existing"] >= 1
    finally:
        db.close()


def test_create_rule_without_set_returns_error_and_creates_nothing(test_engine):
    db = _session(test_engine)
    try:
        result = ai.execute_tool(
            db,
            "create_categorization_rule",
            {
                "match_field": "description",
                "match_op": "contains",
                "match_value": "savings",
            },
        )
        assert "error" in result
        assert db.query(Rule).count() == 0
    finally:
        db.close()


def test_create_rule_empty_match_value_returns_error(test_engine):
    db = _session(test_engine)
    try:
        result = ai.execute_tool(
            db,
            "create_categorization_rule",
            {
                "match_field": "description",
                "match_op": "contains",
                "match_value": "   ",
                "set_type": "transfer",
            },
        )
        assert "error" in result
        assert db.query(Rule).count() == 0
    finally:
        db.close()


# ── create_savings_goal ────────────────────────────────────────────────────────

def test_create_savings_goal_valid(test_engine):
    db = _session(test_engine)
    try:
        result = ai.execute_tool(
            db,
            "create_savings_goal",
            {"name": "Emergency fund", "target_amount": 5000, "target_date": "2026-12-31"},
        )
        assert "goal" in result
        goals = db.query(SavingsGoal).all()
        assert len(goals) == 1
        assert goals[0].name == "Emergency fund"
        assert goals[0].target_amount == 5000
    finally:
        db.close()


def test_create_savings_goal_nonpositive_returns_error(test_engine):
    db = _session(test_engine)
    try:
        result = ai.execute_tool(
            db,
            "create_savings_goal",
            {"name": "Bad goal", "target_amount": 0},
        )
        assert "error" in result
        assert db.query(SavingsGoal).count() == 0
    finally:
        db.close()


# ── create_category_budget ──────────────────────────────────────────────────────

def test_create_category_budget_valid(test_engine):
    db = _session(test_engine)
    try:
        result = ai.execute_tool(
            db,
            "create_category_budget",
            {"category": "Dining", "limit_amount": 300},
        )
        assert "category_budget" in result
        budgets = db.query(CategoryBudget).all()
        assert len(budgets) == 1
        assert budgets[0].category == "Dining"
        assert budgets[0].limit_amount == 300
    finally:
        db.close()


def test_create_category_budget_nonpositive_returns_error(test_engine):
    db = _session(test_engine)
    try:
        result = ai.execute_tool(
            db,
            "create_category_budget",
            {"category": "Dining", "limit_amount": -5},
        )
        assert "error" in result
        assert db.query(CategoryBudget).count() == 0
    finally:
        db.close()


# ── Dynamic system instruction (context injection) ────────────────────────────

def test_dynamic_system_instruction_injects_recent_descriptions(test_engine):
    """The per-call system instruction should surface real categories and recent
    distinct descriptions so the model can infer a rule match_value in one shot."""
    db = _session(test_engine)
    try:
        _add_txn(db, description="Transfer to my savings account", category="Savings")
        _add_txn(db, description="Coffee shop", category="Dining")

        instruction = ai._dynamic_system_instruction(db)

        assert "Recent transaction descriptions" in instruction
        assert "Transfer to my savings account" in instruction
        assert "Known categories" in instruction
        assert "Savings" in instruction
    finally:
        db.close()


def test_dynamic_system_instruction_caps_and_truncates(test_engine):
    """Descriptions are capped at 40 distinct entries and truncated to ~50 chars."""
    db = _session(test_engine)
    try:
        for i in range(60):
            _add_txn(db, description=f"unique description number {i}")
        # Add the long one LAST so it is the most-recent (highest id) row and thus
        # inside the 40-entry cap — proving the truncation, not the cap.
        long_desc = "X" * 100
        _add_txn(db, description=long_desc, date=date(2025, 1, 1))

        instruction = ai._dynamic_system_instruction(db)

        # The 100-char description must be truncated to 50 chars.
        assert "X" * 100 not in instruction
        assert "X" * 50 in instruction
        # Sample the descriptions portion and assert it is bounded (<= 40 entries).
        tail = instruction.split("Recent transaction descriptions (for inferring rule match text): ", 1)[1]
        entries = [e for e in tail.rstrip(".").split("; ") if e]
        assert len(entries) <= 40
    finally:
        db.close()

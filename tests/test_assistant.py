"""
test_assistant.py — AI assistant routes + tool dispatch.

The Gemini calls themselves are NOT exercised here (no key, no network). What we
lock down is:
  * graceful degradation: /status reports disabled and AI endpoints 503 with no key
  * ai.execute_tool() — the tool->query mapping the model relies on — returns correct
    numbers against a seeded DB (this is the part we can't validate at runtime).
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker


# ── Graceful degradation (no GEMINI_API_KEY) ──────────────────────────────────

class TestAssistantDisabled:
    def test_status_disabled(self, client, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        resp = client.get("/api/assistant/status")
        assert resp.status_code == 200
        assert resp.json() == {"enabled": False}

    def test_chat_503(self, client, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        resp = client.post(
            "/api/assistant/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 503

    def test_categorize_503(self, client, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        resp = client.post(
            "/api/assistant/categorize",
            json={"description": "Uber", "amount": 20, "type": "expense"},
        )
        assert resp.status_code == 503

    def test_insights_503(self, client, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        resp = client.get("/api/assistant/insights")
        assert resp.status_code == 503


# ── Tool dispatch (pure, offline) ─────────────────────────────────────────────

class TestExecuteTool:
    Q1 = {"start_date": "2026-01-01", "end_date": "2026-03-31"}

    def _seed(self, client):
        rows = [
            {"date": "2026-01-02", "amount": 5000.0, "type": "income",  "category": "Salary"},
            {"date": "2026-01-20", "amount": 85.0,   "type": "expense", "category": "Dining"},
            {"date": "2026-02-14", "amount": 110.0,  "type": "expense", "category": "Dining"},
            {"date": "2026-02-05", "amount": 1400.0, "type": "expense", "category": "Rent"},
            {"date": "2026-05-01", "amount": 50.0,   "type": "expense", "category": "Dining"},  # out of Q1
        ]
        for r in rows:
            assert client.post("/api/transactions", json=r).status_code == 201

    def _db(self, test_engine):
        return sessionmaker(bind=test_engine)()

    def test_summary(self, client, test_engine):
        from src.api import ai
        self._seed(client)
        db = self._db(test_engine)
        try:
            s = ai.execute_tool(db, "get_financial_summary", self.Q1)
            assert s["total_income"] == pytest.approx(5000.0)
            assert s["total_expense"] == pytest.approx(85.0 + 110.0 + 1400.0)
        finally:
            db.close()

    def test_by_category_dining_q1(self, client, test_engine):
        from src.api import ai
        self._seed(client)
        db = self._db(test_engine)
        try:
            cats = ai.execute_tool(db, "get_spending_by_category", {**self.Q1, "type": "expense"})
            dining = next(c for c in cats if c["category"] == "Dining")
            assert dining["total"] == pytest.approx(195.0)  # 85 + 110, May excluded
        finally:
            db.close()

    def test_search_transactions_filtered(self, client, test_engine):
        from src.api import ai
        self._seed(client)
        db = self._db(test_engine)
        try:
            rows = ai.execute_tool(db, "search_transactions", {**self.Q1, "category": "Dining"})
            assert len(rows) == 2
            assert {r["amount"] for r in rows} == {85.0, 110.0}
        finally:
            db.close()

    def test_over_time_months(self, client, test_engine):
        from src.api import ai
        self._seed(client)
        db = self._db(test_engine)
        try:
            ot = ai.execute_tool(db, "get_income_expense_over_time", {**self.Q1, "granularity": "month"})
            periods = {p["period"] for p in ot}
            assert "2026-01" in periods and "2026-02" in periods
            assert "2026-05" not in periods
        finally:
            db.close()

    def test_unknown_tool_raises(self, client, test_engine):
        from src.api import ai
        db = self._db(test_engine)
        try:
            with pytest.raises(ValueError):
                ai.execute_tool(db, "nope", {})
        finally:
            db.close()


# ── categorize-batch (v5) ─────────────────────────────────────────────────────

class TestCategorizeBatchDisabled:
    """Gated on GEMINI_API_KEY — 503 when unset (like other AI endpoints)."""

    def test_categorize_batch_503_without_key(self, client, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        resp = client.post("/api/assistant/categorize-batch", json={"only_uncategorized": True})
        assert resp.status_code == 503


class TestCategorizeBatchAvailable:
    """With AI 'available' (mocked — no key, no network), assert response shape
    {results:[{id,category,confidence}]} and the confident-update side effects."""

    def _enable_ai(self, monkeypatch, suggestion):
        """Patch ai.is_enabled -> True and ai.suggest_category -> canned suggestion.

        Patches the names as imported into the assistant route module so the
        endpoint sees the mocks (it calls `ai.is_enabled` / `ai.suggest_category`).
        """
        from src.api import ai as ai_mod
        from src.api.routes import assistant as assistant_mod
        monkeypatch.setattr(ai_mod, "is_enabled", lambda: True)
        monkeypatch.setattr(ai_mod, "suggest_category",
                            lambda desc, amount, type_, known: dict(suggestion))
        # the route imported `ai` as a module ref, so patching ai_mod attrs suffices
        return assistant_mod

    def test_response_shape(self, client, monkeypatch):
        """Returns {results:[{id,category,confidence}]} for the targeted txns."""
        self._enable_ai(monkeypatch, {"category": "Dining", "confidence": 0.9})
        t1 = client.post("/api/transactions", json={
            "date": "2026-01-01", "amount": 12.0, "type": "expense",
            "category": "Uncategorized", "description": "CHIPOTLE"}).json()
        t2 = client.post("/api/transactions", json={
            "date": "2026-01-02", "amount": 8.0, "type": "expense",
            "category": "Uncategorized", "description": "STARBUCKS"}).json()

        resp = client.post("/api/assistant/categorize-batch",
                           json={"only_uncategorized": True})
        assert resp.status_code == 200
        body = resp.json()
        assert "results" in body and isinstance(body["results"], list)
        assert len(body["results"]) == 2
        for r in body["results"]:
            assert set(r.keys()) == {"id", "category", "confidence"}
            assert isinstance(r["id"], int)
            assert isinstance(r["category"], str)
            assert isinstance(r["confidence"], (int, float))
        returned_ids = {r["id"] for r in body["results"]}
        assert returned_ids == {t1["id"], t2["id"]}

    def test_confident_suggestion_updates_category_and_clears_review(self, client, monkeypatch):
        """A confident suggestion (>= threshold) updates category + clears needs_review."""
        self._enable_ai(monkeypatch, {"category": "Dining", "confidence": 0.95})
        # Seed an uncategorized, needs_review row by importing an ambiguous CSV row.
        import io
        csv_bytes = b"Date,Description,Category,Amount\n2026-03-01,VENMO TO BOB,,-20.00\n"
        client.post("/api/transactions/csv",
                    files={"file": ("a.csv", io.BytesIO(csv_bytes), "text/csv")})
        flagged = client.get("/api/transactions?needs_review=true").json()
        assert len(flagged) == 1

        resp = client.post("/api/assistant/categorize-batch", json={})
        assert resp.status_code == 200
        # the row should now be categorized + no longer in review
        tx = client.get(f"/api/transactions/{flagged[0]['id']}").json()
        assert tx["category"] == "Dining"
        assert tx["needs_review"] is False
        assert client.get("/api/transactions?needs_review=true").json() == []

    def test_low_confidence_does_not_update(self, client, monkeypatch):
        """A low-confidence suggestion is returned but does NOT mutate the row."""
        self._enable_ai(monkeypatch, {"category": "Maybe", "confidence": 0.2})
        t = client.post("/api/transactions", json={
            "date": "2026-01-01", "amount": 5.0, "type": "expense",
            "category": "Uncategorized", "description": "MYSTERY"}).json()
        resp = client.post("/api/assistant/categorize-batch",
                           json={"only_uncategorized": True})
        assert resp.status_code == 200
        # still uncategorized (confidence below the 0.6 threshold)
        tx = client.get(f"/api/transactions/{t['id']}").json()
        assert tx["category"] == "Uncategorized"

    def test_ids_target_specific_rows(self, client, monkeypatch):
        """Passing ids restricts categorization to those transactions."""
        self._enable_ai(monkeypatch, {"category": "Dining", "confidence": 0.9})
        t1 = client.post("/api/transactions", json={
            "date": "2026-01-01", "amount": 12.0, "type": "expense",
            "category": "Uncategorized", "description": "A"}).json()
        client.post("/api/transactions", json={
            "date": "2026-01-02", "amount": 8.0, "type": "expense",
            "category": "Uncategorized", "description": "B"}).json()
        resp = client.post("/api/assistant/categorize-batch", json={"ids": [t1["id"]]})
        assert {r["id"] for r in resp.json()["results"]} == {t1["id"]}

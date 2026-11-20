"""
test_errors.py — unhandled server errors come back as a readable JSON 500.

Coverage
--------
* An endpoint that raises -> 500 JSON with a human `detail` string plus a
  structured `error` {id, type, message, status, method, path}; no traceback in
  the body; the traceback + error ID land in the server log instead.
* A ResponseValidationError (a stored row the response_model rejects) -> the same
  shape, with pydantic's error list rendered as one readable line.
* The 500 body still carries CORS headers, so a cross-origin browser can read it.
* 4xx shapes (HTTPException 404, RequestValidationError 422) are unchanged.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator

from src.api.database import get_db
from src.api.errors import install_error_handlers
from src.api.main import app
from src.api.models import Transaction

ERROR_KEYS = {"id", "type", "message", "status", "method", "path"}


def _assert_no_traceback(text: str) -> None:
    assert "Traceback" not in text
    assert 'File "' not in text


def _raising_db():
    raise RuntimeError("database exploded")
    yield  # pragma: no cover — makes this a generator dependency like get_db


class TestUnhandledException:

    def test_raising_endpoint_returns_json_500(self, client, caplog):
        """A dependency that raises -> 500 JSON with id/type/message, no traceback."""
        app.dependency_overrides[get_db] = _raising_db
        with caplog.at_level(logging.ERROR, logger="expense_tracker.errors"):
            resp = client.get("/api/categories")

        assert resp.status_code == 500
        assert resp.headers["content-type"].startswith("application/json")
        body = resp.json()
        err = body["error"]
        assert set(err) == ERROR_KEYS
        assert re.fullmatch(r"[0-9a-f]{8}", err["id"])
        assert err["type"] == "RuntimeError"
        assert err["message"] == "database exploded"
        assert err["status"] == 500
        assert err["method"] == "GET"
        assert err["path"] == "/api/categories"
        # detail stays a human string so detail-only clients still read it.
        assert isinstance(body["detail"], str)
        assert "RuntimeError: database exploded" in body["detail"]
        assert err["id"] in body["detail"]
        _assert_no_traceback(resp.text)

        # The full traceback goes to the server log, tagged with the same ID.
        records = [r for r in caplog.records if r.name == "expense_tracker.errors"]
        assert len(records) == 1
        assert err["id"] in records[0].getMessage()
        assert "GET /api/categories" in records[0].getMessage()
        assert records[0].exc_info is not None

    def test_500_body_carries_cors_headers(self, client):
        """The JSON 500 passes back out through CORSMiddleware."""
        app.dependency_overrides[get_db] = _raising_db
        resp = client.get("/api/categories", headers={"Origin": "http://localhost:3000"})
        assert resp.status_code == 500
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
        assert resp.json()["error"]["type"] == "RuntimeError"

    def test_error_ids_are_unique(self, client):
        app.dependency_overrides[get_db] = _raising_db
        ids = {client.get("/api/categories").json()["error"]["id"] for _ in range(5)}
        assert len(ids) == 5


class TestResponseValidationError:

    def test_stored_row_failing_response_model_is_readable(self, client, test_session):
        """A stored row TransactionOut rejects -> readable cause with row index + id."""
        good = Transaction(date=date(2024, 1, 2), amount=10.0, type="expense", category="Dining")
        bad = Transaction(date=date(2024, 1, 1), amount=5.0, type="bogus", category="Dining")
        test_session.add_all([good, bad])
        test_session.commit()

        resp = client.get("/api/transactions")

        assert resp.status_code == 500
        err = resp.json()["error"]
        assert set(err) == ERROR_KEYS
        assert err["type"] == "ResponseValidationError"
        assert err["path"] == "/api/transactions"
        # Newest first, so the bad (older) row is index 1.
        assert err["message"].startswith(f"row 1 (id {bad.id}) type: ")
        assert "'bogus'" in err["message"]
        assert "(+" not in err["message"]
        _assert_no_traceback(resp.text)

    def test_multiple_errors_summarised_as_first_plus_count(self):
        """Only the first error is spelled out; the rest become "(+N more)"."""

        class Row(BaseModel):
            amount: float

            @field_validator("amount")
            @classmethod
            def positive(cls, v):
                if v <= 0:
                    raise ValueError("amount must be > 0")
                return v

        mini = FastAPI()
        install_error_handlers(mini)

        @mini.get("/rows", response_model=list[Row])
        def rows():
            return [{"id": 7, "amount": 3}, {"id": 1662, "amount": 0}, {"id": 9, "amount": -1}]

        @mini.get("/one", response_model=Row)
        def one():
            return {"amount": "lots"}

        c = TestClient(mini)
        err = c.get("/rows").json()["error"]
        assert err["message"] == (
            "row 1 (id 1662) amount: Value error, amount must be > 0 (got 0) (+1 more)"
        )

        # A single-object response has no row index.
        err = c.get("/one").json()["error"]
        assert err["message"].startswith("amount: Input should be a valid number")


class TestClientErrorShapesUnchanged:

    def test_404_is_plain_detail(self, client):
        resp = client.get("/api/transactions/999999")
        assert resp.status_code == 404
        assert set(resp.json()) == {"detail"}
        assert isinstance(resp.json()["detail"], str)

    def test_422_is_fastapi_default(self, client):
        resp = client.post("/api/transactions", json={"date": "2024-01-01", "amount": -5,
                                                       "type": "expense", "category": "X"})
        assert resp.status_code == 422
        body = resp.json()
        assert set(body) == {"detail"}
        assert isinstance(body["detail"], list)
        assert body["detail"][0]["loc"][-1] == "amount"

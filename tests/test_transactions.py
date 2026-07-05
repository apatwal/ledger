"""
test_transactions.py — CRUD tests for /api/transactions.

Coverage
--------
* POST   /api/transactions  — happy path, 422 validation failures
* GET    /api/transactions  — listing, filtering by date/type/category, pagination
* GET    /api/transactions/{id} — happy path, 404
* PUT    /api/transactions/{id} — happy path, partial fields, 404, validation
* DELETE /api/transactions/{id} — 204 happy path, 404, double-delete
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# POST /api/transactions
# ---------------------------------------------------------------------------

class TestCreateTransaction:

    def test_create_income(self, client):
        """Valid income transaction → 201 with all expected fields."""
        payload = {
            "date": "2024-03-01",
            "amount": 3000.00,
            "type": "income",
            "category": "Salary",
            "description": "March salary",
        }
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] is not None
        assert data["date"] == "2024-03-01"
        assert data["amount"] == 3000.00
        assert data["type"] == "income"
        assert data["category"] == "Salary"
        assert data["description"] == "March salary"
        assert data["source"] == "manual"          # default
        assert "created_at" in data

    def test_create_expense(self, client):
        """Valid expense transaction → 201."""
        payload = {
            "date": "2024-03-05",
            "amount": 150.50,
            "type": "expense",
            "category": "Groceries",
        }
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "expense"
        assert data["amount"] == pytest.approx(150.50)
        assert data["description"] is None          # optional, omitted

    def test_create_with_source_csv(self, client):
        """Explicit source=csv is accepted and persisted."""
        payload = {
            "date": "2024-03-10",
            "amount": 50.00,
            "type": "expense",
            "category": "Transport",
            "source": "csv",
        }
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 201
        assert resp.json()["source"] == "csv"

    def test_create_savings_category(self, client):
        """Savings category with type=expense is valid per contract."""
        payload = {
            "date": "2024-03-15",
            "amount": 500.00,
            "type": "expense",
            "category": "Savings",
        }
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 201
        assert resp.json()["category"] == "Savings"

    # --- Validation failures (422) ---

    def test_create_missing_date(self, client):
        payload = {"amount": 100.0, "type": "income", "category": "Salary"}
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 422

    def test_create_missing_amount(self, client):
        payload = {"date": "2024-03-01", "type": "income", "category": "Salary"}
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 422

    def test_create_missing_type(self, client):
        payload = {"date": "2024-03-01", "amount": 100.0, "category": "Salary"}
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 422

    def test_create_missing_category(self, client):
        payload = {"date": "2024-03-01", "amount": 100.0, "type": "income"}
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 422

    def test_create_negative_amount(self, client):
        """Amount must be > 0."""
        payload = {
            "date": "2024-03-01",
            "amount": -50.0,
            "type": "expense",
            "category": "Groceries",
        }
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 422

    def test_create_zero_amount(self, client):
        """Zero amount is invalid (must be > 0)."""
        payload = {
            "date": "2024-03-01",
            "amount": 0.0,
            "type": "expense",
            "category": "Groceries",
        }
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 422

    def test_create_transfer(self, client):
        """type 'transfer' (v2) is a valid type and is accepted → 201."""
        payload = {
            "date": "2024-03-01",
            "amount": 233.99,
            "type": "transfer",
            "category": "Payments and Credits",
            "description": "INTERNET PAYMENT - THANK YOU",
        }
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 201
        assert resp.json()["type"] == "transfer"

    def test_create_invalid_type(self, client):
        """type must be one of income | expense | transfer; others → 422."""
        payload = {
            "date": "2024-03-01",
            "amount": 100.0,
            "type": "withdrawal",      # not a valid enum value
            "category": "Other",
        }
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 422

    def test_create_invalid_source(self, client):
        """source must be 'manual' or 'csv'."""
        payload = {
            "date": "2024-03-01",
            "amount": 100.0,
            "type": "income",
            "category": "Salary",
            "source": "api",
        }
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 422

    def test_create_invalid_date_format(self, client):
        """Date must be ISO YYYY-MM-DD."""
        payload = {
            "date": "01/03/2024",
            "amount": 100.0,
            "type": "income",
            "category": "Salary",
        }
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 422

    def test_create_empty_category(self, client):
        """Whitespace-only category is invalid."""
        payload = {
            "date": "2024-03-01",
            "amount": 100.0,
            "type": "income",
            "category": "   ",
        }
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/transactions/{id}
# ---------------------------------------------------------------------------

class TestGetTransaction:

    def test_get_existing(self, client):
        """Retrieve a transaction that was just created."""
        created = client.post("/api/transactions", json={
            "date": "2024-04-01",
            "amount": 200.0,
            "type": "expense",
            "category": "Dining",
        }).json()
        resp = client.get(f"/api/transactions/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]
        assert resp.json()["category"] == "Dining"

    def test_get_nonexistent(self, client):
        """Non-existent ID → 404."""
        resp = client.get("/api/transactions/999999")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    def test_get_after_create_fields_match(self, client):
        """All fields returned by GET match what was POSTed."""
        payload = {
            "date": "2024-05-10",
            "amount": 75.25,
            "type": "expense",
            "category": "Transport",
            "description": "Bus pass",
        }
        created = client.post("/api/transactions", json=payload).json()
        fetched = client.get(f"/api/transactions/{created['id']}").json()
        for key in ("date", "amount", "type", "category", "description"):
            assert fetched[key] == created[key], f"Mismatch on {key}"


# ---------------------------------------------------------------------------
# GET /api/transactions  (list + filters)
# ---------------------------------------------------------------------------

class TestListTransactions:

    def test_list_empty(self, client):
        """Empty DB returns empty list."""
        resp = client.get("/api/transactions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_all(self, seeded_client):
        """All seeded transactions appear in the listing."""
        client, created = seeded_client
        resp = client.get("/api/transactions")
        assert resp.status_code == 200
        assert len(resp.json()) == len(created)

    def test_list_newest_first(self, seeded_client):
        """Results are ordered newest first (descending by date/id)."""
        client, _ = seeded_client
        resp = client.get("/api/transactions")
        items = resp.json()
        # Compare dates; newest should be first
        dates = [item["date"] for item in items]
        assert dates == sorted(dates, reverse=True) or \
               [item["id"] for item in items] == sorted(
                   [item["id"] for item in items], reverse=True
               ), "Expected newest-first ordering"

    def test_filter_by_type_income(self, seeded_client):
        """type=income filter returns only income rows."""
        client, created = seeded_client
        resp = client.get("/api/transactions?type=income")
        assert resp.status_code == 200
        items = resp.json()
        assert all(item["type"] == "income" for item in items)
        # There are 2 income rows in SAMPLE_TRANSACTIONS
        assert len(items) == 2

    def test_filter_by_type_expense(self, seeded_client):
        """type=expense filter returns only expense rows."""
        client, created = seeded_client
        resp = client.get("/api/transactions?type=expense")
        items = resp.json()
        assert all(item["type"] == "expense" for item in items)

    def test_filter_by_category(self, seeded_client):
        """category filter returns only matching rows."""
        client, _ = seeded_client
        resp = client.get("/api/transactions?category=Rent")
        items = resp.json()
        assert all(item["category"] == "Rent" for item in items)
        assert len(items) == 2  # Two Rent entries in SAMPLE_TRANSACTIONS

    def test_filter_by_start_date(self, seeded_client):
        """start_date filter excludes older transactions."""
        client, _ = seeded_client
        resp = client.get("/api/transactions?start_date=2024-02-01")
        items = resp.json()
        assert all(item["date"] >= "2024-02-01" for item in items)

    def test_filter_by_end_date(self, seeded_client):
        """end_date filter excludes newer transactions."""
        client, _ = seeded_client
        resp = client.get("/api/transactions?end_date=2024-01-31")
        items = resp.json()
        assert all(item["date"] <= "2024-01-31" for item in items)

    def test_filter_by_date_range(self, seeded_client):
        """Combined start_date + end_date narrows results correctly."""
        client, _ = seeded_client
        resp = client.get("/api/transactions?start_date=2024-01-01&end_date=2024-01-31")
        items = resp.json()
        assert all("2024-01-01" <= item["date"] <= "2024-01-31" for item in items)

    def test_pagination_limit(self, seeded_client):
        """limit param caps the number of results."""
        client, _ = seeded_client
        resp = client.get("/api/transactions?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_pagination_offset(self, seeded_client):
        """offset param skips rows."""
        client, created = seeded_client
        total = len(created)
        resp_all = client.get("/api/transactions").json()
        resp_offset = client.get("/api/transactions?offset=2").json()
        assert len(resp_offset) == total - 2
        # The first item returned with offset=2 should be the 3rd item without offset
        assert resp_offset[0]["id"] == resp_all[2]["id"]

    def test_pagination_limit_and_offset(self, seeded_client):
        """limit + offset together work correctly."""
        client, _ = seeded_client
        resp = client.get("/api/transactions?limit=2&offset=1")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_unknown_type_filter_matches_nothing(self, seeded_client):
        """An unknown 'type' query filter is treated as a literal match.

        The backend types the `type` query param as a free-form string filter
        (not the income|expense enum), so an unknown value like 'transfer'
        simply matches no rows and returns 200 with an empty list rather than
        422. This is acceptable for a filter param (vs. body validation, which
        the contract requires to be 422). See report.md note O-1.
        """
        client, _ = seeded_client
        resp = client.get("/api/transactions?type=transfer")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_valid_type_filter_still_works(self, seeded_client):
        """A valid type filter ('income') returns only matching rows."""
        client, _ = seeded_client
        resp = client.get("/api/transactions?type=income")
        assert resp.status_code == 200
        assert all(t["type"] == "income" for t in resp.json())


# ---------------------------------------------------------------------------
# PUT /api/transactions/{id}
# ---------------------------------------------------------------------------

class TestUpdateTransaction:

    def test_update_happy_path(self, client):
        """Full update replaces all fields."""
        created = client.post("/api/transactions", json={
            "date": "2024-06-01",
            "amount": 100.0,
            "type": "expense",
            "category": "Groceries",
        }).json()
        tx_id = created["id"]

        update_payload = {
            "date": "2024-06-02",
            "amount": 200.0,
            "type": "income",
            "category": "Salary",
            "description": "Updated",
        }
        resp = client.put(f"/api/transactions/{tx_id}", json=update_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == tx_id
        assert data["date"] == "2024-06-02"
        assert data["amount"] == 200.0
        assert data["type"] == "income"
        assert data["category"] == "Salary"
        assert data["description"] == "Updated"

    def test_update_persists(self, client):
        """Updated values are returned on subsequent GET."""
        created = client.post("/api/transactions", json={
            "date": "2024-06-01",
            "amount": 100.0,
            "type": "expense",
            "category": "Groceries",
        }).json()
        tx_id = created["id"]

        update_payload = {
            "date": "2024-06-05",
            "amount": 999.0,
            "type": "income",
            "category": "Bonus",
        }
        client.put(f"/api/transactions/{tx_id}", json=update_payload)
        fetched = client.get(f"/api/transactions/{tx_id}").json()
        assert fetched["amount"] == 999.0
        assert fetched["category"] == "Bonus"

    def test_update_nonexistent(self, client):
        """Updating a non-existent ID → 404."""
        resp = client.put("/api/transactions/999999", json={
            "date": "2024-06-01",
            "amount": 100.0,
            "type": "expense",
            "category": "Groceries",
        })
        assert resp.status_code == 404
        assert "detail" in resp.json()

    def test_update_invalid_amount(self, client):
        """Negative amount on update → 422."""
        created = client.post("/api/transactions", json={
            "date": "2024-06-01",
            "amount": 100.0,
            "type": "expense",
            "category": "Groceries",
        }).json()
        resp = client.put(f"/api/transactions/{created['id']}", json={
            "date": "2024-06-01",
            "amount": -50.0,
            "type": "expense",
            "category": "Groceries",
        })
        assert resp.status_code == 422

    def test_update_invalid_type(self, client):
        """Invalid type on update → 422."""
        created = client.post("/api/transactions", json={
            "date": "2024-06-01",
            "amount": 100.0,
            "type": "expense",
            "category": "Groceries",
        }).json()
        resp = client.put(f"/api/transactions/{created['id']}", json={
            "date": "2024-06-01",
            "amount": 100.0,
            "type": "savings",
            "category": "Savings",
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/transactions/{id}
# ---------------------------------------------------------------------------

class TestDeleteTransaction:

    def test_delete_happy_path(self, client):
        """Deleting an existing transaction → 204."""
        created = client.post("/api/transactions", json={
            "date": "2024-07-01",
            "amount": 50.0,
            "type": "expense",
            "category": "Dining",
        }).json()
        tx_id = created["id"]
        resp = client.delete(f"/api/transactions/{tx_id}")
        assert resp.status_code == 204

    def test_delete_removes_from_db(self, client):
        """After deletion, GET returns 404."""
        created = client.post("/api/transactions", json={
            "date": "2024-07-01",
            "amount": 50.0,
            "type": "expense",
            "category": "Dining",
        }).json()
        tx_id = created["id"]
        client.delete(f"/api/transactions/{tx_id}")
        resp = client.get(f"/api/transactions/{tx_id}")
        assert resp.status_code == 404

    def test_delete_nonexistent(self, client):
        """Deleting a non-existent ID → 404."""
        resp = client.delete("/api/transactions/999999")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    def test_delete_idempotent_second_call(self, client):
        """Second delete of same ID → 404 (not 204 again)."""
        created = client.post("/api/transactions", json={
            "date": "2024-07-01",
            "amount": 50.0,
            "type": "expense",
            "category": "Dining",
        }).json()
        tx_id = created["id"]
        client.delete(f"/api/transactions/{tx_id}")
        resp = client.delete(f"/api/transactions/{tx_id}")
        assert resp.status_code == 404

    def test_delete_does_not_affect_others(self, seeded_client):
        """Deleting one transaction doesn't remove others."""
        client, created = seeded_client
        tx_id = created[0]["id"]
        client.delete(f"/api/transactions/{tx_id}")
        remaining = client.get("/api/transactions").json()
        remaining_ids = [t["id"] for t in remaining]
        assert tx_id not in remaining_ids
        # All others still present
        for tx in created[1:]:
            assert tx["id"] in remaining_ids


# ---------------------------------------------------------------------------
# Health + miscellaneous
# ---------------------------------------------------------------------------

class TestHealth:

    def test_health_endpoint(self, client):
        """GET /api/health → 200 { status: 'ok' }."""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

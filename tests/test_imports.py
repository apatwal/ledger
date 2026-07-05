"""
test_imports.py — v5.2 import history + reassign / undo.

- CSV import creates one `import_batches` row and tags every imported txn with
  its `batch_id`; the import response carries `batch_id`.
- GET /api/imports lists batches newest-first with filename/account/counts.
- POST /api/imports/{id}/reassign sets the account on the batch AND all its
  transactions (empty/null → Unassigned); 404 on unknown id.
- DELETE /api/imports/{id} removes the batch and all its transactions (undo);
  404 on unknown id.
"""

from __future__ import annotations

from .conftest import fixture_csv_file

# Known fixture sizes (verified against the backend).
DISCOVER_IMPORTED = 15
CHASE_IMPORTED = 157


def _import(client, name: str, account: str | None = None):
    data = {"account": account} if account is not None else None
    resp = client.post("/api/transactions/csv", files=fixture_csv_file(name), data=data)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _count_txns(client) -> int:
    return len(client.get("/api/transactions?limit=1000").json())


# ---------------------------------------------------------------------------
# batch_id on import response + transaction tagging
# ---------------------------------------------------------------------------

class TestBatchTagging:

    def test_import_response_has_batch_id(self, client):
        """CSV import response includes an int batch_id (v5.2)."""
        data = _import(client, "discover_sample.csv", "Discover")
        assert "batch_id" in data
        assert isinstance(data["batch_id"], int)
        assert data["batch_id"] >= 1

    def test_each_transaction_tagged_to_batch(self, client):
        """Every imported transaction is linked to the batch.

        Note: `batch_id` is not exposed on TransactionOut (see report.md D-2),
        so batch membership is verified behaviorally — reassigning the batch
        updates exactly the imported rows, proving all were tagged to it.
        """
        data = _import(client, "discover_sample.csv", "Discover")
        batch_id = data["batch_id"]
        txs = client.get("/api/transactions?limit=1000").json()
        assert len(txs) == DISCOVER_IMPORTED
        # All rows belong to the batch: reassigning it touches all of them.
        upd = client.post(f"/api/imports/{batch_id}/reassign", json={"account": "X"}).json()
        assert upd["updated"] == DISCOVER_IMPORTED

    def test_two_imports_get_distinct_batch_ids(self, client):
        """Separate imports produce distinct batch ids."""
        b1 = _import(client, "discover_sample.csv", "Discover")["batch_id"]
        b2 = _import(client, "chase_sample.csv")["batch_id"]
        assert b1 != b2

    def test_manual_transaction_not_in_any_batch(self, client):
        """A manually-created transaction is not tied to any batch.

        Verified behaviorally: no import batch is created for a manual POST.
        """
        client.post("/api/transactions", json={
            "date": "2026-01-01", "amount": 10.0, "type": "expense",
            "category": "Groceries"})
        assert client.get("/api/imports").json() == []


# ---------------------------------------------------------------------------
# GET /api/imports
# ---------------------------------------------------------------------------

class TestListImports:

    def test_empty_when_no_imports(self, client):
        assert client.get("/api/imports").json() == []

    def test_lists_two_batches_newest_first(self, client):
        """Import Discover then Chase → two batches, Chase (newest) first."""
        _import(client, "discover_sample.csv", "Discover")
        _import(client, "chase_sample.csv")
        batches = client.get("/api/imports").json()
        assert len(batches) == 2
        # newest-first: Chase was imported second → higher id → first
        assert batches[0]["filename"] == "chase_sample.csv"
        assert batches[1]["filename"] == "discover_sample.csv"
        assert batches[0]["id"] > batches[1]["id"]

    def test_batch_metadata_and_counts(self, client):
        """Each batch carries correct filename, account, and counts."""
        d = _import(client, "discover_sample.csv", "Discover")
        ch = _import(client, "chase_sample.csv")
        batches = {b["filename"]: b for b in client.get("/api/imports").json()}

        disc = batches["discover_sample.csv"]
        assert disc["account"] == "Discover"
        assert disc["imported"] == DISCOVER_IMPORTED
        assert disc["skipped"] == 0
        assert disc["transfers"] == d["transfers"]
        assert disc["needs_review"] == d["needs_review"]

        chase = batches["chase_sample.csv"]
        assert chase["account"] is None          # imported without an account
        assert chase["imported"] == CHASE_IMPORTED
        assert chase["skipped"] == 0
        assert chase["transfers"] == ch["transfers"]
        assert chase["needs_review"] == ch["needs_review"]

    def test_batch_counts_match_import_response(self, client):
        """The batch row's counts equal what the import response reported."""
        resp = _import(client, "chase_sample.csv", "Chase")
        batch = client.get("/api/imports").json()[0]
        assert batch["id"] == resp["batch_id"]
        assert batch["imported"] == resp["imported"]
        assert batch["skipped"] == resp["skipped"]
        assert batch["transfers"] == resp["transfers"]
        assert batch["needs_review"] == resp["needs_review"]


# ---------------------------------------------------------------------------
# POST /api/imports/{id}/reassign
# ---------------------------------------------------------------------------

class TestReassign:

    def test_reassign_sets_batch_and_all_txns(self, client):
        """Reassign sets the batch account AND every transaction in it."""
        # Chase fixture imported WITHOUT an account (Unassigned).
        data = _import(client, "chase_sample.csv")
        batch_id = data["batch_id"]

        resp = client.post(f"/api/imports/{batch_id}/reassign", json={"account": "Chase"})
        assert resp.status_code == 200
        assert resp.json()["updated"] == CHASE_IMPORTED

        # batch account updated
        batch = client.get("/api/imports").json()[0]
        assert batch["account"] == "Chase"
        # all its transactions updated
        chase_txns = client.get("/api/transactions?account=Chase&limit=1000").json()
        assert len(chase_txns) == CHASE_IMPORTED
        assert all(t["account"] == "Chase" for t in chase_txns)

    def test_reassign_reflected_in_by_account(self, client):
        """After reassign, by-account attributes the spend to the new card."""
        data = _import(client, "chase_sample.csv")   # Unassigned
        # Before: it's under "Unassigned"
        before = {b["account"]: b for b in client.get("/api/stats/by-account").json()}
        assert "Unassigned" in before

        client.post(f"/api/imports/{data['batch_id']}/reassign", json={"account": "Chase"})
        after = {b["account"]: b for b in client.get("/api/stats/by-account").json()}
        assert "Chase" in after
        assert "Unassigned" not in after     # nothing left unassigned

    def test_reassign_to_null_is_unassigned(self, client):
        """Reassigning to null/empty clears the account back to Unassigned."""
        data = _import(client, "discover_sample.csv", "Discover")
        batch_id = data["batch_id"]
        resp = client.post(f"/api/imports/{batch_id}/reassign", json={"account": None})
        assert resp.status_code == 200
        assert resp.json()["updated"] == DISCOVER_IMPORTED

        # batch account cleared
        batch = client.get("/api/imports").json()[0]
        assert batch["account"] is None
        # transactions no longer under "Discover"
        assert client.get("/api/transactions?account=Discover").json() == []
        # they show up as Unassigned in by-account
        accounts = {b["account"] for b in client.get("/api/stats/by-account").json()}
        assert "Unassigned" in accounts

    def test_reassign_empty_string_is_unassigned(self, client):
        """An empty-string account is treated as Unassigned (null)."""
        data = _import(client, "discover_sample.csv", "Discover")
        resp = client.post(f"/api/imports/{data['batch_id']}/reassign", json={"account": "   "})
        assert resp.status_code == 200
        batch = client.get("/api/imports").json()[0]
        assert batch["account"] is None

    def test_reassign_only_affects_target_batch(self, client):
        """Reassigning one batch leaves the other batch's transactions untouched."""
        disc = _import(client, "discover_sample.csv", "Discover")
        chase = _import(client, "chase_sample.csv")
        # reassign only the Chase batch
        client.post(f"/api/imports/{chase['batch_id']}/reassign", json={"account": "Chase"})
        # Discover rows unchanged
        disc_txns = client.get("/api/transactions?account=Discover&limit=1000").json()
        assert len(disc_txns) == DISCOVER_IMPORTED

    def test_reassign_unknown_id_404(self, client):
        resp = client.post("/api/imports/999999/reassign", json={"account": "X"})
        assert resp.status_code == 404
        assert "detail" in resp.json()


# ---------------------------------------------------------------------------
# DELETE /api/imports/{id}  (undo an import)
# ---------------------------------------------------------------------------

class TestDeleteImport:

    def test_delete_removes_batch_and_transactions(self, client):
        """Deleting a batch removes it and all its transactions."""
        disc = _import(client, "discover_sample.csv", "Discover")
        chase = _import(client, "chase_sample.csv")
        total_before = _count_txns(client)
        assert total_before == DISCOVER_IMPORTED + CHASE_IMPORTED

        resp = client.delete(f"/api/imports/{chase['batch_id']}")
        assert resp.status_code == 204

        # total drops by the Chase batch size
        total_after = _count_txns(client)
        assert total_after == total_before - CHASE_IMPORTED
        assert total_after == DISCOVER_IMPORTED

        # batch disappears from the history
        remaining = client.get("/api/imports").json()
        assert len(remaining) == 1
        assert remaining[0]["filename"] == "discover_sample.csv"

    def test_delete_leaves_other_batch_intact(self, client):
        """Undoing one import does not touch the other's transactions."""
        disc = _import(client, "discover_sample.csv", "Discover")
        _import(client, "chase_sample.csv", "Chase")
        client.delete(f"/api/imports/{disc['batch_id']}")
        # Chase rows still present (all of them)
        chase_txns = client.get("/api/transactions?limit=1000").json()
        assert len(chase_txns) == CHASE_IMPORTED
        assert all(t["account"] == "Chase" for t in chase_txns)
        # and its batch survives in the history
        remaining = client.get("/api/imports").json()
        assert [b["filename"] for b in remaining] == ["chase_sample.csv"]

    def test_delete_does_not_touch_manual_rows(self, client):
        """Manual (batch_id=null) transactions survive an import undo."""
        manual = client.post("/api/transactions", json={
            "date": "2026-01-01", "amount": 10.0, "type": "expense",
            "category": "Groceries"}).json()
        disc = _import(client, "discover_sample.csv", "Discover")
        client.delete(f"/api/imports/{disc['batch_id']}")
        # manual row still retrievable
        assert client.get(f"/api/transactions/{manual['id']}").status_code == 200
        assert _count_txns(client) == 1

    def test_delete_unknown_id_404(self, client):
        resp = client.delete("/api/imports/999999")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    def test_delete_then_absent_from_list(self, client):
        """A deleted batch no longer appears in GET /api/imports."""
        disc = _import(client, "discover_sample.csv", "Discover")
        bid = disc["batch_id"]
        client.delete(f"/api/imports/{bid}")
        ids = [b["id"] for b in client.get("/api/imports").json()]
        assert bid not in ids

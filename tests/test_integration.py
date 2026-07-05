"""
test_integration.py — end-to-end flow exercising the whole API surface.

Realistic flow:
  1. Seed a few transactions manually via POST /api/transactions
  2. Import additional transactions via POST /api/transactions/csv
  3. Query every stats endpoint (summary, by-category, over-time)
  4. Assert the aggregates are internally consistent and match hand-computed
     expected values across the combined manual + CSV dataset.
  5. Exercise update + delete and re-verify stats reflect the change.

This complements the unit/API tests by verifying the endpoints agree with
each other on a single shared dataset.
"""

from __future__ import annotations

import io
import pytest


def _csv(content: str, filename: str = "import.csv") -> dict:
    return {"file": (filename, io.BytesIO(content.encode("utf-8")), "text/csv")}


class TestEndToEndFlow:

    def test_full_lifecycle(self, client):
        # ------------------------------------------------------------------
        # 1. Manual seed — two income, two expense (one of which is Savings)
        # ------------------------------------------------------------------
        manual = [
            {"date": "2024-01-05", "amount": 4000.0, "type": "income",  "category": "Salary",    "description": "Jan pay"},
            {"date": "2024-01-10", "amount": 900.0,  "type": "expense", "category": "Rent",      "description": "Jan rent"},
            {"date": "2024-01-12", "amount": 300.0,  "type": "expense", "category": "Savings",   "description": "Emergency fund"},
            {"date": "2024-02-05", "amount": 4000.0, "type": "income",  "category": "Salary",    "description": "Feb pay"},
        ]
        for tx in manual:
            r = client.post("/api/transactions", json=tx)
            assert r.status_code == 201, r.text

        # ------------------------------------------------------------------
        # 2. CSV import — adds 2 expenses + 1 income across Jan/Feb.
        #    The negative-amount row is stored as abs (200.0).
        # ------------------------------------------------------------------
        csv_content = (
            "date,amount,type,category,description\n"
            "2024-01-20,150.00,expense,Groceries,Jan groceries\n"
            "2024-02-15,-200.00,expense,Investment,Index fund (neg sign)\n"
            "2024-02-18,500.00,income,Bonus,Quarterly bonus\n"
            "BADDATE,99.00,expense,Misc,should fail\n"      # 1 error row
        )
        imp = client.post("/api/transactions/csv", files=_csv(csv_content))
        assert imp.status_code == 200
        imp_data = imp.json()
        assert imp_data["imported"] == 3
        assert imp_data["skipped"] == 1
        assert len(imp_data["errors"]) == 1

        # ------------------------------------------------------------------
        # Combined dataset (7 rows total):
        #   income:  4000 + 4000 + 500            = 8500
        #   expense: 900(Rent) + 300(Savings)
        #            + 150(Groceries) + 200(Investment) = 1550
        #   savings: 300(Savings) + 200(Investment)     = 500
        #   net:     8500 - 1550                         = 6950
        #   savings_rate: 500 / 8500                     = 0.0588
        # ------------------------------------------------------------------
        all_txs = client.get("/api/transactions").json()
        assert len(all_txs) == 7

        # 3a. Summary
        summary = client.get("/api/stats/summary").json()
        assert summary["total_income"] == pytest.approx(8500.0)
        assert summary["total_expense"] == pytest.approx(1550.0)
        assert summary["net"] == pytest.approx(6950.0)
        assert summary["savings"] == pytest.approx(500.0)
        assert summary["savings_rate"] == pytest.approx(500.0 / 8500.0, abs=1e-4)
        assert summary["count"] == 7

        # 3b. By-category (expenses), pct sums to ~100, descending
        by_cat = client.get("/api/stats/by-category").json()
        cat_totals = {c["category"]: c["total"] for c in by_cat}
        assert cat_totals["Rent"] == pytest.approx(900.0)
        assert cat_totals["Savings"] == pytest.approx(300.0)
        assert cat_totals["Investment"] == pytest.approx(200.0)
        assert cat_totals["Groceries"] == pytest.approx(150.0)
        # pct is a full percentage that sums to ~100
        assert sum(c["pct"] for c in by_cat) == pytest.approx(100.0, abs=0.5)
        totals_in_order = [c["total"] for c in by_cat]
        assert totals_in_order == sorted(totals_in_order, reverse=True)

        # 3c. By-category income
        by_cat_income = client.get("/api/stats/by-category?type=income").json()
        inc_totals = {c["category"]: c["total"] for c in by_cat_income}
        assert inc_totals["Salary"] == pytest.approx(8000.0)
        assert inc_totals["Bonus"] == pytest.approx(500.0)

        # 3d. Over-time (monthly), ascending, per-period correctness
        over_time = client.get("/api/stats/over-time?granularity=month").json()
        periods = [p["period"] for p in over_time]
        assert periods == sorted(periods)            # ascending
        assert periods == ["2024-01", "2024-02"]
        by_period = {p["period"]: p for p in over_time}
        # Jan: income 4000; expense 900+300+150=1350; savings 300; net 2650
        assert by_period["2024-01"]["income"] == pytest.approx(4000.0)
        assert by_period["2024-01"]["expense"] == pytest.approx(1350.0)
        assert by_period["2024-01"]["savings"] == pytest.approx(300.0)
        assert by_period["2024-01"]["net"] == pytest.approx(2650.0)
        # Feb: income 4000+500=4500; expense 200; savings 200; net 4300
        assert by_period["2024-02"]["income"] == pytest.approx(4500.0)
        assert by_period["2024-02"]["expense"] == pytest.approx(200.0)
        assert by_period["2024-02"]["savings"] == pytest.approx(200.0)
        assert by_period["2024-02"]["net"] == pytest.approx(4300.0)

        # Cross-endpoint consistency: sum of per-period income == summary income
        assert sum(p["income"] for p in over_time) == pytest.approx(summary["total_income"])
        assert sum(p["expense"] for p in over_time) == pytest.approx(summary["total_expense"])
        assert sum(p["savings"] for p in over_time) == pytest.approx(summary["savings"])

        # ------------------------------------------------------------------
        # 4. Mutate: delete the Groceries row, then update the Rent row's amount
        # ------------------------------------------------------------------
        groceries = next(t for t in all_txs if t["category"] == "Groceries")
        del_resp = client.delete(f"/api/transactions/{groceries['id']}")
        assert del_resp.status_code == 204

        rent = next(t for t in all_txs if t["category"] == "Rent")
        upd_resp = client.put(f"/api/transactions/{rent['id']}", json={
            "date": rent["date"],
            "amount": 1000.0,                 # was 900
            "type": "expense",
            "category": "Rent",
            "description": "Rent increased",
        })
        assert upd_resp.status_code == 200

        # ------------------------------------------------------------------
        # 5. Re-verify summary reflects the mutations:
        #    expense now: 1000(Rent) + 300(Savings) + 200(Investment) = 1500
        #    (Groceries 150 removed, Rent +100)
        #    income unchanged 8500; net 7000; count 6
        # ------------------------------------------------------------------
        summary2 = client.get("/api/stats/summary").json()
        assert summary2["count"] == 6
        assert summary2["total_income"] == pytest.approx(8500.0)
        assert summary2["total_expense"] == pytest.approx(1500.0)
        assert summary2["net"] == pytest.approx(7000.0)
        assert summary2["savings"] == pytest.approx(500.0)   # unchanged

        # Categories endpoint reflects the live data + defaults
        cats = client.get("/api/categories").json()
        assert "Rent" in cats
        assert "Salary" in cats

    def test_csv_template_round_trips_through_import(self, client):
        """The downloadable template is itself a valid importable CSV (header + examples)."""
        tmpl = client.get("/api/transactions/csv/template")
        assert tmpl.status_code == 200
        template_text = tmpl.text

        # Feed the template straight back into the import endpoint.
        resp = client.post("/api/transactions/csv", files=_csv(template_text))
        assert resp.status_code == 200
        data = resp.json()
        # The template ships with example rows; they should import cleanly.
        assert data["imported"] >= 1
        assert data["errors"] == []

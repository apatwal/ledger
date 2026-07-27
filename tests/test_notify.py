"""
test_notify.py — Real-time duplicate alerts via ntfy (v12).

Coverage
--------
* send_ntfy: returns False (no-op) when NTFY_TOPIC is unset; sends via the
  monkeypatched HTTP seam when the topic is set, passing the right URL/headers.
* detect_and_notify_new_duplicates:
  - Two identical expense rows → ONE notification; both rows get dup_notified=True.
  - Running again sends NO second notification (no-spam).
  - A dismissed group → no notification.
* compute_duplicate_groups behavior is exercised by the (unchanged) test_duplicates
  suite; here we confirm the notifier reuses it correctly.
* POST /api/plaid/webhook with a TRANSACTIONS payload for an unknown item_id →
  200 no-op, no crash (Plaid unconfigured in tests).
* POST /api/notify/test returns {"sent": bool}.
"""

from __future__ import annotations

from datetime import date

from src.api import notify
from src.api.models import Transaction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_expense(session, **kwargs) -> Transaction:
    defaults = dict(
        date=date(2026, 3, 2),
        amount=100.00,
        type="expense",
        category="Shopping",
        description="Rent",
        account="BofA",
        source="manual",
    )
    defaults.update(kwargs)
    tx = Transaction(**defaults)
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return tx


class _Recorder:
    """Captures calls to notify._http_post so no real network is touched."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, body, headers):
        self.calls.append({"url": url, "body": body, "headers": headers})


# ---------------------------------------------------------------------------
# send_ntfy
# ---------------------------------------------------------------------------

class TestSendNtfy:

    def test_returns_false_when_topic_unset(self, monkeypatch):
        monkeypatch.delenv("NTFY_TOPIC", raising=False)
        # Guard: if it tried to send, this would blow up (no network).
        monkeypatch.setattr(notify, "_http_post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not send")))
        assert notify.send_ntfy("t", "m") is False

    def test_sends_with_headers_when_topic_set(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(notify, "_http_post", rec)
        monkeypatch.setenv("NTFY_TOPIC", "my-topic")
        monkeypatch.delenv("NTFY_SERVER", raising=False)

        ok = notify.send_ntfy("Title!", "Body text", click="https://x/dashboard",
                              tags="warning", priority="high")
        assert ok is True
        assert len(rec.calls) == 1
        call = rec.calls[0]
        assert call["url"] == "https://ntfy.sh/my-topic"
        assert call["body"] == b"Body text"
        assert call["headers"]["Title"] == "Title!"
        assert call["headers"]["Click"] == "https://x/dashboard"
        assert call["headers"]["Tags"] == "warning"
        assert call["headers"]["Priority"] == "high"

    def test_custom_server(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(notify, "_http_post", rec)
        monkeypatch.setenv("NTFY_TOPIC", "t")
        monkeypatch.setenv("NTFY_SERVER", "https://ntfy.example.com/")
        notify.send_ntfy("a", "b")
        assert rec.calls[0]["url"] == "https://ntfy.example.com/t"

    def test_never_raises_on_send_failure(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr(notify, "_http_post", boom)
        monkeypatch.setenv("NTFY_TOPIC", "t")
        assert notify.send_ntfy("a", "b") is False


# ---------------------------------------------------------------------------
# detect_and_notify_new_duplicates
# ---------------------------------------------------------------------------

class TestDetectAndNotify:

    def test_new_duplicate_fires_one_alert_and_marks_notified(self, test_session, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(notify, "_http_post", rec)
        monkeypatch.setenv("NTFY_TOPIC", "dupes")

        a = _add_expense(test_session)
        b = _add_expense(test_session)

        sent = notify.detect_and_notify_new_duplicates(test_session)
        assert sent == 1
        assert len(rec.calls) == 1
        # Body summarizes the duplicate.
        body = rec.calls[0]["body"].decode("utf-8")
        assert "$100.00" in body
        assert "Rent" in body
        assert "charged 2×" in body

        test_session.refresh(a)
        test_session.refresh(b)
        assert a.dup_notified is True
        assert b.dup_notified is True

    def test_no_spam_on_second_run(self, test_session, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(notify, "_http_post", rec)
        monkeypatch.setenv("NTFY_TOPIC", "dupes")

        _add_expense(test_session)
        _add_expense(test_session)

        assert notify.detect_and_notify_new_duplicates(test_session) == 1
        # Second run: everything already notified → no new alert.
        assert notify.detect_and_notify_new_duplicates(test_session) == 0
        assert len(rec.calls) == 1

    def test_new_member_joining_group_re_alerts(self, test_session, monkeypatch):
        """A genuinely NEW duplicate row (added after the first alert) re-triggers."""
        rec = _Recorder()
        monkeypatch.setattr(notify, "_http_post", rec)
        monkeypatch.setenv("NTFY_TOPIC", "dupes")

        _add_expense(test_session)
        _add_expense(test_session)
        assert notify.detect_and_notify_new_duplicates(test_session) == 1

        # A third identical charge appears → un-notified member in the group.
        _add_expense(test_session)
        assert notify.detect_and_notify_new_duplicates(test_session) == 1
        assert len(rec.calls) == 2

    def test_dismissed_group_no_alert(self, test_session, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(notify, "_http_post", rec)
        monkeypatch.setenv("NTFY_TOPIC", "dupes")

        _add_expense(test_session, dup_dismissed=True)
        _add_expense(test_session, dup_dismissed=True)

        assert notify.detect_and_notify_new_duplicates(test_session) == 0
        assert rec.calls == []

    def test_single_row_no_alert(self, test_session, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(notify, "_http_post", rec)
        monkeypatch.setenv("NTFY_TOPIC", "dupes")

        _add_expense(test_session)
        assert notify.detect_and_notify_new_duplicates(test_session) == 0
        assert rec.calls == []

    def test_no_topic_still_marks_notified(self, test_session, monkeypatch):
        """With no NTFY_TOPIC the send is a no-op but rows are still marked so a
        later topic-configured run doesn't retroactively blast old dupes."""
        monkeypatch.delenv("NTFY_TOPIC", raising=False)
        a = _add_expense(test_session)
        b = _add_expense(test_session)
        # One "alert" is counted (a group needing notification was found).
        sent = notify.detect_and_notify_new_duplicates(test_session)
        assert sent == 1
        test_session.refresh(a)
        test_session.refresh(b)
        assert a.dup_notified is True and b.dup_notified is True


# ---------------------------------------------------------------------------
# POST /api/plaid/webhook
# ---------------------------------------------------------------------------

class TestWebhookEndpoint:

    def test_unknown_item_id_is_200_noop(self, client, monkeypatch):
        # Sandbox env => signature verification is skipped (only enforced in prod).
        monkeypatch.setenv("PLAID_ENV", "sandbox")
        resp = client.post(
            "/api/plaid/webhook",
            json={
                "webhook_type": "TRANSACTIONS",
                "webhook_code": "SYNC_UPDATES_AVAILABLE",
                "item_id": "does-not-exist",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "unknown_item"

    def test_non_transactions_webhook_is_200_noop(self, client, monkeypatch):
        monkeypatch.setenv("PLAID_ENV", "sandbox")
        resp = client.post(
            "/api/plaid/webhook",
            json={"webhook_type": "ITEM", "webhook_code": "ERROR", "item_id": "x"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "ignored"

    def test_empty_body_is_200(self, client, monkeypatch):
        monkeypatch.setenv("PLAID_ENV", "sandbox")
        resp = client.post("/api/plaid/webhook", content=b"")
        assert resp.status_code == 200, resp.text

    def test_production_missing_verification_is_200_unverified(self, client, monkeypatch):
        """In production a missing Plaid-Verification header is rejected — but we
        still return 200 (never invite a retry-storm of unverifiable payloads)."""
        monkeypatch.setenv("PLAID_ENV", "production")
        resp = client.post(
            "/api/plaid/webhook",
            json={"webhook_type": "TRANSACTIONS", "webhook_code": "DEFAULT_UPDATE",
                  "item_id": "x"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "unverified"


# ---------------------------------------------------------------------------
# POST /api/notify/test
# ---------------------------------------------------------------------------

class TestNotifyTestEndpoint:

    def test_shape_false_when_unconfigured(self, client, monkeypatch):
        monkeypatch.delenv("NTFY_TOPIC", raising=False)
        resp = client.post("/api/notify/test")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "sent" in body
        assert body["sent"] is False

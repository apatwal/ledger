"""
test_notify.py — Real-time duplicate alerts via ntfy (v12).

Coverage
--------
* send_ntfy: returns False (no-op) when NTFY_TOPIC is unset; sends via the
  monkeypatched HTTP seam when the topic is set, passing the right URL/headers.
* Header encoding regression (the production bug): every header value must be
  latin-1 encodable, because http.client encodes headers as latin-1 and a raw
  emoji Title raised UnicodeEncodeError before the request was ever sent — which
  send_ntfy swallowed, silently breaking ALL notifications. Emoji titles are sent
  as RFC 2047 encoded-words (and must round-trip); ASCII titles pass through
  unchanged.
* detect_and_notify_new_duplicates:
  - Two identical expense rows → ONE notification; both rows get dup_notified=True.
  - Running again sends NO second notification (no-spam).
  - A dismissed group → no notification.
* compute_duplicate_groups behavior is exercised by the (unchanged) test_duplicates
  suite; here we confirm the notifier reuses it correctly.
* POST /api/plaid/webhook with a TRANSACTIONS payload for an unknown item_id →
  200 no-op, no crash (Plaid unconfigured in tests).
* POST /api/notify/test returns {"sent", "topic_configured", "server", "error"}.
"""

from __future__ import annotations

from datetime import date
from email.header import make_header, decode_header

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
# Header encoding — regression tests for the emoji-Title production bug
# ---------------------------------------------------------------------------

class TestHeaderEncoding:
    """http.client encodes HTTP headers as latin-1, so a raw non-ASCII header
    value raises UnicodeEncodeError before the request leaves the process. That
    killed every notification in production (send_ntfy swallowed it and returned
    False). These tests assert on the headers send_ntfy actually builds."""

    EMOJI_TITLE = "⚠️ Possible duplicate charge"

    def _headers_for(self, monkeypatch, title, **kwargs) -> dict:
        rec = _Recorder()
        monkeypatch.setattr(notify, "_http_post", rec)
        monkeypatch.setenv("NTFY_TOPIC", "my-topic")
        assert notify.send_ntfy(title, "Body", **kwargs) is True
        return rec.calls[0]["headers"]

    def test_all_header_values_are_latin1_encodable(self, monkeypatch):
        """THE regression assertion — this is what fails on the old code."""
        headers = self._headers_for(
            monkeypatch,
            self.EMOJI_TITLE,
            tags="warning,café",           # non-ASCII tag
            click="https://x/dashboard?q=café",
            priority="high",
        )
        for name, value in headers.items():
            str(value).encode("latin-1")  # raises UnicodeEncodeError if unsafe

    def test_emoji_title_round_trips(self, monkeypatch):
        """The emoji must survive the wire, not be stripped: decoding the RFC 2047
        encoded-word gives back the original title."""
        headers = self._headers_for(monkeypatch, self.EMOJI_TITLE)
        assert headers["Title"] != self.EMOJI_TITLE  # it IS encoded
        assert headers["Title"].startswith("=?UTF-8?B?")
        assert str(make_header(decode_header(headers["Title"]))) == self.EMOJI_TITLE

    def test_non_ascii_tag_round_trips(self, monkeypatch):
        headers = self._headers_for(monkeypatch, "Plain", tags="warning,café")
        assert str(make_header(decode_header(headers["Tags"]))) == "warning,café"

    def test_ascii_title_passed_through_unchanged(self, monkeypatch):
        """Don't needlessly encode — ASCII headers stay human-readable."""
        headers = self._headers_for(
            monkeypatch, "Expense Tracker", tags="bar_chart",
            click="https://x/dashboard", priority="high",
        )
        assert headers["Title"] == "Expense Tracker"
        assert headers["Tags"] == "bar_chart"
        assert headers["Click"] == "https://x/dashboard"
        assert headers["Priority"] == "high"

    def test_body_still_utf8_not_encoded_word(self, monkeypatch):
        """Only headers are latin-1 constrained; the body keeps raw UTF-8."""
        rec = _Recorder()
        monkeypatch.setattr(notify, "_http_post", rec)
        monkeypatch.setenv("NTFY_TOPIC", "my-topic")
        notify.send_ntfy(self.EMOJI_TITLE, "$100.00 — charged 2×.")
        assert rec.calls[0]["body"] == "$100.00 — charged 2×.".encode("utf-8")

    def test_encode_header_helper(self):
        assert notify._encode_header("plain ascii") == "plain ascii"
        assert notify._encode_header("") == ""
        assert notify._encode_header("📊 July 2026 recap") == "=?UTF-8?B?" \
            "8J+TiiBKdWx5IDIwMjYgcmVjYXA=?="

    def test_duplicate_alert_headers_are_latin1_encodable(self, test_session,
                                                          monkeypatch):
        """End-to-end through the real duplicate-alert path (its title is the emoji
        one that broke in production)."""
        rec = _Recorder()
        monkeypatch.setattr(notify, "_http_post", rec)
        monkeypatch.setenv("NTFY_TOPIC", "dupes")
        _add_expense(test_session)
        _add_expense(test_session)

        assert notify.detect_and_notify_new_duplicates(test_session) == 1
        headers = rec.calls[0]["headers"]
        for value in headers.values():
            str(value).encode("latin-1")
        assert str(make_header(decode_header(headers["Title"]))) == self.EMOJI_TITLE


# ---------------------------------------------------------------------------
# send_ntfy_verbose
# ---------------------------------------------------------------------------

class TestSendNtfyVerbose:

    def test_reports_unset_topic(self, monkeypatch):
        monkeypatch.delenv("NTFY_TOPIC", raising=False)
        sent, error = notify.send_ntfy_verbose("t", "m")
        assert sent is False
        assert "NTFY_TOPIC" in error

    def test_reports_send_error_without_leaking_topic(self, monkeypatch):
        def boom(url, body, headers):
            raise RuntimeError(f"HTTP Error 403: Forbidden ({url})")

        monkeypatch.setattr(notify, "_http_post", boom)
        monkeypatch.setenv("NTFY_TOPIC", "super-secret-topic")
        sent, error = notify.send_ntfy_verbose("t", "m")
        assert sent is False
        assert "403" in error
        assert "super-secret-topic" not in error

    def test_success_has_no_error(self, monkeypatch):
        monkeypatch.setattr(notify, "_http_post", _Recorder())
        monkeypatch.setenv("NTFY_TOPIC", "t")
        assert notify.send_ntfy_verbose("t", "m") == (True, None)


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
        # Self-diagnosing: says WHY it didn't send.
        assert body["topic_configured"] is False
        assert "NTFY_TOPIC" in body["error"]
        assert body["server"] == "https://ntfy.sh"

    def test_shape_when_configured(self, client, monkeypatch):
        monkeypatch.setenv("NTFY_TOPIC", "secret-topic")
        monkeypatch.setenv("NTFY_SERVER", "https://ntfy.example.com")
        monkeypatch.setattr(notify, "_http_post", _Recorder())

        resp = client.post("/api/notify/test")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["sent"] is True
        assert body["topic_configured"] is True
        assert body["error"] is None
        assert body["server"] == "https://ntfy.example.com"
        # The topic is a secret — never echoed back.
        assert "secret-topic" not in resp.text

    def test_reports_error_when_send_fails(self, client, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setenv("NTFY_TOPIC", "ledger-dupes-9f3a2b71")
        monkeypatch.setattr(notify, "_http_post", boom)
        body = client.post("/api/notify/test").json()
        assert body["sent"] is False
        assert body["topic_configured"] is True
        assert body["error"] == "network down"

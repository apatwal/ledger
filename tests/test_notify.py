"""
test_notify.py — Real-time duplicate alerts via ntfy (v12).

Coverage
--------
* send_ntfy: returns False (no-op) when NTFY_TOPIC is unset; sends via the
  monkeypatched HTTP seam when the topic is set, passing the right URL/headers.
* NTFY_TOKEN (authenticated publishing): set => an `Authorization: Bearer <token>`
  header; unset => NO Authorization header (anonymous, the old behavior). The token
  is a SECRET: it must never appear in the returned error string OR in the log
  output, even when the underlying exception text contains it.
* Bounded retry: ONE retry (after notify.RETRY_DELAY_SECONDS) on the transient
  statuses 429 / 5xx, and NO retry on anything else (403, generic errors) — this
  code runs inside the Plaid sync path, so the added latency is capped. The 429
  message must be actionable (name NTFY_TOKEN when publishing anonymously).
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

import logging
import urllib.error
from datetime import date
from email.header import make_header, decode_header

import pytest

from src.api import notify
from src.api.models import Transaction

# A realistic ntfy access token (tk_ + 29 chars) — used wherever a test needs the
# real thing, so the redaction assertions exercise a production-shaped secret.
TOKEN = "tk_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_ntfy_token(monkeypatch):
    """Default every test to ANONYMOUS publishing.

    main.py calls load_dotenv() at import time, so a real NTFY_TOKEN in the
    developer's .env would otherwise leak an Authorization header into assertions
    here. Tests that want a token set it explicitly.
    """
    monkeypatch.delenv("NTFY_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def sleeps(monkeypatch) -> list[float]:
    """Record (and never actually perform) the retry pause.

    send_ntfy_verbose sleeps RETRY_DELAY_SECONDS before its one retry; without this
    the suite would really wait 2s per retrying test. Retry tests assert on the
    recorded delays.
    """
    recorded: list[float] = []
    monkeypatch.setattr(notify, "_sleep", recorded.append)
    return recorded


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _http_error(code: int, reason: str = "Too Many Requests") -> urllib.error.HTTPError:
    """A real urllib HTTPError, exactly what _http_post raises in production
    (str() => "HTTP Error 429: Too Many Requests")."""
    return urllib.error.HTTPError("https://ntfy.sh/my-topic", code, reason, {}, None)


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


class _Flaky(_Recorder):
    """_http_post stub with a scripted outcome per attempt.

    Pass one entry per call — an exception to raise, or None to succeed. The last
    entry repeats if it is called again (so `_Flaky(err)` always fails), which is
    what lets a test assert the exact CALL COUNT rather than assuming it.
    """

    def __init__(self, *outcomes):
        super().__init__()
        self.outcomes = list(outcomes) or [None]

    def __call__(self, url, body, headers):
        super().__call__(url, body, headers)
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if outcome is not None:
            raise outcome


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
# NTFY_TOKEN — authenticated publishing (fixes ntfy.sh's per-IP anonymous 429)
# ---------------------------------------------------------------------------

class TestNtfyToken:
    """ntfy.sh rate-limits ANONYMOUS publishing per source IP; Render's egress IP
    is shared across tenants, so production got 429s from other people's traffic.
    Authenticated requests are metered per-ACCOUNT instead."""

    def _headers_for(self, monkeypatch) -> dict:
        rec = _Recorder()
        monkeypatch.setattr(notify, "_http_post", rec)
        monkeypatch.setenv("NTFY_TOPIC", "my-topic")
        assert notify.send_ntfy("Title", "Body") is True
        return rec.calls[0]["headers"]

    def test_authorization_header_when_token_set(self, monkeypatch):
        monkeypatch.setenv("NTFY_TOKEN", TOKEN)
        headers = self._headers_for(monkeypatch)
        assert headers["Authorization"] == f"Bearer {TOKEN}"

    def test_no_authorization_header_when_token_unset(self, monkeypatch):
        """Unset => behave exactly as before: anonymous, no auth header at all."""
        monkeypatch.delenv("NTFY_TOKEN", raising=False)
        assert "Authorization" not in self._headers_for(monkeypatch)

    def test_blank_token_is_treated_as_unset(self, monkeypatch):
        """render.yaml/.env.example ship the key blank — whitespace must not become
        a bogus `Authorization: Bearer ` header."""
        monkeypatch.setenv("NTFY_TOKEN", "   ")
        assert "Authorization" not in self._headers_for(monkeypatch)

    def test_token_helper_strips(self, monkeypatch):
        monkeypatch.setenv("NTFY_TOKEN", f"  {TOKEN}\n")
        assert notify._token() == TOKEN
        monkeypatch.delenv("NTFY_TOKEN", raising=False)
        assert notify._token() == ""

    def test_auth_header_is_latin1_encodable(self, monkeypatch):
        """The Authorization header joins the latin-1 regression guard."""
        monkeypatch.setenv("NTFY_TOKEN", TOKEN)
        rec = _Recorder()
        monkeypatch.setattr(notify, "_http_post", rec)
        monkeypatch.setenv("NTFY_TOPIC", "my-topic")
        notify.send_ntfy("⚠️ Possible duplicate charge", "Body",
                         tags="warning,café", priority="high")
        for value in rec.calls[0]["headers"].values():
            str(value).encode("latin-1")  # raises UnicodeEncodeError if unsafe


class TestTokenNeverLeaks:
    """The token is a secret. It must not reach an API response or a log file even
    when the underlying exception text embeds it (urllib errors can echo headers)."""

    def _fail_with_token_in_message(self, monkeypatch):
        def boom(url, body, headers):
            raise RuntimeError(
                f"rejected request headers Authorization: Bearer {TOKEN}"
            )

        monkeypatch.setattr(notify, "_http_post", boom)
        monkeypatch.setenv("NTFY_TOPIC", "super-secret-topic")
        monkeypatch.setenv("NTFY_TOKEN", TOKEN)

    def test_token_redacted_from_error(self, monkeypatch):
        self._fail_with_token_in_message(monkeypatch)
        sent, error = notify.send_ntfy_verbose("t", "m")
        assert sent is False
        assert TOKEN not in error
        assert "<token>" in error
        # The rest of the message survives — still diagnosable.
        assert "rejected request headers" in error

    def test_token_redacted_from_logs(self, monkeypatch, caplog):
        self._fail_with_token_in_message(monkeypatch)
        with caplog.at_level(logging.WARNING, logger="expense_tracker.notify"):
            notify.send_ntfy_verbose("t", "m")
        assert caplog.text  # it DID log the failure
        assert TOKEN not in caplog.text
        assert "<token>" in caplog.text

    def test_topic_and_token_both_redacted(self, monkeypatch):
        def boom(url, body, headers):
            raise RuntimeError(f"POST {url} failed with token {TOKEN}")

        monkeypatch.setattr(notify, "_http_post", boom)
        monkeypatch.setenv("NTFY_TOPIC", "super-secret-topic")
        monkeypatch.setenv("NTFY_TOKEN", TOKEN)
        _, error = notify.send_ntfy_verbose("t", "m")
        assert "super-secret-topic" not in error
        assert TOKEN not in error

    def test_redact_helper(self):
        assert notify._redact("boom", "", "") == "boom"
        assert notify._redact(f"Bearer {TOKEN}", "", TOKEN) == "Bearer <token>"
        assert notify._redact(f"x {TOKEN} y", "", TOKEN) == "x <token> y"
        # Topic redaction is unchanged by the new parameter.
        assert notify._redact("https://ntfy.sh/abc", "abc", "") == \
            "https://ntfy.sh/<topic>"
        # A short stub topic must not corrupt genuine error text.
        assert notify._redact("network down", "n", "") == "network down"


# ---------------------------------------------------------------------------
# Bounded retry on transient failures (429 / 5xx)
# ---------------------------------------------------------------------------

class TestRetry:
    """Exactly ONE retry, and only for transient statuses. This runs inside the
    Plaid sync path, so the worst case is a single RETRY_DELAY_SECONDS pause."""

    def _run(self, monkeypatch, *outcomes, token: str | None = None):
        flaky = _Flaky(*outcomes)
        monkeypatch.setattr(notify, "_http_post", flaky)
        monkeypatch.setenv("NTFY_TOPIC", "my-topic")
        if token:
            monkeypatch.setenv("NTFY_TOKEN", token)
        return flaky, notify.send_ntfy_verbose("t", "m")

    def test_429_retried_once_then_gives_up(self, monkeypatch, sleeps):
        flaky, (sent, error) = self._run(monkeypatch, _http_error(429))
        assert len(flaky.calls) == 2          # original + ONE retry, no more
        assert sent is False
        assert "429" in error
        assert "rate-limit" in error.lower()
        assert sleeps == [notify.RETRY_DELAY_SECONDS]

    def test_429_then_success(self, monkeypatch, sleeps):
        flaky, result = self._run(monkeypatch, _http_error(429), None)
        assert result == (True, None)
        assert len(flaky.calls) == 2
        assert sleeps == [notify.RETRY_DELAY_SECONDS]

    def test_5xx_retried_once(self, monkeypatch, sleeps):
        flaky, (sent, error) = self._run(
            monkeypatch, _http_error(503, "Service Unavailable"))
        assert len(flaky.calls) == 2
        assert sent is False
        assert "503" in error
        assert sleeps == [notify.RETRY_DELAY_SECONDS]

    def test_5xx_then_success(self, monkeypatch):
        flaky, result = self._run(
            monkeypatch, _http_error(500, "Internal Server Error"), None)
        assert result == (True, None)
        assert len(flaky.calls) == 2

    def test_403_not_retried(self, monkeypatch, sleeps):
        """A bad/expired token never fixes itself — retrying only adds latency."""
        flaky, (sent, error) = self._run(
            monkeypatch, _http_error(403, "Forbidden"), token=TOKEN)
        assert len(flaky.calls) == 1
        assert sent is False
        assert "403" in error
        assert sleeps == []

    def test_401_not_retried(self, monkeypatch):
        flaky, _ = self._run(monkeypatch, _http_error(401, "Unauthorized"))
        assert len(flaky.calls) == 1

    def test_generic_exception_not_retried(self, monkeypatch, sleeps):
        flaky, (sent, error) = self._run(monkeypatch, RuntimeError("network down"))
        assert len(flaky.calls) == 1
        assert (sent, error) == (False, "network down")
        assert sleeps == []

    def test_success_never_sleeps(self, monkeypatch, sleeps):
        flaky, result = self._run(monkeypatch, None)
        assert result == (True, None)
        assert len(flaky.calls) == 1
        assert sleeps == []

    def test_retry_reuses_same_url_body_and_headers(self, monkeypatch):
        flaky, _ = self._run(monkeypatch, _http_error(429), token=TOKEN)
        first, second = flaky.calls
        assert first == second
        assert second["headers"]["Authorization"] == f"Bearer {TOKEN}"

    def test_send_ntfy_still_never_raises_on_retryable_error(self, monkeypatch):
        monkeypatch.setattr(notify, "_http_post", _Flaky(_http_error(429)))
        monkeypatch.setenv("NTFY_TOPIC", "my-topic")
        assert notify.send_ntfy("t", "m") is False

    def test_status_code_helper(self):
        assert notify._status_code(_http_error(429)) == 429
        # Stubbed/wrapped errors: parsed out of urllib's message form.
        assert notify._status_code(
            RuntimeError("HTTP Error 502: Bad Gateway")) == 502
        assert notify._status_code(RuntimeError("network down")) is None

    def test_is_retryable_helper(self):
        assert notify._is_retryable(429) is True
        assert notify._is_retryable(500) is True
        assert notify._is_retryable(599) is True
        assert notify._is_retryable(403) is False
        assert notify._is_retryable(404) is False
        assert notify._is_retryable(200) is False
        assert notify._is_retryable(None) is False


class Test429Message:
    """The 429 error must say what to DO about it — the whole point of the
    self-diagnosing /api/notify/test endpoint."""

    def _error(self, monkeypatch, token: str | None = None) -> str:
        monkeypatch.setattr(notify, "_http_post", _Flaky(_http_error(429)))
        monkeypatch.setenv("NTFY_TOPIC", "my-topic")
        if token:
            monkeypatch.setenv("NTFY_TOKEN", token)
        sent, error = notify.send_ntfy_verbose("t", "m")
        assert sent is False
        return error

    def test_anonymous_429_names_ntfy_token_as_the_fix(self, monkeypatch):
        error = self._error(monkeypatch)
        assert "NTFY_TOKEN" in error
        assert "ANONYMOUS" in error
        assert "IP" in error

    def test_authenticated_429_blames_account_quota(self, monkeypatch):
        error = self._error(monkeypatch, token=TOKEN)
        assert "quota" in error.lower()
        assert "NTFY_TOKEN" not in error   # already set — don't misdirect
        assert TOKEN not in error

    def test_non_429_gets_no_rate_limit_hint(self, monkeypatch):
        monkeypatch.setattr(notify, "_http_post",
                            _Flaky(_http_error(404, "Not Found")))
        monkeypatch.setenv("NTFY_TOPIC", "my-topic")
        _, error = notify.send_ntfy_verbose("t", "m")
        assert error == "HTTP Error 404: Not Found"


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

    def test_token_configured_false_when_unset(self, client, monkeypatch):
        monkeypatch.delenv("NTFY_TOKEN", raising=False)
        monkeypatch.setenv("NTFY_TOPIC", "t")
        monkeypatch.setattr(notify, "_http_post", _Recorder())
        assert client.post("/api/notify/test").json()["token_configured"] is False

    def test_token_configured_true_and_value_never_echoed(self, client, monkeypatch):
        """Confirms NTFY_TOKEN reached the process — as a BOOL only."""
        monkeypatch.setenv("NTFY_TOPIC", "secret-topic")
        monkeypatch.setenv("NTFY_TOKEN", TOKEN)
        monkeypatch.setattr(notify, "_http_post", _Recorder())

        resp = client.post("/api/notify/test")
        assert resp.status_code == 200, resp.text
        assert resp.json()["token_configured"] is True
        assert TOKEN not in resp.text

    def test_429_response_is_actionable(self, client, monkeypatch):
        """The exact production symptom: reports 429 and how to fix it, with no
        secret in the payload."""
        monkeypatch.setenv("NTFY_TOPIC", "ledger-dupes-9f3a2b71")
        monkeypatch.delenv("NTFY_TOKEN", raising=False)
        monkeypatch.setattr(notify, "_http_post", _Flaky(_http_error(429)))

        body = client.post("/api/notify/test").json()
        assert body["sent"] is False
        assert body["topic_configured"] is True
        assert body["token_configured"] is False
        assert "429" in body["error"]
        assert "NTFY_TOKEN" in body["error"]

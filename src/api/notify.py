"""
Real-time notifications via ntfy.sh (v12).

GATED by env like the rest of the app: with NTFY_TOPIC unset, send_ntfy() is a
no-op that returns False, so local dev + the whole test suite stay green with no
network calls. When NTFY_TOPIC is set, a POST is issued to
    {NTFY_SERVER or https://ntfy.sh}/{NTFY_TOPIC}
with the message as the body and ntfy's Title/Click/Tags/Priority request headers
(ntfy reads these headers). We use urllib only (no new dependency), mirroring the
Clerk user-lookup pattern in auth.py — including a real User-Agent.

Header encoding (bug fix): HTTP headers go out latin-1 (http.client), so a raw
emoji in Title — e.g. "⚠️ Possible duplicate charge" — raises UnicodeEncodeError
BEFORE the request is even sent, silently killing every notification. Every
header value therefore passes through `_encode_header`, which leaves pure-ASCII
values untouched and rewrites non-ASCII ones as an RFC 2047 encoded-word
(=?UTF-8?B?...?=); ntfy decodes that and the emoji survives. The BODY is
unaffected — it is already sent as UTF-8 bytes with charset=utf-8.

Authentication (NTFY_TOKEN): ntfy.sh rate-limits ANONYMOUS publishing per source
IP. On a shared-IP host like Render the outbound IP is shared across tenants, so
we get HTTP 429 from traffic that isn't even ours. ntfy applies its limits
per-ACCOUNT for authenticated requests, so setting NTFY_TOKEN (an ntfy access
token) and publishing with `Authorization: Bearer <token>` fixes it. Unset =>
anonymous publishing exactly as before. The token is a SECRET: it is redacted out
of every error string and log line by `_redact`, alongside the topic.

Retry: a single bounded retry (after ~2s) on the transient statuses — HTTP 429 and
5xx. Anything else (403 bad token, DNS failure, timeout) fails immediately. This
code runs inside the Plaid sync path, so added latency is capped at one short
sleep and must never stall a sync.

Nothing here EVER raises: a notification failure must never break a sync. The
actual HTTP send is isolated in `_http_post` so tests can monkeypatch it (and the
retry pause in `_sleep`, so the suite never waits). `send_ntfy_verbose` returns
(sent, error) for the self-diagnosing /api/notify/test endpoint; `send_ntfy` is
the bool-only wrapper used everywhere else.

Duplicate alerts (no-spam):
  detect_and_notify_new_duplicates(db) re-uses compute_duplicate_groups() and
  fires ONE ntfy per group that still has an un-notified member, then flips
  dup_notified=True on every row in that group. A standing duplicate is therefore
  alerted exactly once; a genuinely new duplicate charge (a fresh row that joins
  or forms a group) triggers a new alert on the next sync.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import time
import urllib.request

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Transaction

logger = logging.getLogger("expense_tracker.notify")


# ── Config helpers ─────────────────────────────────────────────────────────────

def _topic() -> str:
    return (os.environ.get("NTFY_TOPIC") or "").strip()


def _server() -> str:
    return (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").strip().rstrip("/")


def _token() -> str:
    """ntfy access token (`tk_...`). Empty => publish anonymously (the old
    behavior). Set it to escape ntfy.sh's per-IP anonymous rate limit."""
    return (os.environ.get("NTFY_TOKEN") or "").strip()


def _app_url() -> str:
    return (os.environ.get("APP_URL") or "").strip().rstrip("/")


# ── Header encoding (latin-1 safety) ───────────────────────────────────────────

def _encode_header(value: str) -> str:
    """Make a header value safe for http.client's latin-1 header encoding.

    Pure-ASCII values are returned unchanged (headers/logs stay readable). Anything
    with non-ASCII (emoji titles, an accented merchant or account nickname) becomes
    an RFC 2047 base64 encoded-word — ntfy decodes it, so "⚠️ Possible duplicate
    charge" arrives intact instead of blowing up with UnicodeEncodeError.
    """
    text = str(value)
    if text.isascii():
        return text
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"=?UTF-8?B?{b64}?="


def _redact(reason: str, topic: str, token: str = "") -> str:
    """Strip the ntfy topic AND access token (both secrets) out of an error string
    before it is logged or handed to an API caller.

    The topic only ever reaches an exception message via the URL we built, so the
    `/{topic}` path form is the one that matters. A bare topic is also scrubbed,
    but only when it is long enough to be a real topic — blind substring
    replacement on a short test stub like "t" would corrupt genuine error text
    ("network down" → "ne<topic>work down").

    The token reaches an exception message via the header dict some libraries echo
    back, so the `Bearer {token}` form is scrubbed unconditionally; the bare token
    is scrubbed under the same length guard (real ntfy tokens are `tk_` + 29 chars,
    so the guard never actually spares one).
    """
    out = reason
    if topic:
        out = out.replace(f"/{topic}", "/<topic>")
        if len(topic) >= 6:
            out = out.replace(topic, "<topic>")
    if token:
        out = out.replace(f"Bearer {token}", "Bearer <token>")
        if len(token) >= 6:
            out = out.replace(token, "<token>")
    return out


# ── Transient-failure retry ─────────────────────────────────────────────────────

RETRY_DELAY_SECONDS = 2.0


def _sleep(seconds: float) -> None:
    """The pause before the single retry. A seam so tests never actually wait."""
    time.sleep(seconds)


def _status_code(exc: BaseException) -> int | None:
    """HTTP status behind a failed send, or None if it wasn't an HTTP error.

    `urllib.error.HTTPError` carries `.code`; we also parse urllib's message form
    ("HTTP Error 429: Too Many Requests") so a stubbed/wrapped error still routes
    to the right retry decision.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    m = re.search(r"HTTP Error (\d{3})", str(exc))
    return int(m.group(1)) if m else None


def _is_retryable(status: int | None) -> bool:
    """Retry only genuinely transient statuses: 429 (rate limited) and 5xx (server
    side). NOT 4xx auth/permission failures — those never fix themselves and a
    retry just doubles the latency of a sync."""
    return status == 429 or (status is not None and 500 <= status <= 599)


# ── HTTP send (seam: _http_post) ────────────────────────────────────────────────

def _http_post(url: str, body: bytes, headers: dict) -> None:
    """Issue the actual ntfy POST. Isolated so tests can monkeypatch it. Mirrors
    auth.py's urllib usage (real User-Agent — some hosts 403 the default UA)."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=5):  # noqa: S310 (trusted host, gated by env)
        pass


def send_ntfy_verbose(
    title: str,
    message: str,
    *,
    click: str | None = None,
    tags: str | None = None,
    priority: str | None = None,
) -> tuple[bool, str | None]:
    """Send a push notification via ntfy and report WHY it failed.

    Returns (sent, error): (True, None) on a successful POST, else (False, reason)
    — "NTFY_TOPIC is not set" for the gated no-op, or the exception text for a
    network/HTTP failure (with the topic AND token redacted, never leaked). A 429
    gets an actionable hint appended. Retries ONCE on 429/5xx. NEVER raises.
    """
    topic = _topic()
    if not topic:
        return False, "NTFY_TOPIC is not set"
    token = _token()
    url = f"{_server()}/{topic}"
    headers = {
        "User-Agent": "expense-tracker/1.0",
        "Content-Type": "text/plain; charset=utf-8",
        "Title": _encode_header(title),
    }
    # Authenticated publishing => ntfy meters us per-account instead of per-IP
    # (the shared Render egress IP is what earns anonymous 429s). Deliberately NOT
    # run through _encode_header: an RFC 2047 encoded-word is not valid in an
    # Authorization header, and a real ntfy token (`tk_` + base62) is pure ASCII.
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if click:
        headers["Click"] = _encode_header(click)
    if tags:
        headers["Tags"] = _encode_header(tags)
    if priority:
        headers["Priority"] = _encode_header(priority)

    body = message.encode("utf-8")
    error = ""
    status: int | None = None
    for attempt in (1, 2):  # original + at most ONE retry
        try:
            _http_post(url, body, headers)
            return True, None
        except Exception as e:  # network/timeout/HTTP error — never break the caller
            status = _status_code(e)
            # Log the REDACTED text: the raw exception can echo the URL (topic) or
            # the Authorization header (token) straight into the log.
            error = _redact(str(e) or e.__class__.__name__, topic, token)
            logger.warning("[notify] ntfy send failed (attempt %d/2): %s",
                           attempt, error)
            if attempt == 1 and _is_retryable(status):
                _sleep(RETRY_DELAY_SECONDS)
                continue
            break

    if status == 429:
        error += (
            " — ntfy account message quota exhausted; wait for it to reset or "
            "upgrade the ntfy plan."
            if token else
            " — ntfy.sh rate-limits ANONYMOUS publishing per source IP, and this "
            "host's outbound IP is shared with other tenants. Fix: create an ntfy "
            "account, generate an access token, and set NTFY_TOKEN so publishing "
            "is metered per-account."
        )
    return False, error


def send_ntfy(
    title: str,
    message: str,
    *,
    click: str | None = None,
    tags: str | None = None,
    priority: str | None = None,
) -> bool:
    """Send a push notification via ntfy. Returns True on a successful POST, False
    when NTFY_TOPIC is unset (no-op) or the send failed. NEVER raises."""
    sent, _ = send_ntfy_verbose(
        title, message, click=click, tags=tags, priority=priority
    )
    return sent


# ── Duplicate detection + notification ──────────────────────────────────────────

def detect_and_notify_new_duplicates(db: Session) -> int:
    """Detect duplicate expense groups and push ONE ntfy alert per group that has
    at least one un-notified member, then mark every member notified so the same
    standing duplicate is never re-alerted. Returns the number of alerts sent.

    Best-effort: any failure is swallowed (logged) — this must NEVER break a sync.
    """
    # Imported here (not at module top) to avoid an import cycle with duplicates.py.
    from .routes.duplicates import compute_duplicate_groups

    try:
        rows = db.execute(
            select(Transaction).where(
                Transaction.type == "expense",
                Transaction.dup_dismissed == False,  # noqa: E712
            ).order_by(Transaction.date.desc(), Transaction.id.desc())
        ).scalars().all()
    except Exception as e:
        logger.warning("[notify] duplicate query failed: %s", e)
        return 0

    try:
        groups = compute_duplicate_groups(rows)
    except Exception as e:
        logger.warning("[notify] duplicate grouping failed: %s", e)
        return 0

    # Map transaction id -> ORM row so we can flip dup_notified after alerting.
    by_id = {r.id: r for r in rows}
    app_url = _app_url()
    sent = 0

    for group in groups:
        members = [by_id[t.id] for t in group.transactions if t.id in by_id]
        if not members:
            continue
        if all(bool(getattr(m, "dup_notified", False)) for m in members):
            continue  # already alerted this standing duplicate — no spam

        title = "⚠️ Possible duplicate charge"
        desc = group.description or "Unknown"
        acct = group.account or "account"
        message = (
            f"${group.amount:.2f} at {desc} on {group.date} "
            f"({acct}) — charged {group.count}×."
        )
        click = f"{app_url}/dashboard" if app_url else None
        try:
            send_ntfy(title, message, click=click, tags="warning,money_with_wings",
                      priority="high")
            sent += 1
        except Exception as e:  # send_ntfy already swallows, but belt-and-suspenders
            logger.warning("[notify] send raised unexpectedly: %s", e)

        for m in members:
            m.dup_notified = True

    if sent:
        try:
            db.commit()
        except Exception as e:
            logger.warning("[notify] commit after dup notify failed: %s", e)
            db.rollback()
    return sent

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

Nothing here EVER raises: a notification failure must never break a sync. The
actual HTTP send is isolated in `_http_post` so tests can monkeypatch it.
`send_ntfy_verbose` returns (sent, error) for the self-diagnosing
/api/notify/test endpoint; `send_ntfy` is the bool-only wrapper used everywhere
else.

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


def _redact(reason: str, topic: str) -> str:
    """Strip the ntfy topic (a secret — it is the whole auth story for a topic) out
    of an error string before it is handed to an API caller.

    The topic only ever reaches an exception message via the URL we built, so the
    `/{topic}` path form is the one that matters. A bare topic is also scrubbed,
    but only when it is long enough to be a real topic — blind substring
    replacement on a short test stub like "t" would corrupt genuine error text
    ("network down" → "ne<topic>work down").
    """
    if not topic:
        return reason
    out = reason.replace(f"/{topic}", "/<topic>")
    if len(topic) >= 6:
        out = out.replace(topic, "<topic>")
    return out


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
    network/HTTP failure (with the topic redacted, never leaked). NEVER raises.
    """
    topic = _topic()
    if not topic:
        return False, "NTFY_TOPIC is not set"
    url = f"{_server()}/{topic}"
    headers = {
        "User-Agent": "expense-tracker/1.0",
        "Content-Type": "text/plain; charset=utf-8",
        "Title": _encode_header(title),
    }
    if click:
        headers["Click"] = _encode_header(click)
    if tags:
        headers["Tags"] = _encode_header(tags)
    if priority:
        headers["Priority"] = _encode_header(priority)
    try:
        _http_post(url, message.encode("utf-8"), headers)
        return True, None
    except Exception as e:  # network/timeout/HTTP error — never break the caller
        logger.warning("[notify] ntfy send failed: %s", e)
        return False, _redact(str(e) or e.__class__.__name__, topic)


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

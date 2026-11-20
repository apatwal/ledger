"""
Unhandled-error reporting — turn a server crash into something the user can read.

Without this, an exception nobody caught comes back as a plain-text
"Internal Server Error" body, and the browser has nothing to show but "500".
Here every unhandled exception (and every FastAPI ResponseValidationError, which
is a crash too: the route ran but returned data its response_model rejects)
becomes a JSON 500:

    {
      "detail": "Server error: ResponseValidationError: row 0 (id 1662) amount: ... (error ID 3f9c2a1b)",
      "error": {"id": "3f9c2a1b", "type": "ResponseValidationError",
                "message": "row 0 (id 1662) amount: ...", "status": 500,
                "method": "GET", "path": "/api/transactions"}
    }

`detail` is a plain string so any client that only reads `detail` (the shape
every HTTPException already uses) still shows something sensible. `error` is the
structured version the UI's Details panel renders.

The exception TYPE and MESSAGE go to the browser; the stack trace never does —
it goes to the server log only, tagged with the same error ID so it can be found
in Render's logs. HTTPException / RequestValidationError (4xx) responses are NOT
touched: they keep FastAPI's default shapes, which the frontend already parses.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import ResponseValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("expense_tracker.errors")

# Keep the browser-facing cause short: it is a one-line diagnosis, not a dump.
_MAX_MESSAGE_LEN = 300
_MAX_INPUT_REPR = 40


def new_error_id() -> str:
    """Short random ID (8 hex chars) linking the on-screen error to its log line."""
    return secrets.token_hex(4)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _row_id(body: Any, index: int) -> Any:
    """Best-effort: the `id` of row `index` in a list response body, else None.

    FastAPI hands ResponseValidationError the raw value the route returned, so a
    row is usually an ORM object (attribute `id`) or a dict (key "id")."""
    if not isinstance(body, (list, tuple)) or not 0 <= index < len(body):
        return None
    row = body[index]
    if isinstance(row, dict):
        return row.get("id")
    return getattr(row, "id", None)


def describe_response_validation(exc: ResponseValidationError) -> str:
    """Turn pydantic's error list into one readable line.

    loc looks like ('response', 0, 'amount') for a list response or
    ('response', 'amount') for a single object. Renders the first error as
    "row 0 (id 1662) amount: Input should be greater than 0 (got 0.0)" and
    appends "(+N more)" when there are others."""
    errors = list(exc.errors() or [])
    if not errors:
        return "the response did not match its schema"
    first = errors[0]
    loc = [p for p in (first.get("loc") or ()) if p != "response"]

    where = ""
    if loc and isinstance(loc[0], int):
        index = loc.pop(0)
        row_id = _row_id(getattr(exc, "body", None), index)
        where = f"row {index}" + (f" (id {row_id})" if row_id is not None else "") + " "
    field = ".".join(str(p) for p in loc) or "(root)"

    text = f"{where}{field}: {first.get('msg') or 'invalid value'}"
    if "input" in first:
        text += f" (got {_truncate(repr(first['input']), _MAX_INPUT_REPR)})"
    if len(errors) > 1:
        text += f" (+{len(errors) - 1} more)"
    return text


def _describe(exc: BaseException) -> str:
    if isinstance(exc, ResponseValidationError):
        return describe_response_validation(exc)
    return str(exc) or "(no message)"


def error_payload(exc: BaseException, method: str, path: str) -> dict:
    """Log the full traceback under a fresh error ID; return the JSON body."""
    error_id = new_error_id()
    exc_type = type(exc).__name__
    message = _truncate(_describe(exc), _MAX_MESSAGE_LEN)
    # Full traceback, server-side only. exc_info is explicit because the
    # middleware path logs from inside its own except block, but the
    # ResponseValidationError handler is called outside one.
    logger.error(
        "Unhandled %s [error_id=%s] %s %s: %s",
        exc_type, error_id, method, path, message,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return {
        "detail": f"Server error: {exc_type}: {message} (error ID {error_id})",
        "error": {
            "id": error_id,
            "type": exc_type,
            "message": message,
            "status": 500,
            "method": method,
            "path": path,
        },
    }


async def _response_validation_handler(request: Request, exc: ResponseValidationError) -> JSONResponse:
    return JSONResponse(error_payload(exc, request.method, request.url.path), status_code=500)


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Fallback only: UnhandledErrorMiddleware normally catches first. This fires
    # for an exception raised OUTSIDE it (e.g. in an outer middleware), where
    # Starlette's ServerErrorMiddleware is the last line of defence.
    return JSONResponse(error_payload(exc, request.method, request.url.path), status_code=500)


class UnhandledErrorMiddleware:
    """Pure-ASGI catch-all that converts an uncaught exception into the JSON 500.

    Why a middleware and not just `@app.exception_handler(Exception)`: Starlette
    runs the Exception handler in ServerErrorMiddleware, the OUTERMOST layer —
    outside CORSMiddleware — so that response would carry no CORS headers and a
    cross-origin browser could not read its body. Registered inside CORS (see
    install_error_handlers), this layer's response passes back out through CORS
    like any normal response. Auth is a route dependency, not a middleware, so
    it raises inside this layer too.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if started:
                # Headers are already on the wire — too late to swap in a JSON
                # body. Let the server log it and drop the connection.
                raise
            payload = error_payload(exc, scope.get("method", ""), scope.get("path", ""))
            await JSONResponse(payload, status_code=500)(scope, receive, send)


def install_error_handlers(app: FastAPI) -> None:
    """Register the JSON-500 handlers on `app`.

    Call this BEFORE adding CORSMiddleware: Starlette wraps later-added
    middleware around earlier ones, so adding this first keeps it inside CORS."""
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_exception_handler(ResponseValidationError, _response_validation_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

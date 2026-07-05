"""
AI layer — Google Gemini (google-genai SDK).

Powers three product features on top of the existing query logic:
  * Q&A assistant  -> run_assistant()      (function-calling loop over the stats/search tools)
  * Auto-categorize -> suggest_category()  (structured JSON output)
  * Insights        -> generate_insights() (one-shot narrative)

The model never touches the DB directly: every tool routes through execute_tool(),
which calls the SAME query functions the REST API uses. execute_tool() is pure
(no network, no key) so the tool->query mapping is unit-testable offline.

Config (environment):
  GEMINI_API_KEY   required for any AI call (absent -> features disabled / 503)
  GEMINI_MODEL     optional, defaults to "gemini-2.5-flash"
"""
from __future__ import annotations

import os
import json
from datetime import date
from typing import Any, Optional

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from .models import Transaction
from .routes import stats as stats_routes

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

TODAY = "2026-06-28"

SYSTEM_INSTRUCTION = (
    "You are the assistant inside a personal expense-tracker app called Expense Tracker. "
    f"Today's date is {TODAY}. All amounts are in US dollars (USD). "
    "Answer questions about the user's own money using the tools provided — never guess at "
    "figures, always call a tool to get real data, then state the numbers you found. "
    "Note: 'transfer' transactions (money moved between the user's own accounts) are excluded "
    "from all income/expense/savings statistics. "
    "Be concise and conversational: lead with the direct answer and the specific dollar "
    "figures, then a short supporting detail if useful. Do not use markdown headings."
)


class AINotConfigured(RuntimeError):
    """Raised when an AI call is attempted without GEMINI_API_KEY set."""


def is_enabled() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def get_client():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise AINotConfigured("GEMINI_API_KEY is not set")
    from google import genai  # imported lazily so the app boots without the key

    return genai.Client(api_key=key)


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return date.fromisoformat(value)


# ── Tool dispatch (pure — reuses the existing query functions) ─────────────────

def execute_tool(db: Session, name: str, args: dict[str, Any]) -> Any:
    """Run a single assistant tool by name against the DB. JSON-serializable result."""
    start = _parse_date(args.get("start_date"))
    end = _parse_date(args.get("end_date"))

    if name == "get_financial_summary":
        return stats_routes.get_summary(start_date=start, end_date=end, db=db).model_dump()

    if name == "get_spending_by_category":
        rows = stats_routes.get_by_category(
            start_date=start, end_date=end, type=args.get("type") or "expense", db=db
        )
        return [r.model_dump() for r in rows]

    if name == "get_income_expense_over_time":
        rows = stats_routes.get_over_time(
            granularity=args.get("granularity") or "month",
            start_date=start,
            end_date=end,
            db=db,
        )
        return [r.model_dump() for r in rows]

    if name == "search_transactions":
        conditions = []
        if start:
            conditions.append(Transaction.date >= start)
        if end:
            conditions.append(Transaction.date <= end)
        if args.get("type"):
            conditions.append(Transaction.type == args["type"])
        if args.get("category"):
            conditions.append(Transaction.category == args["category"])
        stmt = select(Transaction).order_by(Transaction.date.desc(), Transaction.id.desc())
        if conditions:
            stmt = stmt.where(and_(*conditions))
        try:
            limit = int(args.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        stmt = stmt.limit(min(max(limit, 1), 200))
        rows = db.execute(stmt).scalars().all()
        return [
            {
                "date": str(t.date),
                "amount": t.amount,
                "type": t.type,
                "category": t.category,
                "description": t.description,
            }
            for t in rows
        ]

    raise ValueError(f"Unknown tool: {name}")


# ── Q&A assistant (manual function-calling loop) ───────────────────────────────

_DATE_PROPS = {
    "start_date": {"type": "string", "description": "Inclusive start date, YYYY-MM-DD. Omit for all-time."},
    "end_date": {"type": "string", "description": "Inclusive end date, YYYY-MM-DD. Omit for all-time."},
}

_TOOL_SCHEMAS = [
    {
        "name": "get_financial_summary",
        "description": "Totals for a date range: total_income, total_expense, net, savings, savings_rate, and transaction count. Transfers are excluded.",
        "parameters_json_schema": {"type": "object", "properties": dict(_DATE_PROPS), "required": []},
    },
    {
        "name": "get_spending_by_category",
        "description": "Totals grouped by category for a date range. Use to find the biggest categories.",
        "parameters_json_schema": {
            "type": "object",
            "properties": {**_DATE_PROPS, "type": {"type": "string", "enum": ["expense", "income"], "description": "Defaults to expense."}},
            "required": [],
        },
    },
    {
        "name": "get_income_expense_over_time",
        "description": "Income, expense, and net per time period. Use for trends over time.",
        "parameters_json_schema": {
            "type": "object",
            "properties": {"granularity": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "Defaults to month."}, **_DATE_PROPS},
            "required": [],
        },
    },
    {
        "name": "search_transactions",
        "description": "List individual transactions matching the filters, newest first. Use to inspect specific entries.",
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                **_DATE_PROPS,
                "type": {"type": "string", "enum": ["income", "expense", "transfer"]},
                "category": {"type": "string"},
                "limit": {"type": "integer", "description": "Max rows (default 50, max 200)."},
            },
            "required": [],
        },
    },
]

_MAX_TOOL_ROUNDS = 8


def run_assistant(db: Session, messages: list[dict]) -> dict:
    """Run the agentic chat loop. messages: [{role: 'user'|'assistant', content: str}]."""
    from google.genai import types

    client = get_client()

    tool = types.Tool(function_declarations=[
        types.FunctionDeclaration(**schema) for schema in _TOOL_SCHEMAS
    ])
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=[tool],
        temperature=0.2,
    )

    contents = [
        types.Content(
            role="model" if m.get("role") in ("assistant", "model") else "user",
            parts=[types.Part.from_text(text=m.get("content", ""))],
        )
        for m in messages
        if m.get("content")
    ]

    tool_calls: list[str] = []
    response = client.models.generate_content(model=MODEL, contents=contents, config=config)

    for _ in range(_MAX_TOOL_ROUNDS):
        calls = response.function_calls or []
        if not calls:
            break
        # record the model's tool-call turn, then answer every call
        contents.append(response.candidates[0].content)
        parts = []
        for fc in calls:
            tool_calls.append(fc.name)
            args = dict(fc.args or {})
            try:
                result = execute_tool(db, fc.name, args)
                payload = {"result": result}
            except Exception as e:  # surface to the model so it can recover
                payload = {"error": str(e)}
            parts.append(types.Part.from_function_response(name=fc.name, response=payload))
        contents.append(types.Content(role="tool", parts=parts))
        response = client.models.generate_content(model=MODEL, contents=contents, config=config)

    reply = (response.text or "").strip() or "I couldn't find an answer to that."
    seen: set[str] = set()
    deduped = [t for t in tool_calls if not (t in seen or seen.add(t))]
    return {"reply": reply, "tool_calls": deduped}


# ── Auto-categorize (structured output) ────────────────────────────────────────

def suggest_category(
    description: str,
    amount: Optional[float],
    type_: str,
    known_categories: list[str],
) -> dict:
    """Suggest the single best category for a transaction. Returns {category, confidence}."""
    from google.genai import types
    from pydantic import BaseModel

    class _Suggestion(BaseModel):
        category: str
        confidence: float

    client = get_client()
    prompt = (
        f"Transaction to categorize:\n"
        f"- type: {type_}\n"
        f"- amount: {amount}\n"
        f"- description: {description!r}\n\n"
        f"Known categories: {', '.join(known_categories)}.\n"
        "Choose the single best category. Strongly prefer an existing known category; "
        "only invent a short new one (1-2 words) if none fit. "
        "confidence is 0.0-1.0 for how sure you are."
    )
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction="You categorize personal-finance transactions into one concise category.",
            response_mime_type="application/json",
            response_schema=_Suggestion,
            temperature=0.0,
        ),
    )
    parsed = getattr(response, "parsed", None)
    if parsed is None:
        data = json.loads(response.text)
        parsed = _Suggestion(**data)
    confidence = max(0.0, min(1.0, float(parsed.confidence)))
    return {"category": parsed.category.strip(), "confidence": round(confidence, 2)}


# ── Insights (one-shot narrative) ──────────────────────────────────────────────

def generate_insights(db: Session, start_date: Optional[date], end_date: Optional[date]) -> str:
    """Generate a short narrative read of the user's finances for the period."""
    from google.genai import types

    client = get_client()
    summary = stats_routes.get_summary(start_date=start_date, end_date=end_date, db=db).model_dump()
    by_category = [c.model_dump() for c in stats_routes.get_by_category(
        start_date=start_date, end_date=end_date, type="expense", db=db
    )]
    over_time = [o.model_dump() for o in stats_routes.get_over_time(
        granularity="month", start_date=start_date, end_date=end_date, db=db
    )]

    data = {"summary": summary, "expenses_by_category": by_category, "monthly": over_time}
    prompt = (
        "Here is the user's financial data for the selected period (USD; transfers excluded "
        "from all figures):\n"
        f"{json.dumps(data)}\n\n"
        "Write a concise, friendly read of their finances in 3-5 sentences. Cover: whether "
        "they are net positive ('in the black') or negative ('in the red') and by how much, "
        "their largest expense categories with dollar figures, their savings rate, and any "
        "notable month-to-month trend. Use specific numbers and plain language. No markdown "
        "headings or bullet lists — just short sentences."
    )
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.4),
    )
    return (response.text or "").strip() or "No insights available for this period."

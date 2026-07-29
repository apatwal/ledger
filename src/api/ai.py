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
    "You can both answer questions AND take actions on the user's behalf using the tools "
    "provided. For questions, never guess at figures — always call a read tool to get real "
    "data, then state the numbers you found. "
    "You can also CREATE things: use create_categorization_rule to make a persistent "
    "classification rule (e.g. 'count transfers to my savings as transfers', 'always "
    "categorize Starbucks as Dining') — creating a rule ALSO immediately re-applies it to "
    "existing matching transactions, re-labelling them. Use create_savings_goal to set up a "
    "savings goal, and create_category_budget to set a monthly spending limit on a category. "
    "When picking a category or account for a rule/goal/budget, use the real known categories "
    "and connected account labels listed below. "
    "PREFER TO ACT rather than ask. When the user asks to create a categorization rule but "
    "doesn't spell out the exact match text, INFER a reasonable match_field/match_op/match_value "
    "yourself from (a) the recent transaction descriptions and connected account labels provided "
    "below, and/or (b) calling search_transactions to find representative rows (e.g. search by "
    "the account or a keyword like 'savings' or 'transfer'), then call create_categorization_rule "
    "and confirm what you created plus how many existing rows it updated. When the user names an "
    "account (e.g. 'my savings account'), map it to the closest connected account label provided. "
    "For a request like 'count transfers to my savings as transfers', a reasonable rule is "
    "match_field=description, match_op=contains, match_value set to the savings account name or a "
    "'savings'/'transfer' token you see in the data, and set_type=transfer. Only ask the user a "
    "clarifying question as a LAST RESORT, when you genuinely cannot infer any reasonable match — "
    "never ask when a sensible inference exists. "
    "Note: 'transfer' transactions (money moved between the user's own accounts) are excluded "
    "from all income/expense/savings statistics. "
    "Be concise and conversational: lead with the direct answer and the specific dollar "
    "figures. After taking an action, confirm concretely what you did — for a rule, summarize "
    "the rule and say how many existing transactions it updated; for a goal or budget, state "
    "its details. Do not use markdown headings."
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

    if name == "create_categorization_rule":
        return _create_categorization_rule(db, args)

    if name == "create_savings_goal":
        return _create_savings_goal(db, args)

    if name == "create_category_budget":
        return _create_category_budget(db, args)

    raise ValueError(f"Unknown tool: {name}")


# ── Creation tools (lazy imports avoid circular deps with the route modules) ────

def _create_categorization_rule(db: Session, args: dict[str, Any]) -> Any:
    """Persist a new categorization rule and immediately apply it to existing
    matching transactions. Returns {rule, updated_existing} or {error}."""
    from .schemas import RuleCreate
    from .routes.rules import create_rule, apply_single_rule_to_existing

    match_value = (args.get("match_value") or "").strip()
    if not match_value:
        return {"error": "match_value must not be empty."}

    set_type = args.get("set_type") or None
    set_category = (args.get("set_category") or "").strip() or None
    set_account = (args.get("set_account") or "").strip() or None
    if not (set_type or set_category or set_account):
        return {"error": "Provide at least one of set_type, set_category, or set_account."}

    try:
        body = RuleCreate(
            name=(args.get("name") or "").strip() or None,
            match_field=args.get("match_field"),
            match_op=args.get("match_op"),
            match_value=match_value,
            amount_min=args.get("amount_min"),
            amount_max=args.get("amount_max"),
            set_type=set_type,
            set_category=set_category,
            set_account=set_account,
        )
    except Exception as e:  # invalid enum / validation error
        return {"error": f"Invalid rule: {e}"}

    rule = create_rule(body, db)
    updated = apply_single_rule_to_existing(db, rule)
    return {
        "rule": {
            "id": rule.id,
            "name": rule.name,
            "match_field": rule.match_field,
            "match_op": rule.match_op,
            "match_value": rule.match_value,
            "set_type": rule.set_type,
            "set_category": rule.set_category,
            "set_account": rule.set_account,
        },
        "updated_existing": updated,
    }


def _create_savings_goal(db: Session, args: dict[str, Any]) -> Any:
    from .routes.budgets import create_savings_goal as _mk_goal

    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "name must not be empty."}
    try:
        target_amount = float(args.get("target_amount"))
    except (TypeError, ValueError):
        return {"error": "target_amount must be a number."}
    if target_amount <= 0:
        return {"error": "target_amount must be greater than 0."}

    target_date = _parse_date(args.get("target_date"))
    account = (args.get("account") or "").strip() or None
    goal = _mk_goal(db, name, target_amount, target_date, account)
    return {
        "goal": {
            "id": goal.id,
            "name": goal.name,
            "target_amount": goal.target_amount,
            "target_date": str(goal.target_date) if goal.target_date else None,
            "account": goal.account,
        }
    }


def _create_category_budget(db: Session, args: dict[str, Any]) -> Any:
    from .routes.budgets import create_category_budget as _mk_budget

    category = (args.get("category") or "").strip()
    if not category:
        return {"error": "category must not be empty."}
    try:
        limit_amount = float(args.get("limit_amount"))
    except (TypeError, ValueError):
        return {"error": "limit_amount must be a number."}
    if limit_amount <= 0:
        return {"error": "limit_amount must be greater than 0."}

    budget = _mk_budget(db, category, limit_amount)
    return {
        "category_budget": {
            "id": budget.id,
            "category": budget.category,
            "limit_amount": budget.limit_amount,
            "period": budget.period,
        }
    }


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
    {
        "name": "create_categorization_rule",
        "description": (
            "Create a persistent classification rule AND immediately apply it to all existing "
            "matching transactions (re-labelling their type/category/account). Use for requests "
            "like 'always categorize X as Y' or 'count transfers to my savings as transfers'. "
            "The rule matches a field (match_field) against match_value using match_op, then "
            "applies whichever of set_type/set_category/set_account you provide — at least ONE "
            "of those three is required. Use exact category/account values from the lists given "
            "in the system context. After it runs, tell the user the rule and how many existing "
            "rows were updated."
        ),
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                "match_field": {"type": "string", "enum": ["description", "category", "account", "any"], "description": "Which transaction field to test."},
                "match_op": {"type": "string", "enum": ["contains", "equals", "regex"], "description": "How to compare match_value against the field."},
                "match_value": {"type": "string", "description": "The text/pattern to match (non-empty)."},
                "set_type": {"type": "string", "enum": ["income", "expense", "transfer"], "description": "New type to assign on a match."},
                "set_category": {"type": "string", "description": "New category to assign on a match (use a real known category)."},
                "set_account": {"type": "string", "description": "New account label to assign on a match (use a real connected account label)."},
                "amount_min": {"type": "number", "description": "Optional lower bound on the transaction amount."},
                "amount_max": {"type": "number", "description": "Optional upper bound on the transaction amount."},
                "name": {"type": "string", "description": "Optional human-readable rule name."},
            },
            "required": ["match_field", "match_op", "match_value"],
        },
    },
    {
        "name": "create_savings_goal",
        "description": (
            "Create a savings goal the user is working toward. target_amount must be positive. "
            "account (optional) must be one of the connected account labels from the system "
            "context — the goal tracks that account's balance growth. Confirm the goal details "
            "to the user afterward."
        ),
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Goal name, e.g. 'Emergency fund'."},
                "target_amount": {"type": "number", "description": "Positive dollar target."},
                "target_date": {"type": "string", "description": "Optional target date, YYYY-MM-DD."},
                "account": {"type": "string", "description": "Optional connected account label whose growth funds the goal."},
            },
            "required": ["name", "target_amount"],
        },
    },
    {
        "name": "create_category_budget",
        "description": (
            "Create (or update) a monthly spending limit for one category. limit_amount must be "
            "positive. Prefer a real known category from the system context. Confirm the budget "
            "to the user afterward."
        ),
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "The category to cap (use a real known category)."},
                "limit_amount": {"type": "number", "description": "Positive monthly dollar limit."},
            },
            "required": ["category", "limit_amount"],
        },
    },
]

_MAX_TOOL_ROUNDS = 8


def _dynamic_system_instruction(db: Session) -> str:
    """SYSTEM_INSTRUCTION plus the user's real known categories and connected
    account labels, so the model picks valid values for rules/goals/budgets."""
    from sqlalchemy import text as _sql_text

    try:
        categories = [
            c for c in db.execute(
                _sql_text("SELECT DISTINCT category FROM transactions ORDER BY category")
            ).scalars().all()
            if c
        ]
    except Exception:
        categories = []
    try:
        from .routes.budgets import _account_labels
        labels = _account_labels(db)
    except Exception:
        labels = []

    # A bounded sample of recent DISTINCT descriptions so the model can infer a
    # good rule match_value in one shot (instead of asking the user for the exact
    # text). Hard-capped to control token usage: 40 most-recent distinct non-null
    # descriptions, each truncated to ~50 chars.
    try:
        rows = db.execute(
            select(Transaction.description)
            .where(Transaction.description.isnot(None))
            .where(Transaction.description != "")
            .order_by(Transaction.date.desc(), Transaction.id.desc())
        ).scalars().all()
        descriptions: list[str] = []
        seen: set[str] = set()
        for d in rows:
            d = (d or "").strip()
            if not d or d in seen:
                continue
            seen.add(d)
            descriptions.append(d[:50])
            if len(descriptions) >= 40:
                break
    except Exception:
        descriptions = []

    return (
        f"{SYSTEM_INSTRUCTION}\n\n"
        f"Known categories: {', '.join(categories) or '(none yet)'}.\n"
        f"Connected account labels: {', '.join(labels) or '(none)'}.\n"
        f"Recent transaction descriptions (for inferring rule match text): "
        f"{'; '.join(descriptions) or '(none yet)'}."
    )


def run_assistant(db: Session, messages: list[dict]) -> dict:
    """Run the agentic chat loop. messages: [{role: 'user'|'assistant', content: str}]."""
    from google.genai import types

    client = get_client()

    tool = types.Tool(function_declarations=[
        types.FunctionDeclaration(**schema) for schema in _TOOL_SCHEMAS
    ])
    config = types.GenerateContentConfig(
        system_instruction=_dynamic_system_instruction(db),
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


# ── Budget creation (structured output) ────────────────────────────────────────

BUDGET_SYSTEM_INSTRUCTION = (
    "You are the budgeting assistant inside a personal expense-tracker app. "
    f"Today's date is {TODAY}. All amounts are in US dollars (USD). "
    "The user is asking you to set up budgets. Turn their request into concrete "
    "actions of two kinds:\n"
    "  - kind='goal': a savings goal with a name, a positive target_amount, an "
    "optional target_date (YYYY-MM-DD; infer a sensible date like the end of the "
    "named month), and an optional account (the connected account whose balance "
    "growth funds the goal — pick one from the provided account labels only if the "
    "user names or clearly implies it, else leave null).\n"
    "  - kind='category_limit': a monthly spending cap on ONE category with a "
    "positive limit_amount. Prefer an EXISTING known category; use its exact "
    "spelling. Only invent a short new category if none fit.\n"
    "For a savings goal you MAY also add a few category_limit actions that form a "
    "realistic monthly savings plan toward the goal, but only if the user seems to "
    "want a plan. If the message is NOT about creating budgets, return an empty "
    "actions list and a brief helpful reply. Always write `reply` as a short, "
    "natural, first-person confirmation of what you set up (no markdown headings)."
)


def plan_budget(
    messages: list[dict],
    known_categories: Optional[list[str]] = None,
    account_labels: Optional[list[str]] = None,
) -> dict:
    """Turn a chat request into concrete budget/goal creation actions via Gemini
    structured output. Returns {"actions": [ {kind, ...}, ... ], "reply": str}.
    Actions are dicts; the caller (assistant route) persists them via the shared
    budget create helpers. The caller passes in known categories + account labels
    so this stays pure w.r.t. the DB."""
    from google.genai import types
    from pydantic import BaseModel

    class _BudgetAction(BaseModel):
        kind: str  # "goal" | "category_limit"
        # goal fields
        name: Optional[str] = None
        target_amount: Optional[float] = None
        target_date: Optional[str] = None  # YYYY-MM-DD
        account: Optional[str] = None
        # category_limit fields
        category: Optional[str] = None
        limit_amount: Optional[float] = None

    class _BudgetPlan(BaseModel):
        actions: list[_BudgetAction]
        reply: str

    client = get_client()

    convo = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}"
        for m in messages
        if m.get("content")
    )
    prompt = (
        f"Known categories: {', '.join(known_categories or []) or '(none yet)'}.\n"
        f"Connected account labels: {', '.join(account_labels or []) or '(none)'}.\n\n"
        f"Conversation:\n{convo}\n\n"
        "Produce the budget actions and a confirmation reply."
    )
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=BUDGET_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=_BudgetPlan,
            temperature=0.2,
        ),
    )
    parsed = getattr(response, "parsed", None)
    if parsed is None:
        data = json.loads(response.text)
        parsed = _BudgetPlan(**data)
    return {
        "actions": [a.model_dump() for a in parsed.actions],
        "reply": (parsed.reply or "").strip(),
    }


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

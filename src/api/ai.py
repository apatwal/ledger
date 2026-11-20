"""
AI layer — OpenAI (official `openai` SDK, Chat Completions).

Powers four product features on top of the existing query logic:
  * Q&A assistant  -> run_assistant()      (tool-calling loop over the stats/search tools)
  * Auto-categorize -> suggest_category()  (strict JSON-schema structured output)
  * Insights        -> generate_insights() (one-shot narrative)
  * Budget planning -> plan_budget()       (strict JSON-schema structured output)

The model never touches the DB directly: every tool routes through execute_tool(),
which calls the SAME query functions the REST API uses. execute_tool() is pure
(no network, no key) so the tool->query mapping is unit-testable offline.

Every OpenAI request goes through _chat(), which turns SDK failures (bad key,
rate limit / quota, timeout, unreachable, model errors) into AIProviderError —
a status code (502/503) plus a readable detail the routes hand back as-is.

Config (environment):
  OPENAI_API_KEY   required for any AI call (absent -> features disabled / 503)
  OPENAI_MODEL     optional, defaults to "gpt-4o-mini"
"""
from __future__ import annotations

import os
import json
from datetime import date
from typing import Any, Optional

from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from .models import Transaction
from .routes import stats as stats_routes

DEFAULT_MODEL = "gpt-4o-mini"

# Per-request timeout (seconds) and SDK retry count. The SDK retries connection
# errors, 408/409/429 and 5xx with backoff; one retry keeps the worst case for a
# single call to ~1 minute instead of the SDK default's ~1.5.
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 1

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
    """Raised when an AI call is attempted without OPENAI_API_KEY set."""


class AIProviderError(RuntimeError):
    """An OpenAI request failed. `status_code` (502 or 503) and `detail` (a
    readable sentence, never containing the API key) are what the route returns."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def get_model() -> str:
    """The chat model to use — read per call so a .env loaded after import counts."""
    return (os.environ.get("OPENAI_MODEL") or "").strip() or DEFAULT_MODEL


def is_enabled() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def get_client():
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise AINotConfigured("OPENAI_API_KEY is not set")
    from openai import OpenAI  # imported lazily so the app boots fast without AI

    return OpenAI(api_key=key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=MAX_RETRIES)


def _provider_message(e: Exception) -> str:
    """The human part of an OpenAI error body ('message'), trimmed."""
    body = getattr(e, "body", None)
    msg = None
    if isinstance(body, dict):
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
        msg = inner.get("message")
    msg = (msg or getattr(e, "message", None) or str(e) or "").strip()
    return msg if len(msg) <= 300 else msg[:299] + "…"


def translate_openai_error(e: Exception) -> AIProviderError:
    """Map an `openai` SDK exception to a status code + readable detail.

    503 = the AI is unusable until someone fixes config/billing or waits (bad key,
    no access to the model, rate limit / quota). 502 = this request failed
    upstream (timeout, unreachable, OpenAI 5xx, rejected or unparseable reply)."""
    import openai

    model = get_model()
    if isinstance(e, openai.AuthenticationError):
        # Deliberately not echoing OpenAI's message: it quotes part of the key.
        return AIProviderError(
            503, "AI request failed: OpenAI rejected the server's API key. Check OPENAI_API_KEY on the server."
        )
    if isinstance(e, openai.PermissionDeniedError):
        return AIProviderError(
            503, f"AI request failed: OpenAI denied access to model '{model}': {_provider_message(e)}"
        )
    if isinstance(e, openai.NotFoundError):
        return AIProviderError(
            503, f"AI request failed: OpenAI model '{model}' was not found. Check OPENAI_MODEL on the server."
        )
    if isinstance(e, openai.RateLimitError):
        code = getattr(e, "code", None)
        if code == "insufficient_quota":
            return AIProviderError(
                503, "AI request failed: the OpenAI account is out of credits or over its usage quota."
            )
        return AIProviderError(
            503, "AI request failed: OpenAI rate limit reached. Wait a moment and try again."
        )
    if isinstance(e, openai.APITimeoutError):  # subclass of APIConnectionError — check first
        return AIProviderError(
            502, f"AI request failed: OpenAI didn't respond within {int(REQUEST_TIMEOUT_SECONDS)} seconds. Try again."
        )
    if isinstance(e, openai.APIConnectionError):
        return AIProviderError(502, "AI request failed: couldn't reach the OpenAI API. Try again in a moment.")
    if isinstance(e, openai.LengthFinishReasonError):
        return AIProviderError(502, "AI request failed: the model's reply was cut off before it finished.")
    if isinstance(e, openai.ContentFilterFinishReasonError):
        return AIProviderError(502, "AI request failed: OpenAI's content filter blocked the reply.")
    if isinstance(e, openai.APIStatusError):
        return AIProviderError(
            502, f"AI request failed: OpenAI returned HTTP {e.status_code}: {_provider_message(e)}"
        )
    return AIProviderError(502, f"AI request failed: {type(e).__name__}: {_provider_message(e)}")


def _chat(client, *, parse: bool = False, **kwargs):
    """One Chat Completions request (`parse=True` for structured output), with
    OpenAI SDK errors translated to AIProviderError."""
    import openai

    try:
        if parse:
            return client.chat.completions.parse(model=get_model(), **kwargs)
        return client.chat.completions.create(model=get_model(), **kwargs)
    except openai.OpenAIError as e:
        raise translate_openai_error(e) from e


def _parsed(completion, what: str):
    """The pydantic object from a parse() completion, or AIProviderError when the
    model refused or returned nothing usable."""
    message = completion.choices[0].message
    if getattr(message, "parsed", None) is not None:
        return message.parsed
    refusal = getattr(message, "refusal", None)
    if refusal:
        raise AIProviderError(502, f"AI request failed: the model declined to {what}: {refusal}")
    raise AIProviderError(502, f"AI request failed: the model returned no usable {what} result.")


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
        "parameters": {"type": "object", "properties": dict(_DATE_PROPS), "required": []},
    },
    {
        "name": "get_spending_by_category",
        "description": "Totals grouped by category for a date range. Use to find the biggest categories.",
        "parameters": {
            "type": "object",
            "properties": {**_DATE_PROPS, "type": {"type": "string", "enum": ["expense", "income"], "description": "Defaults to expense."}},
            "required": [],
        },
    },
    {
        "name": "get_income_expense_over_time",
        "description": "Income, expense, and net per time period. Use for trends over time.",
        "parameters": {
            "type": "object",
            "properties": {"granularity": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "Defaults to month."}, **_DATE_PROPS},
            "required": [],
        },
    },
    {
        "name": "search_transactions",
        "description": "List individual transactions matching the filters, newest first. Use to inspect specific entries.",
        "parameters": {
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
        "parameters": {
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
        "parameters": {
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
        "parameters": {
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


# OpenAI function-tool form of _TOOL_SCHEMAS (non-strict, so optional params stay
# optional exactly as declared).
_OPENAI_TOOLS = [{"type": "function", "function": schema} for schema in _TOOL_SCHEMAS]


def _history_to_openai(messages: list[dict]) -> list[dict]:
    """Chat history -> OpenAI messages. 'assistant'/'model' -> assistant, anything
    else -> user; empty turns dropped."""
    return [
        {
            "role": "assistant" if m.get("role") in ("assistant", "model") else "user",
            "content": m.get("content", ""),
        }
        for m in messages
        if m.get("content")
    ]


def run_assistant(db: Session, messages: list[dict]) -> dict:
    """Run the agentic chat loop. messages: [{role: 'user'|'assistant', content: str}]."""
    client = get_client()

    convo: list[dict] = [
        {"role": "system", "content": _dynamic_system_instruction(db)},
        *_history_to_openai(messages),
    ]

    def _ask():
        return _chat(client, messages=convo, tools=_OPENAI_TOOLS, temperature=0.2)

    tool_calls: list[str] = []
    response = _ask()

    for _ in range(_MAX_TOOL_ROUNDS):
        message = response.choices[0].message
        calls = [c for c in (message.tool_calls or []) if getattr(c, "type", "function") == "function"]
        if not calls:
            break
        # record the model's tool-call turn, then answer every call
        convo.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"},
                }
                for c in calls
            ],
        })
        for c in calls:
            name = c.function.name
            tool_calls.append(name)
            try:
                args = json.loads(c.function.arguments or "{}") or {}
                if not isinstance(args, dict):
                    raise ValueError("tool arguments must be a JSON object")
                result = execute_tool(db, name, args)
                payload = {"result": result}
            except Exception as e:  # surface to the model so it can recover
                payload = {"error": str(e)}
            convo.append({
                "role": "tool",
                "tool_call_id": c.id,
                "content": json.dumps(payload, default=str),
            })
        response = _ask()

    reply = (response.choices[0].message.content or "").strip() or "I couldn't find an answer to that."
    seen: set[str] = set()
    deduped = [t for t in tool_calls if not (t in seen or seen.add(t))]
    return {"reply": reply, "tool_calls": deduped}


# ── Auto-categorize (structured output) ────────────────────────────────────────

class _Suggestion(BaseModel):
    category: str
    confidence: float


def suggest_category(
    description: str,
    amount: Optional[float],
    type_: str,
    known_categories: list[str],
) -> dict:
    """Suggest the single best category for a transaction. Returns {category, confidence}."""
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
    completion = _chat(
        client,
        parse=True,
        messages=[
            {"role": "system", "content": "You categorize personal-finance transactions into one concise category."},
            {"role": "user", "content": prompt},
        ],
        response_format=_Suggestion,
        temperature=0.0,
    )
    parsed = _parsed(completion, "categorize the transaction")
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


def plan_budget(
    messages: list[dict],
    known_categories: Optional[list[str]] = None,
    account_labels: Optional[list[str]] = None,
) -> dict:
    """Turn a chat request into concrete budget/goal creation actions via OpenAI
    structured output (strict JSON schema). Returns {"actions": [ {kind, ...}, ... ], "reply": str}.
    Actions are dicts; the caller (assistant route) persists them via the shared
    budget create helpers. The caller passes in known categories + account labels
    so this stays pure w.r.t. the DB."""
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
    completion = _chat(
        client,
        parse=True,
        messages=[
            {"role": "system", "content": BUDGET_SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
        response_format=_BudgetPlan,
        temperature=0.2,
    )
    parsed = _parsed(completion, "plan the budget")
    return {
        "actions": [a.model_dump() for a in parsed.actions],
        "reply": (parsed.reply or "").strip(),
    }


# ── Insights (one-shot narrative) ──────────────────────────────────────────────

def generate_insights(db: Session, start_date: Optional[date], end_date: Optional[date]) -> str:
    """Generate a short narrative read of the user's finances for the period."""
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
    response = _chat(client, messages=[{"role": "user", "content": prompt}], temperature=0.4)
    return (response.choices[0].message.content or "").strip() or "No insights available for this period."

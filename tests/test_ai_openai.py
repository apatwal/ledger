"""
test_ai_openai.py — the OpenAI-backed AI layer (src/api/ai.py), fully mocked.

No test here talks to OpenAI: `ai.get_client` is replaced with a fake client
whose `chat.completions.create` / `.parse` return canned completions (and the
conftest guard deletes the real OPENAI_API_KEY and points OPENAI_BASE_URL at a
closed port). Coverage:
  * run_assistant: a tool call is executed against the DB and the final text is
    returned; a tool exception is fed back to the model as {"error": ...}; the
    history-role mapping and the tool schemas sent to OpenAI.
  * suggest_category / plan_budget: strict structured output parses into the
    pydantic models; a refusal becomes a readable 502.
  * Routes: missing key -> 503; OpenAI auth / rate-limit / quota / timeout /
    connection / 5xx errors -> readable 502/503 JSON details (no traceback, no key).
"""
from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import openai
import pytest
from sqlalchemy.orm import sessionmaker

from src.api import ai

try:  # openai>=3 ships its own httpx fork; older SDKs use httpx itself
    _http = importlib.import_module("httpx2")
except ImportError:  # pragma: no cover
    _http = importlib.import_module("httpx")

_REQ = _http.Request("POST", "https://api.openai.com/v1/chat/completions")


# ── Fakes ──────────────────────────────────────────────────────────────────────

def _tool_call(call_id: str, name: str, args: dict | str):
    arguments = args if isinstance(args, str) else json.dumps(args)
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _completion(content=None, tool_calls=None, parsed=None, refusal=None):
    message = SimpleNamespace(
        role="assistant", content=content, tool_calls=tool_calls,
        parsed=parsed, refusal=refusal,
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


class FakeCompletions:
    """Stands in for client.chat.completions. `script` is a list of completions
    (or exceptions to raise), consumed in order; every request is recorded."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def _next(self, kind, kwargs):
        # Snapshot messages: run_assistant keeps appending to the same list.
        self.calls.append({"kind": kind, **kwargs, "messages": [dict(m) for m in kwargs["messages"]]})
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def create(self, **kwargs):
        return self._next("create", kwargs)

    def parse(self, **kwargs):
        return self._next("parse", kwargs)


@pytest.fixture
def fake_openai(monkeypatch):
    """Enable AI with a dummy key and route get_client() to a FakeCompletions.
    Returns a setter: fake_openai([completion, ...]) -> FakeCompletions."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    def install(script):
        completions = FakeCompletions(script)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        monkeypatch.setattr(ai, "get_client", lambda: client)
        return completions

    return install


def _session(test_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=test_engine)()


def _status_error(cls, status, body):
    """Build an SDK status error the way the client does: the exception's `body`
    is the inner `error` object of the JSON response."""
    return cls(
        f"Error code: {status} - {body}",
        response=_http.Response(status, request=_REQ, json=body),
        body=body.get("error", body),
    )


# ── Client / config ────────────────────────────────────────────────────────────

class TestConfig:
    def test_default_model_and_override(self, monkeypatch):
        assert ai.get_model() == "gpt-4o-mini"
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
        assert ai.get_model() == "gpt-4.1-mini"

    def test_get_client_requires_key(self):
        with pytest.raises(ai.AINotConfigured):
            ai.get_client()

    def test_get_client_sets_timeout_and_retries(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
        client = ai.get_client()
        assert isinstance(client, openai.OpenAI)
        assert client.timeout == ai.REQUEST_TIMEOUT_SECONDS
        assert client.max_retries == ai.MAX_RETRIES

    def test_requests_use_configured_model(self, fake_openai, monkeypatch, test_engine):
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-nano")
        fake = fake_openai([_completion(content="Hi!")])
        db = _session(test_engine)
        try:
            ai.run_assistant(db, [{"role": "user", "content": "hello"}])
        finally:
            db.close()
        assert fake.calls[0]["model"] == "gpt-4.1-nano"


# ── Tool schemas ───────────────────────────────────────────────────────────────

class TestToolSchemas:
    def test_every_tool_is_an_openai_function(self):
        names = [t["function"]["name"] for t in ai._OPENAI_TOOLS]
        assert names == [
            "get_financial_summary", "get_spending_by_category",
            "get_income_expense_over_time", "search_transactions",
            "create_categorization_rule", "create_savings_goal", "create_category_budget",
        ]
        for t in ai._OPENAI_TOOLS:
            assert t["type"] == "function"
            fn = t["function"]
            assert set(fn) == {"name", "description", "parameters"}
            assert fn["description"]
            assert fn["parameters"]["type"] == "object"

    def test_required_fields_preserved(self):
        req = {t["function"]["name"]: t["function"]["parameters"]["required"] for t in ai._OPENAI_TOOLS}
        assert req["create_categorization_rule"] == ["match_field", "match_op", "match_value"]
        assert req["create_savings_goal"] == ["name", "target_amount"]
        assert req["create_category_budget"] == ["category", "limit_amount"]
        assert req["search_transactions"] == []


# ── run_assistant (tool loop) ─────────────────────────────────────────────────

class TestRunAssistant:
    def _seed(self, client):
        for r in [
            {"date": "2026-06-03", "amount": 80.0, "type": "expense", "category": "Groceries"},
            {"date": "2026-06-10", "amount": 45.5, "type": "expense", "category": "Groceries"},
            {"date": "2026-06-12", "amount": 30.0, "type": "expense", "category": "Dining"},
        ]:
            assert client.post("/api/transactions", json=r).status_code == 201

    def test_tool_call_is_executed_and_final_text_returned(self, client, test_engine, fake_openai):
        self._seed(client)
        fake = fake_openai([
            _completion(tool_calls=[_tool_call(
                "call_1", "get_spending_by_category",
                {"start_date": "2026-06-01", "end_date": "2026-06-30"},
            )]),
            _completion(content="  You spent $125.50 on groceries this month.  "),
        ])
        db = _session(test_engine)
        try:
            out = ai.run_assistant(db, [
                {"role": "user", "content": "hi"},
                {"role": "model", "content": "Hello!"},
                {"role": "user", "content": ""},  # empty turns are dropped
                {"role": "user", "content": "How much on groceries this month?"},
            ])
        finally:
            db.close()

        assert out == {"reply": "You spent $125.50 on groceries this month.",
                       "tool_calls": ["get_spending_by_category"]}
        assert len(fake.calls) == 2

        first = fake.calls[0]
        assert first["kind"] == "create"
        assert first["tools"] == ai._OPENAI_TOOLS
        roles = [m["role"] for m in first["messages"]]
        assert roles == ["system", "user", "assistant", "user"]  # "model" -> assistant
        assert "Known categories" in first["messages"][0]["content"]

        # Second request carries the assistant tool-call turn + the tool result.
        second = fake.calls[1]["messages"]
        assistant_turn, tool_turn = second[-2], second[-1]
        assert assistant_turn["role"] == "assistant"
        assert assistant_turn["tool_calls"][0]["id"] == "call_1"
        assert assistant_turn["tool_calls"][0]["function"]["name"] == "get_spending_by_category"
        assert tool_turn["role"] == "tool" and tool_turn["tool_call_id"] == "call_1"
        result = json.loads(tool_turn["content"])["result"]
        groceries = next(r for r in result if r["category"] == "Groceries")
        assert groceries["total"] == pytest.approx(125.5)

    def test_tool_exception_is_fed_back_to_model(self, test_engine, fake_openai):
        fake = fake_openai([
            _completion(tool_calls=[
                _tool_call("call_bad", "no_such_tool", {}),
                _tool_call("call_json", "search_transactions", "{not json"),
            ]),
            _completion(content="Sorry, I couldn't look that up."),
        ])
        db = _session(test_engine)
        try:
            out = ai.run_assistant(db, [{"role": "user", "content": "do something"}])
        finally:
            db.close()

        assert out["reply"] == "Sorry, I couldn't look that up."
        assert out["tool_calls"] == ["no_such_tool", "search_transactions"]
        tool_msgs = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["call_bad", "call_json"]
        bad = json.loads(tool_msgs[0]["content"])
        assert bad == {"error": "Unknown tool: no_such_tool"}
        assert "error" in json.loads(tool_msgs[1]["content"])

    def test_create_tool_persists_and_dedupes_names(self, test_engine, fake_openai):
        from src.api.models import CategoryBudget
        fake_openai([
            _completion(tool_calls=[_tool_call("c1", "create_category_budget",
                                               {"category": "Dining", "limit_amount": 200})]),
            _completion(tool_calls=[_tool_call("c2", "create_category_budget",
                                               {"category": "Dining", "limit_amount": 250})]),
            _completion(content="Set Dining to $250/month."),
        ])
        db = _session(test_engine)
        try:
            out = ai.run_assistant(db, [{"role": "user", "content": "cap dining"}])
            assert out["tool_calls"] == ["create_category_budget"]
            assert db.query(CategoryBudget).one().limit_amount == 250
        finally:
            db.close()

    def test_iteration_cap_returns_fallback(self, test_engine, fake_openai):
        loop = [_completion(tool_calls=[_tool_call(f"c{i}", "get_financial_summary", {})])
                for i in range(ai._MAX_TOOL_ROUNDS + 1)]
        fake = fake_openai(loop)
        db = _session(test_engine)
        try:
            out = ai.run_assistant(db, [{"role": "user", "content": "loop forever"}])
        finally:
            db.close()
        assert len(fake.calls) == ai._MAX_TOOL_ROUNDS + 1
        assert out["reply"] == "I couldn't find an answer to that."


# ── Structured outputs ─────────────────────────────────────────────────────────

class TestStructuredOutputs:
    def test_suggest_category_parses(self, fake_openai):
        fake = fake_openai([_completion(parsed=ai._Suggestion(category=" Dining ", confidence=1.4))])
        out = ai.suggest_category("CHIPOTLE 123", 12.5, "expense", ["Dining", "Groceries"])
        assert out == {"category": "Dining", "confidence": 1.0}  # trimmed + clamped
        call = fake.calls[0]
        assert call["kind"] == "parse"
        assert call["response_format"] is ai._Suggestion
        assert call["messages"][0]["role"] == "system"
        assert "CHIPOTLE 123" in call["messages"][1]["content"]

    def test_plan_budget_parses_actions(self, fake_openai):
        plan = ai._BudgetPlan(
            actions=[
                ai._BudgetAction(kind="goal", name="Japan", target_amount=2000,
                                 target_date="2026-12-31"),
                ai._BudgetAction(kind="category_limit", category="Dining", limit_amount=300),
            ],
            reply=" Created a Japan goal and a Dining cap. ",
        )
        fake = fake_openai([_completion(parsed=plan)])
        out = ai.plan_budget([{"role": "user", "content": "save 2k for Japan, cap dining at 300"}],
                             known_categories=["Dining"], account_labels=["Chase Savings"])
        assert out["reply"] == "Created a Japan goal and a Dining cap."
        assert out["actions"][0]["kind"] == "goal"
        assert out["actions"][0]["target_amount"] == 2000
        assert out["actions"][1] == {
            "kind": "category_limit", "name": None, "target_amount": None, "target_date": None,
            "account": None, "category": "Dining", "limit_amount": 300,
        }
        call = fake.calls[0]
        assert call["kind"] == "parse" and call["response_format"] is ai._BudgetPlan
        assert "Chase Savings" in call["messages"][1]["content"]

    def test_budget_plan_schema_is_strict_compatible(self):
        from openai.lib._pydantic import to_strict_json_schema
        schema = to_strict_json_schema(ai._BudgetPlan)
        action = schema["$defs"]["_BudgetAction"]
        assert action["additionalProperties"] is False
        assert set(action["required"]) == set(action["properties"])

    def test_refusal_becomes_readable_502(self, fake_openai):
        fake_openai([_completion(parsed=None, refusal="I can't help with that.")])
        with pytest.raises(ai.AIProviderError) as exc:
            ai.suggest_category("x", 1.0, "expense", ["Dining"])
        assert exc.value.status_code == 502
        assert "I can't help with that." in exc.value.detail

    def test_generate_insights_returns_text(self, client, test_engine, fake_openai):
        fake = fake_openai([_completion(content=" You're in the black by $500. ")])
        db = _session(test_engine)
        try:
            assert ai.generate_insights(db, None, None) == "You're in the black by $500."
        finally:
            db.close()
        assert fake.calls[0]["kind"] == "create"
        assert '"summary"' in fake.calls[0]["messages"][0]["content"]


# ── Routes: missing key + OpenAI failures ─────────────────────────────────────

_CHAT = ("post", "/api/assistant/chat", {"json": {"messages": [{"role": "user", "content": "hi"}]}})
_CATEGORIZE = ("post", "/api/assistant/categorize",
               {"json": {"description": "Uber", "amount": 20, "type": "expense"}})
_BUDGET = ("post", "/api/assistant/budget", {"json": {"messages": [{"role": "user", "content": "save"}]}})
_INSIGHTS = ("get", "/api/assistant/insights", {})


def _call(client, endpoint):
    method, path, kwargs = endpoint
    return getattr(client, method)(path, **kwargs)


class TestRouteErrors:
    @pytest.mark.parametrize("endpoint", [_CHAT, _CATEGORIZE, _BUDGET, _INSIGHTS])
    def test_missing_key_503(self, client, endpoint):
        resp = _call(client, endpoint)
        assert resp.status_code == 503
        assert "OPENAI_API_KEY" in resp.json()["detail"]

    @pytest.mark.parametrize("make_error, status, needle", [
        (lambda: _status_error(openai.AuthenticationError, 401, {"error": {
            "message": "Incorrect API key provided: sk-proj-****1234", "code": "invalid_api_key"}}),
         503, "rejected the server's API key"),
        (lambda: _status_error(openai.RateLimitError, 429, {"error": {
            "message": "Rate limit reached", "code": "rate_limit_exceeded"}}),
         503, "rate limit"),
        (lambda: _status_error(openai.RateLimitError, 429, {"error": {
            "message": "You exceeded your current quota", "code": "insufficient_quota"}}),
         503, "out of credits"),
        (lambda: _status_error(openai.NotFoundError, 404, {"error": {
            "message": "The model does not exist", "code": "model_not_found"}}),
         503, "OPENAI_MODEL"),
        (lambda: openai.APITimeoutError(request=_REQ), 502, "didn't respond within 30 seconds"),
        (lambda: openai.APIConnectionError(request=_REQ), 502, "couldn't reach the OpenAI API"),
        (lambda: _status_error(openai.InternalServerError, 500, {"error": {
            "message": "The server had an error"}}),
         502, "HTTP 500: The server had an error"),
    ])
    @pytest.mark.parametrize("endpoint", [_CHAT, _CATEGORIZE, _BUDGET, _INSIGHTS])
    def test_openai_errors_are_readable(self, client, fake_openai, endpoint, make_error, status, needle):
        fake_openai([make_error()])
        resp = _call(client, endpoint)
        assert resp.status_code == status
        body = resp.json()
        assert isinstance(body["detail"], str)
        assert body["detail"].startswith("AI request failed: ")
        assert needle in body["detail"]
        assert "Traceback" not in resp.text
        assert "sk-" not in resp.text  # never echo any part of a key

    def test_categorize_batch_maps_openai_error(self, client, fake_openai):
        client.post("/api/transactions", json={
            "date": "2026-01-01", "amount": 12.0, "type": "expense",
            "category": "Uncategorized", "description": "CHIPOTLE"})
        fake_openai([_status_error(openai.RateLimitError, 429, {"error": {"message": "slow down"}})])
        resp = client.post("/api/assistant/categorize-batch", json={"only_uncategorized": True})
        assert resp.status_code == 503
        assert "rate limit" in resp.json()["detail"]

    def test_chat_success_shape_unchanged(self, client, fake_openai):
        fake_openai([_completion(content="Hello there.")])
        resp = _call(client, _CHAT)
        assert resp.status_code == 200
        assert resp.json() == {"reply": "Hello there.", "tool_calls": []}

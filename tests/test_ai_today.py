"""
test_ai_today.py — the AI prompts carry the REAL current date in the user's zone.

ai.py used to hard-code TODAY = "2026-06-28", so the assistant answered "what day
is it?" with a months-old date and read "this month" as June 2026. The date is
now computed per request in APP_TIMEZONE (default America/New_York), because
Render runs in UTC and a bare date.today() flips to tomorrow every US evening.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import sessionmaker


def test_today_defaults_to_new_york(monkeypatch):
    from src.api import ai
    monkeypatch.delenv("APP_TIMEZONE", raising=False)
    assert ai.today() == datetime.now(ZoneInfo("America/New_York")).date()


def test_app_timezone_override_is_honored(monkeypatch):
    # UTC+14 and UTC-11 are 25 hours apart, so their calendar dates ALWAYS differ:
    # if the override were ignored, at least one of these would fail.
    from src.api import ai
    monkeypatch.setenv("APP_TIMEZONE", "Pacific/Kiritimati")
    ahead = ai.today()
    monkeypatch.setenv("APP_TIMEZONE", "Pacific/Pago_Pago")
    behind = ai.today()
    assert ahead == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
    assert behind == datetime.now(ZoneInfo("Pacific/Pago_Pago")).date()
    assert ahead != behind


def test_unknown_timezone_falls_back_to_utc(monkeypatch):
    from src.api import ai
    monkeypatch.setenv("APP_TIMEZONE", "Not/AZone")
    assert ai.today() == datetime.now(ZoneInfo("UTC")).date()


def test_date_sentence_names_weekday_and_iso_date(monkeypatch):
    from src.api import ai
    monkeypatch.delenv("APP_TIMEZONE", raising=False)
    d = ai.today()
    assert ai._date_sentence() == f"Today is {d.strftime('%A')}, {d.isoformat()}."


def test_prompts_carry_current_date_not_the_old_hardcoded_one(test_engine, monkeypatch):
    from src.api import ai
    monkeypatch.delenv("APP_TIMEZONE", raising=False)
    db = sessionmaker(bind=test_engine)()
    try:
        prompt = ai._dynamic_system_instruction(db)
    finally:
        db.close()
    assert ai._date_sentence() in prompt
    assert "2026-06-28" not in prompt
    assert "2026-06-28" not in ai.SYSTEM_INSTRUCTION
    assert "2026-06-28" not in ai.BUDGET_SYSTEM_INSTRUCTION

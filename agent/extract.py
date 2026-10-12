"""
Memory extraction: one Claude API call (structured output) per inbound user message.

Runs before the orchestrator so the facts are already in memory when it reads its context.
Failures are logged and swallowed — extraction must never block a reply.
"""
from __future__ import annotations

import logging
import uuid
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from config import settings
from utils.time_utils import now_kst

log = logging.getLogger(__name__)

Kind = Literal["commitment", "workout", "injury", "skip_reason", "mood", "goal", "preference", "context"]

_INSTRUCTIONS = """\
You extract durable facts from one chat message a user sent to their workout-buddy bot.

Return entries only for facts worth remembering later:
- commitment: a plan with a time and/or workout ("21시에 갈게", "내일 하체 할게")
- workout: details of a workout they say they did (muscle groups, weights, PRs)
- injury: pain, injury, illness, soreness
- skip_reason: why they can't or won't work out (회식, 야근, 출장, 피곤)
- mood: a clearly stated emotional state (stressed, very motivated, exhausted)
- goal: a goal change ("주 4회 해볼게", "다이어트 시작")
- preference: likes/dislikes, usual gym time, preferred split, rest days
- context: other life context that affects training (moving, new job, trip dates)

Rules:
- Write each entry as one short Korean statement, paraphrasing the user (e.g. "10/14(화) 21시에 헬스장 가기로 함 (등 운동)").
- Resolve relative dates ("오늘", "내일", "이번 주") into explicit dates using the current time given.
- Only facts the user actually stated. No guesses, no advice, no restating the bot's words.
- Greetings, small talk, and questions with no personal facts → return no entries.
"""

_WEEKDAYS_KO = "월화수목금토일"


class ExtractedEntry(BaseModel):
    kind: Kind
    text: str = Field(description="One short Korean statement.")


class Extraction(BaseModel):
    entries: list[ExtractedEntry]


_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
    return _client


async def extract_entries(user_text: str, last_bot_message: str | None = None) -> list[ExtractedEntry]:
    now = now_kst()
    context = f"Current time: {now:%Y-%m-%d} ({_WEEKDAYS_KO[now.weekday()]}) {now:%H:%M} KST"
    if last_bot_message:
        context += f"\nThe bot's previous message (context only, don't extract from it): {last_bot_message}"
    response = await _get_client().beta.messages.parse(
        model=settings.utility_model,
        max_tokens=2048,
        system=_INSTRUCTIONS,
        messages=[{"role": "user", "content": f"{context}\n\nUser message:\n{user_text}"}],
        output_format=Extraction,
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        log.warning("Memory extraction returned no output | stop_reason=%s", response.stop_reason)
        return []
    return response.parsed_output.entries


async def extract_and_store(user_id: str, turn_id: uuid.UUID, user_text: str,
                            last_bot_message: str | None = None) -> int:
    """Extract entries from a user message and append them to memory. Returns the count stored."""
    from db.models import AsyncSessionLocal
    from memory import store as memory

    if not settings.anthropic_api_key:
        return 0
    try:
        entries = await extract_entries(user_text, last_bot_message)
    except anthropic.APIError:
        log.exception("Memory extraction failed | user=%s", user_id)
        return 0

    stored = 0
    async with AsyncSessionLocal() as session:
        for e in entries:
            try:
                await memory.write(session, user_id, e.text, e.kind, source="extractor", source_turn_id=turn_id)
                stored += 1
            except memory.MemoryStoreError:
                log.warning("Skipped invalid extracted entry | user=%s | kind=%s", user_id, e.kind)
    log.info("Memory extracted | user=%s | entries=%d", user_id, stored)
    return stored

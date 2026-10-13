"""
In-process MCP tools for the orchestrator (Claude Agent SDK).

All tools are bound to one RunState (user + trigger) by build_tools(). Each tool opens its own
AsyncSession because the model may call tools in parallel and an AsyncSession is not
concurrency-safe.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from agent.triggers import Trigger
from config import settings
from utils.time_utils import KST, now_kst, parse_local_time_today

log = logging.getLogger(__name__)

SERVER_NAME = "fitnudge"
SEND_MESSAGE = "send_message"


def mcp_name(tool_name: str) -> str:
    return f"mcp__{SERVER_NAME}__{tool_name}"


@dataclass
class RunState:
    user_id: str
    trigger: Trigger
    sent: list[str] = field(default_factory=list)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def build_tools(state: RunState) -> list[SdkMcpTool]:
    from channels import base as channel_base
    from channels import delivery
    from db import queries as db
    from db.models import AsyncSessionLocal
    from memory import store as memory
    from scheduler import jobs as scheduler_jobs
    from utils import message_guard
    from utils.text_style import count_sentences

    user_id = state.user_id

    @tool(
        SEND_MESSAGE,
        f"Send one chat message to the user: Korean, at most {settings.max_message_sentences} short "
        "sentences (longer texts are rejected). This is the only way the user sees anything you write. "
        "May be blocked by policy (quiet hours, daily caps).",
        _schema({"text": {"type": "string", "description": "The message, in Korean."}}, ["text"]),
    )
    async def send_message(args: dict[str, Any]) -> dict[str, Any]:
        text = (args.get("text") or "").strip()
        if not text:
            return _err("Message text is empty.")
        sentences = count_sentences(text)
        if sentences > settings.max_message_sentences or len(text) > settings.max_message_chars:
            return _err(
                f"Not sent: {sentences} sentences / {len(text)} characters. Rewrite it in at most "
                f"{settings.max_message_sentences} short sentences — keep only what matters most."
            )
        async with state.send_lock:
            async with AsyncSessionLocal() as session:
                decision = await message_guard.check_send(
                    session, user_id, proactive=state.trigger.proactive, sent_this_run=len(state.sent)
                )
                if not decision.allowed:
                    return _err(decision.reason)
                try:
                    await delivery.deliver(session, user_id, text, proactive=state.trigger.proactive)
                except channel_base.RecipientUnavailable:
                    return _err("The user cannot receive messages right now. End this run.")
            state.sent.append(text)
        return _ok("Message delivered.")

    @tool(
        "memory_read",
        "Read memory entries (time-keyed plaintext notes about the user) older than the snapshot shows.",
        _schema(
            {
                "days": {"type": "integer", "minimum": 1, "maximum": 365,
                         "description": "How many days back to read."},
                "kinds": {"type": "array", "items": {"type": "string", "enum": list(memory.KINDS)},
                          "description": "Optional filter by kind."},
            },
            ["days"],
        ),
    )
    async def memory_read(args: dict[str, Any]) -> dict[str, Any]:
        days = max(1, min(int(args.get("days", 30)), 365))
        async with AsyncSessionLocal() as session:
            entries = await memory.read(session, user_id, since=now_kst() - timedelta(days=days),
                                        kinds=args.get("kinds") or None)
        return _ok(memory.format_entries(entries))

    @tool(
        "memory_write",
        "Append a memory entry: a short plaintext statement (Korean when paraphrasing the user). "
        "Use `supersedes` with an entry id (e.g. 'ab12cd34') to correct or resolve an older entry.",
        _schema(
            {
                "text": {"type": "string"},
                "kind": {"type": "string", "enum": list(memory.KINDS)},
                "supersedes": {"type": "string", "description": "Optional id of the entry this replaces."},
            },
            ["text", "kind"],
        ),
    )
    async def memory_write(args: dict[str, Any]) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            try:
                entry = await memory.write(session, user_id, args["text"], args["kind"],
                                           supersedes=args.get("supersedes") or None)
            except memory.MemoryStoreError as exc:
                return _err(str(exc))
        return _ok(f"Saved: {memory.format_entry(entry)}")

    @tool(
        "get_workout_log",
        "Confirmed workout sessions for the last N days (newest first).",
        _schema({"days": {"type": "integer", "minimum": 1, "maximum": 365}}, ["days"]),
    )
    async def get_workout_log(args: dict[str, Any]) -> dict[str, Any]:
        days = max(1, min(int(args.get("days", 30)), 365))
        async with AsyncSessionLocal() as session:
            sessions = await db.get_workout_sessions(session, user_id, days=days)
        if not sessions:
            return _ok("No workouts recorded in that period.")
        return _ok("\n".join(f"{s.date} {', '.join(s.muscle_groups)}" for s in sessions))

    @tool(
        "record_workout",
        "Record a workout the user says they completed, update the streak, and return the new stats.",
        _schema(
            {
                "muscle_groups": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                  "description": "Korean names, e.g. ['등', '이두']."},
                "day": {"type": "string", "enum": ["today", "yesterday"],
                        "description": "When it happened. Default today."},
                "notes": {"type": "string", "description": "Optional details (weights, PRs, how it felt)."},
            },
            ["muscle_groups"],
        ),
    )
    async def record_workout(args: dict[str, Any]) -> dict[str, Any]:
        groups = [g.strip() for g in args.get("muscle_groups", []) if g and g.strip()]
        if not groups:
            return _err("muscle_groups is empty.")
        notes = (args.get("notes") or "").strip()
        day = now_kst().date() - timedelta(days=1 if args.get("day") == "yesterday" else 0)
        async with AsyncSessionLocal() as session:
            await db.insert_workout_session(session, user_id, muscle_groups=groups,
                                            agent_notes_text=notes, day=day)
            streak = await db.update_streak(session, user_id)
            week = await db.count_workouts_this_week(session, user_id)
            user = await db.get_user(session, user_id)
            # No memory entry here: workout_sessions is the record, and the extractor already
            # captured the user's own description of the workout.
            await scheduler_jobs.cancel_user_followup(user_id, session)  # nothing left to remind about
        return _ok(
            f"Recorded {day}: {', '.join(groups)}. Current streak: {streak.current_streak} days "
            f"(longest {streak.longest_streak}). This week: {week}/{user.weekly_goal_count or 3}. "
            "Any pending follow-up was cancelled."
        )

    @tool(
        "schedule_followup",
        "Wake yourself up later for this user (replaces any pending follow-up), e.g. a no-reply "
        "nudge. For a time the user committed to, use commit_plan instead. Give either `at` "
        "(HH:MM, KST; the next occurrence) or `in_minutes`. Proactive messages can't be sent during "
        f"quiet hours ({settings.quiet_start:02d}:00–{settings.quiet_end:02d}:00).",
        _schema(
            {
                "at": {"type": "string", "pattern": "^[0-2]?[0-9]:[0-5][0-9]$"},
                "in_minutes": {"type": "integer", "minimum": 5, "maximum": 1440},
                "reason": {"type": "string", "description": "Why, e.g. 'pre-reminder for 21:00 back workout'."},
            },
            ["reason"],
        ),
    )
    async def schedule_followup(args: dict[str, Any]) -> dict[str, Any]:
        try:
            run_at = scheduler_jobs.resolve_followup_time(at=args.get("at"), in_minutes=args.get("in_minutes"))
        except ValueError as exc:
            return _err(str(exc))
        async with AsyncSessionLocal() as session:
            if state.trigger.proactive:
                unanswered = await db.count_unanswered_proactive_today(session, user_id)
                if unanswered >= settings.max_unanswered_proactive:
                    return _err("Today's nudges are finished (the release message was sent). "
                                "Don't schedule more; wait for the user.")
            kind = "nudge" if state.trigger.proactive else "custom"
            await scheduler_jobs.schedule_user_followup(user_id, run_at, args["reason"], session, kind=kind)
        return _ok(f"Follow-up scheduled for {run_at.astimezone(KST):%m-%d %H:%M} KST: {args['reason']}")

    @tool(
        "commit_plan",
        "The user committed to working out at a time (\"21시에 갈게\"). Schedules a reminder ~30 min "
        "before and, automatically, a check-in ~15 min after if no workout is recorded. Replaces any "
        "pending follow-up.",
        _schema(
            {
                "at": {"type": "string", "pattern": "^[0-2]?[0-9]:[0-5][0-9]$",
                       "description": "Planned start, HH:MM KST (the next occurrence)."},
                "plan": {"type": "string", "description": "Short Korean description, e.g. '등 운동'."},
            },
            ["at", "plan"],
        ),
    )
    async def commit_plan(args: dict[str, Any]) -> dict[str, Any]:
        try:
            commitment_at = parse_local_time_today(args["at"])
        except ValueError:
            return _err(f"Invalid time '{args.get('at')}'. Use HH:MM (24h, KST).")
        plan = " ".join((args.get("plan") or "운동").split())[:100]
        async with AsyncSessionLocal() as session:
            summary = await scheduler_jobs.schedule_commitment(user_id, commitment_at, plan, session)
        return _ok(f"Plan noted: {plan} at {commitment_at:%H:%M}. {summary}")

    @tool(
        "snooze_proactive",
        "Stop proactive messages for a while (the user is busy, sick, travelling, or said not today). "
        "Cancels any pending follow-up. days: 0 = rest of today, N = through N more days, "
        "-1 = end an existing snooze. Replies to the user are never affected.",
        _schema(
            {
                "days": {"type": "integer", "minimum": -1, "maximum": 14},
                "reason": {"type": "string"},
            },
            ["days", "reason"],
        ),
    )
    async def snooze_proactive(args: dict[str, Any]) -> dict[str, Any]:
        days = int(args["days"])
        async with AsyncSessionLocal() as session:
            if days < 0:
                await db.set_proactive_snooze(session, user_id, None)
                return _ok("Snooze ended; proactive messages are allowed again.")
            until = datetime.combine(now_kst().date() + timedelta(days=days + 1),
                                     time(settings.quiet_end), tzinfo=KST)
            await db.set_proactive_snooze(session, user_id, until)
            await scheduler_jobs.cancel_user_followup(user_id, session)
        log.info("Proactive snoozed | user=%s | until=%s", user_id, until.isoformat())
        return _ok(f"No proactive messages until {until:%m-%d %H:%M} KST ({args['reason']}).")

    @tool(
        "update_profile",
        "Update a profile setting the user stated. weekly_goal: sessions per week (1–14). "
        "rest_days: days they never train, e.g. '토,일' or 'none'. usual_time: when they're usually "
        "free to work out, HH:MM or 'none' (used to time the daily nudge).",
        _schema(
            {
                "field": {"type": "string", "enum": ["weekly_goal", "rest_days", "usual_time"]},
                "value": {"type": "string"},
            },
            ["field", "value"],
        ),
    )
    async def update_profile(args: dict[str, Any]) -> dict[str, Any]:
        field_name, value = args["field"], str(args.get("value", "")).strip()
        try:
            column, parsed, shown = _parse_profile_value(field_name, value)
        except ValueError as exc:
            return _err(str(exc))
        async with AsyncSessionLocal() as session:
            await db.update_user_fields(session, user_id, **{column: parsed})
        return _ok(f"Updated {field_name} → {shown}.")

    @tool("cancel_followup", "Cancel the pending follow-up, if any.", _schema({}, []))
    async def cancel_followup(args: dict[str, Any]) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            await scheduler_jobs.cancel_user_followup(user_id, session)
        return _ok("Follow-up cancelled.")

    return [send_message, memory_read, memory_write, get_workout_log, record_workout,
            commit_plan, schedule_followup, cancel_followup, snooze_proactive, update_profile]


_WEEKDAY_NAMES = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}


def _parse_profile_value(field_name: str, value: str) -> tuple[str, Any, str]:
    """Validate an update_profile value. Returns (column, parsed value, display text)."""
    none = value.lower() in {"none", "없음", ""}
    if field_name == "weekly_goal":
        if not value.isdigit() or not 1 <= int(value) <= 14:
            raise ValueError("weekly_goal must be a number from 1 to 14.")
        return "weekly_goal_count", int(value), value
    if field_name == "rest_days":
        if none:
            return "rest_weekdays", [], "none"
        days = sorted({_WEEKDAY_NAMES[c] for c in value if c in _WEEKDAY_NAMES})
        if not days:
            raise ValueError("rest_days must name weekdays in Korean (월 화 수 목 금 토 일) or be 'none'.")
        return "rest_weekdays", days, ",".join("월화수목금토일"[d] for d in days)
    if field_name == "usual_time":
        if none:
            return "default_departure_time", None, "none"
        try:
            hour, minute = (int(x) for x in value.split(":"))
            return "default_departure_time", time(hour, minute), f"{hour:02d}:{minute:02d}"
        except ValueError:
            raise ValueError("usual_time must be HH:MM (24h) or 'none'.") from None
    raise ValueError(f"Unknown field '{field_name}'.")

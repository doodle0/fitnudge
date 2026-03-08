"""
LangChain tool definitions for the FitNudge Orchestrator.
All tools are created via a factory that closes over user_id.

Each tool opens its own DB session via AsyncSessionLocal so that the agent can
call multiple tools in parallel (asyncio.gather) without hitting asyncpg's
"another operation is in progress" error that occurs when a single session is
used concurrently.
"""
from __future__ import annotations


def _format_workout_history(sessions) -> str:
    if not sessions:
        return "No workout history found."
    lines = []
    for s in sessions:
        groups = ", ".join(s.muscle_groups) if s.muscle_groups else "운동 종류 미기록"
        lines.append(f"[{s.date}] {groups}")
    return "\n".join(lines)


def get_all_tools(user_id: str) -> list:
    """
    Return all agent tools pre-bound to user_id.
    Each tool creates its own AsyncSession to support parallel execution.
    """
    from langchain_core.tools import tool

    from db import queries as db
    from db.models import AsyncSessionLocal
    from kakao import calendar as kakao_cal
    from kakao import message as kakao_msg
    from scheduler import jobs as scheduler_jobs
    from utils import message_guard

    @tool
    async def get_workout_history(days: int = 30) -> str:
        """Get the user's workout history for the last N days.
        Returns a list of sessions with dates and muscle groups trained."""
        async with AsyncSessionLocal() as session:
            sessions = await db.get_workout_sessions(session, user_id, days=days)
            return _format_workout_history(sessions)

    @tool
    async def get_notes(days: int = 14) -> str:
        """Get the agent's saved notes about this user from the last N days.
        Notes include commitments, injuries, mood, and stated plans."""
        async with AsyncSessionLocal() as session:
            notes = await db.get_notes(session, user_id, days=days)
            if not notes:
                return "No notes found."
            return "\n".join(f"[{n.date}] {n.note}" for n in notes)

    @tool
    async def save_note(note: str) -> str:
        """Save a notable fact about the user with today's date.
        Use after every meaningful user message to capture: commitments made,
        injuries mentioned, reasons for skipping, emotional state, goal changes."""
        async with AsyncSessionLocal() as session:
            await db.insert_note(session, user_id, note)
        return "Note saved."

    @tool
    async def get_location_status() -> str:
        """Check the user's current location status.
        Returns one of: 'at_work', 'commuting', 'near_gym', 'at_home', 'unknown'.
        Location data is populated by the /webhook/location endpoint."""
        # Location state is set by the geofence webhook (Phase 3).
        # Phase 2: always returns 'unknown' until the webhook is wired.
        return "unknown"

    @tool
    async def get_calendar_events(date: str = "today") -> str:
        """Get the user's Kakao Calendar events for a given date (default: today).
        Use to detect overtime or dinner events that should suppress nudging."""
        async with AsyncSessionLocal() as session:
            events = await kakao_cal.get_calendar_events(user_id, target_date=date, session=session)
        return kakao_cal.format_calendar_events(events)

    @tool
    async def get_streak() -> str:
        """Get the user's current and longest workout streak."""
        async with AsyncSessionLocal() as session:
            streak = await db.get_streak(session, user_id)
        if streak is None:
            return "No streak data available."
        return (
            f"Current streak: {streak.current_streak} days, "
            f"Longest: {streak.longest_streak} days, "
            f"Last workout: {streak.last_workout_date}"
        )

    @tool
    async def send_kakao_message(message_text: str) -> str:
        """Send a KakaoTalk message to the user.
        Write in Korean. Keep it to 2–4 sentences. Sound like a friend, not a system."""
        async with AsyncSessionLocal() as session:
            await kakao_msg.send_message(user_id, message_text, session)
            await message_guard.increment(user_id, session)
            await db.insert_conversation_turn(session, user_id, role="agent", content=message_text)
        return "Message sent."

    @tool
    async def save_workout_record(muscle_groups: list[str], notes: str = "") -> str:
        """Record a completed workout session for today.
        muscle_groups: list of Korean muscle group names the user reported.
        Call this when the user confirms they finished working out."""
        async with AsyncSessionLocal() as session:
            await db.insert_workout_session(
                session,
                user_id,
                muscle_groups=muscle_groups,
                agent_notes_text=notes,
            )
            await db.update_streak(session, user_id)
        return "Workout recorded and streak updated."

    @tool
    async def schedule_followup(delay_minutes: int, reason: str) -> str:
        """Schedule the agent to wake up again in delay_minutes minutes for this user.
        Cancels any existing pending follow-up first.
        reason: brief description (e.g. 'pre-commitment reminder', 'no response nudge')."""
        async with AsyncSessionLocal() as session:
            await scheduler_jobs.schedule_user_followup(user_id, delay_minutes, reason, session)
        return f"Follow-up scheduled in {delay_minutes} minutes: {reason}"

    @tool
    async def cancel_followup() -> str:
        """Cancel any pending follow-up scheduled for this user today."""
        async with AsyncSessionLocal() as session:
            await scheduler_jobs.cancel_user_followup(user_id, session)
        return "Follow-up cancelled."

    return [
        get_workout_history,
        get_notes,
        save_note,
        get_location_status,
        get_calendar_events,
        get_streak,
        send_kakao_message,
        save_workout_record,
        schedule_followup,
        cancel_followup,
    ]

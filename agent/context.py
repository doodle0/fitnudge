"""
Per-run context snapshot, rendered as the agent's first (and only) user turn.
All volatile data lives here so the system prompt stays cacheable.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from agent.triggers import Trigger
from config import settings
from memory import store as memory
from timing.windows import window_for
from utils.time_utils import KST, now_kst

_WEEKDAYS_KO = "월화수목금토일"
RECENT_TURNS = 20
WORKOUT_DAYS = 14
MEMORY_DAYS = 14


async def build_context(session: AsyncSession, user_id: str, trigger: Trigger) -> str:
    from db import queries as db

    user = await db.get_user(session, user_id)
    if user is None:
        raise LookupError(f"Unknown user {user_id}")
    identity = await db.get_identity_for_user(session, user_id, user.active_channel)
    streak = await db.get_streak(session, user_id)
    workouts = await db.get_workout_sessions(session, user_id, days=WORKOUT_DAYS)
    week_count = await db.count_workouts_this_week(session, user_id)
    today_workout = await db.get_workout_session_today(session, user_id)
    proactive_count, reply_count = await db.get_daily_counts(session, user_id)
    unanswered = await db.count_unanswered_proactive_today(session, user_id)
    followup = await db.get_scheduled_followup(session, user_id)
    entries = await memory.window(session, user_id, days=MEMORY_DAYS)
    turns = await db.get_recent_turns(session, user_id, limit=RECENT_TURNS)
    profile = await db.get_timing_profile(session, user_id)

    now = now_kst()
    lines: list[str] = []

    lines += ["# Trigger",
              f"kind: {trigger.kind} ({'proactive' if trigger.proactive else 'reactive'})",
              f"reason: {trigger.describe()}"]
    if trigger.first_contact:
        lines.append("first_contact: yes — this is the user's first message ever. Greet them, introduce "
                     "yourself in one line, and mention they can run /start to let you message them first.")

    lines += ["", "# Now",
              f"{now:%Y-%m-%d} ({_WEEKDAYS_KO[now.weekday()]}) {now:%H:%M} KST"]

    lines += ["", "# User",
              f"name: {user.display_name or '(unknown)'}",
              f"weekly goal: {user.weekly_goal_count or 3} sessions (app default — don't present it as the "
              "user's own goal unless memory says they set it)",
              f"proactive messages allowed: {'yes' if identity and identity.opted_in_proactive else 'no'}",
              f"rest days: {', '.join(_WEEKDAYS_KO[d] for d in (user.rest_weekdays or [])) or 'none set'}",
              f"usual workout time: {user.default_departure_time.strftime('%H:%M') if user.default_departure_time else 'not set'}"]
    if user.proactive_snoozed_until and user.proactive_snoozed_until > now:
        lines.append(f"proactive snoozed until: {user.proactive_snoozed_until.astimezone(KST):%m-%d %H:%M}")

    followup_text = (f"{followup.scheduled_for.astimezone(KST):%m-%d %H:%M} — {followup.kind}: {followup.reason}"
                     if followup and followup.scheduled_for else "none")
    lines += ["", "# Today",
              f"worked out today: {'yes (' + ', '.join(today_workout.muscle_groups) + ')' if today_workout else 'no'}",
              f"proactive messages sent: {proactive_count}/{settings.max_daily_proactive}",
              f"unanswered proactive messages: {unanswered}/{settings.max_unanswered_proactive}",
              f"replies sent: {reply_count}",
              f"pending follow-up: {followup_text}",
              f"daily nudge window: {window_for(now.date(), user, profile).describe()}"
              f"{' — already used today' if profile and profile.last_window_fired_on == now.date() else ''}",
              f"quiet hours (no proactive messages): {settings.quiet_start:02d}:00–{settings.quiet_end:02d}:00"]

    lines += ["", "# Streak & week",
              f"current streak: {streak.current_streak if streak else 0} days "
              f"(longest {streak.longest_streak if streak else 0}), "
              f"last workout: {streak.last_workout_date if streak and streak.last_workout_date else 'none'}",
              f"this week (since Monday): {week_count}/{user.weekly_goal_count or 3}"]

    lines += ["", f"# Workout log (last {WORKOUT_DAYS} days, newest first)"]
    if workouts:
        for w in workouts:
            lines.append(f"{w.date} ({_WEEKDAYS_KO[w.date.weekday()]}) {', '.join(w.muscle_groups)}")
    else:
        lines.append("(no workouts recorded)")

    lines += ["", f"# Memory (last {MEMORY_DAYS} days + 📌 pinned)", memory.format_entries(entries)]

    lines += ["", "# Recent conversation (oldest first)"]
    if turns:
        for t in turns:
            who = settings.bot_name if t.role == "agent" else "user"
            lines.append(f"[{t.created_at.astimezone(KST):%m-%d %H:%M}] {who}: {t.content}")
    else:
        lines.append("(no messages yet)")

    lines += ["", "# Your task"]
    if trigger.proactive:
        step = unanswered + 1
        ladder = {1: "first nudge — a specific suggestion plus an easy out",
                  2: "no reply yet — a different angle or a smaller ask (\"10분만\")",
                  3: "last one — a warm release (\"오늘은 쉬어도 돼요, 내일 봐요\"); schedule nothing after it"}
        lines.append(f"Proactive ladder step {min(step, 3)} of 3: {ladder.get(step, ladder[3])}.")
        lines.append("Decide whether a proactive message is genuinely helpful right now. If yes, send one "
                     "with send_message (and schedule a follow-up if warranted). If not, end without sending.")
    else:
        lines.append("The user's latest message is the last 'user' line above. Reply with send_message.")

    return "\n".join(lines)

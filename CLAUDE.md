# CLAUDE.md — FitNudge Technical Implementation Guide

> This file is the technical specification for a coding agent implementing FitNudge. All user-facing strings, KakaoTalk messages, and in-app content are in **Korean**. All code, variable names, comments, and this document are in **English**.

---

## Project Overview

FitNudge is an LLM-powered KakaoTalk fitness accountability bot. A LangChain agent is woken by a scheduler or incoming user message, gathers context via tools, decides whether and how to engage the user, sends psychologically-crafted Korean messages, and records outcomes. The system is conversational and memory-driven — it replaces rule-based message templates entirely with an LLM that reads history and writes naturally.

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.13+, FastAPI |
| **Package Manager** | [uv](https://docs.astral.sh/uv/) — dependencies declared in `pyproject.toml`, locked in `uv.lock` |
| **LLM Agent** | LangChain `create_react_agent`, `langchain-anthropic` (Claude claude-sonnet-4-5 or later) |
| **Agent Memory** | `ConversationSummaryBufferMemory` (LangChain) backed by PostgreSQL |
| **Scheduler** | APScheduler 3.x with `SQLAlchemyJobStore` (persistent across restarts) |
| **Primary DB** | PostgreSQL 15+ |
| **Cache / State** | Redis 7+ |
| **HTTP Client** | `httpx` (async) for all Kakao API calls |
| **Deployment** | Railway or Render (always-on container); or AWS ECS Fargate |

---

## Environment Variables

```env
# Anthropic
ANTHROPIC_API_KEY=

# Kakao
KAKAO_REST_API_KEY=
KAKAO_CLIENT_SECRET=
KAKAO_REDIRECT_URI=
KAKAO_ADMIN_KEY=

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@host/db
REDIS_URL=redis://localhost:6379/0

# App
APP_BASE_URL=https://your-app.com
SECRET_KEY=                        # For signing internal requests
MAX_DAILY_MESSAGES=5               # Hard cap on agent messages per user per day
AGENT_SILENT_AFTER_HOUR=22         # Local hour (24h) after which agent never messages
```

---

## Project Structure

```
fitnudge/
├── main.py                        # FastAPI app entry point
├── agent/
│   ├── orchestrator.py            # Main LangChain agent (create_react_agent)
│   ├── tools.py                   # All @tool definitions
│   ├── subagents/
│   │   ├── history_analyzer.py    # WorkoutHistoryAnalyzer sub-agent
│   │   ├── message_crafter.py     # MessageCrafter sub-agent
│   │   └── note_keeper.py         # NoteKeeper sub-agent
│   └── prompts/
│       ├── orchestrator_system.py
│       ├── initial_nudge.py
│       ├── commitment_confirm.py
│       ├── pre_commitment_reminder.py
│       ├── overdue_checkin.py
│       ├── no_response_renudge.py
│       ├── completion_celebration.py
│       ├── skip_acknowledgment.py
│       ├── history_analysis.py
│       ├── note_extraction.py
│       └── weekly_summary.py
├── scheduler/
│   ├── jobs.py                    # APScheduler job definitions
│   └── triggers.py                # Logic for deciding when to invoke agent
├── kakao/
│   ├── auth.py                    # OAuth flow, token refresh
│   ├── message.py                 # send_kakao_message()
│   ├── location.py                # Location API, geofencing
│   └── calendar.py                # Calendar read/write
├── db/
│   ├── models.py                  # SQLAlchemy ORM models
│   ├── schema.sql                 # Raw SQL schema for reference
│   └── queries.py                 # Typed query helpers
├── routes/
│   ├── auth.py                    # /auth/kakao, /auth/callback
│   ├── webhook.py                 # /webhook/message, /webhook/location
│   └── internal.py                # /internal/scheduler-tick (cron endpoint)
├── utils/
│   ├── haversine.py
│   ├── time_utils.py              # Korean timezone helpers
│   └── message_guard.py          # Enforces MAX_DAILY_MESSAGES hard cap
├── .env.example
├── pyproject.toml                 # uv-managed dependencies
├── uv.lock                        # locked dependency graph
└── CLAUDE.md
```

---

## Database Schema

```sql
-- Users
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kakao_id BIGINT UNIQUE NOT NULL,
  kakao_nickname TEXT,
  access_token TEXT NOT NULL,
  refresh_token TEXT NOT NULL,
  token_expires_at TIMESTAMPTZ NOT NULL,
  workplace_lat DOUBLE PRECISION,
  workplace_lng DOUBLE PRECISION,
  default_departure_time TIME,
  weekly_goal_count INT DEFAULT 3,
  preferred_exercises TEXT[],
  timezone TEXT DEFAULT 'Asia/Seoul',
  onboarding_complete BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Workout sessions
CREATE TABLE workout_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  muscle_groups TEXT[] NOT NULL,      -- e.g. ARRAY['등', '이두']
  raw_user_message TEXT,              -- verbatim what the user said
  agent_notes TEXT,
  source TEXT CHECK (source IN ('user_report', 'geofence', 'manual')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Date-keyed notes (agent's memory of notable events)
CREATE TABLE agent_notes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL DEFAULT CURRENT_DATE,
  note TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_agent_notes_user_date ON agent_notes(user_id, date DESC);

-- Conversation history (full turn log)
CREATE TABLE conversation_turns (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  role TEXT CHECK (role IN ('agent', 'user')) NOT NULL,
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Streak tracking
CREATE TABLE streaks (
  user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  current_streak INT DEFAULT 0,
  longest_streak INT DEFAULT 0,
  last_workout_date DATE,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Daily message count guard
CREATE TABLE daily_message_counts (
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL DEFAULT CURRENT_DATE,
  count INT DEFAULT 0,
  PRIMARY KEY (user_id, date)
);

-- Scheduled follow-ups
CREATE TABLE scheduled_followups (
  user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  job_id TEXT NOT NULL,              -- APScheduler job ID
  reason TEXT,
  scheduled_for TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## LangChain Agent Setup

### Orchestrator Agent

```python
# agent/orchestrator.py
from langchain.agents import create_react_agent, AgentExecutor
from langchain_anthropic import ChatAnthropic
from langchain.memory import ConversationSummaryBufferMemory
from langchain_community.chat_message_histories import SQLChatMessageHistory
from agent.tools import get_all_tools
from agent.prompts.orchestrator_system import build_orchestrator_prompt

def build_agent(user_id: str) -> AgentExecutor:
    llm = ChatAnthropic(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        temperature=0.7,
    )

    # Persistent memory per user backed by PostgreSQL
    message_history = SQLChatMessageHistory(
        session_id=user_id,
        connection_string=settings.DATABASE_URL,
    )
    memory = ConversationSummaryBufferMemory(
        llm=llm,
        chat_memory=message_history,
        max_token_limit=2000,
        memory_key="conversation_history",
        return_messages=True,
    )

    tools = get_all_tools(user_id)
    prompt = build_orchestrator_prompt()

    agent = create_react_agent(llm=llm, tools=tools, prompt=prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        memory=memory,
        verbose=True,
        max_iterations=10,
        handle_parsing_errors=True,
    )


async def invoke_agent(user_id: str, trigger_reason: str, user_message: str = None):
    """
    Main entry point. Called by scheduler or webhook handler.
    Enforces hard daily message cap before invoking.
    """
    if not await message_guard.can_send(user_id):
        return  # Hard cap reached — do not invoke agent

    user_context = await build_user_context(user_id)
    
    input_text = f"Trigger: {trigger_reason}"
    if user_message:
        input_text += f"\nUser message: {user_message}"
    input_text += f"\n\nUser context:\n{user_context}"

    executor = build_agent(user_id)
    await executor.ainvoke({"input": input_text})
```

### Tool Definitions

```python
# agent/tools.py
from langchain.tools import tool
from functools import partial

def get_all_tools(user_id: str):
    """Returns all tools pre-bound to the user_id."""

    @tool
    async def get_workout_history(days: int = 30) -> str:
        """Get the user's workout history for the last N days.
        Returns a list of sessions with dates and muscle groups trained."""
        sessions = await db.get_workout_sessions(user_id, days=days)
        return format_workout_history(sessions)

    @tool
    async def get_notes(days: int = 14) -> str:
        """Get the agent's saved notes about this user from the last N days.
        Notes include commitments, injuries, mood, stated plans."""
        notes = await db.get_notes(user_id, days=days)
        return "\n".join(f"[{n.date}] {n.note}" for n in notes)

    @tool
    async def save_note(note: str) -> str:
        """Save a notable fact about the user with today's date.
        Use after every meaningful user message to capture: commitments made,
        injuries mentioned, reasons for skipping, emotional state, goal changes."""
        await db.insert_note(user_id, note)
        return "Note saved."

    @tool
    async def get_location_status() -> str:
        """Check the user's current location status.
        Returns one of: 'at_work', 'commuting', 'near_gym', 'at_home', 'unknown'."""
        return await kakao.get_location_status(user_id)

    @tool
    async def get_calendar_events(date: str = "today") -> str:
        """Get the user's Kakao Calendar events for a given date (default: today).
        Use to detect overtime or dinner events that should suppress nudging."""
        events = await kakao.get_calendar_events(user_id, date)
        return format_calendar_events(events)

    @tool
    async def get_streak() -> str:
        """Get the user's current and longest workout streak."""
        streak = await db.get_streak(user_id)
        return f"Current streak: {streak.current}, Longest: {streak.longest}, Last workout: {streak.last_workout_date}"

    @tool
    async def send_kakao_message(message_text: str) -> str:
        """Send a KakaoTalk message to the user.
        Write in Korean. Keep it to 2-4 sentences. Sound like a friend, not a system."""
        await kakao.send_message(user_id, message_text)
        await message_guard.increment(user_id)
        await db.insert_conversation_turn(user_id, role="agent", content=message_text)
        return "Message sent."

    @tool
    async def save_workout_record(muscle_groups: list[str], notes: str = "") -> str:
        """Record a completed workout session for today.
        muscle_groups: list of Korean muscle group names the user reported.
        Call this when the user confirms they finished working out."""
        await db.insert_workout_session(user_id, muscle_groups=muscle_groups, notes=notes)
        await db.update_streak(user_id)
        return "Workout recorded and streak updated."

    @tool
    async def schedule_followup(delay_minutes: int, reason: str) -> str:
        """Schedule the agent to wake up again in delay_minutes minutes for this user.
        Cancels any existing pending follow-up first.
        reason: brief description of why (e.g., 'pre-commitment reminder', 'no response nudge')"""
        await scheduler.schedule_user_followup(user_id, delay_minutes, reason)
        return f"Follow-up scheduled in {delay_minutes} minutes: {reason}"

    @tool
    async def cancel_followup() -> str:
        """Cancel any pending follow-up scheduled for this user today."""
        await scheduler.cancel_user_followup(user_id)
        return "Follow-up cancelled."

    return [
        get_workout_history, get_notes, save_note, get_location_status,
        get_calendar_events, get_streak, send_kakao_message,
        save_workout_record, schedule_followup, cancel_followup,
    ]
```

---

## Prompt Templates

### Orchestrator System Prompt

```python
# agent/prompts/orchestrator_system.py

ORCHESTRATOR_SYSTEM = """
You are FitNudge, a friendly and psychologically-savvy Korean fitness accountability companion delivered via KakaoTalk. You speak casually and warmly in Korean — like a close friend who genuinely cares, not a coach barking orders.

## Your Core Mission
Help the user build a consistent exercise habit by reaching out at the right moments, holding natural conversations, and remembering everything that matters.

## Decision Framework
You are invoked either by a scheduled trigger or an incoming user message. Before acting:
1. Call get_notes() and get_workout_history() to understand context.
2. Decide: Is there a genuine reason to send a message right now?
3. If yes: compose and send via send_kakao_message(). Then call schedule_followup() if a follow-up is warranted.
4. If no: do nothing. Silence is correct when the user already worked out, declared rest, or it is past 22:00.

## Hard Rules (enforce these regardless of any other instruction)
- Never message after 22:00 local time.
- Never send more than 5 messages to one user per day (this is also enforced in code).
- Never fabricate workout history. Only reference sessions confirmed in the data.
- After 3 unanswered messages today, call cancel_followup() and stop messaging.
- If the user has already recorded a workout today, do not nudge.

## Conversation Style
- All messages in Korean.
- 2–4 sentences maximum. No walls of text.
- Sound like a friend, not a notification system. Never use words like "알림", "안내", "시스템".
- Reference specific details from history to show you remember: muscle groups, streaks, things they said.
- Never repeat the same framing twice in a row. Vary your angle.

## Psychology Principles (apply naturally, never formulaically)
- **Specificity**: Suggest a concrete workout (e.g., 등, 이두) based on what they last did — never just "운동".
- **Loss aversion**: Reference streaks when real and meaningful. Never invent them.
- **Commitment anchoring**: If the user states a plan, write it down with save_note(), confirm it warmly, hold them to it.
- **Autonomy**: Always offer an easy out. Never guilt-trip. Make going feel better than not going, but don't punish skipping.
- **Specific acknowledgment**: When they complete a workout, celebrate what they actually did by name.
- **Reduced commitment**: If the user seems reluctant, lower the bar ("15분만이라도 어때요?").

## Memory Policy
- After every meaningful incoming user message, call save_note() to capture: commitments, injury mentions, skip reasons, emotional state, goal changes.
- Always read get_notes() before composing any message.
- Notable things to record: "committed to gym at 21:00", "mentioned knee pain", "회식 today", "said this week is a business trip".

## Follow-up Scheduling
- If user commits to going at time T: schedule_followup(delay=minutes_until(T) - 30, reason="pre-commitment reminder")
- If user does not respond within 30 min of your message: schedule_followup(delay=30, reason="no response nudge")
- If committed time T passes by 10+ min with no update: send one gentle overdue check-in, then schedule_followup(delay=60, reason="final check")
- When user reports completion or explicitly skips: cancel_followup()

## Recording Workouts
- When user reports completing a workout (any natural phrasing, e.g., "ㅇㅇ 오늘 등이랑 이두 했어"), parse the muscle groups and call save_workout_record().
- Do not require structured input — understand what they say naturally.
- Confirm warmly and reference the updated streak.

## Trigger Reason
{trigger_reason}

## Current User Context
{user_context}
"""
```

### MessageCrafter Sub-Agent Prompt

```python
# agent/prompts/message_crafter.py

# This sub-agent is invoked by the Orchestrator as a tool when it wants
# a high-quality message for a specific psychological goal.

INITIAL_NUDGE_PROMPT = """
Write a KakaoTalk message in Korean to nudge the user to exercise after detecting they have left work.

Rules:
- 2 sentences maximum
- Reference the last 1-2 workouts specifically (muscle groups and day)
- Suggest today's workout based on what they haven't done recently
- Casual, warm tone — like a friend who remembers
- Include one relevant emoji
- Do NOT say "운동하세요" or any command form. Make it an invitation.

User's recent workouts:
{workout_history}

Current streak: {streak}

Recent notes: {notes}

Write only the message text. No explanation.
"""

COMMITMENT_CONFIRM_PROMPT = """
Write a short, warm Korean KakaoTalk reply confirming the user's workout plan.

The user said: "{user_message}"
Extracted plan: go at {committed_time}, workout type: {workout_type}

Rules:
- 1-2 sentences only
- Sound happy and supportive, not clinical
- Optionally include a small encouragement about the specific workout
- Do not repeat their exact words back to them

Write only the message text.
"""

PRE_COMMITMENT_REMINDER_PROMPT = """
Write a short Korean KakaoTalk reminder that it's almost time for the user's planned gym session.

Committed time: {committed_time} (in ~30 minutes)
Workout type: {workout_type}
Current streak: {streak}

Rules:
- 1-2 sentences
- Frame it as their own plan, not your reminder ("슬슬 준비하실 시간이죠?" not "운동 시간입니다")
- Casual and energetic

Write only the message text.
"""

OVERDUE_CHECKIN_PROMPT = """
Write a non-judgmental Korean KakaoTalk check-in for when the user's committed gym time has passed.

Committed time: {committed_time}
Minutes overdue: {minutes_overdue}

Rules:
- 1-2 sentences
- Open-ended — they might already be working out
- Zero guilt or disappointment
- Ask them to message when done

Write only the message text.
"""

NO_RESPONSE_RENUDGE_PROMPT = """
Write a Korean KakaoTalk re-nudge message. This is nudge #{nudge_number} today (max 3).

Rules for each nudge number:
- Nudge 1: Streak + warmth ("X일 스트릭인데 오늘 하면 X+1일이에요 💪")
- Nudge 2: Reduced commitment — make it feel easy ("15분만이라도 어때요?")
- Nudge 3: Soft opt-out — fully release pressure ("오늘 힘드시면 내일 봐요 😄")

Never repeat the previous message's framing.

Current streak: {streak}
Previous message sent: "{previous_message}"

Write only the message text.
"""

COMPLETION_CELEBRATION_PROMPT = """
Write a warm Korean KakaoTalk celebration for the user completing their workout.

What they did: {muscle_groups}
Updated streak: {streak}
Workouts this week: {weekly_count} / {weekly_goal}

Rules:
- Name what they actually did (do not be generic)
- Celebrate the streak if it is meaningful (3+ days)
- Plant a gentle seed for tomorrow without pressure (optional, if appropriate)
- 2-3 sentences maximum

Write only the message text.
"""

SKIP_ACKNOWLEDGMENT_PROMPT = """
Write a zero-judgment Korean KakaoTalk reply for when the user skips their workout.

Reason they gave: "{skip_reason}"
Current streak (now broken or preserved): {streak}
Workouts this week: {weekly_count} / {weekly_goal}

Rules:
- Fully accept the skip. No guilt, no "next time be better"
- If they gave a social reason (회식, etc.) you can make a light joke
- Keep it brief and warm — preserve the relationship
- End on a forward-looking but unpressured note

Write only the message text.
"""

WEEKLY_SUMMARY_PROMPT = """
Write a weekly workout summary in Korean for a KakaoTalk message.

This week: {weekly_count} workouts out of {weekly_goal} goal
Muscle groups trained: {muscle_groups_this_week}
Current streak: {streak}
Notable events this week (from notes): {notable_notes}

Rules:
- 3-4 sentences
- Lead with what they accomplished, not what they missed
- Acknowledge any gap gently and briefly if relevant
- End with energy and positivity for next week
- Reference specific muscle groups they trained

Write only the message text.
"""
```

### WorkoutHistoryAnalyzer Sub-Agent Prompt

```python
# agent/prompts/history_analysis.py

HISTORY_ANALYSIS_PROMPT = """
Analyze this user's workout history and return a structured JSON summary.

Workout log (date, muscle_groups):
{workout_log}

Return a JSON object with these fields:
- current_streak: int (consecutive days ending today or yesterday)
- longest_streak: int
- last_workout_date: string (YYYY-MM-DD)
- days_since_last_workout: int
- most_trained: string (most frequent muscle group)
- least_trained: string (least frequent muscle group in trained set)
- muscle_rotation_suggestion: string (what muscle group to suggest next based on last 3 sessions)
- patterns: list of strings (notable observations, e.g. "never exercises on Fridays", "always pairs 등 and 이두")
- long_absence: bool (true if 5+ day gap exists recently)
- best_week_count: int (highest weekly workout count ever)
- this_week_count: int

Return only valid JSON. No explanation.
"""

NOTE_EXTRACTION_PROMPT = """
The user sent this KakaoTalk message: "{user_message}"

Extract any notable facts worth saving as agent notes. Return a JSON array of strings.

Look for:
- Commitments: "X시에 갈게", "내일 갈게", "오늘은 Y 할게"
- Physical issues: pain, injury, soreness, illness mentions
- Skip reasons: 회식, 출장, 피곤, 약속
- Emotional state: very tired, stressed, motivated
- Goal changes: "주 X회 해볼게"
- Completed workout details: muscle groups done

If nothing notable: return [].

Example output: ["committed to gym at 21:00", "mentioned right knee pain"]

Return only a JSON array. No explanation.
"""
```

---

## Scheduler Implementation

```python
# scheduler/jobs.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=settings.DATABASE_URL)},
    timezone="Asia/Seoul",
)

async def schedule_user_followup(user_id: str, delay_minutes: int, reason: str):
    """Schedule a one-time agent invocation for a specific user."""
    await cancel_user_followup(user_id)  # Cancel any existing follow-up

    job_id = f"followup_{user_id}"
    run_at = datetime.now(tz=KST) + timedelta(minutes=delay_minutes)

    scheduler.add_job(
        invoke_agent_job,
        trigger="date",
        run_date=run_at,
        id=job_id,
        args=[user_id, reason],
        replace_existing=True,
    )

    await db.upsert_scheduled_followup(user_id, job_id=job_id, reason=reason, scheduled_for=run_at)

async def cancel_user_followup(user_id: str):
    job_id = f"followup_{user_id}"
    try:
        scheduler.remove_job(job_id)
    except JobLookupError:
        pass
    await db.delete_scheduled_followup(user_id)

async def invoke_agent_job(user_id: str, reason: str):
    """Called by APScheduler. Invokes the Orchestrator for a specific user."""
    from agent.orchestrator import invoke_agent
    await invoke_agent(user_id=user_id, trigger_reason=reason)


# Cron job: check all users every 5 minutes during active hours (17:00–22:00 KST)
@scheduler.scheduled_job("cron", minute="*/5", hour="17-21", timezone="Asia/Seoul")
async def check_all_users():
    users = await db.get_active_users()
    for user in users:
        if await should_invoke_for_user(user):
            await invoke_agent(user_id=str(user.id), trigger_reason="scheduled_check")

# Cron job: weekly summary every Sunday at 21:00 KST
@scheduler.scheduled_job("cron", day_of_week="sun", hour=21, timezone="Asia/Seoul")
async def send_weekly_summaries():
    users = await db.get_active_users()
    for user in users:
        await invoke_agent(user_id=str(user.id), trigger_reason="weekly_summary")
```

---

## Kakao API Integration

### OAuth Flow

```python
# kakao/auth.py
import httpx

KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_USER_URL = "https://kapi.kakao.com/v2/user/me"

async def exchange_code_for_tokens(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(KAKAO_TOKEN_URL, data={
            "grant_type": "authorization_code",
            "client_id": settings.KAKAO_REST_API_KEY,
            "client_secret": settings.KAKAO_CLIENT_SECRET,
            "redirect_uri": settings.KAKAO_REDIRECT_URI,
            "code": code,
        })
        resp.raise_for_status()
        return resp.json()  # access_token, refresh_token, expires_in

async def refresh_access_token(refresh_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(KAKAO_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "client_id": settings.KAKAO_REST_API_KEY,
            "client_secret": settings.KAKAO_CLIENT_SECRET,
            "refresh_token": refresh_token,
        })
        resp.raise_for_status()
        return resp.json()

async def get_valid_token(user_id: str) -> str:
    """Returns a valid access token, refreshing if needed."""
    user = await db.get_user(user_id)
    if user.token_expires_at < datetime.now(tz=UTC) + timedelta(minutes=5):
        tokens = await refresh_access_token(user.refresh_token)
        await db.update_user_tokens(user_id, tokens)
        return tokens["access_token"]
    return user.access_token
```

### Sending Messages

```python
# kakao/message.py
SEND_MESSAGE_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

async def send_message(user_id: str, text: str) -> None:
    """Send a plain text KakaoTalk message to the user (send-to-me API)."""
    token = await get_valid_token(user_id)
    import json
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            SEND_MESSAGE_URL,
            headers={"Authorization": f"Bearer {token}"},
            data={
                "template_object": json.dumps({
                    "object_type": "text",
                    "text": text,
                    "link": {"web_url": settings.APP_BASE_URL},
                })
            }
        )
        if resp.status_code == 401:
            # Token expired mid-flight — refresh and retry once
            await refresh_access_token_for_user(user_id)
            token = await get_valid_token(user_id)
            resp = await client.post(SEND_MESSAGE_URL, ...)
        resp.raise_for_status()
```

### Kakao Local API — Gym Search

```python
# kakao/location.py
KAKAO_LOCAL_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"

async def search_nearby_gyms(lat: float, lng: float, radius: int = 1000) -> list:
    cache_key = f"gym_search:{lat:.4f}:{lng:.4f}"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            KAKAO_LOCAL_URL,
            headers={"Authorization": f"KakaoAK {settings.KAKAO_REST_API_KEY}"},
            params={"query": "헬스장", "x": lng, "y": lat, "radius": radius, "sort": "distance"},
        )
        resp.raise_for_status()
        gyms = resp.json()["documents"]

    await redis.setex(cache_key, 3600, json.dumps(gyms))
    return gyms
```

---

## Webhook Handler

```python
# routes/webhook.py
from fastapi import APIRouter, Request, BackgroundTasks

router = APIRouter()

@router.post("/webhook/message")
async def kakao_message_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receives incoming KakaoTalk messages from users.
    Must respond with 200 OK immediately; process agent in background.
    """
    payload = await request.json()
    user_kakao_id = payload["userRequest"]["user"]["id"]
    user_message = payload["userRequest"]["utterance"]

    user = await db.get_user_by_kakao_id(user_kakao_id)
    if not user:
        return {"version": "2.0", "template": {"outputs": []}}

    # Save raw turn immediately
    await db.insert_conversation_turn(str(user.id), role="user", content=user_message)

    # Process agent asynchronously — do not block the webhook response
    background_tasks.add_task(
        invoke_agent,
        user_id=str(user.id),
        trigger_reason="incoming_user_message",
        user_message=user_message,
    )

    return {"version": "2.0", "template": {"outputs": []}}
```

---

## Message Guard (Hard Daily Cap)

```python
# utils/message_guard.py
async def can_send(user_id: str) -> bool:
    """Enforced in code — the LLM cannot bypass this."""
    today = date.today().isoformat()
    count = await db.get_daily_message_count(user_id, today)
    current_hour = datetime.now(tz=KST).hour
    if current_hour >= settings.AGENT_SILENT_AFTER_HOUR:
        return False
    return count < settings.MAX_DAILY_MESSAGES

async def increment(user_id: str) -> None:
    today = date.today().isoformat()
    await db.increment_daily_message_count(user_id, today)
```

---

## Context Builder

```python
# agent/orchestrator.py (context helper)
async def build_user_context(user_id: str) -> str:
    user = await db.get_user(user_id)
    streak = await db.get_streak(user_id)
    today_workout = await db.get_workout_session_today(user_id)
    message_count_today = await db.get_daily_message_count(user_id, date.today().isoformat())
    pending_followup = await db.get_scheduled_followup(user_id)

    return f"""
User: {user.kakao_nickname}
Weekly goal: {user.weekly_goal_count} sessions/week
Current streak: {streak.current_streak} days (longest: {streak.longest_streak})
Last workout: {streak.last_workout_date}
Worked out today: {'Yes' if today_workout else 'No'}
Messages sent today: {message_count_today} / {settings.MAX_DAILY_MESSAGES}
Pending follow-up: {pending_followup.reason if pending_followup else 'None'} at {pending_followup.scheduled_for if pending_followup else 'N/A'}
Current local time: {datetime.now(tz=KST).strftime('%H:%M')}
"""
```

---

## Notable Event Detection (Automatic)

The `WorkoutHistoryAnalyzer` sub-agent runs at the start of each day's first invocation and populates the Orchestrator's context. The Orchestrator is instructed to watch for these events in notes and naturally incorporate them:

| Event | Detection |
|---|---|
| Injury mention | Note extraction finds pain/injury keywords: "무릎", "허리", "삐었어", "다쳤어" |
| Streak milestone | Analyzer: current_streak equals longest_streak |
| Long absence | Analyzer: days_since_last_workout >= 5 |
| Implicit rest day | Analyzer pattern: user never exercises on weekday X for 3+ consecutive weeks |
| External obstacle | Note extraction: "출장", "회식", "감기", "입원" |
| Muscle pair habit | Analyzer pattern: always trains same muscle groups together |
| Goal change | Note extraction: "주 X회" + "해볼게" or similar |

---

## Redis Usage

| Key Pattern | TTL | Purpose |
|---|---|---|
| `token:{user_id}` | 6h | Cached Kakao access token |
| `gym_search:{lat}:{lng}` | 1h | Cached Kakao Local gym results |
| `location:{user_id}` | 5m | Last known location status |

All other state is in PostgreSQL (durable, queryable).

---

## Error Handling

- **Kakao 401**: Refresh token and retry once. If refresh fails, mark user as `needs_reauth` in DB, suppress nudges.
- **Kakao 429**: Exponential backoff with jitter. Log and skip if rate limited.
- **LangChain agent exception**: Catch, log, do not crash. The scheduler will retry on next cron tick.
- **APScheduler job failure**: Logged automatically. Failed jobs do not block other users.
- **Webhook timeout**: Always return 200 immediately. All agent work is in BackgroundTasks.

---

## Security

- Encrypt `access_token` and `refresh_token` columns at rest using `pgcrypto` or application-level AES-256.
- Validate incoming webhook requests using HMAC signature if Kakao provides one; otherwise validate the user ID against the DB.
- Rate-limit `/auth/kakao` to 10 req/min per IP.
- Never log access tokens, location coordinates, or conversation content to stdout in production.
- Implement `DELETE /user/me` for full GDPR/PIPA-compliant data deletion.

---

## Implementation Priority

1. **Phase 1**: Kakao OAuth + send-to-me message + webhook receiver + DB schema — verify end-to-end message round-trip
2. **Phase 2**: LangChain agent skeleton with tools, system prompt, manual trigger via HTTP — verify agent reasons correctly and uses tools
3. **Phase 3**: APScheduler integration, commute detection (time-based first), follow-up scheduling, note save/read loop
4. **Phase 4**: Sub-agents (WorkoutHistoryAnalyzer, MessageCrafter), ConversationSummaryBufferMemory, full prompt set
5. **Phase 5**: Beta, prompt tuning, edge cases (injury detection, long absence re-entry, weekly summary)

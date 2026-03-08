# FitNudge — Phase Development Log

This file documents what was built in each phase, what was verified, and any deviations from the original plan.

> **Pre-commit rule**: Before committing any phase, confirm every item in that phase's **Verification Checklist** is checked off.

---

## Phase 1 — Kakao Integration ✅
**Commit**: `4860f41`
**Goal**: Kakao OAuth + send-to-me message + webhook receiver + DB schema. Verify end-to-end message round-trip.

### Files Created

| File | Purpose |
|---|---|
| `config.py` | Pydantic-settings `Settings` class; reads `.env` |
| `main.py` | FastAPI app entry point; startup/shutdown lifecycle |
| `db/models.py` | SQLAlchemy async ORM (7 tables); `create_tables()`, `AsyncSessionLocal` |
| `db/schema.sql` | Raw SQL schema for reference / manual inspection |
| `db/queries.py` | Typed async query helpers — all DB access goes through here |
| `kakao/auth.py` | OAuth token exchange, refresh, `get_valid_token(user_id, session)` |
| `kakao/message.py` | `send_message(user_id, text, session)` — send-to-me API, auto-refresh on 401 |
| `kakao/location.py` | `search_nearby_gyms()`, `get_location_status()` — Kakao Local API |
| `kakao/calendar.py` | `get_calendar_events()` — Kakao Calendar API |
| `routes/auth.py` | `GET /auth/kakao`, `GET /auth/callback`, `DELETE /auth/kakao/unlink` |
| `routes/webhook.py` | `POST /webhook/message`, `POST /webhook/location` |
| `routes/internal.py` | `POST /internal/scheduler-tick` (HMAC-gated cron fallback) |
| `scheduler/jobs.py` | APScheduler setup; `schedule_user_followup()`, `cancel_user_followup()` |
| `scheduler/triggers.py` | `should_invoke_for_user()` — Phase 1 stub, always returns False |
| `agent/orchestrator.py` | `invoke_agent()` — Phase 1 stub, checks message guard and logs |
| `utils/time_utils.py` | `now_kst()`, `is_silent_hour()`, `minutes_until()` |
| `utils/haversine.py` | `haversine_meters(lat1, lng1, lat2, lng2)` |
| `utils/message_guard.py` | `can_send(user_id, session)`, `increment(user_id, session)` |
| `.env.example` | Environment variable template |
| `.gitignore` | Excludes `.env`, `.venv/`, `__pycache__/` |
| `pyproject.toml` | uv-managed dependencies |
| `uv.lock` | Locked dependency graph |

### Database Tables

| Table | Purpose |
|---|---|
| `users` | Kakao identity, OAuth tokens, workplace coords, weekly goal |
| `workout_sessions` | Completed workout records with muscle groups |
| `agent_notes` | Date-keyed free-text notes saved by the agent |
| `conversation_turns` | Full message log (role: agent/user) |
| `streaks` | Current and longest streak per user |
| `daily_message_counts` | Hard cap enforcement (per user per day) |
| `scheduled_followups` | APScheduler job references for pending follow-ups |

### Deviations from Original Plan

- `KAKAO_REDIRECT_URI` must be registered in the Kakao Developer console exactly as used in `.env`
- `account_email` scope removed from OAuth — requires extra Kakao business verification; `talk_message` is sufficient for Phase 1
- `psycopg2-binary` added as a dependency — APScheduler's `SQLAlchemyJobStore` requires a sync PostgreSQL driver separate from `asyncpg`
- Running uvicorn requires `--host 0.0.0.0` in WSL to be reachable from the Windows host browser
- `asyncpg` bumped from `0.29.0` → `0.30.0` for Python 3.13 compatibility

### Verification Checklist

- [x] `uv run uvicorn main:app --host 0.0.0.0` starts without errors
- [x] `GET /health` returns `{"status": "ok"}`
- [x] DB tables created on startup (confirmed via psql or logs)
- [x] `GET /auth/kakao` redirects to Kakao login page
- [x] `GET /auth/callback` returns JSON with `user_id`, `kakao_id`, `nickname`
- [x] User row persisted in `users` table after OAuth

---

## Phase 2 — Agent Prototype 🔲
**Goal**: LangChain agent skeleton with tool definitions, system prompt v1, and a manual HTTP trigger. Verify the agent reasons correctly and calls tools.

### Planned Deliverables

- `agent/orchestrator.py` — full `build_agent()` + `invoke_agent()` with LangChain `create_react_agent`
- `agent/tools.py` — all `@tool` definitions pre-bound to `user_id`
- `agent/prompts/orchestrator_system.py` — Orchestrator system prompt
- `routes/internal.py` — add `POST /internal/trigger` for manual agent invocation
- Verify: agent calls `get_notes()` and `get_workout_history()` on invocation
- Verify: agent calls `send_kakao_message()` and message appears in KakaoTalk

### Verification Checklist

- [ ] `POST /internal/trigger` with a valid `user_id` invokes the agent
- [ ] Agent logs show ReAct reasoning steps (tool calls visible in `--verbose`)
- [ ] Agent calls at least `get_notes` and `get_workout_history` before deciding
- [ ] A KakaoTalk message is received on the test account
- [ ] `daily_message_counts` row incremented after message sent
- [ ] Message guard blocks a 6th message if daily cap is reached

---

## Phase 3 — Full Agent Loop 🔲
**Goal**: APScheduler running, commute detection (time-based first), follow-up scheduling, note save/read loop.

### Planned Deliverables

- `scheduler/triggers.py` — full `should_invoke_for_user()` implementation
- `scheduler/jobs.py` — cron job for `check_all_users` (every 5 min, 17:00–22:00 KST)
- `scheduler/jobs.py` — weekly summary cron (Sunday 21:00 KST)
- Agent correctly calls `schedule_followup()` and `cancel_followup()`
- Agent saves notes after every meaningful user message

### Verification Checklist

- [ ] Scheduler fires `check_all_users` every 5 minutes during active hours
- [ ] Agent invoked when configured departure time is reached (±10 min)
- [ ] `schedule_followup()` creates an APScheduler job; agent wakes at correct time
- [ ] `cancel_followup()` removes the job; no phantom invocations
- [ ] Notes saved after user replies; visible in `agent_notes` table
- [ ] Agent reads saved notes in the next invocation

---

## Phase 4 — Agent v1 Complete 🔲
**Goal**: Sub-agents, full prompt set, `ConversationSummaryBufferMemory`.

### Planned Deliverables

- `agent/subagents/history_analyzer.py` — `WorkoutHistoryAnalyzer`
- `agent/subagents/message_crafter.py` — `MessageCrafter`
- `agent/subagents/note_keeper.py` — `NoteKeeper`
- All prompt templates in `agent/prompts/`
- `ConversationSummaryBufferMemory` wired to `SQLChatMessageHistory`

### Verification Checklist

- [ ] `WorkoutHistoryAnalyzer` returns correct JSON summary for a test user
- [ ] `MessageCrafter` produces contextually appropriate Korean messages
- [ ] Old conversation turns are summarised; context window stays within limits
- [ ] Full scenario from README §4 plays out correctly end-to-end (manual test)
- [ ] Notable event detection fires correctly (injury, streak milestone, long absence)

---

## Phase 5 — MVP Launch 🔲
**Goal**: Beta testing, prompt A/B testing, edge case handling, weekly report.

### Planned Deliverables

- Edge cases: injury suppression, long-absence re-entry, consistent skip-day detection
- Weekly summary generation and delivery (Sunday 21:00 KST)
- Prompt A/B test harness
- Production hardening: token encryption, rate limiting on `/auth/kakao`, full data deletion

### Verification Checklist

- [ ] Injury keyword in user message suppresses that muscle group in next nudge
- [ ] 5+ day absence triggers re-entry message with lowered bar ("10분만이라도")
- [ ] Weekly summary sent Sunday 21:00 KST with correct stats
- [ ] 3 unanswered nudges → agent goes silent for the day
- [ ] `DELETE /auth/kakao/unlink` removes all user data from all tables
- [ ] No tokens or coordinates logged to stdout in production mode

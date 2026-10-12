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

## Phase 2 — Agent Prototype ✅
**Goal**: LangChain agent skeleton with tool definitions, system prompt v1, and a manual HTTP trigger. Verify the agent reasons correctly and calls tools.

### Files Created / Modified

| File | Change |
|---|---|
| `agent/prompts/orchestrator_system.py` | Orchestrator system prompt with ReAct format instructions |
| `agent/tools.py` | All 10 `@tool` definitions via `get_all_tools(user_id, session)` factory |
| `agent/orchestrator.py` | Full `build_agent()` + `invoke_agent()` with `create_react_agent` |
| `routes/internal.py` | Added `POST /internal/trigger` (HMAC-gated, `TriggerRequest` body) |

### Key Design Decisions

- Tools are created inside a factory `get_all_tools(user_id, session)` that closes over both `user_id` and the DB `AsyncSession` — no global session state.
- `build_agent()` instantiates a new `ChatAnthropic` + `AgentExecutor` per invocation so each request gets a clean context. Memory (Phase 4) will be added later.
- `POST /internal/trigger` uses a separate HMAC message (`b"trigger"`) from the scheduler-tick endpoint so tokens are not interchangeable.
- `get_location_status` returns `"unknown"` in Phase 2; real data arrives via the geofence webhook wired in Phase 3.

### Deviations from Original Plan

- Switched from `create_react_agent` to `create_tool_calling_agent` — the ReAct text parser breaks on single-parameter non-string tools (passes JSON string directly to `int` field). Claude has native tool use; `create_tool_calling_agent` is the correct choice.
- Each tool creates its own `AsyncSession` via `AsyncSessionLocal()` instead of sharing the request session — required because the agent calls multiple tools in parallel via `asyncio.gather`, and a single `AsyncSession` cannot handle concurrent queries.
- `ConversationSummaryBufferMemory` deferred to Phase 4 as planned; Phase 2 passes context entirely via the `{input}` string.

### Verification Checklist

- [x] `POST /internal/trigger` with a valid `user_id` invokes the agent
- [x] Agent logs show tool calls visible in `--verbose` (native tool calling, not ReAct text)
- [x] Agent calls at least `get_notes` and `get_workout_history` before deciding
- [x] A KakaoTalk message is received on the test account
- [x] `daily_message_counts` row incremented after message sent
- [x] Message guard blocks a 6th message if daily cap is reached

---

> **Plan revision (2026-10-13):** the master plan in `CLAUDE.md` was rewritten. LangChain is being replaced by the Claude Agent SDK + Claude API, notes become a time-keyed plaintext memory store, messaging moves behind a channel abstraction, and personas, a training engine, and a knowledge base were added. The old Phases 3–5 below were replaced by Phases 3–8.
>
> **Channel decision (2026-10-13):** Kakao proactive messaging (Event API) requires a business-verified channel and app, a wallet, and 15 KRW per message, and recurring nudges may be classified as ads. Kakao Channels is therefore deferred to Phase 8. Phases 3–7 build and drill the full loop on **Discord DMs**. Research is recorded in `CLAUDE.md` §10.3.

---

## Phase 3 — Foundation Reset ✅
**Goal**: Move to the Claude Agent SDK, introduce the memory store and channel abstraction, and hold a working reactive conversation over Discord DMs.
**Status**: complete (2026-10-13). Verified offline, with live Claude, and live on Discord (bot `운완이#9699`, DM with the developer's account).

### Files Created / Modified

| File | Change |
|---|---|
| `agent/runtime.py` | Agent SDK orchestrator: one fresh `ClaudeSDKClient` session per run, PreToolUse guard hook, isolated CLI |
| `agent/tools.py` | 7 in-process MCP tools: `send_message`, `memory_read`, `memory_write`, `get_workout_log`, `record_workout`, `schedule_followup`, `cancel_followup` |
| `agent/context.py` | Per-run context snapshot (trigger, time, today, streak, workouts, memory window, last 20 turns) |
| `agent/prompts/system.py` | Byte-stable system prompt (rules, nudge playbook, memory/workout/follow-up policy, style) |
| `agent/extract.py` | Claude API structured-output memory extraction per inbound message |
| `agent/dispatch.py` | Inbound handling + per-user run serialization with coalescing |
| `agent/triggers.py` | `Trigger` (reactive vs proactive) |
| `memory/store.py` | Time-keyed plaintext memory: append, supersede, auto-pin `injury`/`goal`, window, formatting |
| `channels/base.py`, `channels/delivery.py`, `channels/discord.py` | Channel protocol + registry, the single outbound path, Discord DM adapter |
| `discord_bot/bot.py` | Gateway client in the FastAPI lifespan; DMs only; `/start`, `/pause`, `/resume` |
| `utils/message_guard.py` | `check_send()` — reactive vs proactive rules; `record_send()` |
| `db/models.py`, `db/queries.py` | Platform-neutral users, `channel_identities`, `memory_entries`, split counters, KST dates |
| `db/migrations/` + `alembic.ini` | `0001_baseline` (old schema), `0002_platform_neutral_memory` |
| `scheduler/jobs.py` | `resolve_followup_time()` (validates lead time + quiet hours), one-shot follow-up jobs |
| `routes/internal.py`, `main.py` | Trigger endpoint on the new dispatcher; lifespan starts scheduler + Discord |
| `utils/text_style.py` | Sentence counter for the message-length rule |
| `tests/` | 48 tests: unit, DB, and offline end-to-end through the real SDK CLI (`tests/fake_anthropic.py`) |
| Removed | `agent/orchestrator.py`, old LangChain prompt, `agent/subagents/`, `routes/webhook.py` (Kakao), `langchain*` deps |

### Key Design Decisions

- **One SDK session per run, no resume.** Context is rebuilt from Postgres every run.
- **CLI isolation:** `tools=[]` (no built-ins), `strict_mcp_config`, `permission_mode="dontAsk"`, `setting_sources=[]`, an empty temp `cwd`, and a dedicated `CLAUDE_CONFIG_DIR` (`.claude-agent/`, gitignored).
- **Guards run twice:** in the PreToolUse hook (the model sees the denial reason) and again inside `send_message`, under a per-run lock so parallel sends can't race past the per-run cap.
- **Proactive runs are pre-checked** before starting the SDK, so blocked proactive triggers cost nothing.
- **Extraction runs before the agent**, so the agent sees the new facts. The agent only writes memory for corrections or resolutions.
- **Runs are serialized per user.** Triggers that arrive mid-run coalesce into one follow-up run, and a user message outranks a proactive trigger.

### Deviations from Original Plan

- `agent/hooks.py` folded into `agent/runtime.py` (one small hook). Added `agent/dispatch.py` and `agent/triggers.py`.
- `create_tables()` removed entirely, so Alembic is required in dev too. **Existing dev DBs: run `uv run alembic stamp 0001_baseline` once, then `uv run alembic upgrade head`.**
- Workout parsing is done by the orchestrator through `record_workout`, not a separate extraction call (taxonomy comes in Phase 5).
- `uv` resolves Python 3.14 here (`.python-version` is gitignored), so all deps were bumped to versions with 3.14 wheels.
- SQLAlchemy 2.1 maps `postgresql://` to psycopg 3. The APScheduler job-store URL now names `postgresql+psycopg2://` explicitly.
- All "today" logic uses the KST date instead of the server's `date.today()`.
- `--no-session-persistence` is print-mode only, so it isn't used. Instead each run's CLI transcript (it contains conversation content) is deleted when the run ends, and a 30-minute sweep removes leftovers older than `TRANSCRIPT_MAX_AGE_MINUTES` (60).
- Slash commands sync in the background and only when they changed (global sync is rate-limited and was blocking login).
- Observed: the CLI appends a mid-conversation `system` message after tool results. Harmless; noted for prompt-caching work.
- Bot display name: **운완이** (`BOT_NAME`).
- Follow-up lead cap raised from 18 h to 24 h: a plan for "21시" made after 03:00 is more than 18 h away.

### Live Run (2026-10-13, Claude Opus 5.5, console channel instead of Discord)

Three messages from a new user: greeting → "오늘 저녁 9시에 등 하러 갈게. 근데 어제부터 오른쪽 무릎이 좀 아파" → "방금 등이랑 이두 했어. 풀업 처음으로 5개 성공!". Total agent cost **$0.08** (~$0.02–0.03 per reply).
- Worked: first-contact intro with `/start`, commitment + pinned injury extracted, knee-friendly exercise swap + see-a-doctor line, `record_workout` with the specific PR praised, and no follow-up scheduled because the user hadn't opted in to proactive messages.
- Fixed after the run: `record_workout` no longer writes a duplicate workout memory, and the context labels the weekly goal as an app default (the agent had said "목표가 3번이시던데").
- Replies sometimes ran to 4 sentences. Fixed: a firmer style rule, plus `send_message` rejects texts over `MAX_MESSAGE_SENTENCES` (3, counted by `utils/text_style.py`: punctuation or emoji as breaks) or `MAX_MESSAGE_CHARS` (400), so the model rewrites.

### Verification Checklist

Offline = `TEST_DATABASE_URL=… uv run pytest` (real SDK CLI against a scripted fake Messages API).

- [x] Phase 2 checklist passes again on the Agent SDK runtime — *trigger → tool calls → message on a real Discord account → counter incremented; cap enforcement unit-tested*
- [x] Built-in SDK tools (Bash/Read/Write/Web) are unavailable to the agent — *e2e asserts the model sees exactly the 7 MCP tools*
- [x] Over-limit `send_message` is denied and the model sees the reason — *e2e (per-run cap); proactive caps unit-tested in `test_message_guard.py`*
- [x] Replies to inbound user messages still go out after the proactive cap or quiet hours start — *unit test*
- [x] Inbound message → memory entries extracted and visible in `memory_entries` — *live: commitment + pinned injury extracted with resolved dates*
- [x] Next invocation's context includes memory entries — *`test_context.py`*
- [x] `langchain*` packages removed from `pyproject.toml` / `uv.lock`
- [x] DM to the bot → agent reply arrives in the same DM; turn logged in `conversation_turns` — *live 2026-10-13: ~6 s end to end (extraction 1.7 s, CLI start 1 s, model 4 s), $0.03*
- [x] Bot can open a DM to an opted-in user (proactive path smoke test via `/internal/trigger`) — *live: after `/start`, a proactive DM was delivered (`proactive=True`, $0.015). On a plain "evening check-in" trigger at 01:30 the agent correctly chose silence ($0.011)*
- [x] Undeliverable send sets `opted_in_proactive = false` without crashing — *unit test with a fake channel; the Discord `Forbidden` mapping is not yet exercised live*
- [x] Gateway login, slash-command registration, and app boot/shutdown — *live check against Discord (bot `운완이#9699`)*

---

## Phase 4 — Proactive Loop 🔲
**Goal**: The agent reaches out at good times and follows through on commitments.

### Planned Deliverables

- `timing/`: send-window computation, nightly habit learning, calendar/rest-day suppression
- Cron tick (every 5 min) calls `timing.is_window_open`; one-shot follow-up jobs
- Escalation ladder (initial → new angle → reduced ask → soft release → silence)
- Commitment tracking: pre-reminder and post-time check-in

### Verification Checklist

- [ ] No proactive message inside quiet hours (test with a frozen clock)
- [ ] A commitment "21시에 갈게" produces a ~20:30 reminder and a ~21:15 check-in if no update
- [ ] 3 unanswered proactive messages → no further proactive messages that day
- [ ] A recorded workout or stated skip cancels pending follow-ups
- [ ] A stated 회식 (from memory) suppresses that evening's nudge

---

## Phase 5 — Training Expertise 🔲
**Goal**: Evidence-based suggestions and Q&A.

### Planned Deliverables

- `training/taxonomy.py`, `recovery.py`, `rotation.py` with unit tests
- `knowledge/` initial topics with sources and `last_reviewed`; `lookup_training_knowledge`
- Injury handling: pinned `injury` memory, excluded groups, professional referral

### Verification Checklist

- [ ] `uv run pytest training/` passes on fixed histories
- [ ] Suggested muscle group matches the rotation engine, and the message states the reason
- [ ] Q&A answers cite knowledge-base content; out-of-scope questions are acknowledged as such
- [ ] Knee-pain mention → no heavy leg suggestions until resolved

---

## Phase 6 — Personas & Onboarding 🔲
**Goal**: Selectable personas with tunable personality variables, plus a conversational onboarding.

### Planned Deliverables

- `personas/presets.py`, `personas/render.py`; `user_persona` table
- In-chat persona switching and variable tweaks via `update_profile`
- Onboarding: goal, rest days, persona, optional calendar/location, disclaimer

### Verification Checklist

- [ ] Each preset produces distinct, on-voice Korean (native-speaker review)
- [ ] `push=5` messages still include an easy out
- [ ] Rendered persona block is byte-stable across invocations (cache hits observed)

---

## Phase 7 — Beta 🔲
**Goal**: Measured quality, privacy, and hardening.

### Planned Deliverables

- `evals/` scenario suite with code and LLM-graded checks
- Weekly recap (Sunday evening)
- `DELETE /user/me`, data export, "다 지워줘" / "내 기록 보여줘" intents
- `/pause`, `/resume`, `/기록`, `/삭제`; bot token handling; no content in logs

### Verification Checklist

- [ ] Eval suite passes; results recorded here
- [ ] No fabricated workout references across the eval suite
- [ ] Delete removes the user's rows from every table
- [ ] Production logs contain no tokens, coordinates, or message content

---

## Phase 8 — Kakao Channels Expansion 💤
**Goal**: Add `channels/kakao.py` so the same agent runs on a KakaoTalk channel. **Blocked** until the prerequisites in `CLAUDE.md` §10.3 are met.

### Prerequisites

- [ ] 사업자등록 → channel business verification → Developers app converted to a 비즈 앱 and connected
- [ ] Written answer from 챗봇 관리자센터 on whether personalized nudges count as ad messages
- [ ] Wallet created; per-message budget approved

### Planned Deliverables

- Open Builder bot: fallback block → skill webhook with callback enabled
- Event API proactive sends keyed by `botUserKey`
- Optional: Kakao Calendar suppression, geofence signal

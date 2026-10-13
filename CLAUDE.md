# CLAUDE.md — FitNudge Developer Guide

> **Audience:** developers and coding agents working on this repo. The external, user-facing overview is [`README.md`](README.md).
>
> **Language policy**
> - Everything the *end user* sees (bot messages, persona text, onboarding, error replies) is **Korean**.
> - Code, identifiers, comments, commit messages, docs, and all developer ↔ coding-agent conversation are **English**.
> - Prompts sent to Claude are written in **English** with Korean examples and Korean style rules. The model *outputs* Korean.

---

## 1. What FitNudge Is

FitNudge is a chat-based fitness companion powered by Claude. It is being built and drilled end to end on **Discord** (DMs); **KakaoTalk Channels** is the planned production frontend once its prerequisites are met (§10). It messages the user at good moments, talks with them like a friend who knows a lot about training, remembers their workout history and what they've said, and **nudges** them toward working out without forcing them.

### Product pillars (every design decision must serve one of these)

| # | Pillar | Where it lives |
|---|---|---|
| P1 | **Claude-native agent**: Claude Agent SDK for the conversational orchestrator, plain Claude API for single-shot tasks | `agent/` (§4) |
| P2 | **Nudge, never force.** The user always has an easy, guilt-free way out | Hard rules (§9), persona prompts (§8) |
| P3 | **Real behavioral-science techniques**, used in the user's interest | Nudge playbook (§6) |
| P4 | **Chat frontend behind a channel abstraction**: **Discord now** (end-to-end drill), **KakaoTalk Channels later** (future expansion) | `channels/` (§10) |
| P5 | **Memory as time-keyed plaintext**: append-only `(timestamp → text)` records per user | `memory/` (§5) |
| P6 | **Professional, evidence-based training guidance**, including proactive "train this muscle group today" suggestions | `training/` + `knowledge/` (§7) |
| P7 | **Proactive outreach at the best time** for each user | `scheduler/` + `timing/` (§6.3) |
| P8 | **Answers workout questions** using the same evidence base | `knowledge/` (§7.3) |
| P9 | **Preset personas** tuned by a few **personality variables** | `personas/` (§8) |
| P10 | **Korean service, English engineering** | Language policy above |

---

## 2. Working Conventions

- **Python 3.13+, managed with `uv`.** Use `uv add`, `uv run`, `uvx`. Never use bare `pip`/`python`/`venv`. Commit `uv.lock`.
- Run locally: `uv run alembic upgrade head`, then `uv run uvicorn main:app --host 0.0.0.0 --reload` (`--host 0.0.0.0` is required under WSL). The Discord bot starts with the app when `DISCORD_BOT_TOKEN` is set.
- Schema changes go through Alembic (`uv run alembic revision --autogenerate -m "…"`). Regenerate `db/schema.sql` from the models for reference.
- Tests: `TEST_DATABASE_URL=postgresql+asyncpg://…/fitnudge_test uv run pytest`. The schema of that database is dropped. Without it, DB tests skip. `tests/test_e2e_agent.py` drives the real Agent SDK CLI against `tests/fake_anthropic.py` (offline, free). Proactive tests must not depend on the wall clock (use the `no_quiet_hours` fixture).
- **Phase log:** [`docs/PHASES.md`](docs/PHASES.md) records what each phase built, how it was verified, and where it deviated from plan. **Every item in a phase's verification checklist must be checked before that phase is committed.** Record each deviation there when it happens.
- All DB access goes through `db/queries.py`. All outbound user messages go through `channels/` (never call Discord/Kakao directly from agent code). Nothing outside `channels/` may assume a specific platform — the Kakao expansion must be a new adapter, not a refactor.
- Each tool opens its own `AsyncSession` because the model may call tools in parallel (see the Phase 2 deviation in PHASES.md).
- Never log access tokens, coordinates, or message content in production (`LOG_CONTENT=false`).

---

## 3. Architecture

```
                 ┌──────────────── triggers ────────────────┐
Discord gateway ─┤  inbound DM / slash command (reactive)   │
  APScheduler   ─┤  timing window / follow-up / weekly recap│
 (future) Kakao ─┤  skill webhook, geofence, calendar       │
                 └───────────────────┬──────────────────────┘
                                     ▼
                         agent.runtime.invoke(user, trigger)
                                     │
             ┌───────────────────────┼─────────────────────────────┐
             ▼                       ▼                             ▼
     context builder        Claude Agent SDK              code-enforced guards
     (DB → prompt:          orchestrator session          (PreToolUse hooks +
      profile, persona,     ├─ in-process MCP tools        message_guard)
      memory window,        │   (memory, training,
      recovery table,       │    knowledge, scheduling,
      recent turns)         │    send_message)
                            └─ subagents (recap writer, ...)
                                     │
                                     ▼
                       channels.send(user, text) → Discord (now) | Kakao (future)
```

- The agent is **not long-running**. Each trigger starts a fresh SDK session, gives it a context snapshot built from Postgres, lets it act, and tears it down. Postgres is the only source of truth. **SDK session resume is not used**, because SDK sessions are local files and don't survive container restarts or horizontal scaling.
- **Reactive** triggers (the user wrote to us) always get a reply. **Proactive** triggers (we decide to reach out) go through every guard in §9.

---

## 4. Agent Runtime (Claude Agent SDK + Claude API)

### 4.1 Which surface for what

| Job | Surface | Why |
|---|---|---|
| Orchestrator: read context, decide, converse, call tools, send | **Claude Agent SDK** (`claude-agent-sdk`) with an in-process MCP server of our tools | Gives us the agent loop, hooks, and subagents without writing our own harness |
| Memory extraction from a user message → list of memory entries | **Claude API** (`anthropic`), structured output | One call with a fixed schema. No agent loop needed |
| Workout parsing ("등이랑 이두 했어" → `["back","biceps"]`) | **Claude API**, structured output | Same as above |
| Weekly recap draft | Agent SDK **subagent** (`recap_writer`) or a single API call | Either works. Start with the API call |
| Training Q&A | Orchestrator + `lookup_training_knowledge` tool | Answers must be grounded in the curated knowledge base |

### 4.2 Models

- Configured through env: `AGENT_MODEL` (orchestrator) and `UTILITY_MODEL` (extraction/parsing). Both default to `claude-opus-5-5`.
- Choosing a cheaper model for `UTILITY_MODEL` (e.g. `claude-haiku-5-5`) is a cost decision for the project owner. Make it only after evals show quality holds.
- Model IDs are exact strings with no date suffix. Current models don't accept `temperature` or `budget_tokens`; control them with `effort` instead.

### 4.3 Orchestrator shape (reference sketch, not final code)

```python
# agent/runtime.py
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, create_sdk_mcp_server

async def invoke(user_id: str, trigger: Trigger) -> None:
    ctx = await build_context(user_id, trigger)          # §4.5
    server = create_sdk_mcp_server(name="fitnudge", version="1.0.0",
                                   tools=build_tools(user_id, trigger))   # §4.4
    options = ClaudeAgentOptions(
        model=settings.agent_model,
        system_prompt=render_system_prompt(ctx.persona),  # stable → cacheable
        mcp_servers={"fitnudge": server},
        allowed_tools=[f"mcp__fitnudge__{t}" for t in TOOL_NAMES],
        # No built-in Claude Code tools (Bash/Read/Write/Web*). This is a chat bot, not a coding agent.
        hooks={"PreToolUse": [HookMatcher(matcher="mcp__fitnudge__send_message",
                                          hooks=[guard_send_message])]},
        max_turns=12,
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(ctx.render_user_turn())        # trigger + volatile context
        async for _ in client.receive_response():
            pass                                          # log tool calls / usage
```

Rules:
- Before writing the migration, check exact SDK names and option fields against the current docs (`code.claude.com/docs/en/agent-sdk`). Pin the SDK version in `pyproject.toml`.
- Explicitly disable the SDK's built-in tools (file system, shell, web) and don't load filesystem settings (`setting_sources`). The bot must not be able to touch the container.
- `send_message` is guarded twice: once by the PreToolUse hook (it can deny with a reason the model sees) and again inside the tool (defense in depth).
- **Prompt caching:** the system prompt (persona + rules + playbook) and the tool list stay byte-stable. Timestamps, the memory window, and the trigger go in the user turn.
- Wrap each invocation in a timeout and catch all exceptions. A failed run is logged with its trigger and never crashes the scheduler or webhook.

### 4.4 Tool surface (in-process MCP, all bound to one `user_id`)

| Tool | Purpose |
|---|---|
| `memory_read(since, until?, kinds?)` | Time-ranged read of memory entries (§5) |
| `memory_write(text, kind, at?)` | Append a memory entry. `at` defaults to now |
| `get_workout_log(days)` | Confirmed sessions only |
| `record_workout(muscle_groups, notes?, performed_at?)` | Record a completed session, update streak, write a memory entry |
| `get_recovery_status()` | Per-muscle-group days since last trained + readiness (§7.2). Computed in code |
| `suggest_next_session()` | Deterministic rotation suggestion with a rationale (§7.2) |
| `lookup_training_knowledge(query)` | Retrieve cited passages from `knowledge/` (§7.3) |
| `get_calendar_events(date)` | *(future, with Kakao or another calendar)* overtime/dinner suppression. Not registered in the Discord drill |
| `get_location_status()` | *(future)* `at_work` / `left_work` / `near_gym` / `unknown`. Not registered in the Discord drill |
| `send_message(text)` | Send through the active channel. Guarded |
| `commit_plan(at, plan)` | The user committed to a time: pre-reminder (T−30), then a code-chained check-in (T+15) |
| `schedule_followup(at \| in_minutes, reason)` / `cancel_followup()` | One pending follow-up per user; refused after the ladder's release step |
| `snooze_proactive(days, reason)` | No proactive messages until a date (회식, sick, trip); cancels follow-ups |
| `update_profile(field, value)` | Whitelisted fields only: weekly goal, rest days, usual workout time (persona variables in Phase 6) |

The tools return plain text that the model can read easily. Errors come back as tool errors rather than exceptions.

### 4.5 Context snapshot (per invocation)

Built in code, injected as the first user turn:
- Trigger type and payload, local time (KST), weekday
- Profile: nickname, weekly goal, rest days, preferred exercises, persona + variables
- Today: worked out?, proactive messages sent / cap, unanswered count, pending follow-up
- Streak, this week's count vs goal
- Recovery table (from `training/`)
- Memory window: last 14 days of entries plus any older pinned `injury` / `goal` entries
- Last ~20 conversation turns verbatim

There is no LangChain memory. Long-term continuity comes from memory entries, not from replaying transcripts.

---

## 5. Memory Model — Time-Keyed Plaintext

Memory is a per-user, **append-only key–value log where the key is a timestamp and the value is a short plaintext statement.** It must be human-readable, easy to diff, and easy to delete.

```
2026-10-13T18:35+09:00  [commitment]  21시에 헬스장 가기로 함 (등 운동)
2026-10-13T22:02+09:00  [workout]     등, 이두 완료. 풀업 처음으로 5개 성공했다고 함
2026-10-10T19:10+09:00  [injury]      오른쪽 무릎 통증 언급 — 하체 고중량 피하기
2026-10-08T12:00+09:00  [context]     이번 주 수–금 부산 출장
```

- Table `memory_entries(id, user_id, at TIMESTAMPTZ, kind TEXT, text TEXT, pinned BOOL, superseded_by UUID NULL, created_at)` with an index on `(user_id, at DESC)`. This replaces `agent_notes`. Migrate existing rows with `at = date + 12:00 KST, kind = 'note'`.
- `kind` ∈ `commitment | workout | injury | skip_reason | mood | goal | preference | context | note`.
- **Values are plaintext**, in Korean when quoting or paraphrasing the user. There are no JSON blobs in `text`.
- Corrections never edit an entry. A new entry is written and the old one's `superseded_by` is set.
- `injury` and `goal` entries are pinned automatically until a later entry resolves them.
- Writes come from two paths: the orchestrator's `memory_write`, and an automatic extraction pass (Claude API, structured output) on every inbound user message. Both are idempotent per message.
- `workout_sessions` stays as the **structured** source of truth for confirmed workouts (streaks and recovery need typed data). Memory is the narrative layer on top of it.
- Users can say "내 기록 보여줘" / "다 지워줘". The export and delete paths (§11) cover this table.

---

## 6. Nudge Playbook (P2, P3)

### 6.1 Techniques

These are real, named behavioral-science mechanisms, used to help the user do something *they* said they want. The orchestrator system prompt describes each one, with the guardrail next to it.

| Technique | Mechanism | How FitNudge applies it | Guardrail |
|---|---|---|---|
| **Implementation intentions** (Gollwitzer) | "When X, I will Y" plans raise follow-through | Ask for *when + what*: "몇 시쯤, 뭐 할 거예요?" Save as `commitment` | The user picks the plan. We never assign one |
| **Commitment & consistency** (Cialdini) | People act in line with their stated commitments | Remind them of *their own* plan in their words | Never shame them for breaking it |
| **Loss aversion / streaks** (Kahneman & Tversky) | Losses loom larger than gains | "4일째인데 오늘 하면 5일" | Only real streaks. Never use it right after a streak breaks |
| **Endowed progress** (Nunes & Drèze) | Progress already made motivates completion | "이번 주 목표 3회 중 2회 끝" | Real counts only |
| **Goal gradient** | Effort rises near the goal | Lean in when one session from the weekly goal | — |
| **Fresh start effect** (Dai, Milkman, Riis) | Temporal landmarks prompt new starts | Re-engage on Mondays, the 1st of the month, after holidays, after long gaps | Lowered bar after an absence |
| **Foot-in-the-door / reduced ask** | A small yes leads to a bigger yes | "10분만", "스트레칭이라도" | The small ask is a real option, not a trick |
| **Specificity / choice architecture** | A concrete default lowers decision cost | Suggest a specific muscle group from the recovery data | Easy to swap ("하체 말고 다른 거?") |
| **Temptation bundling** (Milkman) | Pair a want with a should | Tie the workout to something they mentioned enjoying (a podcast, a post-gym meal) | Only from their own stated preferences |
| **Specific praise / reinforcement** | Specific feedback reinforces the behavior | Name what they did: "풀업 5개 처음이잖아요" | Only facts from the data |
| **Autonomy support** (Self-Determination Theory) | Felt choice sustains motivation | Every nudge includes an easy out | Mandatory (§9) |

**Forbidden, regardless of persona:** invented social proof ("다른 사람들은 다 했어요"), fake scarcity or urgency, guilt, shame, comments about appearance or weight, fear appeals about health, lying about data, and pressure after a stated injury, illness, or emotional distress.

### 6.2 Escalation ladder (proactive, per day)

1. Initial nudge: a specific suggestion plus an easy out
2. No reply after ~45–90 min: a different angle (progress or streak) or the reduced ask ("10분만")
3. Soft release ("오늘은 쉬어도 돼요, 내일 봐요"), then **silence for the rest of the day**

Three steps match `MAX_UNANSWERED_PROACTIVE=3`. The context tells the agent which step it's on, and `schedule_followup` refuses after the release.

Any reply resets the ladder. A stated skip or rest ends it immediately with zero-judgment acknowledgment.

### 6.3 Timing (P7)

`timing/` computes a per-user **send window** each day. The agent chooses *whether* to message. Code decides *when it's allowed*.

Signals, in priority order:
1. An explicit commitment ("21시에 갈게"): pre-reminder about 30 min before, check-in about 15 min after
2. Learned habits: the median hour of past reported workouts and of past reply latency, per weekday, updated nightly
3. Configured `default_departure_time`
4. Declared rest days and stated obstacles from memory (회식, 야근, 출장)
5. *(future, Kakao expansion)* geofence exit from the workplace and calendar suppression

The tick (every 5 min, `timing/tick.py`) asks `timing.windows.check_window(user, now)` and fires each user's window at most once per day. The window starts at the learned time, else the usual time, else 18:30 on weekdays / 11:00 on weekends, and lasts 60 min. Obstacles and rest days are structured (`snooze_proactive`, `update_profile`), not parsed from memory text. Follow-ups are one-shot APScheduler jobs in `SQLAlchemyJobStore`. A commitment's check-in is chained in code after its pre-reminder fires.

---

## 7. Training Expertise (P6, P8)

### 7.1 Principle

**The LLM explains. Code and curated sources decide the facts.** Recovery math, rotation, and volume counting are deterministic. Factual claims come from `knowledge/` with citations. The model never invents numbers.

### 7.2 Recommendation engine (`training/`)

- Canonical muscle-group taxonomy with Korean aliases (`등/광배 → back`, `하체/스쿼트 → legs` …) in `training/taxonomy.py`. The workout parser maps free text onto it.
- `recovery.py`: days since each group was last trained, a readiness flag (default ≥48 h for most groups, ≥72 h after heavy lower-body work, per the knowledge base), and weekly frequency per group.
- `rotation.py`: suggests the next session from readiness, the user's split habits (e.g. always 등+이두), preferences, and pinned injuries (excluded groups). It returns a suggestion **and the reason** so the message can cite it ("가슴은 이틀 쉬었고 등은 4일째라 오늘은 등 어때요?").
- Unit-tested with fixed histories. This is the one module that must have tests.

### 7.3 Knowledge base (`knowledge/`)

- Markdown files, one topic each (recovery, weekly volume, progressive overload, warm-up, beginner splits, cardio guidelines, sleep and recovery, common injuries → when to see a professional). Each claim carries a source (e.g. ACSM position stands, WHO physical activity guidelines, peer-reviewed meta-analyses) and a `last_reviewed` date.
- `lookup_training_knowledge` does a simple retrieval over these files (keyword/BM25 first, embeddings only if needed).
- Q&A rules: answer from retrieved passages. Say plainly when the knowledge base doesn't cover a question. **No medical diagnosis.** For pain, injury, or medical conditions, suggest seeing a professional (의사/물리치료사) and pin an `injury` memory.
- Content is reviewed by a human before it's merged. The knowledge base is product content, not generated output.

---

## 8. Personas (P9)

A persona is a **preset** (identity + voice) plus a small set of **personality variables** that tune it. Both render into the stable part of the system prompt.

The bot's display name is **운완이** (`BOT_NAME`). Persona presets change its voice, not its name.

### 8.1 Presets (initial set, names TBD)

| id | Concept | Default voice |
|---|---|---|
| `buddy` | Gym friend who remembers everything | 반말, playful, emoji-friendly |
| `coach` | Calm, knowledgeable PT | 존댓말, concise, explains the "why" |
| `cheerleader` | All-in hype | 반말/해요체, high energy |
| `tsundere` | Teasing but caring | 반말, dry humor, never mean |

### 8.2 Personality variables (bounded, per user)

| Variable | Range | Effect |
|---|---|---|
| `formality` | `banmal` / `haeyo` / `hapsyo` | Speech level |
| `warmth` | 1–5 | Emotional expressiveness |
| `push` | 1–5 | How assertive nudges are. **Caps at the autonomy rule**: even 5 includes an out |
| `humor` | 0–3 | Jokes and teasing |
| `emoji` | 0–3 | Emoji density |
| `verbosity` | 1–3 | 1–2 sentences up to 3–4 sentences |

- Stored in `user_persona(user_id, preset_id, variables JSONB)`. Users change these in conversation ("좀 더 세게 말해줘" → `push+1`) through `update_profile`.
- Defined in `personas/presets.py` as data. Rendering is `personas/render.py`, which turns preset + variables into a deterministic prompt block (stable bytes for caching).
- **Hard rules (§9) and forbidden techniques (§6.1) are outside the persona block and override it.**

---

## 9. Hard Rules — Enforced in Code

The prompt also states these, but code is the guarantee.

| Rule | Enforcement point |
|---|---|
| Max `MAX_DAILY_PROACTIVE` (default 4) proactive messages per user per day | `send_message` hook + tool, `daily_message_counts` |
| No proactive messages between `QUIET_START` and `QUIET_END` (default 22:00–08:00 local) | `timing` + hook |
| After 3 unanswered proactive messages, stop proactive messages for the day | hook (counts from `conversation_turns`) |
| No nudge once today's workout is recorded, on a declared rest day, or during an active illness/trip window | `timing.is_window_open` |
| Proactive messages only to users who opted in during onboarding and haven't paused (`/pause`) | `timing.is_window_open` |
| Reactive replies are always allowed, but rate-limited (`MAX_DAILY_REPLIES`, default 50) for abuse protection | hook |
| Persona can't override the autonomy rule or forbidden techniques | Prompt structure + eval suite |
| Never reference a workout not in `workout_sessions` | Prompt + eval suite |

Note: the current `utils/message_guard.can_send` blocks the *whole invocation*, including replies to the user. Phase 3 splits it into proactive and reactive guards.

---

## 10. Messaging Channels (P4)

### 10.1 Abstraction

```python
# channels/base.py
class Channel(Protocol):
    name: str                                    # "discord" | "kakao"
    async def send(self, identity: ChannelIdentity, text: str) -> None: ...
    async def start(self) -> None: ...           # gateway connect / no-op for webhooks
```

- Inbound messages are normalized to `InboundMessage(channel, external_user_id, text, received_at)` and handed to `agent.runtime.invoke` with a reactive trigger.
- Users are platform-neutral. Platform IDs live in `channel_identities(user_id, channel, external_id, opted_in_proactive, created_at)` with `UNIQUE(channel, external_id)`. A user has one *active* channel (`users.active_channel`).
- Agent code only knows `send_message`. It never sees a Discord or Kakao ID.

### 10.2 Discord (current target: end-to-end drill)

Discord lets a bot DM users first with no business verification and no per-message cost, so the whole loop can be built and tested there: proactive nudge → reply → follow-up → recorded workout.

- **Library:** `discord.py` (pin the version). The client runs **inside the FastAPI process** and is started and stopped in the app lifespan alongside APScheduler, on the same event loop. Run one instance only (the gateway connection is a singleton; no sharding needed at our scale).
- **Conversation surface:** 1:1 **DMs** with the bot. DM message content reaches the bot without the privileged Message Content intent. Don't enable that intent, and ignore guild channel messages.
- **Reaching users first:** a bot can open a DM only with users it shares a server with. Users join a small FitNudge server (invite link), then run `/start` there or in DM. If a user closes DMs or leaves, the send fails (`Forbidden`): mark `opted_in_proactive = false` and stop proactive sends.
- **Slash commands** (Korean names and descriptions): `/start` (onboarding, consent to proactive DMs, disclaimer), `/pause` and `/resume` (proactive DMs), `/persona`, `/기록` (export), `/삭제` (delete everything). Free text in DM is always the main interface. Commands are shortcuts.
- **Reply latency:** none of Kakao's 5-second limits apply. Show a typing indicator (`async with dm.typing()`) while the agent runs.
- **Formatting:** plain text only. No embeds for conversational turns, to keep parity with a future Kakao text bubble. Messages over 2000 characters must never happen (the style rules keep them at 1–4 sentences); truncate defensively.
- **Retired from the Phase 1 code:** Kakao Login OAuth as the identity source, send-to-me messaging, `/webhook/message` and `/webhook/location`. Keep `kakao/` code in the tree, but leave it unwired, for the expansion.

### 10.3 KakaoTalk Channels (future expansion)

Kakao Channels + Kakao i Open Builder is the intended production frontend for Korean users. It's deferred because **proactive messaging requires business verification**. Researched facts (2026-10):

| Need | Kakao mechanism | Prerequisites / limits |
|---|---|---|
| Reply to a user | Open Builder **skill** on the fallback block → our webhook | Any channel. 5 s skill timeout |
| Slow (LLM) replies | Skill **callback**: respond `{"version":"2.0","useCallback":true}`, then POST the reply to the one-time `callbackUrl` | Turned on per block. Docs cite a 1–5 min validity window (inconsistent). Bot test doesn't fully support it |
| Message a user first | **Event API** `POST https://bot-api.kakao.com/v2/bots/{botId}/talk`, user by `botUserKey` / `plusfriendUserKey` / `appUserId` | **Business-verified channel + business-verified Developers app, connected; Kakao Login enabled; event name on a block; wallet.** 15 KRW/message (VAT excl.). Recipient must have added the channel. Max 100 users/request |
| Ad classification | Recurring same-topic messages are treated as **ads** | Ads: 08:00–20:50 only, "(광고)" prefix, opt-out, single card bubble, channel friends only. **Must confirm with Kakao how personalized, user-subscribed nudges are classified** |

Prerequisites to start the expansion:
1. 사업자등록 (개인사업자 is enough) → channel business verification → Developers app converted to a 비즈 앱 and connected to the channel
2. Written confirmation from 챗봇 관리자센터 support on the ad classification of nudges. If they count as ads, the 20:50 cutoff and "(광고)" prefix conflict with §6, so the product would need a redesign before launching on Kakao
3. Wallet + per-message budget

Implementation then is a `channels/kakao.py` adapter: the skill webhook with callback for replies, the Event API for proactive messages, and `botUserKey` as the `external_id`. Optional add-ons that only make sense on Kakao: Kakao Calendar (회식/야근 suppression) and a geofence signal from a companion client.

Sources: [Event API](https://kakaobusiness.gitbook.io/main/tool/chatbot/main_notions/event-api), [AI chatbot callback guide](https://kakaobusiness.gitbook.io/main/tool/chatbot/skill_guide/ai_chatbot_callback_guide), [Event API pricing](https://cs.kakao.com/helps_html/1073196772?locale=ko).

---

## 11. Data, Security, Privacy

- **Tables:** `users` (platform-neutral; Kakao OAuth columns become nullable and move to the Kakao expansion), `channel_identities`, `user_persona`, `workout_sessions`, `memory_entries` (replaces `agent_notes`), `conversation_turns`, `streaks`, `daily_message_counts` (split into proactive/reactive columns), `scheduled_followups`, `timing_profile`. `db/schema.sql` mirrors the ORM. Use Alembic migrations from Phase 3 onward. Stop relying on `create_tables()` in production.
- `DISCORD_BOT_TOKEN` is a secret: never log it, and rotate it if leaked. Inbound Discord events are authenticated by the gateway connection itself.
- (Kakao expansion) Encrypt OAuth tokens at rest (application-level AES-GCM or pgcrypto). Verify skill webhook requests (bot ID / shared header).
- `/internal/*` stays HMAC-gated.
- PIPA/GDPR: `DELETE /user/me` and the "다 지워줘" intent hard-delete all user rows across tables. "내 기록 보여줘" exports memory and workouts as plaintext.
- Health-data sensitivity: injuries and moods are personal data. Minimize what's stored, never log it, and state in onboarding that FitNudge is not medical advice.

---

## 12. Target Project Structure

```
fitnudge/
├── main.py
├── config.py
├── agent/
│   ├── runtime.py            # Agent SDK orchestrator + PreToolUse guard hook
│   ├── tools.py              # in-process MCP tools (@tool)
│   ├── dispatch.py           # inbound handling, per-user run serialization/coalescing
│   ├── triggers.py           # Trigger (reactive vs proactive)
│   ├── context.py            # context snapshot builder
│   ├── extract.py            # Claude API: memory extraction, workout parsing
│   ├── subagents.py          # AgentDefinition(s), e.g. recap_writer
│   └── prompts/              # system prompt sections (English, Korean examples)
├── personas/                 # presets.py, render.py
├── memory/                   # store.py (time-keyed plaintext log)
├── training/                 # taxonomy.py, recovery.py, rotation.py (+ tests)
├── knowledge/                # curated, cited markdown + retrieval.py
├── timing/                   # send windows, habit learning
├── channels/                 # base.py, discord.py (now), kakao.py (future)
├── discord_bot/              # bot.py: discord.py client, slash commands (started in main.py lifespan)
├── kakao/                    # low-level Kakao REST clients — kept, unwired until the expansion
├── scheduler/                # APScheduler setup + jobs
├── routes/                   # internal (HMAC trigger), health; Kakao routes unwired
├── db/                       # models.py, queries.py, schema.sql, migrations/
├── utils/
├── tests/                    # unit, DB, and offline e2e (fake Messages API) tests
├── evals/                    # scenario transcripts + graders (§13)
└── docs/PHASES.md
```

---

## 13. Quality & Evals

- **Scenario evals** in `evals/`: scripted multi-turn situations (commit → remind → complete, skip for 회식, injury mention, 7-day absence, persona switch, an off-topic question, a medical question). Each has assertions:
  - Code checks: tool calls made, guard respected, no message in quiet hours
  - LLM-graded checks: Korean naturalness, persona adherence, autonomy present, no forbidden technique, no fabricated history
- Run evals before changing prompts or models. Record results in PHASES.md.
- Unit tests: `training/`, `timing/`, `memory/`, guards. Run with `uv run pytest`.

---

## 14. Environment Variables

```env
# Claude
ANTHROPIC_API_KEY=
AGENT_MODEL=claude-opus-5-5
UTILITY_MODEL=claude-opus-5-5

# Channels
PRIMARY_CHANNEL=discord          # discord | kakao (future)
DISCORD_BOT_TOKEN=
DISCORD_GUILD_ID=                # FitNudge server; slash commands sync here during dev

# Kakao (future expansion — unused in the Discord drill)
KAKAO_REST_API_KEY=
KAKAO_CLIENT_SECRET=
KAKAO_REDIRECT_URI=
KAKAO_ADMIN_KEY=
KAKAO_BOT_ID=

# Storage
DATABASE_URL=postgresql+asyncpg://user:pass@host/db
REDIS_URL=redis://localhost:6379/0

# App
APP_BASE_URL=https://your-app.com
SECRET_KEY=
LOG_CONTENT=false

# Guards
MAX_DAILY_PROACTIVE=4
MAX_DAILY_REPLIES=50
QUIET_START=22
QUIET_END=8
```

---

## 15. Migration: LangChain → Claude Agent SDK

1. `uv add claude-agent-sdk anthropic` and pin both. The Agent SDK runs the Claude Code runtime, so confirm the deploy image can run it.
2. Port the LangChain `@tool`s in `agent/tools.py` to SDK `@tool` functions in one `create_sdk_mcp_server`. Keep the per-tool `AsyncSession` pattern. Leave the calendar and location tools unregistered (§4.4).
3. Replace `build_agent()`/`AgentExecutor` with `agent/runtime.py` (§4.3). Move the guard into a PreToolUse hook and keep the in-tool check.
4. Port the system prompt. Drop the ReAct/LangChain formatting and restructure it into: rules → playbook → persona block → style.
5. Add the Claude API utility calls (`agent/extract.py`) for memory extraction and workout parsing.
6. Swap `agent_notes` → `memory_entries` (Alembic migration + backfill).
7. Re-run the Phase 2 verification checklist against the new runtime. Then `uv remove langchain langchain-anthropic langchain-community`.

---

## 16. Roadmap

Details and checklists: [`docs/PHASES.md`](docs/PHASES.md).

| Phase | Status | Scope |
|---|---|---|
| 1 | ✅ | Kakao OAuth, send-to-me, webhook receiver, DB schema |
| 2 | ✅ | LangChain agent prototype with tools + manual trigger (to be replaced) |
| 3 | ✅ | **Foundation reset on Discord:** Agent SDK migration, channel abstraction + Discord adapter, platform-neutral users, memory store, guard split, Alembic. Ends with a working reactive DM conversation |
| 4 | ✅ | **Proactive loop (end-to-end drill):** timing windows, follow-ups, escalation ladder, commitment tracking — all over Discord DMs |
| 5 | 🔲 | **Expertise:** training engine, knowledge base, Q&A, injury handling |
| 6 | 🔲 | **Personas** + personality variables, onboarding flow (`/start`) |
| 7 | 🔲 | **Beta (Discord):** eval suite, weekly recap, privacy commands, hardening, prompt tuning |
| 8 | 💤 | **Kakao Channels expansion:** `channels/kakao.py`, gated on the prerequisites in §10.3 |

---

## 17. Open Questions

- Kakao expansion: business registration timing, and how Kakao classifies personalized nudges (ad or not) (§10.3)
- Persona names and character sheets (Korean copywriting; needs a native-speaker review)
- Knowledge base sourcing and who signs off on content
- Whether location/geofencing and calendar signals are worth adding at the Kakao stage, or learned timing is enough

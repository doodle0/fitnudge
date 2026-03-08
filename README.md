# 📱 FitNudge — Kakao AI Exercise Motivation Bot
## Planning Report (Revised: LLM Agent Architecture)

*From Commute Detection to Workout Completion — A Conversational AI Nudge System*

---

## 1. Project Overview

FitNudge is a KakaoTalk chatbot that uses an LLM-powered agent to detect when a user leaves work and engage them in a natural, psychologically-aware conversation to guide them toward exercising. Unlike a rule-based bot with fixed message templates, FitNudge's agent reads the user's full history, understands context, and crafts genuinely personalized messages — like a knowledgeable friend who remembers everything.

| Item | Details |
|---|---|
| **Project Name** | FitNudge — Kakao AI Exercise Motivation Bot |
| **Core Goal** | LLM agent that proactively reaches out at the right time, holds a natural conversation, and records workout outcomes |
| **APIs Used** | Kakao Login, KakaoTalk Message, Kakao Location, Kakao Calendar |
| **AI Framework** | LangChain `create_react_agent` with tool-calling; sub-agents for specialized tasks |
| **Psychological Strategy** | Nudge theory, personalized recall, progressive commitment, loss aversion, specific action prompts |

---

## 2. System Architecture

### 2-1. High-Level Flow

```
[Cron Scheduler]
      │
      ▼
[Orchestrator Agent]  ←──────────────────────────────────┐
      │                                                    │
      ├─ Tool: get_user_context()                         │
      ├─ Tool: get_workout_history()                      │
      ├─ Tool: get_calendar_events()                      │
      ├─ Tool: get_location_status()                      │
      ├─ Tool: send_kakao_message()                       │
      ├─ Tool: save_workout_record()                      │
      ├─ Tool: save_note()                                │
      ├─ Tool: get_notes()                                │
      └─ Tool: schedule_followup(delay_minutes)           │
                                                          │
[Incoming KakaoTalk Message Webhook] ────────────────────┘
      │
      ▼
[Message Router] → Wakes Orchestrator with user message + full context
```

The agent is not always running. It is invoked by two triggers:
1. **Cron-based**: A scheduled job fires at regular intervals (e.g., every 5 minutes) and wakes the agent if a user's context warrants action (commute time, scheduled follow-up due, etc.)
2. **Webhook-based**: An incoming user message on KakaoTalk immediately wakes the agent with the message content

### 2-2. Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.11+ with FastAPI |
| **LLM Agent** | LangChain `create_react_agent` + `langchain-anthropic` (Claude) |
| **Agent Memory / History** | PostgreSQL (persistent) + LangChain `ConversationSummaryBufferMemory` |
| **Scheduler** | APScheduler (persistent job store backed by PostgreSQL) |
| **Cache / State** | Redis 7+ |
| **Database** | PostgreSQL 15+ |
| **Deployment** | Railway / Render (always-on) or AWS ECS (containerized) |
| **Kakao Integration** | Direct Kakao REST API calls via `httpx` |

---

## 3. The LLM Agent Design

### 3-1. Agent Architecture

The system uses a **primary Orchestrator Agent** with access to a suite of tools. For complex sub-tasks, it delegates to specialized sub-agents.

**Orchestrator Agent** — the main decision-maker. Given the user's full context, it decides:
- Whether to send a message right now
- What to say
- When to schedule the next check-in
- What information to record

**Sub-agents (invoked as tools by the Orchestrator):**

| Sub-agent | Role |
|---|---|
| `WorkoutHistoryAnalyzer` | Reads workout history and identifies patterns, muscle group rotation, missed days, streaks |
| `MessageCrafter` | Given a psychological goal and user context, writes the actual Korean message text |
| `NoteKeeper` | Reads/writes the date-keyed notes store; surfaces notable events worth mentioning |

### 3-2. Agent Tools

All tools are Python functions decorated with LangChain's `@tool`.

| Tool | Description |
|---|---|
| `get_user_profile(user_id)` | Returns name, preferences, weekly goal, gym location |
| `get_workout_history(user_id, days=30)` | Returns list of recent workout sessions with dates and muscle groups |
| `get_notes(user_id, days=14)` | Returns recent date-keyed notes (e.g., "user mentioned knee pain", "user committed to 21:00") |
| `save_note(user_id, note)` | Saves a text note with today's date |
| `get_calendar_events(user_id, date)` | Returns today's Kakao Calendar events to detect overtime/dinner |
| `get_location_status(user_id)` | Returns whether user is currently near workplace, home, or gym |
| `send_kakao_message(user_id, message_text)` | Sends a KakaoTalk message to the user |
| `save_workout_record(user_id, date, muscle_groups, notes)` | Persists a completed workout session |
| `schedule_followup(user_id, delay_minutes, reason)` | Schedules the agent to wake up again in N minutes for this user |
| `cancel_followup(user_id)` | Cancels any pending follow-up for today |
| `get_streak(user_id)` | Returns current and longest streak |

---

## 4. Example Conversation Flow (Full Scenario)

This illustrates the complete agent lifecycle for one evening session.

**18:30 — Scheduler fires, agent wakes**

Agent calls tools: `get_location_status` → user has left workplace. `get_workout_history` → chest Monday, legs Tuesday. `get_notes` → no rest day declared. `get_streak` → 2-day streak. `get_calendar_events` → no overtime. Agent sends:

> *"퇴근하셨네요! 그저께 가슴, 어제 하체 했는데 오늘 등 어때요? 💪"*

Agent calls `schedule_followup(delay=30, reason="no response nudge")`.

---

**18:35 — User replies via KakaoTalk**

Incoming: `"오늘 21시에 갈게"`

Agent calls `save_note("user committed to gym at 21:00")`. Calls `cancel_followup()`. Calls `schedule_followup(delay=110, reason="pre-commitment reminder at 20:30")`. Sends:

> *"좋아요! 21시에 봐요 😄 등 운동 파이팅!"*

---

**20:30 — Scheduler fires (pre-commitment reminder)**

Agent sees the 21:00 commitment in notes. No workout recorded yet. Sends:

> *"21시 다 됐어요! 슬슬 준비하실 시간이죠? 오늘 등 각 잡고 가봐요 🏋️"*

Agent calls `schedule_followup(delay=40, reason="check if 21:00 kept")`.

---

**21:10 — Scheduler fires (commitment check, 10 min overdue)**

No workout recorded. Sends:

> *"약속한 시간이 10분 지났는데 운동 잘 하고 계신가요? 다 하고 연락 주세요 💬"*

Agent calls `schedule_followup(delay=60, reason="final check")`.

---

**22:00 — User replies**

Incoming: `"ㅇㅇ 오늘 등이랑 이두 했어"`

Agent calls `save_workout_record(muscle_groups=["등", "이두"])`. Calls `save_note("completed back + biceps")`. Calls `cancel_followup()`. Gets streak → now 3 days. Sends:

> *"오늘도 해냈다! 등이랑 이두까지 🔥 3일 연속이에요. 내일은 쉬어도 되고, 하체 가도 되고 — 오늘 수고했어요 😊"*

---

## 5. Prompt Taxonomy

These are the distinct prompt types the system uses. Each is a separate template injected into the relevant agent or sub-agent.

### 5-1. Orchestrator System Prompt
**Purpose**: Governs all agent decision-making — when to message, what to say, what to record, when to be silent.
**Injected variables**: `user_context`, `conversation_history`, `trigger_reason`
**Key requirements**: Timing rules, tone guidelines, all psychological principles, tool usage policy, and Korean-language instruction.

### 5-2. Initial Nudge Prompt (MessageCrafter sub-agent)
**Purpose**: Craft the first outreach message after commute detection.
**Input context**: Last 3 workouts + muscle groups, streak, day of week, recent notes (injuries, mood, prior commitments).
**Psychological goal**: Warm greeting + specific, concrete workout suggestion based on history. Make going feel easy and obvious.
**Example output**: `"퇴근하셨네요! 그저께 가슴, 어제 하체 했는데 오늘 등 어때요? 💪"`

### 5-3. Commitment Confirmation Prompt
**Purpose**: Respond when the user commits to a specific plan (time, workout type).
**Input context**: What the user said, extracted time, extracted workout type.
**Psychological goal**: Reinforce the commitment without pressure. Make them feel good about deciding.
**Example output**: `"좋아요! 21시에 봐요 😄 등 운동 파이팅!"`

### 5-4. Pre-Commitment Reminder Prompt
**Purpose**: Sent ~30 minutes before the user's stated gym time.
**Input context**: Stated time, stated workout type, streak.
**Psychological goal**: Gentle activation — remind them of *their own* plan, not the agent's.
**Example output**: `"21시 다 됐어요! 슬슬 준비하실 시간이죠?"`

### 5-5. Overdue Check-In Prompt
**Purpose**: Sent when committed time has passed by 10+ minutes with no update.
**Input context**: Committed time, minutes overdue, today's nudge count.
**Psychological goal**: Non-judgmental, open-ended check-in. No guilt. Leave room for them to already be at the gym.
**Example output**: `"약속한 시간이 10분 지났는데 운동 잘 하고 계신가요? 다 하고 연락 주세요 💬"`

### 5-6. No-Response Re-Nudge Prompt
**Purpose**: Sent when no reply after 30 minutes and no commitment was made.
**Input context**: Previous message sent, nudge count today, streak status.
**Psychological goal**: New angle each time — never repeat the same framing. Escalate gradually.
**Constraint**: Max 3 re-nudges per day total. Suggested angle progression:
1. Streak + social warmth (`"3일 스트릭인데 오늘 하면 4일이에요 💪"`)
2. Reduced commitment / easy win (`"15분만이라도 어때요? 간단히 유산소도 좋아요"`)
3. Final soft opt-out (`"오늘 힘드시면 내일 봐요. 쉬는 것도 루틴이에요 😄"`)

### 5-7. Workout Completion Celebration Prompt
**Purpose**: Respond when user reports finishing their workout.
**Input context**: What they reported doing, updated streak, this week's workout count vs. goal.
**Psychological goal**: Specific, warm celebration naming what they actually did. Plant a seed for tomorrow without pressure.
**Example output**: `"오늘도 해냈다! 등이랑 이두까지 🔥 3일 연속이에요. 오늘 수고했어요 😊"`

### 5-8. Skip / Rest Day Acknowledgment Prompt
**Purpose**: Respond when user declines to exercise (`"오늘은 패스"`, `"너무 피곤해"`, `"회식이야"`).
**Input context**: Stated reason, streak status, workouts this week vs. goal.
**Psychological goal**: Zero guilt. Full acceptance. Preserve the relationship — judgment causes disengagement.
**Example output**: `"ㅇㅋ 오늘은 쉬어요. 회식도 사실 사회 운동이잖아요 😄 내일 봐요!"`

### 5-9. History Analysis Prompt (WorkoutHistoryAnalyzer sub-agent)
**Purpose**: Analyze workout history and surface patterns for the Orchestrator to use.
**Input context**: 30-day workout log with dates and muscle groups.
**Output format**: Structured summary — current streak, longest streak, most/least trained muscle group, longest gap, patterns (e.g., "user never exercises on Fridays", "always does back + biceps together").
**Usage**: Called by Orchestrator at the start of each day's first invocation.

### 5-10. Note Extraction Prompt
**Purpose**: Parse a raw user message and extract any notable facts worth saving.
**Input context**: User's raw message text.
**Output format**: List of discrete notes (e.g., `["committed to gym at 21:00", "mentioned right knee discomfort"]`).
**Usage**: Called by Orchestrator after every incoming user message.

### 5-11. Weekly Summary Prompt
**Purpose**: Generate a Sunday evening recap of the user's week.
**Input context**: This week's sessions, muscle groups trained, weekly goal, streak, notable events from notes.
**Psychological goal**: Celebrate wins, acknowledge gaps without judgment, set a positive frame for next week.
**Example output**: `"이번 주 3번 운동했어요! 목표 4회에서 하나 아쉽지만, 등이랑 이두 제대로 했고 스트릭 5일이에요 🔥 다음 주도 같이 해봐요 💪"`

---

## 6. Notable Event Detection

The `NoteKeeper` and `WorkoutHistoryAnalyzer` sub-agents should surface these event types for the Orchestrator:

| Event Type | Detection Signal | How Agent Uses It |
|---|---|---|
| **Injury / pain mention** | Keywords in user messages: "무릎", "허리 아파", "다쳤어", "삐었어" | Avoid that muscle group; ask about recovery in future messages |
| **Streak milestone** | Current streak equals or exceeds personal best | Celebrate explicitly in next message |
| **Long absence (5+ days)** | Gap in workout_sessions table | Use re-entry message; lower the bar ("10분만") |
| **Best frequency week** | Workouts this week > any prior week | Celebrate in weekly summary |
| **Consistent skip day pattern** | User never exercises on a specific weekday (3+ weeks) | Implicitly treat that day as rest day; don't nudge |
| **External obstacle stated** | Notes: "이번 주 출장", "발목 삐었어", "감기 걸렸어" | Suppress nudges during that window; follow up with recovery check |
| **Goal change stated** | User says "주 4회 해볼게" | Update weekly_goal in DB; reference in future context |
| **Paired muscle pattern** | Always trains same muscle group pairs | Suggest the usual pair proactively |

---

## 7. Agent Invocation Criteria

The scheduler invokes the Orchestrator only when at least one condition is true:

| Condition | Signal |
|---|---|
| User has left workplace | Location API: exit from 200m work radius |
| Configured departure time reached (±10 min) | Time-based check |
| A scheduled follow-up is due | APScheduler job fires |
| Incoming user message received | Webhook — always invoke immediately |
| It is 09:00 Sunday | Weekly summary generation |
| User has not worked out in 4+ days and it is a weekday evening | Re-engagement check |

The agent must **not** invoke (enforced in code, not just prompt) if:
- User has already recorded a workout today
- It is past 22:00 local time
- User has declared a rest period in notes
- User has already received 3+ nudges today with no response

---

## 8. Kakao API Configuration

### Required APIs and Scopes
- **Kakao Login API**: OAuth 2.0; scopes: `talk_message`, `location`, `calendar_read`, `calendar`
- **KakaoTalk Message API**: Send-to-me (`/v2/api/talk/memo/default/send`) for all outbound messages
- **Kakao Local API**: Gym keyword search; geofencing for commute detection
- **Kakao Calendar API**: Read events (overtime/dinner detection); write workout events

### Message Format
All messages are sent as plain text via the Send-to-Me API. The agent generates the full message string including emoji — no template scaffolding is needed. This is intentional: the LLM's natural language output replaces fixed templates entirely.

---

## 9. Data Storage Design

### Workout History (PostgreSQL)
Each completed session stores: `user_id`, `date`, `muscle_groups[]`, `raw_user_message`, `agent_notes`.

### Notes Store (PostgreSQL)
A simple date-keyed log per user:

| user_id | date | note |
|---|---|---|
| usr_1 | 2024-01-15 | committed to gym at 21:00 |
| usr_1 | 2024-01-15 | completed back + biceps |
| usr_1 | 2024-01-10 | mentioned right knee discomfort |
| usr_1 | 2024-01-08 | business trip this week |

The agent reads the last 14 days of notes as part of its context on every invocation.

### Conversation History (PostgreSQL + LangChain Memory)
Full conversation turns are stored in PostgreSQL. The Orchestrator uses `ConversationSummaryBufferMemory` — recent turns kept verbatim; older turns compressed into a running summary. This prevents context window overflow while preserving long-term relationship context.

---

## 10. Development Roadmap

| Phase | Duration | Key Tasks | Deliverable |
|---|---|---|---|
| **Phase 1** | Weeks 1–2 | Kakao OAuth, send-to-me message, webhook receiver, basic DB schema | **Kakao Integration Complete** |
| **Phase 2** | Weeks 3–4 | LangChain agent setup, tool definitions, system prompt v1, manual trigger test | **Agent Prototype** |
| **Phase 3** | Weeks 5–6 | Scheduler integration, commute detection, follow-up scheduling, note-saving loop | **Full Agent Loop** |
| **Phase 4** | Weeks 7–8 | Sub-agents (history analyzer, message crafter), prompt refinement, conversation memory | **Agent v1 Complete** |
| **Phase 5** | Weeks 9–10 | Beta testing, prompt A/B testing, edge case handling, weekly report | **MVP Launch** |

---

## 11. Risks and Considerations

### 11-1. LLM-Specific Risks
- **Hallucinated history**: Agent may invent workout details. Mitigation: inject real history as structured data; instruct agent to reference only confirmed facts.
- **Tone drift**: May become repetitive over time. Mitigation: include "vary your approach" in system prompt; monitor and tune.
- **Over-messaging**: Agent may find spurious reasons to message. Mitigation: hard-code max 5 messages/day enforced in application code, independent of the prompt.
- **LLM latency**: ~1–3s per Claude API call. Mitigation: acknowledge webhooks immediately with 200 OK; process in background async task.

### 11-2. Technical Risks
- **Background location on mobile**: iOS/Android restrict background location. Use geofencing API; supplement with time-based fallback.
- **Kakao API rate limits**: Message API 1,000/day on free tier. At scale, upgrade; aggressively cache Kakao Local results in Redis.

### 11-3. UX Risks
- **Notification fatigue**: Enforce 3-nudge maximum strictly in code (not prompt). Go silent for the day after 3 unanswered messages.
- **Privacy**: Location and conversation data are sensitive. Encrypt at rest; offer full data deletion.

---

## 12. Success Metrics (KPIs)

| Metric | Target |
|---|---|
| Post-commute workout conversion rate | 40%+ |
| Weekly goal achievement rate | User-configured goal vs. actual |
| 7-day retention | 60%+ |
| Message response rate | 70%+ |
| Commitment follow-through rate | 65%+ (stated a time → actually went) |
| Agent message quality (human eval, naturalness + relevance) | 4.0+ / 5.0 |

---

> **Core Philosophy: The agent remembers so the user doesn't have to explain themselves.**
>
> *It already knows your history. It already knows what you said yesterday. It just asks: "오늘도?"*

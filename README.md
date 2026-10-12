# FitNudge

**A chat-based workout buddy that remembers, knows its stuff, and nudges without pushing.**

FitNudge is an AI fitness companion that lives in your chat app. It messages you when you're most likely to go, suggests what to train based on what you've already done, answers your training questions, and lets you skip without guilt.

> 🇰🇷 FitNudge talks to you in **Korean**.
> 🚧 **Status:** early development, currently running as a **Discord** bot. Not yet open for public sign-up.

---

## What it's like

```
18:42  FitNudge  퇴근했어요? 월요일 가슴, 어제 하체 했으니까 오늘은 등 어때요? 💪
                 오늘 피곤하면 쉬어도 괜찮고요.
18:50  나        9시쯤 갈게
18:50  FitNudge  좋아요, 9시 등 운동! 8시 반쯤 한 번 톡할게요 😄
  ⋮
22:05  나        ㅇㅇ 등이랑 이두 했어. 풀업 처음으로 5개 함
22:05  FitNudge  풀업 5개 첫 성공이네요 🔥 이번 주 3회 목표 달성!
                 오늘 진짜 수고했어요.
```

## What it does

- **Reaches out at the right time.** FitNudge learns when you usually work out and reply, and works around the plans you tell it about (회식, 야근, 출장). It stays quiet at night and never floods you.
- **Nudges instead of nagging.** It uses proven behavior-change techniques: concrete plans, progress you can see, a smaller ask on hard days. Every message includes an easy way out, and skipping a day is fine.
- **Remembers you.** Your workouts, plans, injuries, and preferences are kept as a simple timeline of plain-language notes. You can ask to see them or delete them at any time.
- **Knows training.** It tracks which muscle groups have recovered and suggests what to train next, with the reason. Its answers come from a curated, cited knowledge base, not guesswork.
- **Answers your questions.** "스쿼트할 때 무릎 아파" or "주 3회면 분할 어떻게 해?" get real answers. Anything medical gets pointed to a professional.
- **Has a personality you choose.** Pick a persona (gym buddy, calm coach, cheerleader, and more), then adjust how formal, warm, pushy, funny, or emoji-heavy it is.

## How it works

FitNudge runs on Anthropic's **Claude**, using the Claude Agent SDK. Each time you message it, or it's a good moment to check in, the agent reviews your recent history, decides whether to say something, writes the message, and saves what matters. Safety limits (message caps, quiet hours, no pressure after injuries) are enforced in code, not left to the AI.

**Channels:** Discord DMs today. A KakaoTalk channel is planned for later.

## Privacy

- Only what's needed to coach you is stored: workouts, short notes, and chat history.
- Message content is never written to logs. Use `/pause` anytime to stop FitNudge from messaging first.
- Say "내 기록 보여줘" to export your data, or "다 지워줘" to delete everything.

## Health disclaimer

FitNudge is a motivation and general-fitness tool, **not medical advice**. If you have pain, an injury, or a medical condition, talk to a doctor or physical therapist.

## For developers

- Stack: Python 3.13, FastAPI, Claude Agent SDK + Claude API, discord.py, PostgreSQL, Redis, APScheduler, managed with [uv](https://docs.astral.sh/uv/).
- Quick start:
  ```bash
  cp .env.example .env      # fill in keys
  uv sync
  uv run alembic upgrade head
  uv run uvicorn main:app --host 0.0.0.0 --reload
  ```
- Architecture, conventions, and roadmap: [`CLAUDE.md`](CLAUDE.md). Phase log: [`docs/PHASES.md`](docs/PHASES.md).

## License

[MIT](LICENSE)

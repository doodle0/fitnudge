import asyncio

import pytest

from agent import dispatch
from agent.triggers import Trigger
from channels.base import InboundMessage


@pytest.fixture
def recorded_runs():
    runs: list[tuple[str, Trigger]] = []
    release = asyncio.Event()

    async def runner(user_id: str, trigger: Trigger) -> None:
        runs.append((user_id, trigger))
        await release.wait()

    dispatch.set_runner(runner)
    yield runs, release
    dispatch.set_runner(None)


async def test_runs_coalesce_per_user(recorded_runs):
    runs, release = recorded_runs
    first = dispatch.submit("u1", Trigger.followup("pre-reminder"))
    await asyncio.sleep(0)
    assert dispatch.submit("u1", Trigger.user_message()) is None
    assert dispatch.submit("u1", Trigger.followup("late followup")) is None  # lower priority: dropped
    other = dispatch.submit("u2", Trigger.user_message())
    release.set()
    await asyncio.gather(first, other)
    kinds = [(u, t.kind) for u, t in runs]
    assert kinds == [("u1", "followup"), ("u2", "user_message"), ("u1", "user_message")]


@pytest.mark.db
async def test_handle_inbound_creates_user_and_queues_reactive_run(session, recorded_runs):
    from db import queries as db

    runs, release = recorded_runs
    release.set()
    task = await dispatch.handle_inbound(InboundMessage(channel="discord", external_user_id="42", text="안녕!"))
    await task
    (user_id, trigger), = runs
    assert trigger.kind == "user_message" and trigger.first_contact is True
    turns = await db.get_recent_turns(session, user_id)
    assert [(t.role, t.content) for t in turns] == [("user", "안녕!")]

    task = await dispatch.handle_inbound(InboundMessage(channel="discord", external_user_id="42", text="또 왔어"))
    await task
    assert runs[-1][0] == user_id and runs[-1][1].first_contact is False

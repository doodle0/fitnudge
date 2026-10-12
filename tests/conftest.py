"""
Test setup.

DB tests need a disposable PostgreSQL database — its schema is dropped and rebuilt:
    TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/fitnudge_test uv run pytest
Without TEST_DATABASE_URL, tests marked `db` are skipped.
"""
from __future__ import annotations

import os

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # must happen before config is imported
os.environ["ANTHROPIC_API_KEY"] = ""             # never call the real API from tests
os.environ["DISCORD_BOT_TOKEN"] = ""

import pytest  # noqa: E402

from channels import base as channel_base  # noqa: E402


def pytest_collection_modifyitems(config, items):
    if TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL not set")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def migrated_db():
    """Drop and rebuild the schema with Alembic once per session."""
    import asyncio

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    async def reset() -> None:
        engine = create_async_engine(TEST_DATABASE_URL)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(reset())
    command.upgrade(Config("alembic.ini"), "head")
    yield


@pytest.fixture
async def session(migrated_db):
    """A session on a clean database (all rows truncated)."""
    from sqlalchemy import text

    from db.models import AsyncSessionLocal

    async with AsyncSessionLocal() as s:
        await s.execute(text(
            "TRUNCATE users, channel_identities, workout_sessions, memory_entries, conversation_turns, "
            "streaks, daily_message_counts, scheduled_followups CASCADE"
        ))
        await s.commit()
        yield s


class FakeChannel(channel_base.NullTyping):
    name = "discord"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.unavailable = False

    async def send(self, external_id: str, text: str) -> None:
        if self.unavailable:
            raise channel_base.RecipientUnavailable("dms closed")
        self.sent.append((external_id, text))


@pytest.fixture
def fake_channel():
    channel = FakeChannel()
    channel_base.register(channel)
    yield channel
    channel_base.unregister(channel.name)


@pytest.fixture
async def user_id(session):
    """A Discord user who opted in to proactive messages."""
    from db import queries as db

    user, _identity, _created = await db.get_or_create_user_for_identity(session, "discord", "1001", "도들")
    await db.set_opted_in_proactive(session, str(user.id), "discord", True)
    return str(user.id)

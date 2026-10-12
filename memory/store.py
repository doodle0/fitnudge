"""
Time-keyed plaintext memory store.

Each entry is one (timestamp → short plaintext statement) record. Entries are append-only:
a correction is a new entry that supersedes the old one. `injury` and `goal` entries are
pinned so they stay in the agent's context until superseded.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import MemoryEntry
from utils.time_utils import KST, now_kst

KINDS = (
    "commitment", "workout", "injury", "skip_reason", "mood",
    "goal", "preference", "context", "note",
)
AUTO_PIN_KINDS = frozenset({"injury", "goal"})
MAX_TEXT_LEN = 500
WINDOW_LIMIT = 100


class MemoryStoreError(ValueError):
    """Raised for invalid memory operations (unknown kind, bad supersede reference)."""


async def write(
    session: AsyncSession,
    user_id: str,
    text: str,
    kind: str,
    at: datetime | None = None,
    source: str = "agent",
    source_turn_id: uuid.UUID | None = None,
    supersedes: str | None = None,
) -> MemoryEntry:
    """Append an entry. `supersedes` is an entry id (or unique id prefix) this entry replaces."""
    text = " ".join(text.split())
    if not text:
        raise MemoryStoreError("Memory text is empty.")
    if kind not in KINDS:
        raise MemoryStoreError(f"Unknown kind '{kind}'. Use one of: {', '.join(KINDS)}.")

    old = await _resolve(session, user_id, supersedes) if supersedes else None

    entry = MemoryEntry(
        id=uuid.uuid4(),
        user_id=uuid.UUID(user_id),
        at=at or now_kst(),
        kind=kind,
        text=text[:MAX_TEXT_LEN],
        pinned=kind in AUTO_PIN_KINDS,
        source=source,
        source_turn_id=source_turn_id,
    )
    session.add(entry)
    await session.flush()
    if old is not None:
        old.superseded_by = entry.id
        old.pinned = False
    await session.commit()
    await session.refresh(entry)
    return entry


async def read(
    session: AsyncSession,
    user_id: str,
    since: datetime,
    until: datetime | None = None,
    kinds: list[str] | None = None,
    include_superseded: bool = False,
) -> list[MemoryEntry]:
    """Entries in [since, until], oldest first."""
    stmt = select(MemoryEntry).where(MemoryEntry.user_id == uuid.UUID(user_id), MemoryEntry.at >= since)
    if until is not None:
        stmt = stmt.where(MemoryEntry.at <= until)
    if kinds:
        stmt = stmt.where(MemoryEntry.kind.in_(kinds))
    if not include_superseded:
        stmt = stmt.where(MemoryEntry.superseded_by.is_(None))
    result = await session.execute(stmt.order_by(MemoryEntry.at.asc()))
    return list(result.scalars().all())


async def window(session: AsyncSession, user_id: str, days: int = 14) -> list[MemoryEntry]:
    """Context window: the last `days` days plus older pinned entries; current entries only."""
    since = now_kst() - timedelta(days=days)
    stmt = (
        select(MemoryEntry)
        .where(
            MemoryEntry.user_id == uuid.UUID(user_id),
            MemoryEntry.superseded_by.is_(None),
            or_(MemoryEntry.at >= since, MemoryEntry.pinned.is_(True)),
        )
        .order_by(MemoryEntry.at.desc())
        .limit(WINDOW_LIMIT)
    )
    result = await session.execute(stmt)
    return list(reversed(result.scalars().all()))


def format_entry(entry: MemoryEntry) -> str:
    at = entry.at.astimezone(KST).strftime("%Y-%m-%dT%H:%M%z")
    at = f"{at[:-2]}:{at[-2:]}"  # +0900 → +09:00
    pin = " 📌" if entry.pinned else ""
    return f"{at}  [{entry.kind}]{pin}  {entry.text}  (#{str(entry.id)[:8]})"


def format_entries(entries: list[MemoryEntry]) -> str:
    return "\n".join(format_entry(e) for e in entries) if entries else "(no entries)"


async def _resolve(session: AsyncSession, user_id: str, ref: str) -> MemoryEntry:
    ref = ref.strip().lstrip("#").lower()
    if len(ref) < 6:
        raise MemoryStoreError("Supersede reference must be at least 6 characters of the entry id.")
    result = await session.execute(
        select(MemoryEntry).where(
            MemoryEntry.user_id == uuid.UUID(user_id),
            cast(MemoryEntry.id, String).like(f"{ref}%"),
        )
    )
    matches = list(result.scalars().all())
    if len(matches) != 1:
        raise MemoryStoreError(f"Entry #{ref} not found (or ambiguous).")
    return matches[0]

from datetime import timedelta

import pytest

from memory import store as memory
from utils.time_utils import now_kst

pytestmark = pytest.mark.db


async def test_write_and_format(session, user_id):
    entry = await memory.write(session, user_id, "  21시에   헬스장 가기로 함 ", "commitment")
    assert entry.text == "21시에 헬스장 가기로 함"
    line = memory.format_entry(entry)
    assert "[commitment]" in line and "+09:00" in line and f"#{str(entry.id)[:8]}" in line


async def test_injury_is_pinned_and_survives_window(session, user_id):
    old = now_kst() - timedelta(days=40)
    await memory.write(session, user_id, "오른쪽 무릎 통증", "injury", at=old)
    await memory.write(session, user_id, "오래된 메모", "note", at=old)
    window = await memory.window(session, user_id, days=14)
    assert [e.text for e in window] == ["오른쪽 무릎 통증"]


async def test_supersede_hides_old_and_unpins(session, user_id):
    injury = await memory.write(session, user_id, "오른쪽 무릎 통증", "injury")
    await memory.write(session, user_id, "무릎 다 나았다고 함", "context", supersedes=str(injury.id)[:8])
    texts = [e.text for e in await memory.window(session, user_id)]
    assert texts == ["무릎 다 나았다고 함"]
    everything = await memory.read(session, user_id, since=now_kst() - timedelta(days=1), include_superseded=True)
    assert len(everything) == 2 and not everything[0].pinned


async def test_invalid_kind_and_reference(session, user_id):
    with pytest.raises(memory.MemoryStoreError, match="Unknown kind"):
        await memory.write(session, user_id, "x", "diary")
    with pytest.raises(memory.MemoryStoreError, match="not found"):
        await memory.write(session, user_id, "x", "note", supersedes="deadbeef")

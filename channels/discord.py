"""
Discord channel adapter: 1:1 DMs between the bot and a user.
"""
from __future__ import annotations

from typing import AsyncContextManager

import discord

from channels.base import RecipientUnavailable

DISCORD_MAX_CHARS = 2000


class DiscordChannel:
    name = "discord"

    def __init__(self, client: discord.Client) -> None:
        self._client = client

    async def _dm(self, external_id: str) -> discord.DMChannel:
        try:
            user = self._client.get_user(int(external_id)) or await self._client.fetch_user(int(external_id))
            return user.dm_channel or await user.create_dm()
        except (discord.NotFound, discord.Forbidden) as exc:
            raise RecipientUnavailable(str(exc)) from exc

    async def send(self, external_id: str, text: str) -> None:
        dm = await self._dm(external_id)
        try:
            await dm.send(text[:DISCORD_MAX_CHARS], allowed_mentions=discord.AllowedMentions.none())
        except (discord.NotFound, discord.Forbidden) as exc:
            raise RecipientUnavailable(str(exc)) from exc

    def typing(self, external_id: str) -> AsyncContextManager[None]:
        return _DiscordTyping(self, external_id)


class _DiscordTyping:
    """Typing indicator that never fails the surrounding work."""

    def __init__(self, channel: DiscordChannel, external_id: str) -> None:
        self._channel = channel
        self._external_id = external_id
        self._cm = None

    async def __aenter__(self) -> None:
        try:
            dm = await self._channel._dm(self._external_id)
            self._cm = dm.typing()
            await self._cm.__aenter__()
        except Exception:  # noqa: BLE001 — cosmetic only
            self._cm = None

    async def __aexit__(self, *exc_info) -> None:
        if self._cm is not None:
            try:
                await self._cm.__aexit__(*exc_info)
            except Exception:  # noqa: BLE001
                pass

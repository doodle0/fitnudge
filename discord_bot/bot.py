"""
Discord gateway client. Runs inside the FastAPI process (started in main.py's lifespan).

- Conversation happens in 1:1 DMs. DM content arrives without the privileged Message Content
  intent, so we never request it; guild messages are ignored.
- Slash commands are shortcuts; free-text DMs are the main interface.
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands

from agent import dispatch
from channels import base as channel_base
from channels.base import InboundMessage
from channels.discord import DiscordChannel
from config import settings

log = logging.getLogger(__name__)

CHANNEL = "discord"

START_REPLY = (
    f"반가워요! 저는 {settings.bot_name}예요 💪\n"
    "운동 기록하고, 루틴 짜고, 오늘 뭐 할지 같이 고민하는 운동 친구예요. "
    "이제 제가 가끔 먼저 톡할게요. 부담스러우면 언제든 /pause 하면 돼요.\n"
    "참고로 저는 의료 조언은 못 해요. 아프거나 다친 데가 있으면 꼭 전문가랑 상담해요!"
)
PAUSE_REPLY = "알겠어요, 제가 먼저 연락하는 건 멈출게요. 말 걸어주면 언제든 대답할게요 🙂 (/resume 으로 다시 켤 수 있어요)"
RESUME_REPLY = "좋아요, 다시 제가 먼저 챙길게요 💪"
DM_CLOSED_REPLY = "DM을 보낼 수 없어요. 서버 설정에서 '다이렉트 메시지 허용'을 켜고 다시 /start 해 주세요!"


class FitNudgeBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.none()
        intents.guilds = True       # needed for slash commands and shared-server DMs
        intents.dm_messages = True  # DM events (content included without the privileged intent)
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._tasks: set[asyncio.Task] = set()
        _register_commands(self)

    async def setup_hook(self) -> None:
        channel_base.register(DiscordChannel(self))
        # Don't block login on command sync (it is rate-limited); run it in the background.
        self._spawn(self._sync_commands_if_changed())

    async def _sync_commands_if_changed(self) -> None:
        """Push slash commands only when they differ from what Discord already has."""
        guild = discord.Object(id=int(settings.discord_guild_id)) if settings.discord_guild_id else None
        if guild is not None:
            self.tree.copy_global_to(guild=guild)
        local = {(c.name, c.description) for c in self.tree.get_commands(guild=guild)}
        remote = {(c.name, c.description) for c in await self.tree.fetch_commands(guild=guild)}
        if local == remote:
            log.info("Slash commands up to date (%d)", len(local))
            return
        synced = await self.tree.sync(guild=guild)
        scope = f"guild {settings.discord_guild_id}" if guild else "globally (may take up to an hour to appear)"
        log.info("Slash commands synced %s: %d", scope, len(synced))

    async def on_ready(self) -> None:
        log.info("Discord connected as %s (id=%s) | guilds=%d", self.user, self.user.id if self.user else "?",
                 len(self.guilds))

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is not None:
            return
        text = message.content.strip()
        if not text:
            return
        msg = InboundMessage(
            channel=CHANNEL,
            external_user_id=str(message.author.id),
            text=text,
            display_name=message.author.display_name,
            received_at=message.created_at,
        )
        self._spawn(dispatch.handle_inbound(msg))

    def _spawn(self, coro) -> None:
        """Run in the background so the gateway is never blocked; keep a reference until done."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(_log_task_error)

    async def close(self) -> None:
        channel_base.unregister(CHANNEL)
        await super().close()


def _log_task_error(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("Background task failed", exc_info=task.exception())


async def _set_proactive(interaction: discord.Interaction, opted_in: bool) -> None:
    from db import queries as db
    from db.models import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        user, _identity, _created = await db.get_or_create_user_for_identity(
            session, CHANNEL, str(interaction.user.id), interaction.user.display_name
        )
        await db.set_opted_in_proactive(session, str(user.id), CHANNEL, opted_in)
        if opted_in:
            await db.mark_onboarding_complete(session, str(user.id))
            await db.set_proactive_snooze(session, str(user.id), None)  # /start and /resume end any snooze


def _register_commands(bot: FitNudgeBot) -> None:
    @bot.tree.command(name="start", description=f"{settings.bot_name}와 시작하기 (먼저 톡 보내기 허용)")
    async def start(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _set_proactive(interaction, True)
        if interaction.guild is None:
            await interaction.followup.send(START_REPLY)
            return
        try:
            await interaction.user.send(START_REPLY)
            await interaction.followup.send("DM으로 인사 보냈어요! 📩", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(DM_CLOSED_REPLY, ephemeral=True)

    @bot.tree.command(name="pause", description="먼저 톡 보내는 것 멈추기")
    async def pause(interaction: discord.Interaction) -> None:
        await _set_proactive(interaction, False)
        await interaction.response.send_message(PAUSE_REPLY, ephemeral=interaction.guild is not None)

    @bot.tree.command(name="resume", description="먼저 톡 보내기 다시 켜기")
    async def resume(interaction: discord.Interaction) -> None:
        await _set_proactive(interaction, True)
        await interaction.response.send_message(RESUME_REPLY, ephemeral=interaction.guild is not None)

    # Usable in DMs with the bot as well as in the FitNudge server
    for command in (start, pause, resume):
        command.allowed_contexts = app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=False)

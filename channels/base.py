"""
Platform-neutral messaging interface.

Nothing outside channels/ may assume a specific platform. A new platform (Kakao) is a new
adapter registered here, not a refactor.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncContextManager, Protocol


class RecipientUnavailable(Exception):
    """The user can no longer receive messages on this channel (DMs closed, account gone)."""


@dataclass(frozen=True)
class InboundMessage:
    channel: str
    external_user_id: str
    text: str
    display_name: str | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


class Channel(Protocol):
    name: str

    async def send(self, external_id: str, text: str) -> None:
        """Deliver one plain-text message. Raises RecipientUnavailable if undeliverable."""

    def typing(self, external_id: str) -> AsyncContextManager[None]:
        """Show a typing indicator while the body runs (no-op where unsupported)."""


class NullTyping:
    """Mixin providing a no-op typing indicator."""

    def typing(self, external_id: str) -> AsyncContextManager[None]:
        return contextlib.nullcontext()


_registry: dict[str, Channel] = {}


def register(channel: Channel) -> None:
    _registry[channel.name] = channel


def unregister(name: str) -> None:
    _registry.pop(name, None)


def get(name: str) -> Channel:
    try:
        return _registry[name]
    except KeyError:
        raise LookupError(f"Channel '{name}' is not running.") from None

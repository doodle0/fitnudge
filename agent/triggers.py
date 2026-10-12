"""
Why the agent was woken up.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TriggerKind = Literal["user_message", "followup", "scheduled", "manual"]


@dataclass(frozen=True)
class Trigger:
    kind: TriggerKind
    proactive: bool                 # True when we would be reaching out first
    reason: str = ""
    first_contact: bool = False     # the user's very first message to the bot

    @classmethod
    def user_message(cls, first_contact: bool = False) -> "Trigger":
        return cls(kind="user_message", proactive=False, reason="incoming user message",
                   first_contact=first_contact)

    @classmethod
    def followup(cls, reason: str) -> "Trigger":
        return cls(kind="followup", proactive=True, reason=reason)

    @classmethod
    def manual(cls, reason: str) -> "Trigger":
        return cls(kind="manual", proactive=True, reason=reason)

    def priority(self) -> int:
        """When runs coalesce, the higher-priority trigger wins. User messages beat everything."""
        return 2 if not self.proactive else 1

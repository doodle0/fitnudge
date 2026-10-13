"""
Why the agent was woken up.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from utils.time_utils import KST

TriggerKind = Literal["user_message", "followup", "scheduled", "manual"]
FollowupKind = Literal["pre_reminder", "checkin", "nudge", "custom"]


@dataclass(frozen=True)
class Trigger:
    kind: TriggerKind
    proactive: bool                 # True when we would be reaching out first
    reason: str = ""
    first_contact: bool = False     # the user's very first message to the bot
    followup_kind: FollowupKind | None = None
    commitment_at: datetime | None = None   # for pre_reminder / checkin: the time the user committed to

    @classmethod
    def user_message(cls, first_contact: bool = False) -> "Trigger":
        return cls(kind="user_message", proactive=False, reason="incoming user message",
                   first_contact=first_contact)

    @classmethod
    def followup(cls, reason: str, kind: FollowupKind = "custom",
                 commitment_at: datetime | None = None) -> "Trigger":
        return cls(kind="followup", proactive=True, reason=reason, followup_kind=kind,
                   commitment_at=commitment_at)

    @classmethod
    def scheduled(cls, reason: str) -> "Trigger":
        return cls(kind="scheduled", proactive=True, reason=reason)

    @classmethod
    def manual(cls, reason: str) -> "Trigger":
        return cls(kind="manual", proactive=True, reason=reason)

    def priority(self) -> int:
        """When runs coalesce, the higher-priority trigger wins. User messages beat everything."""
        return 2 if not self.proactive else 1

    def describe(self) -> str:
        """What the agent should understand about this wake-up."""
        at = f"{self.commitment_at.astimezone(KST):%H:%M}" if self.commitment_at else "?"
        if self.followup_kind == "pre_reminder":
            return (f"pre-reminder: the user planned \"{self.reason}\" at {at}. Remind them of their own plan "
                    "— short and upbeat. A check-in after that time is scheduled automatically.")
        if self.followup_kind == "checkin":
            return (f"check-in: the user planned \"{self.reason}\" at {at}; that time has passed and no workout "
                    "is recorded. One gentle, open question — they may still be at the gym. No guilt.")
        if self.kind == "scheduled":
            return (f"{self.reason}. This is today's first proactive nudge: a specific suggestion from their "
                    "recent workouts plus an easy out.")
        return self.reason or "-"

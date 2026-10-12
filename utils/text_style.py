"""
Lightweight checks on outgoing chat text (Korean casual style).
"""
from __future__ import annotations

import re

# A sentence ends at . ! ? (or runs of them), or at an emoji followed by more text — casual chat
# often uses an emoji instead of punctuation ("운완이에요 😊 제가 …").
_EMOJI = "\U0001F300-\U0001FAFF\u2600-\u27BF"
_BOUNDARY = re.compile(rf"[.!?…~]+(?=\s|$)|(?<=[{_EMOJI}])(?=\s+\S)")


def count_sentences(text: str) -> int:
    parts = [p for p in _BOUNDARY.split(text.strip()) if re.search(r"[\w가-힣]", p or "")]
    return max(1, len(parts)) if text.strip() else 0

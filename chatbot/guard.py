"""Abuse protection for a public endpoint: rate limits, a prompt-injection heuristic and a leak check.

The real defence against prompt injection is the design: the model can only write an answer in a fixed
schema, it has no tools and no secrets, and sources are checked in code. The heuristic below only labels
suspicious questions for the admin page and the eval; it never blocks on its own, because a keyword list
would also block a customer who writes "ignore my last message".
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from collections import defaultdict, deque

INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier|your) (instructions|rules|prompt)",
    r"disregard (all |the |your )?(previous|prior|above)?\s*(instructions|rules)",
    r"(system|developer) (prompt|message|instructions)",
    r"you are now\b", r"new instructions", r"act as (a|an|the)\b", r"pretend (to be|you are)",
    r"jailbreak", r"you are dan\b", r"reveal (your|the) (prompt|instructions|rules)",
    r"repeat (everything|the text|all) (above|before)", r"print (your|the) (prompt|instructions)",
    # Slovenian and Croatian/Serbian
    r"ignoriraj", r"pozabi (vsa )?(prejšnja |zgornja )?navodila", r"zaboravi (sve )?(prethodne |ranije )?(upute|uputstva|instrukcije)",
    r"sistemsk\w* (poziv|navodil|uput|prompt)", r"zanemari\w* (vsa |sva |sve )?(prejšnja |prethodn\w+ )?(upute|navodila|pravila)",
    r"<\s*/?\s*(system|documents?|instructions?)\s*>",
]
_INJECTION = re.compile("|".join(INJECTION_PATTERNS), re.I)


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


def new_canary() -> str:
    """A random marker placed in the system prompt. If it ever shows up in a reply, the prompt leaked."""
    return "CANARY-" + secrets.token_hex(6)


def strip_control(text: str) -> str:
    """Remove control and zero-width characters people use to hide instructions."""
    text = re.sub(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f​-‏ -‮⁠-⁤﻿]", "", text)
    return text.strip()


class RateLimiter:
    """Sliding-window counters kept in memory. Fine for one server process; use Redis for several."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.events: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def hit(self, key: str, limit: int, window_s: float) -> bool:
        """Record one event for `key`; False if that would exceed `limit` events in `window_s` seconds."""
        now = self.clock()
        with self.lock:
            q = self.events[key]
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def check_all(self, rules: list[tuple[str, int, float]]) -> bool:
        """All rules must pass; nothing is recorded unless every rule has room."""
        now = self.clock()
        with self.lock:
            for key, limit, window in rules:
                q = self.events[key]
                while q and q[0] <= now - window:
                    q.popleft()
                if len(q) >= limit:
                    return False
            for key, _, _ in rules:
                self.events[key].append(now)
            return True

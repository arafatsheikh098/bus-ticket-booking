"""In-memory token-bucket rate limits.

Per process, so with several API instances each enforces its own share. That is enough to
stop a script hammering one instance; a shared limit would move to Redis or the gateway.
"""

import math
import time

from fastapi import Depends, Request

from app.config import settings
from app.errors import AppError


class TokenBucket:
    def __init__(self, per_minute: float, burst: int, max_keys: int = 50_000):
        self.rate = per_minute / 60.0
        self.burst = burst
        self.max_keys = max_keys
        self._buckets: dict[str, tuple[float, float]] = {}

    def take(self, key: str) -> float:
        """Take one token. Returns 0 if allowed, otherwise seconds until a token is free."""
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (float(self.burst), now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens < 1:
            self._buckets[key] = (tokens, now)
            return (1 - tokens) / self.rate
        self._buckets[key] = (tokens - 1, now)
        if len(self._buckets) > self.max_keys:
            self._prune(now)
        return 0.0

    def _prune(self, now: float) -> None:
        # Buckets that have refilled completely carry no information, so drop them.
        full = [k for k, (t, last) in self._buckets.items()
                if t + (now - last) * self.rate >= self.burst]
        for key in full:
            del self._buckets[key]


def default_limiters() -> dict[str, TokenBucket]:
    return {
        # Seat clicks: generous for a human, tight for a script.
        "hold:session": TokenBucket(per_minute=60, burst=20),
        # Per IP as well, because a script can invent new session ids. Kept high so
        # families or a campus behind one NAT address are not blocked.
        "hold:ip": TokenBucket(per_minute=600, burst=120),
        "checkout:session": TokenBucket(per_minute=10, burst=5),
        # Ticket lookup and cancel take a PNR and phone number, so limit guessing.
        "ticket:ip": TokenBucket(per_minute=20, burst=10),
    }


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def limit(*buckets: str):
    """FastAPI dependency enforcing the named buckets, keyed by session or client IP."""

    async def check(request: Request) -> None:
        if not settings.rate_limits_enabled:
            return
        limiters = request.app.state.limiters
        for name in buckets:
            kind = name.split(":", 1)[1]
            key = request.headers.get("x-session-id") if kind == "session" else client_ip(request)
            wait = limiters[name].take(f"{name}:{key or client_ip(request)}")
            if wait:
                raise AppError(429, "rate_limited", "Too many requests. Please slow down.",
                               retry_after=math.ceil(wait))

    return Depends(check)

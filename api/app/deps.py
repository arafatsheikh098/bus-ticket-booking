import re

import asyncpg
from fastapi import Header, Request

from app.errors import AppError

_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def get_session(x_session_id: str | None = Header(default=None)) -> str:
    """Guest session id sent by the browser. A production system would use real auth."""
    if not x_session_id or not _SESSION_RE.match(x_session_id):
        raise AppError(400, "session_required", "Send a valid X-Session-Id header")
    return x_session_id


def optional_session(x_session_id: str | None = Header(default=None)) -> str | None:
    return x_session_id if x_session_id and _SESSION_RE.match(x_session_id) else None

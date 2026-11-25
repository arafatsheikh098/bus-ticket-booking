"""Background maintenance loop.

Only one API instance runs it at a time: each instance tries to take a Postgres session
advisory lock, and the one that gets it becomes the sweeper until its connection closes.
"""

import asyncio
import logging

import asyncpg

from app.config import settings
from app.services import holds

log = logging.getLogger("aashon.sweeper")
_SWEEPER_LOCK = 727_003


async def sweep_once(pool: asyncpg.Pool, provider=None) -> dict:
    expired = await holds.expire_lapsed_holds(pool)
    return {"expired": expired}


async def run_sweeper(pool: asyncpg.Pool, provider=None) -> None:
    while True:
        try:
            async with pool.acquire() as leader_conn:
                if not await leader_conn.fetchval("SELECT pg_try_advisory_lock($1)", _SWEEPER_LOCK):
                    await asyncio.sleep(settings.sweeper_interval_seconds * 5)
                    continue
                log.info("this instance is now the sweeper")
                try:
                    while True:
                        result = await sweep_once(pool, provider)
                        if any(result.values()):
                            log.info("sweep %s", result)
                        await asyncio.sleep(settings.sweeper_interval_seconds)
                finally:
                    await leader_conn.execute("SELECT pg_advisory_unlock($1)", _SWEEPER_LOCK)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("sweeper crashed, restarting")
            await asyncio.sleep(1)

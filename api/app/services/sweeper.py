"""Background maintenance loop.

Only one API instance runs it at a time: each instance tries to take a Postgres session
advisory lock, and the one that gets it becomes the sweeper until its connection closes.
"""

import asyncio
import logging
import time

import asyncpg

from app.config import settings
from app.seed import ensure_demo_data
from app.services import holds, payments

log = logging.getLogger("busticket.sweeper")
_SWEEPER_LOCK = 727_003
_RESEED_EVERY_SECONDS = 3600


async def sweep_once(pool: asyncpg.Pool, provider=None) -> dict:
    result = {"expired": await holds.expire_lapsed_holds(pool)}
    if provider is not None:
        result["reconciled"] = await payments.reconcile_overdue(pool, provider)
        result["refunded"] = await payments.retry_refunds(pool, provider)
    return result


async def run_sweeper(pool: asyncpg.Pool, provider=None) -> None:
    while True:
        try:
            leader = False
            async with pool.acquire() as leader_conn:
                leader = await leader_conn.fetchval("SELECT pg_try_advisory_lock($1)", _SWEEPER_LOCK)
                if leader:
                    log.info("this instance is now the sweeper")
                    await _lead(pool, provider, leader_conn)
            if not leader:
                # Not the leader: give the connection back and try again later.
                await asyncio.sleep(settings.sweeper_interval_seconds * 5)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("sweeper crashed, restarting")
            await asyncio.sleep(1)


async def _lead(pool: asyncpg.Pool, provider, leader_conn) -> None:
    last_seed = time.monotonic()
    try:
        while True:
            result = await sweep_once(pool, provider)
            if any(result.values()):
                log.info("sweep %s", result)
            if settings.seed_demo_data and time.monotonic() - last_seed > _RESEED_EVERY_SECONDS:
                # Keep a rolling week of demo trips when the stack runs for days.
                created = await ensure_demo_data(pool)
                if created:
                    log.info("seeded %d new trips", created)
                last_seed = time.monotonic()
            await asyncio.sleep(settings.sweeper_interval_seconds)
    finally:
        await leader_conn.execute("SELECT pg_advisory_unlock($1)", _SWEEPER_LOCK)

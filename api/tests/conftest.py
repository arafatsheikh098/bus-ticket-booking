import json
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app import db
from app.config import settings
from app.main import create_app
from app.seed import layout_for, seed_reference_data, seats_in
from tests.invariants import check_invariants
from tests.stub_provider import StubProvider

TEST_DB = os.environ.get("TEST_DATABASE_URL",
                         "postgresql://aashon:aashon@localhost:5433/aashon_test")
_schema_ready = False


@pytest.fixture(autouse=True)
def test_settings(monkeypatch):
    monkeypatch.setattr(settings, "database_url", TEST_DB)
    monkeypatch.setattr(settings, "run_background_tasks", False)
    monkeypatch.setattr(settings, "seed_demo_data", False)
    monkeypatch.setattr(settings, "rate_limits_enabled", False)
    monkeypatch.setattr(settings, "hold_ttl_seconds", 300)
    monkeypatch.setattr(settings, "payment_grace_seconds", 120)
    monkeypatch.setattr(settings, "fakepay_webhook_secret", "whsec_test")
    return settings


@pytest.fixture
async def pool():
    global _schema_ready
    pool = await db.create_pool(TEST_DB, min_size=2, max_size=30)
    async with pool.acquire() as conn:
        if not _schema_ready:
            await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        else:
            await conn.execute("""TRUNCATE trip_seats, payments, webhook_events, bookings,
                                  holds, trips, buses, operators, cities RESTART IDENTITY CASCADE""")
    if not _schema_ready:
        await db.migrate(pool)
        _schema_ready = True
    async with pool.acquire() as conn:
        await seed_reference_data(conn)
    yield pool
    await check_invariants(pool)
    await pool.close()


async def make_trip(pool, *, coach_type="non_ac", departs_in=timedelta(days=1), fare=700) -> dict:
    async with pool.acquire() as conn:
        bus = await conn.fetchrow("SELECT id FROM buses WHERE coach_type = $1 LIMIT 1", coach_type)
        departs = datetime.now(timezone.utc) + departs_in
        trip_id = await conn.fetchval(
            """INSERT INTO trips (bus_id, origin_id, destination_id, departs_at, arrives_at,
                                  fare_bdt, boarding_point)
               VALUES ($1, (SELECT id FROM cities WHERE slug = 'dhaka'),
                       (SELECT id FROM cities WHERE slug = 'chattogram'), $2, $3, $4, 'Sayedabad')
               RETURNING id""",
            bus["id"], departs, departs + timedelta(hours=6), fare,
        )
        seats = seats_in(layout_for(coach_type))
        await conn.copy_records_to_table("trip_seats", records=[(trip_id, s) for s in seats],
                                         columns=["trip_id", "seat_no"])
    return {"id": trip_id, "seats": seats, "fare": fare}


@pytest.fixture
async def trip(pool):
    return await make_trip(pool)


@pytest.fixture
def provider():
    return StubProvider()


@pytest.fixture
async def client(pool, provider):
    app = create_app(pool=pool, provider=provider)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def session(name: str) -> dict:
    return {"X-Session-Id": f"sess-{name}-0000"}


async def expire_hold(pool, hold_id) -> None:
    """Move a hold's deadline into the past, as if five minutes had passed."""
    async with pool.acquire() as conn:
        deadline = await conn.fetchval(
            """UPDATE holds SET expires_at = now() - interval '1 second' WHERE id = $1
               RETURNING expires_at""", hold_id)
        await conn.execute("UPDATE trip_seats SET held_until = $2 WHERE hold_id = $1",
                           hold_id, deadline)


def signed_webhook(payment: dict, event_type: str, *, secret="whsec_test", event_id=None,
                   timestamp=None) -> tuple[bytes, dict]:
    from tests.fakepay_sign import sign
    body = json.dumps({"id": event_id or f"evt_{time.time_ns()}", "type": event_type,
                       "data": payment}).encode()
    return body, {"X-FakePay-Signature": sign(secret, body, timestamp),
                  "Content-Type": "application/json"}

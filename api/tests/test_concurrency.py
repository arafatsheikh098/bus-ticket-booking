"""The "two people, one seat" problem, under real concurrency against real Postgres."""

import asyncio
import random

from app.errors import AppError
from app.services import holds


async def attempt(pool, trip_id, session_id, seats):
    try:
        return await holds.set_hold(pool, trip_id, session_id, seats)
    except AppError as exc:
        return exc


async def test_hundred_people_one_seat_exactly_one_wins(pool, trip):
    results = await asyncio.gather(*[
        attempt(pool, trip["id"], f"racer-{i:04d}", ["A1"]) for i in range(100)
    ])
    winners = [r for r in results if isinstance(r, dict)]
    losers = [r for r in results if isinstance(r, AppError)]
    assert len(winners) == 1
    assert len(losers) == 99 and all(e.code == "seats_unavailable" for e in losers)
    owner = await pool.fetchval(
        "SELECT hold_id FROM trip_seats WHERE trip_id = $1 AND seat_no = 'A1'", trip["id"])
    assert str(owner) == winners[0]["id"]


async def test_overlapping_group_bookings_never_split_or_deadlock(pool, trip):
    rng = random.Random(7)
    seats = trip["seats"][:12]
    requests = [rng.sample(seats, k=rng.randint(1, 4)) for _ in range(60)]
    results = await asyncio.wait_for(asyncio.gather(*[
        attempt(pool, trip["id"], f"group-{i:04d}", want) for i, want in enumerate(requests)
    ]), timeout=30)

    claimed: dict[str, str] = {}
    for want, result in zip(requests, results):
        if isinstance(result, dict):
            assert sorted(want) == result["seats"], "a hold got only part of what it asked for"
            for seat in want:
                assert seat not in claimed, f"{seat} held twice"
                claimed[seat] = result["id"]
    rows = await pool.fetch(
        "SELECT seat_no, hold_id FROM trip_seats WHERE trip_id = $1 AND status = 'held'", trip["id"])
    assert {r["seat_no"]: str(r["hold_id"]) for r in rows} == claimed


async def test_same_session_double_click_keeps_one_hold(pool, trip):
    results = await asyncio.gather(*[
        attempt(pool, trip["id"], "double-clicker", ["B1", "B2"]) for _ in range(10)
    ])
    ok = [r for r in results if isinstance(r, dict)]
    assert ok and len({r["id"] for r in ok}) == 1
    for r in results:
        if isinstance(r, AppError):
            assert r.code == "concurrent_request"
    count = await pool.fetchval(
        "SELECT count(*) FROM holds WHERE session_id = 'double-clicker' AND status = 'active'")
    assert count == 1


async def test_random_storm_keeps_every_invariant(pool, trip):
    """Many sessions picking, changing and dropping seats at once. The `pool` fixture runs
    the whole-database invariant check when the test ends."""
    rng = random.Random(42)
    seats = trip["seats"][:16]

    async def user(n):
        for _ in range(8):
            choice = rng.sample(seats, k=rng.randint(0, 3))
            await attempt(pool, trip["id"], f"storm-{n:04d}", choice)
            await asyncio.sleep(rng.random() / 100)

    await asyncio.wait_for(asyncio.gather(*[user(n) for n in range(25)]), timeout=60)

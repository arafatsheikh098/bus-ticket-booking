"""Demo data: BD cities, fictional operators, coaches and a rolling week of trips.

Operator names are invented for the demo and do not refer to real companies.
"""

import random
import secrets
from datetime import date, datetime, time, timedelta, timezone

import asyncpg

DHAKA_TZ = timezone(timedelta(hours=6))  # Bangladesh has no daylight saving time.
DAYS_AHEAD = 7

CITIES = [
    ("dhaka", "Dhaka", "ঢাকা"),
    ("chattogram", "Chattogram", "চট্টগ্রাম"),
    ("coxs-bazar", "Cox's Bazar", "কক্সবাজার"),
    ("sylhet", "Sylhet", "সিলেট"),
    ("rajshahi", "Rajshahi", "রাজশাহী"),
    ("khulna", "Khulna", "খুলনা"),
    ("barishal", "Barishal", "বরিশাল"),
]

OPERATORS = ["Nodi Express", "Megh Travels", "Dhanshiri Paribahan", "Pahar Line"]

# (from, to, duration hours, non-AC fare, AC economy fare, AC business fare)
ROUTES = [
    ("dhaka", "chattogram", 6.0, 680, 1100, 1600),
    ("dhaka", "coxs-bazar", 10.0, 1000, 1600, 2500),
    ("chattogram", "coxs-bazar", 4.0, 450, 700, 1000),
    ("dhaka", "sylhet", 6.0, 700, 1150, 1700),
    ("dhaka", "rajshahi", 6.0, 750, 1200, 1750),
    ("dhaka", "khulna", 5.0, 750, 1200, 1750),
    ("chattogram", "sylhet", 7.0, 800, 1300, 1900),
    ("dhaka", "barishal", 4.5, 600, 1000, 1500),
]

BOARDING = {
    "dhaka": ["Sayedabad", "Gabtoli", "Mohakhali", "Arambagh"],
    "chattogram": ["Dampara", "GEC Circle", "Oxygen Mor", "AK Khan"],
    "coxs-bazar": ["Kolatoli", "Sugandha Point"],
    "sylhet": ["Kadamtali", "Humayun Rashid Chattar"],
    "rajshahi": ["Shiroil", "Railgate"],
    "khulna": ["Sonadanga", "Royal Mor"],
    "barishal": ["Nathullabad", "Rupatali"],
}

DEPARTURES = [time(6, 30), time(9, 0), time(13, 30), time(22, 0), time(23, 30)]

COACH_TYPES = ["non_ac", "ac_economy", "ac_business"]


def layout_for(coach_type: str) -> list[list[str | None]]:
    """Seat grid for a coach. 2+2 for regular coaches, 2+1 for business class."""
    if coach_type == "ac_business":
        rows, left, right = "ABCDEFGHIJ", 2, 1
    elif coach_type == "ac_economy":
        rows, left, right = "ABCDEFGHI", 2, 2
    else:
        rows, left, right = "ABCDEFGHIJ", 2, 2
    grid = []
    for r in rows:
        seats = [f"{r}{n}" for n in range(1, left + right + 1)]
        grid.append(seats[:left] + [None] + seats[left:])
    return grid


def seats_in(layout: list[list[str | None]]) -> list[str]:
    return [s for row in layout for s in row if s]


def _pnr() -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "BT" + "".join(secrets.choice(alphabet) for _ in range(6))


async def seed_reference_data(conn: asyncpg.Connection) -> None:
    await conn.executemany(
        "INSERT INTO cities (slug, name, name_bn) VALUES ($1, $2, $3) ON CONFLICT (slug) DO NOTHING",
        CITIES,
    )
    await conn.executemany(
        "INSERT INTO operators (name) VALUES ($1) ON CONFLICT (name) DO NOTHING",
        [(o,) for o in OPERATORS],
    )
    operators = await conn.fetch("SELECT id, name FROM operators ORDER BY id")
    coach = 0
    for op in operators:
        for ct in COACH_TYPES:
            for _ in range(3):
                coach += 1
                await conn.execute(
                    """INSERT INTO buses (operator_id, coach_no, coach_type, layout)
                       VALUES ($1, $2, $3, $4) ON CONFLICT (coach_no) DO NOTHING""",
                    op["id"], f"{op['name'][:2].upper()}-{coach:03d}", ct, layout_for(ct),
                )


async def seed_trips_for_day(conn: asyncpg.Connection, day: date) -> int:
    """Create every scheduled trip for one day (Dhaka time). Returns trips created."""
    cities = {r["slug"]: r["id"] for r in await conn.fetch("SELECT id, slug FROM cities")}
    buses = await conn.fetch("SELECT id, coach_type, layout FROM buses ORDER BY id")
    rng = random.Random(day.toordinal())
    now = datetime.now(timezone.utc)
    created = 0

    directed = []
    for a, b, hours, *fares in ROUTES:
        directed.append((a, b, hours, fares))
        directed.append((b, a, hours, fares))

    for idx, (src, dst, hours, fares) in enumerate(directed):
        for dep_idx, dep in enumerate(DEPARTURES):
            departs = datetime.combine(day, dep, tzinfo=DHAKA_TZ)
            if departs <= now:
                continue
            bus = buses[(idx * len(DEPARTURES) + dep_idx + day.toordinal()) % len(buses)]
            fare = fares[COACH_TYPES.index(bus["coach_type"])]
            trip_id = await conn.fetchval(
                """INSERT INTO trips (bus_id, origin_id, destination_id, departs_at, arrives_at,
                                      fare_bdt, boarding_point)
                   VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
                bus["id"], cities[src], cities[dst], departs,
                departs + timedelta(hours=hours), fare, rng.choice(BOARDING[src]),
            )
            seats = seats_in(bus["layout"])
            await conn.copy_records_to_table(
                "trip_seats", records=[(trip_id, s) for s in seats], columns=["trip_id", "seat_no"]
            )
            await _seed_counter_sales(conn, trip_id, seats, fare, rng)
            created += 1
    return created


async def _seed_counter_sales(conn, trip_id: int, seats: list[str], fare: int, rng: random.Random):
    """Mark some seats as already sold at a bus counter, so the demo looks realistic."""
    sold = sorted(rng.sample(seats, k=int(len(seats) * rng.uniform(0.1, 0.55))))
    if not sold:
        return
    hold_id = await conn.fetchval(
        """INSERT INTO holds (trip_id, session_id, seats, status, expires_at,
                              passenger_name, passenger_phone)
           VALUES ($1, 'counter', $2, 'confirmed', now(), 'Counter sale', '01700000000')
           RETURNING id""",
        trip_id, sold,
    )
    booking_id = await conn.fetchval(
        """INSERT INTO bookings (pnr, hold_id, trip_id, seats, passenger_name, passenger_phone,
                                 amount_bdt, channel, status)
           VALUES ($1, $2, $3, $4, 'Counter sale', '01700000000', $5, 'counter', 'confirmed')
           RETURNING id""",
        _pnr(), hold_id, trip_id, sold, fare * len(sold),
    )
    await conn.execute(
        """UPDATE trip_seats SET status = 'booked', booking_id = $3
           WHERE trip_id = $1 AND seat_no = ANY($2::text[])""",
        trip_id, sold, booking_id,
    )


async def ensure_demo_data(pool: asyncpg.Pool) -> int:
    """Idempotently make sure reference data and the next week of trips exist."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(727002)")
        await seed_reference_data(conn)
        today = datetime.now(DHAKA_TZ).date()
        created = 0
        for offset in range(DAYS_AHEAD):
            day = today + timedelta(days=offset)
            start = datetime.combine(day, time(0), tzinfo=DHAKA_TZ)
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM trips WHERE departs_at >= $1 AND departs_at < $2)",
                start, start + timedelta(days=1),
            )
            if not exists:
                created += await seed_trips_for_day(conn, day)
        return created

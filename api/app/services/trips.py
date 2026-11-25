from datetime import date, datetime, time, timedelta

import asyncpg

from app.errors import NotFound
from app.seed import DHAKA_TZ

# A seat whose hold has lapsed counts as available, even before the sweeper releases it.
SEAT_SNAPSHOT_SQL = """
SELECT seat_no,
       CASE WHEN status = 'held' AND held_until <= now() THEN 'available' ELSE status END AS status,
       CASE WHEN status = 'held' AND held_until > now() THEN hold_id END AS hold_id
FROM trip_seats
WHERE trip_id = $1
"""

TRIP_SQL = """
SELECT t.id, t.departs_at, t.arrives_at, t.fare_bdt, t.boarding_point,
       o.slug AS origin_slug, o.name AS origin, d.slug AS destination_slug, d.name AS destination,
       b.coach_no, b.coach_type, b.layout, op.name AS operator,
       (SELECT count(*) FROM trip_seats s
         WHERE s.trip_id = t.id
           AND (s.status = 'available' OR (s.status = 'held' AND s.held_until <= now())))
         AS seats_available,
       (SELECT count(*) FROM trip_seats s WHERE s.trip_id = t.id) AS seats_total
FROM trips t
JOIN cities o ON o.id = t.origin_id
JOIN cities d ON d.id = t.destination_id
JOIN buses b ON b.id = t.bus_id
JOIN operators op ON op.id = b.operator_id
"""


def trip_summary(row: asyncpg.Record) -> dict:
    return {
        "id": row["id"],
        "operator": row["operator"],
        "coach_no": row["coach_no"],
        "coach_type": row["coach_type"],
        "origin": {"slug": row["origin_slug"], "name": row["origin"]},
        "destination": {"slug": row["destination_slug"], "name": row["destination"]},
        "departs_at": row["departs_at"].isoformat(),
        "arrives_at": row["arrives_at"].isoformat(),
        "fare_bdt": row["fare_bdt"],
        "boarding_point": row["boarding_point"],
        "seats_available": row["seats_available"],
        "seats_total": row["seats_total"],
    }


async def list_cities(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch("SELECT slug, name, name_bn FROM cities ORDER BY name")
    return [dict(r) for r in rows]


async def search_trips(pool: asyncpg.Pool, origin: str, destination: str, day: date) -> list[dict]:
    start = datetime.combine(day, time(0), tzinfo=DHAKA_TZ)
    rows = await pool.fetch(
        TRIP_SQL
        + """WHERE o.slug = $1 AND d.slug = $2
               AND t.departs_at >= $3 AND t.departs_at < $4 AND t.departs_at > now()
             ORDER BY t.departs_at, t.id""",
        origin, destination, start, start + timedelta(days=1),
    )
    return [trip_summary(r) for r in rows]


async def seat_snapshot(conn: asyncpg.Pool | asyncpg.Connection, trip_id: int) -> list[dict]:
    rows = await conn.fetch(SEAT_SNAPSHOT_SQL, trip_id)
    return [
        {
            "seat_no": r["seat_no"],
            "status": r["status"],
            "hold_id": str(r["hold_id"]) if r["hold_id"] else None,
        }
        for r in rows
    ]


async def get_trip(pool: asyncpg.Pool, trip_id: int) -> dict:
    row = await pool.fetchrow(TRIP_SQL + "WHERE t.id = $1", trip_id)
    if row is None:
        raise NotFound("trip")
    return {**trip_summary(row), "layout": row["layout"], "seats": await seat_snapshot(pool, trip_id)}

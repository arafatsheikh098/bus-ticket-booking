import json

import asyncpg

SEAT_CHANNEL = "seat_events"


def seat_change(seat_no: str, status: str, hold_id=None) -> dict:
    return {"seat_no": seat_no, "status": status, "hold_id": str(hold_id) if hold_id else None}


async def publish_seats(conn: asyncpg.Connection, trip_id: int, seats: list[dict]) -> None:
    """Queue a seat-change notification. Postgres only delivers it if the transaction
    commits, so listeners never see a change that was rolled back."""
    if seats:
        payload = json.dumps({"type": "seats", "trip_id": trip_id, "seats": seats})
        await conn.execute("SELECT pg_notify($1, $2)", SEAT_CHANNEL, payload)

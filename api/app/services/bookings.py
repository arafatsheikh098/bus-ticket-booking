import asyncpg

from app.errors import AppError, NotFound
from app.events import publish_seats, seat_change
from app.services.payments import issue_refund, normalize_phone

CANCEL_CUTOFF_HOURS = 2

BOOKING_SQL = """
SELECT b.*, t.departs_at, t.arrives_at, t.boarding_point, t.fare_bdt,
       o.name AS origin, d.name AS destination, op.name AS operator,
       bus.coach_no, bus.coach_type, p.status AS payment_status,
       t.departs_at - now() > make_interval(hours => $3) AS cancellable
FROM bookings b
JOIN trips t ON t.id = b.trip_id
JOIN cities o ON o.id = t.origin_id
JOIN cities d ON d.id = t.destination_id
JOIN buses bus ON bus.id = t.bus_id
JOIN operators op ON op.id = bus.operator_id
LEFT JOIN payments p ON p.hold_id = b.hold_id
WHERE b.pnr = $1 AND b.passenger_phone = $2 AND b.channel = 'online'
"""


def booking_view(row: asyncpg.Record) -> dict:
    return {
        "pnr": row["pnr"],
        "status": row["status"],
        "seats": list(row["seats"]),
        "passenger_name": row["passenger_name"],
        "passenger_phone": row["passenger_phone"],
        "amount_bdt": row["amount_bdt"],
        "payment_status": row["payment_status"],
        "created_at": row["created_at"].isoformat(),
        "cancelled_at": row["cancelled_at"].isoformat() if row["cancelled_at"] else None,
        "cancellable": row["status"] == "confirmed" and row["cancellable"],
        "trip": {
            "id": row["trip_id"],
            "operator": row["operator"],
            "coach_no": row["coach_no"],
            "coach_type": row["coach_type"],
            "origin": row["origin"],
            "destination": row["destination"],
            "departs_at": row["departs_at"].isoformat(),
            "arrives_at": row["arrives_at"].isoformat(),
            "boarding_point": row["boarding_point"],
        },
    }


async def get_booking(pool: asyncpg.Pool, pnr: str, phone: str) -> dict:
    row = await pool.fetchrow(BOOKING_SQL, pnr.strip().upper(), normalize_phone(phone),
                              CANCEL_CUTOFF_HOURS)
    if row is None:
        # Same answer for a wrong PNR and a wrong phone, so PNRs cannot be probed.
        raise NotFound("booking")
    return booking_view(row)


async def cancel_booking(pool: asyncpg.Pool, provider, pnr: str, phone: str) -> dict:
    pnr, phone = pnr.strip().upper(), normalize_phone(phone)
    payment_id = None
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(BOOKING_SQL + " FOR UPDATE OF b", pnr, phone, CANCEL_CUTOFF_HOURS)
        if row is None:
            raise NotFound("booking")
        if row["status"] == "confirmed":
            if not row["cancellable"]:
                raise AppError(409, "too_late_to_cancel",
                               f"Tickets can be cancelled up to {CANCEL_CUTOFF_HOURS} hours "
                               "before departure")
            freed = await conn.fetch(
                """UPDATE trip_seats SET status = 'available', booking_id = NULL
                   WHERE booking_id = $1 RETURNING seat_no""",
                row["id"],
            )
            await conn.execute(
                "UPDATE bookings SET status = 'cancelled', cancelled_at = now() WHERE id = $1",
                row["id"],
            )
            payment_id = await conn.fetchval(
                """UPDATE payments
                   SET status = 'refund_pending', refund_reason = 'cancelled_by_passenger',
                       updated_at = now()
                   WHERE hold_id = $1 AND status = 'succeeded' RETURNING id""",
                row["hold_id"],
            )
            await publish_seats(conn, row["trip_id"],
                                [seat_change(r["seat_no"], "available") for r in freed])
    if payment_id:
        await issue_refund(pool, provider, payment_id)
    return await get_booking(pool, pnr, phone)

"""Seat holds.

The core rule: every change to a seat happens inside a transaction that first locks the
seat rows with SELECT ... FOR UPDATE, always in seat-number order. Two requests for the
same seat therefore queue up on the row lock, and the second one sees the first one's
result. Locking in a fixed order means two overlapping multi-seat requests can never
deadlock.
"""

import asyncpg

from app import metrics
from app.config import settings
from app.errors import AppError, NotFound
from app.events import publish_seats, seat_change

OPEN_STATUSES = ("active", "pending_payment")


class SeatConflict(AppError):
    def __init__(self, unavailable: list[str]):
        super().__init__(
            409, "seats_unavailable", "Some seats were just taken by someone else",
            unavailable=unavailable,
        )


def hold_view(hold: asyncpg.Record, payment: asyncpg.Record | None = None,
              booking: asyncpg.Record | None = None) -> dict:
    return {
        "id": str(hold["id"]),
        "trip_id": hold["trip_id"],
        "seats": sorted(hold["seats"]),
        "status": hold["status"],
        "expires_at": hold["expires_at"].isoformat(),
        "payment": {
            "status": payment["status"],
            "checkout_url": payment["checkout_url"],
            "refund_reason": payment["refund_reason"],
        } if payment else None,
        "booking": {"pnr": booking["pnr"]} if booking else None,
    }


async def lock_seats(conn: asyncpg.Connection, trip_id: int, seats) -> dict[str, asyncpg.Record]:
    rows = await conn.fetch(
        """SELECT seat_no, status, hold_id, held_until <= now() AS lapsed
           FROM trip_seats
           WHERE trip_id = $1 AND seat_no = ANY($2::text[])
           ORDER BY seat_no
           FOR UPDATE""",
        trip_id, sorted(seats),
    )
    return {r["seat_no"]: r for r in rows}


def seat_is_free_for(row: asyncpg.Record, hold_id) -> bool:
    """A seat can be taken if it is free, already ours, or held by a hold that lapsed."""
    if row["status"] == "available":
        return True
    if row["status"] == "held":
        return row["hold_id"] == hold_id or row["lapsed"]
    return False


async def release_hold_seats(conn: asyncpg.Connection, hold_id) -> list[str]:
    """Free every seat that still belongs to this hold. Seats already re-taken by
    someone else after this hold lapsed are left alone."""
    rows = await conn.fetch(
        """UPDATE trip_seats
           SET status = 'available', hold_id = NULL, held_until = NULL
           WHERE hold_id = $1 AND status = 'held'
           RETURNING seat_no""",
        hold_id,
    )
    return sorted(r["seat_no"] for r in rows)


async def close_hold(conn: asyncpg.Connection, hold: asyncpg.Record, status: str) -> None:
    released = await release_hold_seats(conn, hold["id"])
    await conn.execute(
        "UPDATE holds SET status = $2, updated_at = now() WHERE id = $1", hold["id"], status
    )
    await publish_seats(conn, hold["trip_id"], [seat_change(s, "available") for s in released])


async def set_hold(pool: asyncpg.Pool, trip_id: int, session_id: str, seats: list[str]) -> dict | None:
    """Make this session's hold on a trip cover exactly `seats`.

    Declarative on purpose: the browser sends the full selection on every click, so a
    retried or duplicated request has the same effect as one request. The 5-minute timer
    starts with the first seat and is not reset by later changes.
    """
    desired = sorted(set(seats))
    if len(desired) > settings.max_seats_per_hold:
        raise AppError(422, "too_many_seats",
                       f"You can hold at most {settings.max_seats_per_hold} seats")

    try:
        async with pool.acquire() as conn, conn.transaction():
            trip = await conn.fetchrow(
                "SELECT id, departs_at <= now() AS departed FROM trips WHERE id = $1", trip_id
            )
            if trip is None:
                raise NotFound("trip")
            if trip["departed"]:
                raise AppError(409, "trip_departed", "This bus has already left")

            hold = await conn.fetchrow(
                """SELECT *, expires_at <= now() AS lapsed FROM holds
                   WHERE trip_id = $1 AND session_id = $2 AND status = ANY($3::text[])
                   FOR UPDATE""",
                trip_id, session_id, list(OPEN_STATUSES),
            )
            if hold and hold["status"] == "pending_payment":
                raise AppError(409, "payment_in_progress",
                               "Finish or cancel the payment before changing seats")
            if hold and hold["lapsed"]:
                await close_hold(conn, hold, "expired")
                hold = None
            if hold is None and not desired:
                return None

            hold_id = hold["id"] if hold else None
            current = set(hold["seats"]) if hold else set()
            locked = await lock_seats(conn, trip_id, current | set(desired))

            unknown = [s for s in desired if s not in locked]
            if unknown:
                raise AppError(422, "unknown_seats", "These seats do not exist on this bus",
                               seats=unknown)
            taken = [s for s in desired if not seat_is_free_for(locked[s], hold_id)]
            if taken:
                holders = list({locked[s]["hold_id"] for s in taken if locked[s]["hold_id"]})
                if hold is None and holders and await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM holds WHERE id = ANY($1::uuid[]) "
                    "AND session_id = $2)", holders, session_id,
                ):
                    # Our own parallel request (a double click) got there first.
                    raise AppError(409, "concurrent_request",
                                   "Another request is already updating your seats")
                metrics.HOLD_REQUESTS.labels("conflict").inc()
                raise SeatConflict(taken)

            if hold is None:
                hold = await conn.fetchrow(
                    """INSERT INTO holds (trip_id, session_id, status, expires_at)
                       VALUES ($1, $2, 'active', now() + make_interval(secs => $3))
                       RETURNING *""",
                    trip_id, session_id, float(settings.hold_ttl_seconds),
                )
                hold_id = hold["id"]

            to_release = sorted(current - set(desired))
            to_claim = sorted(set(desired) - current)
            changes = []
            if to_release:
                await conn.execute(
                    """UPDATE trip_seats SET status = 'available', hold_id = NULL, held_until = NULL
                       WHERE trip_id = $1 AND seat_no = ANY($2::text[]) AND hold_id = $3""",
                    trip_id, to_release, hold_id,
                )
                changes += [seat_change(s, "available") for s in to_release]
            if to_claim:
                await conn.execute(
                    """UPDATE trip_seats SET status = 'held', hold_id = $3, held_until = $4
                       WHERE trip_id = $1 AND seat_no = ANY($2::text[])""",
                    trip_id, to_claim, hold_id, hold["expires_at"],
                )
                changes += [seat_change(s, "held", hold_id) for s in to_claim]

            hold = await conn.fetchrow(
                """UPDATE holds SET seats = $2, status = $3, updated_at = now()
                   WHERE id = $1 RETURNING *""",
                hold_id, desired, "active" if desired else "released",
            )
            await publish_seats(conn, trip_id, changes)
    except asyncpg.UniqueViolationError:
        # Two requests from the same session tried to open a hold at the same moment.
        raise AppError(409, "concurrent_request", "Another request is already updating your seats")

    metrics.HOLD_REQUESTS.labels("ok").inc()
    return hold_view(hold) if hold["status"] == "active" else None


async def get_hold(pool: asyncpg.Pool, hold_id, session_id: str) -> dict:
    hold = await pool.fetchrow(
        "SELECT * FROM holds WHERE id = $1 AND session_id = $2", hold_id, session_id
    )
    if hold is None:
        raise NotFound("hold")
    payment = await pool.fetchrow("SELECT * FROM payments WHERE hold_id = $1", hold_id)
    booking = await pool.fetchrow("SELECT pnr FROM bookings WHERE hold_id = $1", hold_id)
    return hold_view(hold, payment, booking)


async def get_open_hold(pool: asyncpg.Pool, trip_id: int, session_id: str) -> dict | None:
    hold = await pool.fetchrow(
        """SELECT * FROM holds WHERE trip_id = $1 AND session_id = $2
             AND status = ANY($3::text[]) AND expires_at > now()""",
        trip_id, session_id, list(OPEN_STATUSES),
    )
    if hold is None:
        return None
    payment = await pool.fetchrow("SELECT * FROM payments WHERE hold_id = $1", hold["id"])
    return hold_view(hold, payment)


async def release_hold(pool: asyncpg.Pool, hold_id, session_id: str) -> None:
    async with pool.acquire() as conn, conn.transaction():
        hold = await conn.fetchrow(
            "SELECT * FROM holds WHERE id = $1 AND session_id = $2 FOR UPDATE", hold_id, session_id
        )
        if hold is None:
            raise NotFound("hold")
        if hold["status"] == "pending_payment":
            raise AppError(409, "payment_in_progress", "A payment is in progress for this hold")
        if hold["status"] == "active":
            await close_hold(conn, hold, "released")


async def expire_lapsed_holds(pool: asyncpg.Pool, limit: int = 200) -> int:
    """Release seats of active holds whose time ran out. SKIP LOCKED lets this run while
    users are busy with their own holds, without waiting on them."""
    async with pool.acquire() as conn, conn.transaction():
        holds = await conn.fetch(
            """SELECT * FROM holds
               WHERE status = 'active' AND expires_at <= now()
               ORDER BY expires_at
               LIMIT $1
               FOR UPDATE SKIP LOCKED""",
            limit,
        )
        for hold in holds:
            await close_hold(conn, hold, "expired")
    if holds:
        metrics.HOLDS_EXPIRED.inc(len(holds))
    return len(holds)

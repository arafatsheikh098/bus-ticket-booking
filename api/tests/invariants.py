"""Whole-database consistency checks, run after every scenario."""


async def check_invariants(pool) -> None:
    problems = []

    # A booked seat belongs to exactly one confirmed booking that lists it.
    rows = await pool.fetch("""
        SELECT s.trip_id, s.seat_no FROM trip_seats s
        LEFT JOIN bookings b ON b.id = s.booking_id
        WHERE s.status = 'booked'
          AND (b.id IS NULL OR b.status <> 'confirmed' OR NOT s.seat_no = ANY(b.seats)
               OR b.trip_id <> s.trip_id)""")
    problems += [f"booked seat without matching booking: {dict(r)}" for r in rows]

    # Every seat of every confirmed booking is booked by that booking.
    rows = await pool.fetch("""
        SELECT b.pnr, seat FROM bookings b, unnest(b.seats) AS seat
        LEFT JOIN LATERAL (SELECT 1 FROM trip_seats s
                           WHERE s.trip_id = b.trip_id AND s.seat_no = seat
                             AND s.booking_id = b.id AND s.status = 'booked') ok ON true
        WHERE b.status = 'confirmed' AND ok IS NULL""")
    problems += [f"confirmed booking seat not booked: {dict(r)}" for r in rows]

    # A held seat points at an open hold that lists it, with the hold's deadline.
    rows = await pool.fetch("""
        SELECT s.trip_id, s.seat_no, h.status FROM trip_seats s
        LEFT JOIN holds h ON h.id = s.hold_id
        WHERE s.status = 'held'
          AND (h.id IS NULL OR h.status NOT IN ('active', 'pending_payment')
               OR NOT s.seat_no = ANY(h.seats) OR s.held_until <> h.expires_at)""")
    problems += [f"held seat with bad hold: {dict(r)}" for r in rows]

    # Money: a succeeded payment has a confirmed booking; a confirmed online booking
    # has a succeeded payment.
    rows = await pool.fetch("""
        SELECT p.id, p.status, b.status AS booking FROM payments p
        LEFT JOIN bookings b ON b.hold_id = p.hold_id
        WHERE (p.status = 'succeeded' AND (b.id IS NULL OR b.status <> 'confirmed'))
           OR (p.status IN ('failed', 'cancelled', 'refund_pending', 'refunded')
               AND b.status = 'confirmed')""")
    problems += [f"payment and booking disagree: {dict(r)}" for r in rows]

    # One open hold per session per trip (also enforced by an index).
    rows = await pool.fetch("""
        SELECT trip_id, session_id, count(*) FROM holds
        WHERE status IN ('active', 'pending_payment')
        GROUP BY 1, 2 HAVING count(*) > 1""")
    problems += [f"several open holds: {dict(r)}" for r in rows]

    assert not problems, "\n".join(problems)

"""Checkout, payment webhooks, reconciliation and refunds.

Rules this module follows:

* Never hold a database transaction open while calling the provider over the network.
* Every provider call that creates or moves money carries an idempotency key, so a retry
  after a timeout cannot charge or refund twice.
* Webhooks are treated as a hint that something changed. The handler then asks the
  provider for the payment's current state and acts on that, so late, duplicate and
  out-of-order webhooks all end in the same correct result.
* Lock order is always hold, then payment, then seats, so these paths cannot deadlock.
"""

import json
import logging
import re
import secrets
import uuid

import asyncpg

from app import metrics
from app.config import settings
from app.errors import AppError, NotFound
from app.events import publish_seats, seat_change
from app.services.holds import close_hold, lock_seats, release_hold_seats, seat_is_free_for
from app.services.provider import (
    ProviderError, ProviderRejected, WebhookSignatureError, verify_signature,
)

log = logging.getLogger("busticket.payments")

PHONE_RE = re.compile(r"^01[3-9]\d{8}$")
PNR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
# If the provider stays unreachable this long after a hold's deadline, release the seats
# anyway. A payment that later turns out to have succeeded is booked if the seats are still
# free, or refunded if they are not.
PROVIDER_OUTAGE_CUTOFF_SECONDS = 600.0


def normalize_phone(raw: str) -> str:
    phone = re.sub(r"[\s-]", "", raw or "")
    if phone.startswith("+880"):
        phone = "0" + phone[4:]
    elif phone.startswith("880"):
        phone = "0" + phone[3:]
    if not PHONE_RE.match(phone):
        raise AppError(422, "invalid_phone", "Enter a Bangladeshi mobile number like 01712345678")
    return phone


# ----- Checkout ------------------------------------------------------------------------

async def start_checkout(pool: asyncpg.Pool, provider, hold_id, session_id: str,
                         name: str, phone: str) -> dict:
    name = " ".join((name or "").split())
    if not 2 <= len(name) <= 60:
        raise AppError(422, "invalid_name", "Enter the passenger's name")
    phone = normalize_phone(phone)

    async with pool.acquire() as conn, conn.transaction():
        hold = await conn.fetchrow(
            """SELECT h.*, h.expires_at <= now() AS lapsed, t.fare_bdt,
                      o.name AS origin, d.name AS destination
               FROM holds h
               JOIN trips t ON t.id = h.trip_id
               JOIN cities o ON o.id = t.origin_id
               JOIN cities d ON d.id = t.destination_id
               WHERE h.id = $1 AND h.session_id = $2
               FOR UPDATE OF h""",
            hold_id, session_id,
        )
        if hold is None:
            raise NotFound("hold")
        if hold["status"] == "pending_payment":
            payment = await conn.fetchrow("SELECT * FROM payments WHERE hold_id = $1", hold_id)
            if payment["checkout_url"]:
                return {"checkout_url": payment["checkout_url"]}
            # The earlier provider call failed; retry it below with the same key.
        elif hold["status"] != "active":
            raise AppError(409, "hold_closed", f"This hold is {hold['status']}")
        else:
            if hold["lapsed"]:
                raise AppError(409, "hold_expired", "Your seats were released. Pick them again.")
            if not hold["seats"]:
                raise AppError(422, "no_seats", "Pick at least one seat")
            expires_at = await conn.fetchval(
                """UPDATE holds
                   SET status = 'pending_payment', passenger_name = $2, passenger_phone = $3,
                       expires_at = expires_at + make_interval(secs => $4), updated_at = now()
                   WHERE id = $1 RETURNING expires_at""",
                hold_id, name, phone, float(settings.payment_grace_seconds),
            )
            await conn.execute(
                "UPDATE trip_seats SET held_until = $2 WHERE hold_id = $1", hold_id, expires_at
            )
            payment = await conn.fetchrow(
                """INSERT INTO payments (hold_id, amount_bdt, status)
                   VALUES ($1, $2, 'creating') RETURNING *""",
                hold_id, hold["fare_bdt"] * len(hold["seats"]),
            )

    seats = len(hold["seats"])
    try:
        created = await provider.create_payment(
            amount=payment["amount_bdt"],
            reference=str(hold_id),
            idempotency_key=str(payment["id"]),
            callback_url=f"{settings.callback_base_url}/api/webhooks/fakepay",
            return_url=f"{settings.web_url}/booking/{hold_id}",
            description=f"{seats} seat{'s' if seats > 1 else ''}, "
                        f"{hold['origin']} to {hold['destination']}",
        )
    except (ProviderError, ProviderRejected):
        raise AppError(503, "provider_unavailable",
                       "The payment provider is not responding. Please try again.") from None

    await pool.execute(
        """UPDATE payments SET provider_ref = $2, checkout_url = $3, status = 'pending',
                               updated_at = now()
           WHERE id = $1 AND status = 'creating'""",
        payment["id"], created["id"], created["checkout_url"],
    )
    return {"checkout_url": created["checkout_url"]}


# ----- Applying the provider's view of a payment ---------------------------------------

async def _insert_booking(conn: asyncpg.Connection, hold: asyncpg.Record, amount: int):
    for _ in range(5):
        pnr = "BT" + "".join(secrets.choice(PNR_ALPHABET) for _ in range(6))
        try:
            async with conn.transaction():  # Savepoint, so a PNR clash can be retried.
                return await conn.fetchrow(
                    """INSERT INTO bookings (pnr, hold_id, trip_id, seats, passenger_name,
                                             passenger_phone, amount_bdt, status)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, 'confirmed') RETURNING *""",
                    pnr, hold["id"], hold["trip_id"], sorted(hold["seats"]),
                    hold["passenger_name"], hold["passenger_phone"], amount,
                )
        except asyncpg.UniqueViolationError as exc:
            if exc.constraint_name != "bookings_pnr_key":
                raise
    raise RuntimeError("could not generate a unique PNR")


async def _confirm_paid_hold(conn, hold, payment) -> str:
    """Money arrived. Book the seats if this hold still owns them, otherwise refund."""
    locked = await lock_seats(conn, hold["trip_id"], hold["seats"])
    if hold["seats"] and all(
        s in locked and seat_is_free_for(locked[s], hold["id"]) for s in hold["seats"]
    ):
        booking = await _insert_booking(conn, hold, payment["amount_bdt"])
        await conn.execute(
            """UPDATE trip_seats
               SET status = 'booked', booking_id = $3, hold_id = NULL, held_until = NULL
               WHERE trip_id = $1 AND seat_no = ANY($2::text[])""",
            hold["trip_id"], sorted(hold["seats"]), booking["id"],
        )
        await conn.execute(
            "UPDATE holds SET status = 'confirmed', updated_at = now() WHERE id = $1", hold["id"]
        )
        await conn.execute(
            "UPDATE payments SET status = 'succeeded', updated_at = now() WHERE id = $1",
            payment["id"],
        )
        await publish_seats(conn, hold["trip_id"],
                            [seat_change(s, "booked") for s in sorted(hold["seats"])])
        metrics.BOOKINGS.inc()
        log.info("booking %s confirmed for hold %s", booking["pnr"], hold["id"])
        return "confirmed"

    # Paid too late: the hold lapsed and someone else took at least one seat.
    released = await release_hold_seats(conn, hold["id"])
    await conn.execute(
        "UPDATE holds SET status = 'failed', updated_at = now() WHERE id = $1", hold["id"]
    )
    await conn.execute(
        """UPDATE payments SET status = 'refund_pending', refund_reason = 'seats_unavailable',
                               updated_at = now()
           WHERE id = $1""",
        payment["id"],
    )
    await publish_seats(conn, hold["trip_id"], [seat_change(s, "available") for s in released])
    log.warning("hold %s paid after its seats were taken; refunding", hold["id"])
    return "refund"


async def apply_provider_state(conn: asyncpg.Connection, hold_id, state: dict) -> str:
    """Move our records to match the provider's state. Idempotent. Returns what happened."""
    hold = await conn.fetchrow(
        "SELECT *, expires_at <= now() AS lapsed FROM holds WHERE id = $1 FOR UPDATE", hold_id
    )
    payment = await conn.fetchrow(
        "SELECT * FROM payments WHERE hold_id = $1 FOR UPDATE", hold_id
    )
    if payment["provider_ref"] is None:
        await conn.execute(
            "UPDATE payments SET provider_ref = $2, checkout_url = $3 WHERE id = $1",
            payment["id"], state["id"], state.get("checkout_url"),
        )
    status = state["status"]

    if payment["status"] in ("creating", "pending"):
        if status == "succeeded":
            return await _confirm_paid_hold(conn, hold, payment)
        if status in ("failed", "cancelled"):
            await conn.execute(
                "UPDATE payments SET status = $2, updated_at = now() WHERE id = $1",
                payment["id"], status,
            )
            if hold["status"] == "pending_payment":
                await close_hold(conn, hold, "expired" if hold["lapsed"] else "failed")
            return status
        return "pending"

    if payment["status"] == "refund_pending" and status == "refunded":
        await conn.execute(
            "UPDATE payments SET status = 'refunded', updated_at = now() WHERE id = $1",
            payment["id"],
        )
        metrics.REFUNDS.labels(payment["refund_reason"] or "unknown").inc()
        return "refunded"
    return "noop"


async def issue_refund(pool: asyncpg.Pool, provider, payment_id) -> bool:
    payment = await pool.fetchrow("SELECT * FROM payments WHERE id = $1", payment_id)
    if payment is None or payment["status"] != "refund_pending":
        return True
    try:
        try:
            state = await provider.refund(payment["provider_ref"],
                                          idempotency_key=f"{payment['id']}:refund")
        except ProviderRejected:
            state = await provider.get_payment(payment["provider_ref"])
    except ProviderError:
        log.warning("refund for payment %s failed, will retry", payment_id)
        return False
    if state and state["status"] == "refunded":
        async with pool.acquire() as conn, conn.transaction():
            await apply_provider_state(conn, payment["hold_id"], state)
        return True
    log.error("payment %s cannot be refunded, provider says %s", payment_id,
              state and state["status"])
    return False


# ----- Webhooks ------------------------------------------------------------------------

async def handle_webhook(pool: asyncpg.Pool, provider, body: bytes, signature: str | None) -> dict:
    try:
        verify_signature(settings.fakepay_webhook_secret, signature, body)
    except WebhookSignatureError as exc:
        metrics.WEBHOOKS.labels("bad_signature").inc()
        raise AppError(401, "bad_signature", str(exc)) from None
    try:
        event = json.loads(body)
        event_id, event_type, ref = event["id"], event["type"], event["data"]["id"]
        reference = event["data"]["reference"]
    except (ValueError, KeyError, TypeError):
        raise AppError(400, "bad_event", "Malformed webhook body") from None

    if await pool.fetchval("SELECT 1 FROM webhook_events WHERE event_id = $1", event_id):
        metrics.WEBHOOKS.labels("duplicate").inc()
        return {"result": "duplicate"}

    try:
        state = await provider.get_payment(ref)
    except ProviderError:
        # Answer with an error so the provider retries this webhook later.
        raise AppError(503, "provider_unavailable", "Could not confirm payment state") from None
    try:
        hold_id = uuid.UUID(reference)
    except ValueError:
        hold_id = None

    async with pool.acquire() as conn, conn.transaction():
        recorded = await conn.fetchval(
            """INSERT INTO webhook_events (event_id, event_type, provider_ref)
               VALUES ($1, $2, $3) ON CONFLICT DO NOTHING RETURNING event_id""",
            event_id, event_type, ref,
        )
        if recorded is None:
            metrics.WEBHOOKS.labels("duplicate").inc()
            return {"result": "duplicate"}
        known = hold_id and state and await conn.fetchval(
            "SELECT 1 FROM payments WHERE hold_id = $1", hold_id
        )
        if not known:
            metrics.WEBHOOKS.labels("unknown_payment").inc()
            return {"result": "unknown_payment"}
        outcome = await apply_provider_state(conn, hold_id, state)

    metrics.WEBHOOKS.labels(outcome).inc()
    if outcome == "refund":
        payment_id = await pool.fetchval("SELECT id FROM payments WHERE hold_id = $1", hold_id)
        await issue_refund(pool, provider, payment_id)
    return {"result": outcome}


# ----- Reconciliation (run by the sweeper) ---------------------------------------------

async def reconcile_overdue(pool: asyncpg.Pool, provider, limit: int = 50) -> int:
    """Settle payments whose webhook never arrived.

    Covers holds still waiting for payment after their deadline, and payments that are
    unresolved although their hold was already closed during a provider outage.
    """
    rows = await pool.fetch(
        """SELECT h.id AS hold_id, p.id AS payment_id, p.provider_ref,
                   h.expires_at <= now() - make_interval(secs => $2) AS long_overdue
            FROM holds h JOIN payments p ON p.hold_id = h.id
            WHERE p.status IN ('creating', 'pending')
              AND (h.expires_at <= now() OR h.status <> 'pending_payment')
            ORDER BY h.expires_at
            LIMIT $1""",
        limit, PROVIDER_OUTAGE_CUTOFF_SECONDS,
    )
    settled = 0
    for row in rows:
        try:
            if row["provider_ref"]:
                state = await provider.get_payment(row["provider_ref"])
            else:
                state = await provider.find_by_reference(str(row["hold_id"]))
            if state and state["status"] == "pending":
                # Close the door before giving the seats away. If the customer paid at
                # this exact moment, cancel returns "succeeded" and we book instead.
                state = await provider.cancel(state["id"])
        except (ProviderError, ProviderRejected):
            if row["long_overdue"]:
                await _release_during_outage(pool, row["hold_id"])
            continue

        async with pool.acquire() as conn, conn.transaction():
            if state is None:
                # The provider never saw this payment, so nobody was charged.
                hold = await conn.fetchrow(
                    "SELECT * FROM holds WHERE id = $1 FOR UPDATE", row["hold_id"]
                )
                await conn.execute(
                    """UPDATE payments SET status = 'failed', updated_at = now()
                       WHERE id = $1 AND status IN ('creating', 'pending')""",
                    row["payment_id"],
                )
                if hold["status"] == "pending_payment":
                    await close_hold(conn, hold, "expired")
                outcome = "failed"
            else:
                outcome = await apply_provider_state(conn, row["hold_id"], state)
        if outcome == "refund":
            await issue_refund(pool, provider, row["payment_id"])
        settled += outcome != "pending"
    return settled


async def _release_during_outage(pool: asyncpg.Pool, hold_id) -> None:
    async with pool.acquire() as conn, conn.transaction():
        hold = await conn.fetchrow("SELECT * FROM holds WHERE id = $1 FOR UPDATE", hold_id)
        if hold["status"] == "pending_payment":
            log.warning("provider unreachable; releasing seats of hold %s", hold_id)
            await close_hold(conn, hold, "expired")


async def retry_refunds(pool: asyncpg.Pool, provider, limit: int = 20) -> int:
    ids = await pool.fetch(
        """SELECT id FROM payments
           WHERE status = 'refund_pending' AND updated_at < now() - interval '5 seconds'
           ORDER BY updated_at LIMIT $1""",
        limit,
    )
    done = 0
    for row in ids:
        done += await issue_refund(pool, provider, row["id"])
    return done

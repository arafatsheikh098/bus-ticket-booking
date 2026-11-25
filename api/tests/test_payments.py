import time

import pytest

from app.services import payments
from app.services.provider import WebhookSignatureError, verify_signature
from tests.conftest import expire_hold, session, signed_webhook
from tests.fakepay_sign import sign

PASSENGER = {"name": "Arafat Sheikh", "phone": "01712345678"}


async def hold_and_checkout(client, trip, seats, who="a"):
    h = session(who)
    hold = (await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": seats},
                             headers=h)).json()
    r = await client.post(f"/api/holds/{hold['id']}/checkout", json=PASSENGER, headers=h)
    assert r.status_code == 200, r.text
    return hold, r.json()["checkout_url"]


async def send_webhook(client, provider, ref, event_type=None, **kw):
    payment = provider.payments[ref]
    body, headers = signed_webhook(payment, event_type or f"payment.{payment['status']}", **kw)
    return await client.post("/api/webhooks/fakepay", content=body, headers=headers)


async def hold_status(client, hold_id, who="a"):
    return (await client.get(f"/api/holds/{hold_id}", headers=session(who))).json()


# ----- Signatures ----------------------------------------------------------------------

def test_signature_accepts_valid_and_rejects_tampering():
    body = b'{"id":"evt_1"}'
    verify_signature("s3cret", sign("s3cret", body), body)
    with pytest.raises(WebhookSignatureError):
        verify_signature("s3cret", sign("s3cret", body), body + b" ")
    with pytest.raises(WebhookSignatureError):
        verify_signature("s3cret", sign("other", body), body)
    with pytest.raises(WebhookSignatureError):
        verify_signature("s3cret", None, body)
    with pytest.raises(WebhookSignatureError):
        verify_signature("s3cret", "garbage", body)


def test_signature_rejects_replayed_old_webhook():
    body = b'{"id":"evt_1"}'
    old = int(time.time()) - 3600
    with pytest.raises(WebhookSignatureError, match="too old"):
        verify_signature("s3cret", sign("s3cret", body, old), body)


async def test_unsigned_webhook_is_rejected(client, provider, trip):
    _, url = await hold_and_checkout(client, trip, ["A1"])
    ref = provider.ref_for(url)
    provider.complete(ref)
    r = await send_webhook(client, provider, ref, secret="wrong-secret")
    assert r.status_code == 401


# ----- Checkout ------------------------------------------------------------------------

async def test_checkout_extends_deadline_once_and_is_idempotent(client, provider, trip):
    hold = (await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": ["A1", "A2"]},
                             headers=session("a"))).json()
    url = f"/api/holds/{hold['id']}/checkout"
    first = (await client.post(url, json=PASSENGER, headers=session("a"))).json()
    second = (await client.post(url, json=PASSENGER, headers=session("a"))).json()
    assert first == second
    assert provider.calls.count("create") == 1
    after = await hold_status(client, hold["id"])
    assert after["status"] == "pending_payment"
    assert after["expires_at"] > hold["expires_at"]
    payment = provider.payments[provider.ref_for(first["checkout_url"])]
    assert payment["amount"] == 2 * trip["fare"]


async def test_cannot_change_seats_during_payment(client, trip):
    await hold_and_checkout(client, trip, ["A1"])
    r = await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": ["A2"]},
                         headers=session("a"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "payment_in_progress"


async def test_checkout_validates_passenger(client, trip):
    hold = (await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": ["A1"]},
                             headers=session("a"))).json()
    url = f"/api/holds/{hold['id']}/checkout"
    r = await client.post(url, json={"name": "X", "phone": "01712345678"}, headers=session("a"))
    assert r.json()["error"]["code"] == "invalid_name"
    r = await client.post(url, json={"name": "Ok Name", "phone": "12345"}, headers=session("a"))
    assert r.json()["error"]["code"] == "invalid_phone"
    r = await client.post(url, json=PASSENGER, headers=session("b"))
    assert r.status_code == 404


async def test_checkout_on_expired_hold_is_refused(client, pool, trip):
    hold = (await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": ["A1"]},
                             headers=session("a"))).json()
    await expire_hold(pool, hold["id"])
    r = await client.post(f"/api/holds/{hold['id']}/checkout", json=PASSENGER,
                          headers=session("a"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "hold_expired"


async def test_provider_outage_at_checkout_can_be_retried(client, provider, trip):
    hold = (await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": ["A1"]},
                             headers=session("a"))).json()
    url = f"/api/holds/{hold['id']}/checkout"
    provider.down = True
    r = await client.post(url, json=PASSENGER, headers=session("a"))
    assert r.status_code == 503
    provider.down = False
    r = await client.post(url, json=PASSENGER, headers=session("a"))
    assert r.status_code == 200
    assert len(provider.payments) == 1


# ----- Webhooks ------------------------------------------------------------------------

async def test_paid_hold_becomes_booking(client, provider, trip):
    hold, url = await hold_and_checkout(client, trip, ["A1", "A2"])
    ref = provider.ref_for(url)
    provider.complete(ref)
    r = await send_webhook(client, provider, ref)
    assert r.json() == {"result": "confirmed"}
    status = await hold_status(client, hold["id"])
    assert status["status"] == "confirmed" and status["payment"]["status"] == "succeeded"
    pnr = status["booking"]["pnr"]
    ticket = (await client.get(f"/api/bookings/{pnr}", params={"phone": "+8801712345678"})).json()
    assert ticket["seats"] == ["A1", "A2"] and ticket["amount_bdt"] == 2 * trip["fare"]


async def test_duplicate_webhook_is_processed_once(client, provider, trip):
    _, url = await hold_and_checkout(client, trip, ["A1"])
    ref = provider.ref_for(url)
    provider.complete(ref)
    first = await send_webhook(client, provider, ref, event_id="evt_same")
    again = await send_webhook(client, provider, ref, event_id="evt_same")
    assert first.json()["result"] == "confirmed"
    assert again.json()["result"] == "duplicate"


async def test_webhook_payload_is_not_trusted_over_provider_state(client, provider, trip):
    """A forged-but-signed or stale 'succeeded' event cannot book while the provider
    still says the payment is pending."""
    hold, url = await hold_and_checkout(client, trip, ["A1"])
    ref = provider.ref_for(url)
    stale = dict(provider.payments[ref], status="succeeded")
    body, headers = signed_webhook(stale, "payment.succeeded")
    r = await client.post("/api/webhooks/fakepay", content=body, headers=headers)
    assert r.json()["result"] == "pending"
    assert (await hold_status(client, hold["id"]))["status"] == "pending_payment"


async def test_declined_payment_releases_seats(client, provider, trip):
    hold, url = await hold_and_checkout(client, trip, ["A1"])
    ref = provider.ref_for(url)
    provider.complete(ref, "failed")
    assert (await send_webhook(client, provider, ref)).json()["result"] == "failed"
    status = await hold_status(client, hold["id"])
    assert status["status"] == "failed"
    seats = (await client.get(f"/api/trips/{trip['id']}")).json()["seats"]
    assert next(s for s in seats if s["seat_no"] == "A1")["status"] == "available"


async def test_late_payment_after_seat_was_taken_is_refunded(client, pool, provider, trip):
    hold, url = await hold_and_checkout(client, trip, ["A1", "A2"])
    await expire_hold(pool, hold["id"])
    # Someone else grabs A2 while the first passenger is still on the payment page.
    r = await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": ["A2"]},
                         headers=session("b"))
    assert r.status_code == 200
    ref = provider.ref_for(url)
    provider.complete(ref)
    assert (await send_webhook(client, provider, ref)).json()["result"] == "refund"
    status = await hold_status(client, hold["id"])
    assert status["status"] == "failed"
    assert status["payment"] == {"status": "refunded", "checkout_url": url,
                                 "refund_reason": "seats_unavailable"}
    assert provider.payments[ref]["status"] == "refunded"
    seats = {s["seat_no"]: s["status"] for s in
             (await client.get(f"/api/trips/{trip['id']}")).json()["seats"]}
    assert seats["A1"] == "available" and seats["A2"] == "held"


async def test_late_payment_still_books_if_seats_are_free(client, pool, provider, trip):
    hold, url = await hold_and_checkout(client, trip, ["A1"])
    await expire_hold(pool, hold["id"])
    ref = provider.ref_for(url)
    provider.complete(ref)
    assert (await send_webhook(client, provider, ref)).json()["result"] == "confirmed"


# ----- Reconciliation ------------------------------------------------------------------

async def test_lost_webhook_is_recovered_by_reconciliation(client, pool, provider, trip):
    hold, url = await hold_and_checkout(client, trip, ["A1"])
    provider.complete(provider.ref_for(url))  # Paid, but the webhook never arrives.
    await expire_hold(pool, hold["id"])
    assert await payments.reconcile_overdue(pool, provider) == 1
    assert (await hold_status(client, hold["id"]))["status"] == "confirmed"


async def test_abandoned_payment_is_cancelled_and_seats_released(client, pool, provider, trip):
    hold, url = await hold_and_checkout(client, trip, ["A1"])
    await expire_hold(pool, hold["id"])
    await payments.reconcile_overdue(pool, provider)
    assert provider.payments[provider.ref_for(url)]["status"] == "cancelled"
    status = await hold_status(client, hold["id"])
    assert status["status"] == "expired" and status["payment"]["status"] == "cancelled"


async def test_payment_the_provider_never_saw_is_failed(client, pool, provider, trip):
    hold = (await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": ["A1"]},
                             headers=session("a"))).json()
    provider.down = True
    await client.post(f"/api/holds/{hold['id']}/checkout", json=PASSENGER, headers=session("a"))
    provider.down = False
    await expire_hold(pool, hold["id"])
    await payments.reconcile_overdue(pool, provider)
    status = await hold_status(client, hold["id"])
    assert status["status"] == "expired" and status["payment"]["status"] == "failed"


async def test_long_provider_outage_releases_seats_then_settles(client, pool, provider, trip):
    hold, url = await hold_and_checkout(client, trip, ["A1"])
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE holds SET expires_at = now() - interval '11 minutes' WHERE id = $1::uuid",
            hold["id"])
        await conn.execute(
            """UPDATE trip_seats SET held_until = (SELECT expires_at FROM holds WHERE id = $1::uuid)
               WHERE hold_id = $1::uuid""", hold["id"])
    provider.down = True
    await payments.reconcile_overdue(pool, provider)
    assert (await hold_status(client, hold["id"]))["status"] == "expired"
    # The provider comes back and reports the customer had paid; seats are still free.
    provider.down = False
    provider.complete(provider.ref_for(url))
    await payments.reconcile_overdue(pool, provider)
    assert (await hold_status(client, hold["id"]))["status"] == "confirmed"


async def test_refund_retries_after_provider_outage(client, pool, provider, trip):
    hold, url = await hold_and_checkout(client, trip, ["A1"])
    await expire_hold(pool, hold["id"])
    await client.put(f"/api/trips/{trip['id']}/hold", json={"seats": ["A1"]}, headers=session("b"))
    ref = provider.ref_for(url)
    provider.complete(ref)
    body, headers = signed_webhook(provider.payments[ref], "payment.succeeded")
    original_refund = provider.refund

    async def failing_refund(*a, **k):
        provider.down = True
        try:
            return await original_refund(*a, **k)
        finally:
            provider.down = False

    provider.refund = failing_refund
    await client.post("/api/webhooks/fakepay", content=body, headers=headers)
    assert (await hold_status(client, hold["id"]))["payment"]["status"] == "refund_pending"
    provider.refund = original_refund
    await pool.execute("UPDATE payments SET updated_at = now() - interval '1 minute'")
    assert await payments.retry_refunds(pool, provider) == 1
    assert (await hold_status(client, hold["id"]))["payment"]["status"] == "refunded"


# ----- Tickets -------------------------------------------------------------------------

async def test_cancel_ticket_frees_seats_and_refunds(client, provider, trip):
    hold, url = await hold_and_checkout(client, trip, ["A1"])
    ref = provider.ref_for(url)
    provider.complete(ref)
    await send_webhook(client, provider, ref)
    pnr = (await hold_status(client, hold["id"]))["booking"]["pnr"]
    r = await client.post(f"/api/bookings/{pnr}/cancel", json={"phone": "01712345678"})
    body = r.json()
    assert body["status"] == "cancelled" and body["payment_status"] == "refunded"
    again = await client.post(f"/api/bookings/{pnr}/cancel", json={"phone": "01712345678"})
    assert again.json()["status"] == "cancelled"
    assert provider.calls.count("refund") == 1


async def test_ticket_lookup_needs_matching_phone(client, provider, trip):
    hold, url = await hold_and_checkout(client, trip, ["A1"])
    ref = provider.ref_for(url)
    provider.complete(ref)
    await send_webhook(client, provider, ref)
    pnr = (await hold_status(client, hold["id"]))["booking"]["pnr"]
    r = await client.get(f"/api/bookings/{pnr}", params={"phone": "01812345678"})
    assert r.status_code == 404

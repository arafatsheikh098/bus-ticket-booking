"""Eid-rush load test: many people fight over one bus while the payment provider misbehaves.

Phase 1: N users all try to grab 1-2 random seats on the same trip at the same moment.
Phase 2: every user who got seats checks out. FakePay is set to duplicate webhooks,
         drop some entirely, and decline some payments. Some users pay late, after their
         hold ran out.
Phase 3: wait for everything to settle (including reconciliation of lost webhooks), then
         check the database: no seat sold twice, and money matches bookings exactly.

Run against the docker compose stack with short timers so reconciliation is quick:

    HOLD_TTL_SECONDS=20 PAYMENT_GRACE_SECONDS=10 RATE_LIMITS_ENABLED=false docker compose up -d --build
    python loadtest/race.py --users 400
"""

import argparse
import asyncio
import random
import statistics
import time
import uuid

import asyncpg
import httpx

API = "http://localhost:8000"
FAKEPAY = "http://localhost:8001"
DB = "postgresql://busticket:busticket@localhost:5433/busticket"
FAKEPAY_KEY = "sk_test_dev_change_me"


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


async def pick_hot_trip(client) -> dict:
    cities = [("dhaka", "chattogram"), ("dhaka", "coxs-bazar"), ("dhaka", "sylhet")]
    for days in range(1, 7):
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + days * 86400 + 6 * 3600))
        for a, b in cities:
            trips = (await client.get(f"{API}/api/trips", params={"from": a, "to": b, "date": day})).json()
            trips = [t for t in trips if t["seats_available"] >= 20]
            if trips:
                return max(trips, key=lambda t: t["seats_available"])
    raise SystemExit("no trip with enough free seats; reseed the database")


async def main(users: int, decline_rate: float, late_rate: float, drop_rate: float):
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=200)
    async with httpx.AsyncClient(timeout=30, limits=limits) as client:
        trip = await pick_hot_trip(client)
        detail = (await client.get(f"{API}/api/trips/{trip['id']}")).json()
        free = [s["seat_no"] for s in detail["seats"] if s["status"] == "available"]
        print(f"Trip {trip['id']}: {trip['origin']['name']} to {trip['destination']['name']}, "
              f"{len(free)} free seats, {users} users racing\n")

        await client.put(f"{FAKEPAY}/admin/chaos", headers={"Authorization": f"Bearer {FAKEPAY_KEY}"},
                         json={"webhook_delay_ms": [50, 2500], "duplicate_rate": 0.5,
                               "drop_rate": drop_rate, "api_latency_ms": 0, "api_error_rate": 0})

        # Open the HTTP connections first. Otherwise the race also measures how fast the
        # machine can accept 200 new TCP connections at once (slow through Docker Desktop
        # on macOS), not how fast the booking logic is.
        await asyncio.gather(*[client.get(f"{API}/api/health") for _ in range(400)])

        # ----- Phase 1: the race --------------------------------------------------------
        rng = random.Random(1)
        wants = [rng.sample(free, k=rng.choice([1, 1, 2])) for _ in range(users)]
        sessions = [f"load-{uuid.uuid4().hex[:16]}" for _ in range(users)]
        latencies, results = [], {}
        start_gate = asyncio.Event()

        async def grab(i):
            await start_gate.wait()
            t0 = time.perf_counter()
            r = await client.put(f"{API}/api/trips/{trip['id']}/hold", json={"seats": wants[i]},
                                 headers={"X-Session-Id": sessions[i]})
            latencies.append((time.perf_counter() - t0) * 1000)
            results[i] = r

        tasks = [asyncio.create_task(grab(i)) for i in range(users)]
        await asyncio.sleep(0.2)
        t_start = time.perf_counter()
        start_gate.set()
        await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - t_start

        winners = {i: r.json() for i, r in results.items() if r.status_code == 200}
        codes = {}
        for r in results.values():
            codes[r.status_code] = codes.get(r.status_code, 0) + 1
        held_seats = [s for h in winners.values() for s in h["seats"]]
        print("Phase 1: race for seats")
        print(f"  requests        {users} in {elapsed:.2f}s ({users / elapsed:.0f} req/s)")
        print(f"  responses       {dict(sorted(codes.items()))}")
        print(f"  latency ms      p50 {pct(latencies, 50):.0f}  p95 {pct(latencies, 95):.0f}  "
              f"p99 {pct(latencies, 99):.0f}  max {max(latencies):.0f}")
        print(f"  seats held      {len(held_seats)} by {len(winners)} users, "
              f"duplicates: {len(held_seats) - len(set(held_seats))}")
        assert len(held_seats) == len(set(held_seats)), "a seat was held twice"

        # ----- Phase 2: checkout under chaos -------------------------------------------
        fp = httpx.AsyncClient(base_url=FAKEPAY, headers={"Authorization": f"Bearer {FAKEPAY_KEY}"},
                               timeout=30)
        plan = {}
        for i in winners:
            roll = rng.random()
            plan[i] = "decline" if roll < decline_rate else "late" if roll < decline_rate + late_rate else "pay"

        async def checkout(i):
            hold = winners[i]
            r = await client.post(f"{API}/api/holds/{hold['id']}/checkout",
                                  json={"name": f"Passenger {i}", "phone": f"017{i:08d}"},
                                  headers={"X-Session-Id": sessions[i]})
            r.raise_for_status()
            ref = r.json()["checkout_url"].rsplit("/", 1)[1]
            if plan[i] == "late":
                await asyncio.sleep(1)
                return ref
            await asyncio.sleep(rng.random() * 2)
            outcome = "failed" if plan[i] == "decline" else "succeeded"
            await fp.post(f"/v1/payments/{ref}/simulate", params={"outcome": outcome})
            return ref

        refs = dict(zip(winners, await asyncio.gather(*[checkout(i) for i in winners])))
        late = [i for i in winners if plan[i] == "late"]
        print("\nPhase 2: checkout with chaos (50% duplicate webhooks, "
              f"{drop_rate:.0%} dropped webhooks)")
        print(f"  plan            {sum(p == 'pay' for p in plan.values())} pay, "
              f"{sum(p == 'decline' for p in plan.values())} decline, {len(late)} pay after deadline")

        if late:
            # The first late payer waits until just past their deadline. A rival grabs their
            # seats in that moment and then the payment lands: it must end in a refund, never
            # a double booking. The other late payers pay a few seconds later, by which time
            # reconciliation has usually cancelled their payment first. Both endings are valid.
            conn = await asyncpg.connect(DB)
            try:
                deadlines = {i: await conn.fetchval("SELECT expires_at FROM holds WHERE id = $1::uuid",
                                                    winners[i]["id"]) for i in late}
            finally:
                await conn.close()
            first = late[0]
            wait = max(0.0, deadlines[first].timestamp() - time.time()) + 0.1
            print(f"  waiting {wait:.0f}s for the first late payer's deadline to pass…")
            await asyncio.sleep(wait)
            rival = await client.put(f"{API}/api/trips/{trip['id']}/hold",
                                     json={"seats": winners[first]["seats"]},
                                     headers={"X-Session-Id": "rival-" + uuid.uuid4().hex[:12]})
            print(f"  rival grabs {winners[first]['seats']} right after the deadline: "
                  f"HTTP {rival.status_code}")
            await fp.post(f"/v1/payments/{refs[first]}/simulate", params={"outcome": "succeeded"})
            await asyncio.sleep(max(0.0, max(d.timestamp() for d in deadlines.values()) - time.time()) + 3)
            for i in late[1:]:
                await fp.post(f"/v1/payments/{refs[i]}/simulate", params={"outcome": "succeeded"})

        # ----- Phase 3: settle and verify ----------------------------------------------
        print("\nPhase 3: waiting for webhooks and reconciliation to settle…")
        t0 = time.time()
        conn = await asyncpg.connect(DB)
        try:
            while True:
                open_ = await conn.fetchval(
                    """SELECT count(*) FROM payments p JOIN holds h ON h.id = p.hold_id
                       WHERE h.id = ANY($1::uuid[])
                         AND p.status IN ('creating', 'pending', 'refund_pending')""",
                    [h["id"] for h in winners.values()])
                if open_ == 0:
                    break
                if time.time() - t0 > 240:
                    raise SystemExit(f"{open_} payments still unsettled after 4 minutes")
                await asyncio.sleep(2)
            print(f"  settled in {time.time() - t0:.0f}s")

            ids = [h["id"] for h in winners.values()]
            summary = await conn.fetch(
                """SELECT h.status AS hold, p.status AS payment, count(*)
                   FROM holds h JOIN payments p ON p.hold_id = h.id
                   WHERE h.id = ANY($1::uuid[]) GROUP BY 1, 2 ORDER BY 3 DESC""", ids)
            for row in summary:
                print(f"  {row['count']:>5}  hold {row['hold']:<10} payment {row['payment']}")

            double = await conn.fetchval(
                """SELECT count(*) FROM (
                     SELECT b.trip_id, seat FROM bookings b, unnest(b.seats) seat
                     WHERE b.status = 'confirmed' AND b.trip_id = $1
                     GROUP BY 1, 2 HAVING count(*) > 1) x""", trip["id"])
            mismatched = await conn.fetchval(
                """SELECT count(*) FROM payments p LEFT JOIN bookings b ON b.hold_id = p.hold_id
                   WHERE (p.status = 'succeeded' AND (b.id IS NULL OR b.status <> 'confirmed'))
                      OR (p.status <> 'succeeded' AND b.status = 'confirmed')""")
            seat_rows = await conn.fetchval(
                """SELECT count(*) FROM trip_seats s JOIN bookings b ON b.id = s.booking_id
                   WHERE s.trip_id = $1 AND b.channel = 'online'""", trip["id"])
            booked_seats = await conn.fetchval(
                """SELECT coalesce(sum(cardinality(seats)), 0) FROM bookings
                   WHERE trip_id = $1 AND status = 'confirmed' AND channel = 'online'""", trip["id"])
            charged = await conn.fetchval(
                """SELECT coalesce(sum(p.amount_bdt), 0) FROM payments p
                   JOIN holds h ON h.id = p.hold_id WHERE h.trip_id = $1 AND p.status = 'succeeded'""",
                trip["id"])
            sold = await conn.fetchval(
                """SELECT coalesce(sum(amount_bdt), 0) FROM bookings
                   WHERE trip_id = $1 AND status = 'confirmed' AND channel = 'online'""", trip["id"])
            dup_events = await conn.fetchval("SELECT count(*) FROM webhook_events")
        finally:
            await conn.close()

        log = (await fp.get("/admin/webhooks", params={"limit": 100000})).json()
        await fp.aclose()
        sent = sum(1 for e in log if e["attempt"] >= 1)
        dropped = sum(1 for e in log if e["attempt"] == 0)
        print("\nVerification")
        print(f"  webhook deliveries sent {sent}, dropped {dropped}, unique events processed {dup_events}")
        print(f"  seats sold twice                {double}")
        print(f"  payment/booking mismatches      {mismatched}")
        print(f"  booked seat rows vs tickets     {seat_rows} vs {booked_seats}")
        print(f"  money charged vs tickets sold   ৳{charged:,} vs ৳{sold:,}")
        ok = double == 0 and mismatched == 0 and seat_rows == booked_seats and charged == sold
        print("\nRESULT:", "PASS" if ok else "FAIL")
        raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--users", type=int, default=400)
    ap.add_argument("--decline-rate", type=float, default=0.15)
    ap.add_argument("--late-rate", type=float, default=0.1)
    ap.add_argument("--drop-rate", type=float, default=0.2)
    args = ap.parse_args()
    asyncio.run(main(args.users, args.decline_rate, args.late_rate, args.drop_rate))

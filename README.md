# Bus Ticket Booking

Intercity bus ticket booking for Bangladesh, built around one hard problem: **two people
click the same seat at the same moment, and exactly one of them gets it.**

Seats show up live on every open seat map. A seat you pick is held for you for five
minutes while you pay. Payments go through a sandbox provider that is deliberately
unreliable (late, duplicated and lost webhooks), and the system still never sells a seat
twice or keeps money for a seat you did not get.

![Live seat map](docs/screenshots/live-seat-map.png)

| Picking seats | Sandbox payment | Ticket |
|---|---|---|
| ![Seats selected with countdown](docs/screenshots/seat-selected.png) | ![FakePay checkout](docs/screenshots/fakepay.png) | ![Ticket](docs/screenshots/ticket.png) |

## What it does

- **Search** buses between seven cities across a rolling week, filtered by coach type.
- **Live seat map.** Seats held or sold by other people change on your screen within
  milliseconds, through WebSockets.
- **Five-minute holds.** Pick up to four seats; they are locked to you while you pay, and
  released automatically if you don't.
- **Checkout** on FakePay, a sandbox payment provider included in this repo.
- **Tickets** with a PNR, lookup by PNR and phone number, and cancellation with refund up to
  two hours before departure.
- **Light and dark themes.** A switch in the header picks light, dark, or your system
  setting. The choice is remembered and applied before the page draws, so it never flashes.

## The interesting parts

| Problem | How it is solved |
|---|---|
| Two people click one seat at once | The seat is one Postgres row. Holding it takes `SELECT … FOR UPDATE` on that row, so requests queue and the second sees the first one's result. |
| Group bookings deadlocking each other | Seat rows are always locked in seat-number order, so two overlapping requests can never wait on each other in a circle. A hold gets all its seats or none. |
| A hot bus on Eid eve | Seats already visibly taken are rejected before any lock is taken, so only plausible winners queue for row locks. |
| Holds that are never paid | A sweeper releases lapsed holds every 2 seconds. A lapsed seat can also be taken immediately, before the sweeper runs. |
| Running several API instances | Only one instance runs the sweeper, chosen with a Postgres advisory lock. Live updates travel over Postgres `LISTEN/NOTIFY`, so a seat changed on one instance reaches viewers on another without Redis. |
| Duplicate webhooks | Every processed event id is stored, so duplicates are ignored. |
| Out-of-order or forged webhooks | Webhooks are HMAC-signed with a timestamp. Even a valid one is treated as a hint: the API asks the provider for the payment's real state and acts on that. |
| Lost webhooks | Reconciliation asks the provider directly once a hold's deadline passes, and cancels unpaid payments before releasing the seats. |
| Money arriving after the seat was resold | The payment is refunded automatically, with an idempotency key so a retry can never refund twice. |
| Provider down during checkout | Checkout can be retried safely: the same idempotency key returns the same payment, never a second one. |
| Scripts and guessing | Token-bucket rate limits: seat clicks per session and per IP, checkout per session, and ticket lookups per IP so PNRs can't be brute-forced. |
| Slow browser on a bad connection | Each viewer has a bounded queue. If it falls behind, its backlog is dropped and it gets one fresh snapshot instead. |

The reasoning behind each decision, and the trade-offs, are in [docs/DESIGN.md](docs/DESIGN.md).

## Architecture

```mermaid
flowchart LR
    B1[Browser] -- REST + WebSocket --> N[nginx]
    B2[Another browser] -- WebSocket --> N
    N --> A[FastAPI]
    A -- row locks, LISTEN/NOTIFY --> P[(PostgreSQL)]
    A -- create, check, cancel, refund --> F[FakePay sandbox]
    F -- signed webhooks --> A
    B1 -. checkout page .-> F
    S[Sweeper: expiry, reconciliation, refund retries] --- A
```

| Part | Stack |
|---|---|
| `api/` | Python 3.13, FastAPI, asyncpg, raw SQL, Prometheus metrics |
| `fakepay/` | FastAPI app that behaves like a hosted-checkout provider, with chaos settings |
| `web/` | React 19, TypeScript, Vite, plain CSS with light, dark and system themes |
| `loadtest/` | asyncio + httpx race and chaos test |
| Infra | Docker Compose, nginx (WebSocket proxy), GitHub Actions |

## Run it

You need Docker.

```bash
docker compose up --build
```

Open <http://localhost:8080>. Open the same bus in two browser windows (or one private
window) to watch seats change live.

| Service | URL |
|---|---|
| Website | http://localhost:8080 |
| API docs | http://localhost:8000/docs |
| Metrics | http://localhost:8000/metrics |
| FakePay | http://localhost:8001/docs |

Make FakePay misbehave while you use the site:

```bash
curl -X PUT localhost:8001/admin/chaos \
  -H "Authorization: Bearer sk_test_dev_change_me" -H "Content-Type: application/json" \
  -d '{"webhook_delay_ms":[2000,8000],"duplicate_rate":0.5,"drop_rate":0.3,"api_latency_ms":0,"api_error_rate":0}'
```

### Develop without Docker for the app

Run each in its own terminal, from the repo root:

```bash
docker compose up -d db                                   # Postgres on :5433
python3 -m venv api/.venv && api/.venv/bin/pip install -r api/requirements-dev.txt python-multipart
cd api && .venv/bin/uvicorn app.main:app --reload         # API on :8000
cd fakepay && ../api/.venv/bin/uvicorn fakepay.app:app --port 8001
cd web && npm install && npm run dev                      # site on :5173
```

## Tests

```bash
docker compose up -d db
cd api && .venv/bin/pytest -q
```

39 tests run against a real Postgres. Every test ends with a whole-database consistency
check: no booked seat without a matching ticket, no held seat pointing at a closed hold,
no payment that disagrees with its booking. Highlights:

- 100 sessions grab the same seat at once: exactly one wins, 99 get a clean conflict.
- 60 overlapping group bookings at once: no partial holds, no deadlocks.
- A random storm of 25 users picking, changing and dropping seats.
- Duplicate, forged, late and lost webhooks; a provider outage at checkout and during
  refunds; payment arriving after the seat was resold.
- Live updates reach viewers of the right trip, and a rolled-back change is never broadcast.

### Load test

`loadtest/race.py` sends 400 users at one bus at the same instant. Everyone who wins
then pays, while FakePay duplicates half of all webhooks and drops one in five.
Some users decline, and some pay after their hold has run out while a rival takes their
seat.

```bash
HOLD_TTL_SECONDS=20 PAYMENT_GRACE_SECONDS=10 RATE_LIMITS_ENABLED=false docker compose up -d --build
api/.venv/bin/python loadtest/race.py --users 400
```

Result on an M2 Pro laptop, everything in Docker Desktop:

```
Phase 1: race for seats
  requests        400 in 2.45s (164 req/s)
  responses       {200: 24, 409: 376}
  latency ms      p50 1200  p95 2135  p99 2307
  seats held      28 by 24 users, duplicates: 0

Phase 3: settled
     18  hold confirmed  payment succeeded
      4  hold expired    payment cancelled
      1  hold failed     payment failed
      1  hold failed     payment refunded      <- paid after the seat was resold

Verification
  webhook deliveries sent 30, dropped 7, unique events processed 18
  seats sold twice                0
  payment/booking mismatches      0
  money charged vs tickets sold   ৳23,100 vs ৳23,100
RESULT: PASS
```

Rate limits are switched off for this test, because all 400 users come from one IP
address. The latency is for the worst case on purpose: hundreds of people fighting over the same 25
seats, so most requests wait their turn for the same few rows.

## Limits and next steps

- **Guest sessions, not accounts.** The browser keeps a random session id. Real users would
  sign in with phone OTP.
- **Simulated payments only.** FakePay stands in for a real gateway. A real integration
  would keep the same flow: signed webhooks, idempotency keys and reconciliation.
- **One database.** Every seat lock lives in one Postgres. For national scale, trips could
  be partitioned across databases by route, since a booking never spans two trips.
- **Client clock for the countdown.** The timer uses the browser clock, so a badly wrong
  clock shows the wrong time left. The server's deadline is still what counts.
- **Rate limits are per process.** Each API instance counts on its own. With several
  instances, a shared limit would move to Redis or the API gateway.
- **Ticket cancellation needs only the PNR and phone number.** Lookups are rate limited per
  IP to stop guessing, but a real system would confirm with an SMS code.
- **Cancellation fees.** Refunds are always in full. Real operators deduct a fee that
  depends on how close departure is.

Operators, coaches and timetables are fictional demo data.

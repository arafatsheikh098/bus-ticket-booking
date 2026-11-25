# Design notes

Why this project is built the way it is, written as answers to the questions an interviewer
would ask. File references point at the code that implements each idea.

## 1. Data model

```
trips ──< trip_seats >── holds ──1 payments
                 │
                 └──── bookings
```

- `trip_seats` has **one row per physical seat per trip**, with a `status` of
  `available`, `held` or `booked`. Primary key `(trip_id, seat_no)`.
- A `CHECK` constraint ties the columns together: a held seat must have a `hold_id` and a
  deadline, and a booked seat must have a `booking_id` and nothing else
  (`api/app/migrations/001_init.sql`).
- `holds` records who is holding which seats and until when. A partial unique index
  allows only one open hold per session per trip.
- `payments` has exactly one row per hold, and `webhook_events` has one row per event
  already processed.

**Why one row per seat, rather than a list of seats on the booking?** It makes double
booking impossible by construction. A row has one `booking_id`, so two bookings cannot own
the same seat. It is also what makes row-level locking work: the lock is on the exact thing
being fought over.

## 2. The race: two people, one seat

`api/app/services/holds.py`, `set_hold`

```sql
SELECT seat_no, status, hold_id, held_until <= now() AS lapsed
FROM trip_seats
WHERE trip_id = $1 AND seat_no = ANY($2)
ORDER BY seat_no
FOR UPDATE
```

1. Lock the wanted seat rows.
2. Check that each one is available, already ours, or held by a hold that has lapsed.
3. If any is not, roll back and return `409` with the list of unavailable seats.
4. Otherwise update the rows and commit.

When two transactions want the same row, the second blocks at `FOR UPDATE` until the
first commits. Postgres then hands it the **new** version of the row, which now says
`held`, so the check fails and it gets a 409. This runs at the default isolation level,
READ COMMITTED, and nothing retries.

**Why not `SERIALIZABLE`?** It would also be correct, but losers get serialization
failures that the code must catch and retry. With explicit row locks, the loser simply
reads the winner's result and gets a clean answer. The locking is also easy to explain.

**Why not optimistic locking with a version column?** That works well when conflicts are
rare. On a popular bus, conflicts are the normal case, and optimistic locking turns every
conflict into a retry loop.

**Why not Redis `SETNX`?** It would put the source of truth for seats outside the
database that stores the bookings. Keeping the two consistent then becomes the problem
you have to solve. Postgres already gives atomic row locks next to the data.

### Deadlocks

User A wants seats `[B1, A1]` and user B wants `[A1, B1]`. If each locked its first seat
and then waited for the second, they would deadlock. Locking with `ORDER BY seat_no` means
both try `A1` first, so one waits for the other and a cycle cannot form. The test
`test_overlapping_group_bookings_never_split_or_deadlock` fires 60 overlapping group
requests at once to prove it.

### Hot trips

Before taking any lock, `set_hold` does a plain read. If a wanted seat is already booked,
or held by another session that has not lapsed, it returns 409 straight away. Only
requests that look like they could win queue for the row lock, and the locked check is
still the one that decides. This keeps database connections free when hundreds of people
want the same 25 seats.

## 3. Holds and expiry

- **The selection is declarative.** The browser sends the whole selection,
  `PUT /trips/{id}/hold {"seats": [...]}`, not "add seat" and "remove seat" commands.
  Sending the same request twice therefore has the same effect as sending it once, and a
  lost response is safe to retry.
- **The timer does not reset.** It starts with the first seat. Otherwise someone could
  keep a seat forever by toggling another one every four minutes.
- **There are two ways a hold expires:**
  - *Lazily:* a seat whose `held_until` has passed counts as free right away, both for new
    holds and on the seat map. Nobody waits for a background job.
  - *Eagerly:* the sweeper releases lapsed holds every 2 seconds, so the database stays
    tidy and viewers get a live update.
  - The sweeper only frees seats that still belong to the lapsed hold
    (`WHERE hold_id = $1`), so it never undoes a seat someone else has taken since.
- **Only one sweeper runs at a time.** Each API instance tries
  `pg_try_advisory_lock`, and the holder runs the sweeper until its connection dies, at
  which point another instance takes over. The sweeper selects rows with
  `FOR UPDATE SKIP LOCKED`, so it never waits behind a user's transaction.

## 4. Live seat maps

`api/app/realtime.py`, `web/src/useTripSeats.ts`

- Every seat change calls `pg_notify('seat_events', …)` **inside the same transaction**.
  Postgres only delivers the notification if the transaction commits, so a rolled-back
  change is never broadcast. A test checks this.
- Each API instance keeps one connection that LISTENs on the channel, and fans each
  message out to the WebSockets watching that trip.
  - This works across instances without Redis or Kafka.
  - The trade-off: NOTIFY is fire-and-forget, and messages sent while a listener is
    disconnected are lost.
  - The design accepts this because the database stays the source of truth. After a
    reconnect, the server sends every viewer a full **snapshot**.
- **No gap between snapshot and updates.** On connect, the server subscribes the client
  *first* and takes the snapshot *second*. A change can then arrive twice, but never
  zero times. Applying "seat X is held" twice is harmless.
- **Slow clients.** Each viewer has a bounded queue of 256 messages. When it overflows,
  the backlog is dropped and replaced by one "resync" marker, so memory cannot grow
  without limit because of one bad connection.
- **On the browser side:**
  - Reconnect with exponential backoff.
  - Run the user's own seat clicks one after another, each based on the latest confirmed
    hold, so fast clicking cannot make requests overtake each other.
  - Show the REST snapshot until the live one arrives, but never let a late REST
    response overwrite newer live data.

## 5. Payments

`api/app/services/payments.py`, `fakepay/fakepay/app.py`

### The flow

1. **Checkout** moves the hold to `pending_payment` and extends the deadline **once** by
   a grace period, so someone typing a PIN doesn't lose the seat at 4:59. It inserts a
   payment row with status `creating`, then commits.
2. **Only after commit** does it call the provider. A database transaction is never kept
   open across a network call. That would hold row locks for as long as the provider
   takes to answer.
3. The provider call uses the payment row's id as the **idempotency key**. If the call
   times out, retrying checkout gives the same provider payment, never a second one.
4. The customer pays on the provider's page and is sent back to `/booking/{hold_id}`,
   which polls until the hold settles.

### Webhooks

- The signature is `HMAC-SHA256(secret, "<timestamp>.<body>")`, and timestamps more than
  5 minutes old are rejected. Because the timestamp is signed, a captured webhook cannot
  be replayed later.
- The event id is checked against `webhook_events`. The insert happens **in the same
  transaction** as the state change. If processing fails, the event is not recorded and
  the provider's retry gets processed.
- **The payload is not trusted.** The handler calls `GET /payments/{id}` and acts on the
  provider's current state. That one rule covers several cases:
  - Out-of-order events (say, `refunded` arriving before `succeeded`) end up correct.
  - A stale or replayed "succeeded" for a payment that is still pending does nothing.
    There's a test for this.

### Applying a successful payment

`_confirm_paid_hold` locks the hold's seats and checks they are still free for this hold.

- **If yes:** create the booking and mark the seats booked.
- **If someone took a seat after the hold lapsed:** release whatever is left, mark the
  payment `refund_pending`, and refund it.

Every refund uses the idempotency key `"<payment id>:refund"`. If the refund call fails,
the payment stays `refund_pending` and the sweeper retries it.

### Reconciliation (the webhook never arrives)

The sweeper looks for payments that are still open after their hold's deadline and asks
the provider what happened:

| Provider says | Action |
|---|---|
| succeeded | Book, or refund if the seats are gone. |
| pending | **Cancel the payment first**, then release the seats. If the customer paid at that exact moment, cancel returns `succeeded`, and we book instead. |
| failed or cancelled | Release the seats. |
| never heard of it | The checkout call never reached the provider, so nobody was charged. Release the seats. |
| unreachable | Keep the seats held and try again. After 10 minutes of outage, release them anyway. If the payment later turns out to have succeeded, book if the seats are still free, otherwise refund. |

### Lock order

Every path that touches several tables locks them in the same order: hold, then payment,
then seats. This is the same deadlock rule as for seats, applied across tables.

## 6. Invariants and how they are tested

`api/tests/invariants.py` runs after **every** test:

1. Every booked seat belongs to a confirmed booking that lists it.
2. Every seat of a confirmed booking is booked by that booking.
3. Every held seat points at an open hold that lists it, with the same deadline.
4. A succeeded payment has a confirmed booking. A failed, cancelled or refunded payment
   does not.
5. There is at most one open hold per session per trip.

The load test checks the same properties after 400 concurrent users and a chaotic
provider, and also checks that money charged equals the value of tickets sold, to the taka.

## 7. Security

- **Rate limits** (`api/app/ratelimit.py`) use token buckets: seat clicks per session and
  per IP (a script can make up session ids, but not IP addresses), checkout per session,
  and ticket lookup and cancellation per IP. A PNR is 6 characters from a 31-letter
  alphabet, and the IP limit makes guessing one alongside a phone number impractical.
- **Input limits.** Seat names are at most 4 characters, a request holds at most 10 seats,
  and nginx rejects bodies over 64 KB.
- **Webhooks** are HMAC-signed with a timestamp and compared in constant time. Even a valid
  webhook is never trusted on its own (see section 5).
- **Browser.** A strict Content-Security-Policy allows no inline scripts and no third-party
  sources. That is why the pre-paint theme script lives in its own file. React escapes all
  output.
- **Infrastructure.** Postgres, the API and FakePay are published on 127.0.0.1 only;
  the public entry point is nginx. Containers run as non-root users.
- **Dependencies** are checked with `pip-audit` and `npm audit`, and the code with `bandit`.

## 8. Things I would change for production

- **Phone OTP sign-in** instead of guest sessions, and an SMS code before cancelling a
  ticket. Rate limits per account would then replace today's limits per session and IP.
- **An outbox table** for refunds and notifications (SMS ticket), processed by a worker,
  instead of calling the provider straight after commit and relying on the sweeper to
  retry.
- **Partition by trip.** Every operation touches exactly one trip, so trips can be spread
  across database shards by route or date, with no cross-shard transactions.
- **Server time on hold responses,** so the countdown is right even when the user's
  clock is wrong.
- **Watch the queue.** If Prometheus shows `busticket_hold_requests_total{result="conflict"}`
  spiking, a waiting-room page in front of a hot trip is better than letting thousands of
  requests queue on the same rows.

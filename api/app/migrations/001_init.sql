CREATE TABLE cities (
    id      SERIAL PRIMARY KEY,
    slug    TEXT NOT NULL UNIQUE,
    name    TEXT NOT NULL,
    name_bn TEXT NOT NULL
);

CREATE TABLE operators (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE buses (
    id          SERIAL PRIMARY KEY,
    operator_id INT  NOT NULL REFERENCES operators(id),
    coach_no    TEXT NOT NULL UNIQUE,
    coach_type  TEXT NOT NULL CHECK (coach_type IN ('ac_business', 'ac_economy', 'non_ac')),
    -- Rows of seat labels; null marks the aisle. e.g. [["A1","A2",null,"A3","A4"], ...]
    layout      JSONB NOT NULL
);

CREATE TABLE trips (
    id             SERIAL PRIMARY KEY,
    bus_id         INT NOT NULL REFERENCES buses(id),
    origin_id      INT NOT NULL REFERENCES cities(id),
    destination_id INT NOT NULL REFERENCES cities(id),
    departs_at     TIMESTAMPTZ NOT NULL,
    arrives_at     TIMESTAMPTZ NOT NULL,
    fare_bdt       INT  NOT NULL CHECK (fare_bdt > 0),
    boarding_point TEXT NOT NULL,
    CHECK (origin_id <> destination_id),
    CHECK (arrives_at > departs_at)
);
CREATE INDEX trips_search_idx ON trips (origin_id, destination_id, departs_at);

-- A hold reserves seats for one guest session for a short time.
CREATE TABLE holds (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id         INT  NOT NULL REFERENCES trips(id),
    session_id      TEXT NOT NULL,
    seats           TEXT[] NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL CHECK (status IN
                        ('active', 'pending_payment', 'confirmed', 'expired', 'released', 'failed')),
    expires_at      TIMESTAMPTZ NOT NULL,
    passenger_name  TEXT,
    passenger_phone TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- One open hold per session per trip, enforced by the database.
CREATE UNIQUE INDEX holds_one_open_per_session
    ON holds (trip_id, session_id) WHERE status IN ('active', 'pending_payment');
CREATE INDEX holds_open_by_expiry
    ON holds (expires_at) WHERE status IN ('active', 'pending_payment');

CREATE TABLE bookings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pnr             TEXT NOT NULL UNIQUE,
    hold_id         UUID NOT NULL UNIQUE REFERENCES holds(id),
    trip_id         INT  NOT NULL REFERENCES trips(id),
    seats           TEXT[] NOT NULL,
    passenger_name  TEXT NOT NULL,
    passenger_phone TEXT NOT NULL,
    amount_bdt      INT  NOT NULL,
    channel         TEXT NOT NULL DEFAULT 'online' CHECK (channel IN ('online', 'counter')),
    status          TEXT NOT NULL CHECK (status IN ('confirmed', 'cancelled')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    cancelled_at    TIMESTAMPTZ
);

-- One row per physical seat on a trip. Because a seat is a single row with a single
-- booking_id, two bookings can never own the same seat.
CREATE TABLE trip_seats (
    trip_id    INT  NOT NULL REFERENCES trips(id),
    seat_no    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'available' CHECK (status IN ('available', 'held', 'booked')),
    hold_id    UUID REFERENCES holds(id),
    held_until TIMESTAMPTZ,
    booking_id UUID REFERENCES bookings(id),
    PRIMARY KEY (trip_id, seat_no),
    CONSTRAINT seat_state_consistent CHECK (
        (status = 'available' AND hold_id IS NULL AND held_until IS NULL AND booking_id IS NULL)
     OR (status = 'held' AND hold_id IS NOT NULL AND held_until IS NOT NULL AND booking_id IS NULL)
     OR (status = 'booked' AND hold_id IS NULL AND held_until IS NULL AND booking_id IS NOT NULL)
    )
);
CREATE INDEX trip_seats_hold_idx ON trip_seats (hold_id) WHERE hold_id IS NOT NULL;

CREATE TABLE payments (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hold_id       UUID NOT NULL UNIQUE REFERENCES holds(id),
    provider_ref  TEXT UNIQUE,
    checkout_url  TEXT,
    amount_bdt    INT  NOT NULL,
    status        TEXT NOT NULL CHECK (status IN
                      ('creating', 'pending', 'succeeded', 'failed', 'cancelled',
                       'refund_pending', 'refunded')),
    refund_reason TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX payments_refund_pending ON payments (updated_at) WHERE status = 'refund_pending';

-- Every webhook event id we have processed, so retries and duplicates are no-ops.
CREATE TABLE webhook_events (
    event_id     TEXT PRIMARY KEY,
    event_type   TEXT NOT NULL,
    provider_ref TEXT,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type Hold, type TripDetail } from "../api";
import Countdown from "../components/Countdown";
import SeatMap, { Legend, seatView } from "../components/SeatMap";
import { COACH_LABEL, dhakaYmd, duration, formatLongDay, formatTime, taka } from "../format";
import { useTripSeats } from "../useTripSeats";

const MAX_SEATS = 4;
const PHONE_KEY = "aashon.phone";

export default function TripPage() {
  const tripId = Number(useParams().tripId);
  const [trip, setTrip] = useState<TripDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [hold, setHold] = useState<Hold | null>(null);
  const [working, setWorking] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<{ text: string; tone: "bad" | "info" } | null>(null);
  const { seats, live, recent, applyLocal, seed } = useTripSeats(tripId);
  const holdRef = useRef<Hold | null>(null);
  const queue = useRef<Promise<void>>(Promise.resolve());

  const updateHold = useCallback((h: Hold | null) => { holdRef.current = h; setHold(h); }, []);

  useEffect(() => {
    api.trip(tripId).then((t) => { setTrip(t); seed(t.seats); }).catch((e) => setLoadError(e.message));
    api.myHold(tripId).then(updateHold).catch(() => updateHold(null));
  }, [tripId, updateHold, seed]);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 4200);
    return () => window.clearTimeout(t);
  }, [toast]);

  const toggle = (seatNo: string) => {
    const before = holdRef.current?.seats ?? [];
    if (!before.includes(seatNo) && before.length >= MAX_SEATS) {
      setToast({ text: `You can pick up to ${MAX_SEATS} seats in one booking.`, tone: "info" });
      return;
    }
    setWorking((w) => new Set(w).add(seatNo));
    // Run seat changes one after another, each based on the latest confirmed hold, so
    // quick clicks can never overtake each other.
    queue.current = queue.current.then(async () => {
      const current = holdRef.current?.seats ?? [];
      const next = current.includes(seatNo) ? current.filter((s) => s !== seatNo) : [...current, seatNo];
      try {
        const h = await api.setHold(tripId, next);
        const dropped = current.filter((s) => !next.includes(s));
        applyLocal([
          ...dropped.map((s) => ({ seat_no: s, status: "available" as const, hold_id: null })),
          ...(h?.seats ?? []).map((s) => ({ seat_no: s, status: "held" as const, hold_id: h!.id })),
        ]);
        updateHold(h);
      } catch (e) {
        if (e instanceof ApiError && e.code === "seats_unavailable") {
          const lost = (e.detail.unavailable as string[] | undefined) ?? [seatNo];
          setToast({ text: `Seat ${lost.join(", ")} was just taken by someone else.`, tone: "bad" });
        } else if (e instanceof ApiError && e.code === "concurrent_request") {
          updateHold(await api.myHold(tripId).catch(() => null));
        } else {
          setToast({ text: e instanceof Error ? e.message : "Something went wrong", tone: "bad" });
        }
      } finally {
        setWorking((w) => { const n = new Set(w); n.delete(seatNo); return n; });
      }
    });
  };

  const onExpired = useCallback(() => {
    if (holdRef.current?.status !== "active") return;
    updateHold(null);
    setToast({ text: "Time is up, so your seats were released. Pick them again to continue.", tone: "info" });
  }, [updateHold]);

  if (loadError) return <div className="empty"><h1>Bus not found</h1><p>{loadError}</p><Link to="/">Back to search</Link></div>;
  if (!trip) return <div className="trip-page"><div className="panel skeleton-panel" /><div className="panel skeleton-panel" /></div>;

  const departed = Date.parse(trip.departs_at) <= Date.now();
  const mine = hold?.seats ?? [];
  const inPayment = hold?.status === "pending_payment";
  const free = Object.values(seats).filter((s) => s.status === "available").length;

  return (
    <>
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to={`/search?from=${trip.origin.slug}&to=${trip.destination.slug}&date=${dhakaYmd(trip.departs_at)}`}>
          ← All buses on this route
        </Link>
      </nav>
      <div className="trip-page">
        <section className="panel seat-panel" aria-labelledby="seat-h">
          <div className="panel-head">
            <h1 id="seat-h">Choose your seats</h1>
            <span className={`live live-${live}`}>
              <span className="live-dot" aria-hidden="true" />
              {live === "live" ? "Live" : live === "connecting" ? "Connecting" : "Reconnecting"}
            </span>
          </div>
          <p className="muted small">{free} seats free. Up to {MAX_SEATS} per booking. Open this page in a second window to watch seats change live.</p>
          <SeatMap
            layout={trip.layout}
            viewOf={(s) => seatView(seats[s], hold?.id, working.has(s))}
            recent={recent}
            onToggle={toggle}
            disabled={inPayment || departed}
          />
          <Legend />
        </section>

        <aside className="panel summary" aria-labelledby="sum-h">
          <h2 id="sum-h" className="visually-hidden">Trip summary</h2>
          <div className="sum-route">
            <div><strong className="t-big">{formatTime(trip.departs_at)}</strong><span>{trip.origin.name}</span><span className="muted small">{trip.boarding_point}</span></div>
            <div className="trip-line" aria-hidden="true"><span>{duration(trip.departs_at, trip.arrives_at)}</span></div>
            <div className="t-end"><strong className="t-big">{formatTime(trip.arrives_at)}</strong><span>{trip.destination.name}</span></div>
          </div>
          <p className="sum-meta">
            {formatLongDay(trip.departs_at)} · {trip.operator} · <span className={`badge badge-${trip.coach_type}`}>{COACH_LABEL[trip.coach_type]}</span>
          </p>

          {hold && (hold.status === "active" || inPayment) && (
            <Countdown until={hold.expires_at} onDone={onExpired}
                       label={inPayment ? "Payment window" : "Seats held for"} />
          )}

          <div className="picked">
            <span className="muted">Seats</span>
            <div className="chips">
              {mine.length ? mine.map((s) => <span key={s} className="chip">{s}</span>)
                : <span className="muted">None yet. Tap a seat on the map.</span>}
            </div>
          </div>
          <dl className="totals">
            <dt>{mine.length} × {taka(trip.fare_bdt)}</dt>
            <dd>{taka(mine.length * trip.fare_bdt)}</dd>
          </dl>

          {departed ? <p className="notice">This bus has already left.</p>
            : inPayment ? <PaymentInProgress hold={hold!} />
            : <PassengerForm hold={hold} onError={(text) => setToast({ text, tone: "bad" })} />}
        </aside>
      </div>
      {toast && <div className={`toast toast-${toast.tone}`} role="status">{toast.text}</div>}
    </>
  );
}

function PassengerForm({ hold, onError }: { hold: Hold | null; onError: (t: string) => void }) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState(() => localStorage.getItem(PHONE_KEY) ?? "");
  const [busy, setBusy] = useState(false);
  const ready = !!hold && hold.seats.length > 0;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!hold) return;
    setBusy(true);
    try {
      const { checkout_url } = await api.checkout(hold.id, name, phone);
      localStorage.setItem(PHONE_KEY, phone);
      localStorage.setItem(`aashon.hold.${hold.id}`, phone);
      window.location.assign(checkout_url);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not start payment");
      setBusy(false);
    }
  };

  return (
    <form className="passenger" onSubmit={submit}>
      <label className="field">
        <span>Passenger name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name"
               placeholder="As on your NID" required minLength={2} maxLength={60} />
      </label>
      <label className="field">
        <span>Mobile number</span>
        <input value={phone} onChange={(e) => setPhone(e.target.value)} autoComplete="tel"
               inputMode="tel" placeholder="01XXXXXXXXX" required />
      </label>
      <button className="btn btn-primary btn-block" disabled={!ready || busy}>
        {busy ? "Opening payment…" : ready ? "Continue to payment" : "Pick a seat first"}
      </button>
      <p className="muted small">You will pay on the FakePay sandbox. No real money is charged.</p>
    </form>
  );
}

function PaymentInProgress({ hold }: { hold: Hold }) {
  return (
    <div className="notice">
      <p><strong>Payment in progress.</strong> Your seats stay locked until the payment window closes.</p>
      <div className="row-actions">
        {hold.payment?.checkout_url && <a className="btn btn-primary" href={hold.payment.checkout_url}>Continue payment</a>}
        <Link className="btn" to={`/booking/${hold.id}`}>Check status</Link>
      </div>
    </div>
  );
}

import type { Booking } from "../api";
import { COACH_LABEL, duration, formatLongDay, formatTime, taka } from "../format";

export default function TicketCard({ booking: b }: { booking: Booking }) {
  const cancelled = b.status === "cancelled";
  return (
    <article className={`ticket ${cancelled ? "ticket-cancelled" : ""}`} aria-label={`Ticket ${b.pnr}`}>
      <header className="ticket-head">
        <div>
          <span className="muted small">PNR</span>
          <strong className="pnr">{b.pnr}</strong>
        </div>
        <span className={`stamp stamp-${b.status}`}>{cancelled ? "Cancelled" : "Confirmed"}</span>
      </header>
      <div className="ticket-route">
        <div><strong className="t-big">{formatTime(b.trip.departs_at)}</strong><span>{b.trip.origin}</span></div>
        <div className="trip-line" aria-hidden="true"><span>{duration(b.trip.departs_at, b.trip.arrives_at)}</span></div>
        <div className="t-end"><strong className="t-big">{formatTime(b.trip.arrives_at)}</strong><span>{b.trip.destination}</span></div>
      </div>
      <dl className="ticket-grid">
        <div><dt>Date</dt><dd>{formatLongDay(b.trip.departs_at)}</dd></div>
        <div><dt>Boarding</dt><dd>{b.trip.boarding_point}</dd></div>
        <div><dt>Operator</dt><dd>{b.trip.operator}</dd></div>
        <div><dt>Coach</dt><dd>{b.trip.coach_no} · {COACH_LABEL[b.trip.coach_type]}</dd></div>
        <div><dt>Passenger</dt><dd>{b.passenger_name}</dd></div>
        <div><dt>Mobile</dt><dd>{b.passenger_phone}</dd></div>
        <div><dt>Seats</dt><dd className="ticket-seats">{b.seats.join(", ")}</dd></div>
        <div><dt>Paid</dt><dd>{taka(b.amount_bdt)}{b.payment_status === "refunded" ? " · refunded" : b.payment_status === "refund_pending" ? " · refund in progress" : ""}</dd></div>
      </dl>
      <p className="ticket-foot muted small">Show this PNR and your mobile number at the counter. Please arrive 30 minutes before departure.</p>
    </article>
  );
}

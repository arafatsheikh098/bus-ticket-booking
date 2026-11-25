import { useEffect, useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, type Booking, type Hold } from "../api";
import TicketCard from "../components/TicketCard";
import { taka } from "../format";

const TERMINAL = new Set(["confirmed", "failed", "expired", "released"]);

export default function BookingStatus() {
  const { holdId = "" } = useParams();
  const [params] = useSearchParams();
  const providerSaid = params.get("status");
  const [hold, setHold] = useState<Hold | null>(null);
  const [booking, setBooking] = useState<Booking | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [waitedLong, setWaitedLong] = useState(false);

  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    const started = Date.now();
    const poll = async () => {
      try {
        const h = await api.hold(holdId);
        if (stop) return;
        setHold(h);
        const refundSettling = h.payment?.status === "refund_pending";
        if (TERMINAL.has(h.status) && !refundSettling) {
          if (h.booking) {
            const phone = localStorage.getItem(`busticket.hold.${holdId}`) ?? localStorage.getItem("busticket.phone") ?? "";
            api.booking(h.booking.pnr, phone).then(setBooking).catch(() => {});
          }
          return;
        }
      } catch (e) {
        if (!stop) setError(e instanceof Error ? e.message : "Could not load booking");
        return;
      }
      setWaitedLong(Date.now() - started > 8000);
      timer = window.setTimeout(poll, 1200);
    };
    poll();
    return () => { stop = true; window.clearTimeout(timer); };
  }, [holdId]);

  if (error) return <div className="empty"><h1>We could not find this booking</h1><p>{error}</p><Link to="/ticket">Look up a ticket</Link></div>;
  if (!hold) return <Waiting text="Loading…" />;

  if (hold.status === "confirmed") {
    return (
      <section className="status-page">
        <div className="status-banner status-good">
          <h1>You're booked</h1>
          <p>Your seats are confirmed. Save the PNR below.</p>
        </div>
        {booking ? <TicketCard booking={booking} /> : <p className="pnr-only">PNR <strong className="pnr">{hold.booking?.pnr}</strong></p>}
        <div className="row-actions">
          <button className="btn" onClick={() => window.print()}>Print ticket</button>
          <Link className="btn" to="/">Book another trip</Link>
        </div>
      </section>
    );
  }

  const refund = hold.payment?.status === "refunded" || hold.payment?.status === "refund_pending";
  if (hold.status === "failed" && refund) {
    return (
      <Outcome title="Payment arrived too late" tone="warn" tripId={hold.trip_id}>
        Your payment came through after your hold had run out, and seat {hold.seats.join(", ")} had
        already been booked by someone else. {hold.payment?.status === "refunded"
          ? `Your ${taka(hold.payment.amount_bdt)} has been refunded in full.`
          : `A full refund of ${taka(hold.payment!.amount_bdt)} is on its way.`}
      </Outcome>
    );
  }
  if (hold.status === "failed") {
    return <Outcome title="Payment declined" tone="bad" tripId={hold.trip_id}>
      The payment did not go through, so your seats were released. No money was taken.
    </Outcome>;
  }
  if (hold.status === "expired" || hold.status === "released") {
    return <Outcome title="Your hold ran out" tone="bad" tripId={hold.trip_id}>
      The payment was not completed in time, so the seats were released. No money was taken.
    </Outcome>;
  }

  return (
    <section className="status-page">
      <Waiting text={providerSaid === "succeeded" ? "Payment received. Confirming your seats…" : "Waiting for your payment…"} />
      {waitedLong && (
        <p className="muted center">
          This is taking longer than usual. If the payment provider's confirmation got lost, we
          check with them directly when your payment window closes, so you don't need to pay again.
        </p>
      )}
      {hold.payment?.checkout_url && hold.status === "pending_payment" && providerSaid !== "succeeded" && (
        <p className="center"><a className="btn" href={hold.payment.checkout_url}>Back to payment page</a></p>
      )}
    </section>
  );
}

function Waiting({ text }: { text: string }) {
  return <div className="waiting" role="status"><span className="spinner" aria-hidden="true" />{text}</div>;
}

function Outcome({ title, tone, tripId, children }: { title: string; tone: "bad" | "warn"; tripId: number; children: ReactNode }) {
  return (
    <section className="status-page">
      <div className={`status-banner status-${tone}`}>
        <h1>{title}</h1>
        <p>{children}</p>
      </div>
      <div className="row-actions center">
        <Link className="btn btn-primary" to={`/trip/${tripId}`}>Pick seats again</Link>
        <Link className="btn" to="/">New search</Link>
      </div>
    </section>
  );
}


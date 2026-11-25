import { useState, type FormEvent } from "react";
import { api, ApiError, type Booking } from "../api";
import TicketCard from "../components/TicketCard";
import { taka } from "../format";

export default function Ticket() {
  const [pnr, setPnr] = useState("");
  const [phone, setPhone] = useState(() => localStorage.getItem("aashon.phone") ?? "");
  const [booking, setBooking] = useState<Booking | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const find = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null); setBooking(null); setConfirming(false);
    try { setBooking(await api.booking(pnr, phone)); }
    catch (err) {
      if (err instanceof ApiError && err.status === 404) setError("No ticket matches that PNR and mobile number.");
      else setError(err instanceof Error ? err.message : "Something went wrong");
    }
    finally { setBusy(false); }
  };

  const cancel = async () => {
    if (!booking) return;
    setBusy(true); setError(null);
    try { setBooking(await api.cancelBooking(booking.pnr, phone)); setConfirming(false); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not cancel"); }
    finally { setBusy(false); }
  };

  return (
    <section className="ticket-page">
      <h1>Find your ticket</h1>
      <form className="lookup" onSubmit={find}>
        <label className="field">
          <span>PNR</span>
          <input value={pnr} onChange={(e) => setPnr(e.target.value.toUpperCase())} placeholder="AS7K2M9Q"
                 required autoCapitalize="characters" spellCheck={false} />
        </label>
        <label className="field">
          <span>Mobile number</span>
          <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="01XXXXXXXXX"
                 inputMode="tel" required />
        </label>
        <button className="btn btn-primary" disabled={busy}>{busy && !booking ? "Searching…" : "Find ticket"}</button>
      </form>
      {error && <p className="notice notice-bad">{error}</p>}
      {booking && (
        <>
          <TicketCard booking={booking} />
          {booking.cancellable && !confirming && (
            <p className="center"><button className="btn btn-danger" onClick={() => setConfirming(true)}>Cancel this ticket</button></p>
          )}
          {confirming && (
            <div className="notice confirm">
              <p>Cancel <strong>{booking.pnr}</strong> and refund {taka(booking.amount_bdt)}? Seats {booking.seats.join(", ")} will be released for others.</p>
              <div className="row-actions">
                <button className="btn btn-danger" onClick={cancel} disabled={busy}>{busy ? "Cancelling…" : "Yes, cancel"}</button>
                <button className="btn" onClick={() => setConfirming(false)}>Keep ticket</button>
              </div>
            </div>
          )}
          {booking.status === "confirmed" && !booking.cancellable && (
            <p className="muted center small">Tickets can be cancelled up to 2 hours before departure.</p>
          )}
        </>
      )}
    </section>
  );
}

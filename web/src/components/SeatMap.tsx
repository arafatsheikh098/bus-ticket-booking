import type { Seat } from "../api";

export type SeatView = "available" | "mine" | "held" | "booked" | "working";

type Props = {
  layout: (string | null)[][];
  viewOf: (seatNo: string) => SeatView;
  recent: Set<string>;
  onToggle: (seatNo: string) => void;
  disabled?: boolean;
};

const LABEL: Record<SeatView, string> = {
  available: "available",
  mine: "selected by you",
  held: "being booked by someone else",
  booked: "sold",
  working: "updating",
};

export default function SeatMap({ layout, viewOf, recent, onToggle, disabled }: Props) {
  const cols = layout[0]?.length ?? 5;
  return (
    <div className="bus" role="group" aria-label="Seat map. Front of the bus is at the top.">
      <div className="bus-front" aria-hidden="true">
        <span className="door">Door</span>
        <span className="wheel" title="Driver">
          <svg viewBox="0 0 24 24" width="26" height="26"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2"/><circle cx="12" cy="12" r="2.5" fill="currentColor"/><path d="M12 3v6.5M4.2 15.5l5.6-2.3M19.8 15.5l-5.6-2.3" stroke="currentColor" strokeWidth="2"/></svg>
        </span>
      </div>
      <div className="seat-grid" style={{ gridTemplateColumns: `repeat(${cols}, var(--seat))` }}>
        {layout.flatMap((row, r) =>
          row.map((seatNo, c) => {
            if (!seatNo) return <span key={`aisle-${r}-${c}`} className="aisle" aria-hidden="true">{c === Math.floor(cols / 2) ? row.find(Boolean)?.[0] : ""}</span>;
            const view = viewOf(seatNo);
            const clickable = !disabled && (view === "available" || view === "mine");
            return (
              <button
                key={seatNo}
                type="button"
                className={`seat seat-${view} ${recent.has(seatNo) ? "seat-pulse" : ""}`}
                aria-label={`Seat ${seatNo}, ${LABEL[view]}`}
                aria-pressed={view === "mine"}
                disabled={!clickable}
                onClick={() => onToggle(seatNo)}
              >
                {seatNo}
              </button>
            );
          }),
        )}
      </div>
    </div>
  );
}

export function seatView(seat: Seat | undefined, myHoldId: string | undefined, working: boolean): SeatView {
  if (working) return "working";
  if (!seat) return "booked";
  if (seat.status === "held") return seat.hold_id && seat.hold_id === myHoldId ? "mine" : "held";
  return seat.status;
}

export function Legend() {
  return (
    <ul className="legend">
      <li><span className="seat seat-available" aria-hidden="true" /> Available</li>
      <li><span className="seat seat-mine" aria-hidden="true" /> Your pick</li>
      <li><span className="seat seat-held" aria-hidden="true" /> Someone is booking</li>
      <li><span className="seat seat-booked" aria-hidden="true" /> Sold</li>
    </ul>
  );
}

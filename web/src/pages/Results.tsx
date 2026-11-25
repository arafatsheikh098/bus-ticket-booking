import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type TripSummary } from "../api";
import SearchForm from "../components/SearchForm";
import { COACH_LABEL, dayIso, dhakaDate, duration, formatLongDay, formatTime, taka } from "../format";

type Filter = "all" | "ac" | "non_ac";
type Sort = "time" | "fare" | "seats";

export default function Results() {
  const [params] = useSearchParams();
  const from = params.get("from") ?? "dhaka";
  const to = params.get("to") ?? "chattogram";
  const date = params.get("date") || dhakaDate(0);
  const [trips, setTrips] = useState<TripSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("time");

  useEffect(() => {
    setTrips(null); setError(null);
    api.search(from, to, date).then(setTrips).catch((e) => setError(e.message));
  }, [from, to, date]);

  const shown = useMemo(() => {
    const list = (trips ?? []).filter((t) =>
      filter === "all" ? true : filter === "ac" ? t.coach_type !== "non_ac" : t.coach_type === "non_ac");
    const by: Record<Sort, (a: TripSummary, b: TripSummary) => number> = {
      time: (a, b) => a.departs_at.localeCompare(b.departs_at),
      fare: (a, b) => a.fare_bdt - b.fare_bdt,
      seats: (a, b) => b.seats_available - a.seats_available,
    };
    return [...list].sort(by[sort]);
  }, [trips, filter, sort]);

  const routeName = trips?.[0] ? `${trips[0].origin.name} → ${trips[0].destination.name}` : "Buses";

  return (
    <>
      <SearchForm from={from} to={to} date={date} compact />
      <section className="results" aria-live="polite">
        <div className="results-head">
          <div>
            <h1>{routeName}</h1>
            <p className="muted">{formatLongDay(dayIso(date))}</p>
          </div>
          <div className="controls">
            <div className="seg" role="group" aria-label="Coach type">
              {(["all", "ac", "non_ac"] as Filter[]).map((f) => (
                <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                  {f === "all" ? "All" : f === "ac" ? "AC" : "Non-AC"}
                </button>
              ))}
            </div>
            <label className="sort">
              Sort
              <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
                <option value="time">Departure</option>
                <option value="fare">Lowest fare</option>
                <option value="seats">Most seats</option>
              </select>
            </label>
          </div>
        </div>

        {error && <p className="notice notice-bad">{error}</p>}
        {!trips && !error && <TripSkeleton />}
        {trips && shown.length === 0 && (
          <div className="empty">
            <h2>No buses left for this day</h2>
            <p>Try another date above. Buses that have already left are hidden.</p>
          </div>
        )}
        <ul className="trip-list">
          {shown.map((t) => <TripCard key={t.id} trip={t} />)}
        </ul>
      </section>
    </>
  );
}

function TripCard({ trip: t }: { trip: TripSummary }) {
  const left = t.seats_available;
  const tone = left === 0 ? "none" : left <= 5 ? "few" : "ok";
  return (
    <li className="trip">
      <div className="trip-times">
        <div>
          <strong className="t-big">{formatTime(t.departs_at)}</strong>
          <span className="muted">{t.boarding_point}</span>
        </div>
        <div className="trip-line" aria-hidden="true"><span>{duration(t.departs_at, t.arrives_at)}</span></div>
        <div className="t-end">
          <strong className="t-big">{formatTime(t.arrives_at)}</strong>
          <span className="muted">{t.destination.name}</span>
        </div>
      </div>
      <div className="trip-meta">
        <span className="operator">{t.operator}</span>
        <span className={`badge badge-${t.coach_type}`}>{COACH_LABEL[t.coach_type]}</span>
        <span className="muted">Coach {t.coach_no}</span>
      </div>
      <div className="trip-buy">
        <span className="fare">{taka(t.fare_bdt)}</span>
        <span className={`left left-${tone}`}>
          {left === 0 ? "Sold out" : `${left} of ${t.seats_total} seats left`}
        </span>
        {left > 0
          ? <Link className="btn btn-primary" to={`/trip/${t.id}`}>Select seats</Link>
          : <span className="btn btn-disabled" aria-disabled="true">Sold out</span>}
      </div>
    </li>
  );
}

function TripSkeleton() {
  return (
    <ul className="trip-list" aria-label="Loading buses">
      {[0, 1, 2].map((i) => <li key={i} className="trip trip-skeleton" />)}
    </ul>
  );
}

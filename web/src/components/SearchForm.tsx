import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, type City } from "../api";
import { dayIso, dhakaDate, formatDay } from "../format";

type Props = { from?: string; to?: string; date?: string; compact?: boolean };

export default function SearchForm({ from = "dhaka", to = "chattogram", date, compact }: Props) {
  const [cities, setCities] = useState<City[]>([]);
  const [origin, setOrigin] = useState(from);
  const [destination, setDestination] = useState(to);
  const [day, setDay] = useState(date ?? dhakaDate(0));
  const navigate = useNavigate();
  const days = Array.from({ length: 7 }, (_, i) => dhakaDate(i));

  useEffect(() => { api.cities().then(setCities).catch(() => setCities([])); }, []);
  useEffect(() => { setOrigin(from); setDestination(to); if (date) setDay(date); }, [from, to, date]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (origin === destination) return;
    navigate(`/search?from=${origin}&to=${destination}&date=${day}`);
  };
  const swap = () => { setOrigin(destination); setDestination(origin); };
  const sameCity = origin === destination;

  return (
    <form className={`search ${compact ? "search-compact" : ""}`} onSubmit={submit}>
      <div className="search-cities">
        <label className="field">
          <span>From</span>
          <select value={origin} onChange={(e) => setOrigin(e.target.value)}>
            {cities.map((c) => <option key={c.slug} value={c.slug}>{c.name} · {c.name_bn}</option>)}
          </select>
        </label>
        <button type="button" className="swap" onClick={swap} aria-label="Swap from and to">⇄</button>
        <label className="field">
          <span>To</span>
          <select value={destination} onChange={(e) => setDestination(e.target.value)}>
            {cities.map((c) => <option key={c.slug} value={c.slug}>{c.name} · {c.name_bn}</option>)}
          </select>
        </label>
      </div>
      <fieldset className="days">
        <legend>Journey date</legend>
        {days.map((d, i) => (
          <label key={d} className={`day ${day === d ? "day-on" : ""}`}>
            <input type="radio" name="day" value={d} checked={day === d} onChange={() => setDay(d)} />
            <span className="day-top">{i === 0 ? "Today" : i === 1 ? "Tomorrow" : formatDay(dayIso(d)).split(" ")[0]}</span>
            <span className="day-num">{formatDay(dayIso(d)).split(" ").slice(1).join(" ")}</span>
          </label>
        ))}
      </fieldset>
      <button className="btn btn-primary search-go" disabled={sameCity}>
        {sameCity ? "Pick two different cities" : "Search buses"}
      </button>
    </form>
  );
}

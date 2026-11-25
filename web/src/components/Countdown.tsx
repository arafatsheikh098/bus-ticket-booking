import { useEffect, useState } from "react";

type Props = { until: string; onDone?: () => void; label?: string };

export default function Countdown({ until, onDone, label = "Seats held for" }: Props) {
  const [left, setLeft] = useState(() => Date.parse(until) - Date.now());

  useEffect(() => {
    const tick = () => {
      const ms = Date.parse(until) - Date.now();
      setLeft(ms);
      if (ms <= 0) onDone?.();
    };
    tick();
    const id = window.setInterval(tick, 250);
    return () => window.clearInterval(id);
  }, [until, onDone]);

  const secs = Math.max(0, Math.ceil(left / 1000));
  const mm = Math.floor(secs / 60), ss = String(secs % 60).padStart(2, "0");
  const tone = secs <= 30 ? "urgent" : secs <= 90 ? "soon" : "calm";
  return (
    <div className={`countdown countdown-${tone}`} role="timer" aria-live="off">
      <span>{label}</span>
      <strong>{mm}:{ss}</strong>
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import type { Seat } from "./api";

export type LiveState = "connecting" | "live" | "reconnecting";

/**
 * Live seat states for one trip, over a WebSocket.
 *
 * The server sends a full snapshot on connect (and again whenever it suspects we missed
 * something), then small deltas. On disconnect we reconnect with backoff and the fresh
 * snapshot repairs anything missed.
 */
export function useTripSeats(tripId: number) {
  const [seats, setSeats] = useState<Record<string, Seat>>({});
  const [live, setLive] = useState<LiveState>("connecting");
  const [recent, setRecent] = useState<Set<string>>(new Set());
  const timers = useRef<number[]>([]);
  const gotSnapshot = useRef(false);

  const markRecent = useCallback((ids: string[]) => {
    setRecent((prev) => new Set([...prev, ...ids]));
    const t = window.setTimeout(() => {
      setRecent((prev) => { const n = new Set(prev); ids.forEach((i) => n.delete(i)); return n; });
    }, 1400);
    timers.current.push(t);
  }, []);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let attempt = 0;
    let retryTimer: number | undefined;
    const proto = location.protocol === "https:" ? "wss" : "ws";

    const connect = () => {
      ws = new WebSocket(`${proto}://${location.host}/api/ws/trips/${tripId}`);
      ws.onopen = () => { attempt = 0; setLive("live"); };
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === "snapshot") {
          gotSnapshot.current = true;
          setSeats(Object.fromEntries((msg.seats as Seat[]).map((s) => [s.seat_no, s])));
        } else if (msg.type === "seats") {
          const changed = msg.seats as Seat[];
          setSeats((prev) => {
            const next = { ...prev };
            for (const s of changed) next[s.seat_no] = s;
            return next;
          });
          markRecent(changed.map((s) => s.seat_no));
        }
      };
      ws.onclose = () => {
        if (closed) return;
        setLive("reconnecting");
        retryTimer = window.setTimeout(connect, Math.min(500 * 2 ** attempt++, 8000));
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retryTimer);
      timers.current.forEach(window.clearTimeout);
      ws?.close();
    };
  }, [tripId, markRecent]);

  /** Apply a change we already know about (our own hold) before the echo arrives. */
  const applyLocal = useCallback((changes: Seat[]) => {
    setSeats((prev) => {
      const next = { ...prev };
      for (const s of changes) next[s.seat_no] = s;
      return next;
    });
  }, []);

  /** Seats from the page's first REST load, used only until the live snapshot arrives,
   * so a late REST response can never overwrite newer live state. */
  const seed = useCallback((initial: Seat[]) => {
    if (!gotSnapshot.current) {
      setSeats(Object.fromEntries(initial.map((s) => [s.seat_no, s])));
    }
  }, []);

  return { seats, live, recent, applyLocal, seed };
}

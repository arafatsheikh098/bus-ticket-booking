import type { CoachType } from "./api";

const TZ = "Asia/Dhaka";

const timeFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ, hour: "numeric", minute: "2-digit", hour12: true,
});
const dayFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ, weekday: "short", day: "numeric", month: "short",
});
const longDayFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ, weekday: "long", day: "numeric", month: "long",
});

export const formatTime = (iso: string) => timeFmt.format(new Date(iso)).replace(" ", "\u00a0");
export const formatDay = (iso: string) => dayFmt.format(new Date(iso));
export const formatLongDay = (iso: string) => longDayFmt.format(new Date(iso));
export const taka = (n: number) => `৳${n.toLocaleString("en-IN")}`;

export function duration(fromIso: string, toIso: string): string {
  const mins = Math.round((Date.parse(toIso) - Date.parse(fromIso)) / 60000);
  const h = Math.floor(mins / 60), m = mins % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

export const COACH_LABEL: Record<CoachType, string> = {
  non_ac: "Non-AC",
  ac_economy: "AC Economy",
  ac_business: "AC Business",
};

/** YYYY-MM-DD for a day offset from today, in Bangladesh time. */
export function dhakaDate(offsetDays = 0): string {
  const d = new Date(Date.now() + offsetDays * 86400000);
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)!.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** Noon on a YYYY-MM-DD day in Dhaka, as an ISO string, for labelling dates safely. */
export const dayIso = (ymd: string) => `${ymd}T12:00:00+06:00`;

/** YYYY-MM-DD of an instant, in Bangladesh time. */
export function dhakaYmd(iso: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date(iso));
}

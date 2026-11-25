export type City = { slug: string; name: string; name_bn: string };
export type CoachType = "non_ac" | "ac_economy" | "ac_business";
export type SeatStatus = "available" | "held" | "booked";
export type Seat = { seat_no: string; status: SeatStatus; hold_id: string | null };

export type TripSummary = {
  id: number;
  operator: string;
  coach_no: string;
  coach_type: CoachType;
  origin: { slug: string; name: string };
  destination: { slug: string; name: string };
  departs_at: string;
  arrives_at: string;
  fare_bdt: number;
  boarding_point: string;
  seats_available: number;
  seats_total: number;
};
export type TripDetail = TripSummary & { layout: (string | null)[][]; seats: Seat[] };

export type Hold = {
  id: string;
  trip_id: number;
  seats: string[];
  status: "active" | "pending_payment" | "confirmed" | "expired" | "released" | "failed";
  expires_at: string;
  payment: { status: string; amount_bdt: number; checkout_url: string | null; refund_reason: string | null } | null;
  booking: { pnr: string } | null;
};

export type Booking = {
  pnr: string;
  status: "confirmed" | "cancelled";
  seats: string[];
  passenger_name: string;
  passenger_phone: string;
  amount_bdt: number;
  payment_status: string | null;
  created_at: string;
  cancelled_at: string | null;
  cancellable: boolean;
  trip: {
    id: number;
    operator: string;
    coach_no: string;
    coach_type: CoachType;
    origin: string;
    destination: string;
    departs_at: string;
    arrives_at: string;
    boarding_point: string;
  };
};

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string,
              public detail: Record<string, unknown> = {}) {
    super(message);
  }
}

const SESSION_KEY = "busticket.session";

export function sessionId(): string {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Session-Id": sessionId() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 204) return null as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = data?.error ?? {};
    throw new ApiError(res.status, err.code ?? "http_error",
                       err.message ?? `Request failed (${res.status})`, err);
  }
  return data as T;
}

export const api = {
  cities: () => request<City[]>("GET", "/api/cities"),
  search: (from: string, to: string, date: string) =>
    request<TripSummary[]>("GET", `/api/trips?from=${from}&to=${to}&date=${date}`),
  trip: (id: number) => request<TripDetail>("GET", `/api/trips/${id}`),
  myHold: (tripId: number) => request<Hold | null>("GET", `/api/trips/${tripId}/hold`),
  setHold: (tripId: number, seats: string[]) =>
    request<Hold | null>("PUT", `/api/trips/${tripId}/hold`, { seats }),
  hold: (id: string) => request<Hold>("GET", `/api/holds/${id}`),
  checkout: (holdId: string, name: string, phone: string) =>
    request<{ checkout_url: string }>("POST", `/api/holds/${holdId}/checkout`, { name, phone }),
  booking: (pnr: string, phone: string) =>
    request<Booking>("GET", `/api/bookings/${encodeURIComponent(pnr)}?phone=${encodeURIComponent(phone)}`),
  cancelBooking: (pnr: string, phone: string) =>
    request<Booking>("POST", `/api/bookings/${encodeURIComponent(pnr)}/cancel`, { phone }),
};

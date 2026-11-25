from prometheus_client import Counter, Gauge

HOLD_REQUESTS = Counter("aashon_hold_requests_total", "Seat hold requests", ["result"])
HOLDS_EXPIRED = Counter("aashon_holds_expired_total", "Holds released because time ran out")
BOOKINGS = Counter("aashon_bookings_total", "Confirmed bookings")
REFUNDS = Counter("aashon_refunds_total", "Refunds issued", ["reason"])
WEBHOOKS = Counter("aashon_webhooks_total", "Payment webhooks received", ["result"])
WS_CLIENTS = Gauge("aashon_ws_clients", "Open live seat map connections")

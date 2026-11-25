from prometheus_client import Counter, Gauge

HOLD_REQUESTS = Counter("busticket_hold_requests_total", "Seat hold requests", ["result"])
HOLDS_EXPIRED = Counter("busticket_holds_expired_total", "Holds released because time ran out")
BOOKINGS = Counter("busticket_bookings_total", "Confirmed bookings")
REFUNDS = Counter("busticket_refunds_total", "Refunds issued", ["reason"])
WEBHOOKS = Counter("busticket_webhooks_total", "Payment webhooks received", ["result"])
WS_CLIENTS = Gauge("busticket_ws_clients", "Open live seat map connections")

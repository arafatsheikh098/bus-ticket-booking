"""FakePay: a sandbox payment provider that misbehaves on purpose.

It behaves like a typical hosted-checkout provider (create a payment, redirect the
customer, receive a signed webhook), and can be told to be unreliable: slow webhooks,
duplicate webhooks, lost webhooks, slow or failing API calls. State lives in memory.
No real money is involved.
"""

import asyncio
import hashlib
import hmac
import html
import json
import os
import random
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, HttpUrl

API_KEY = os.environ.get("FAKEPAY_API_KEY", "sk_test_dev_change_me")
WEBHOOK_SECRET = os.environ.get("FAKEPAY_WEBHOOK_SECRET", "whsec_dev_change_me")
PUBLIC_URL = os.environ.get("FAKEPAY_PUBLIC_URL", "http://localhost:8001").rstrip("/")


class Chaos(BaseModel):
    webhook_delay_ms: tuple[int, int] = (200, 1500)
    duplicate_rate: float = Field(0.25, ge=0, le=1)  # Chance a webhook is sent twice.
    drop_rate: float = Field(0.0, ge=0, le=1)  # Chance a webhook is never sent.
    api_latency_ms: int = Field(0, ge=0)
    api_error_rate: float = Field(0.0, ge=0, le=1)  # Chance an API call returns 503.


class CreatePayment(BaseModel):
    amount: int = Field(gt=0)
    currency: str = "BDT"
    reference: str = Field(min_length=1, max_length=100)
    callback_url: HttpUrl
    return_url: HttpUrl
    description: str = ""


class Store:
    def __init__(self):
        self.payments: dict[str, dict] = {}
        self.by_idempotency: dict[str, str] = {}
        self.lock = asyncio.Lock()
        self.chaos = Chaos()
        self.webhook_log: list[dict] = []


store = Store()
app = FastAPI(title="FakePay sandbox", version="1.0.0")
_background: set[asyncio.Task] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sign(body: bytes, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    t = int(timestamp or time.time())
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


async def require_key(authorization: str | None = Header(default=None)) -> None:
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(401, "invalid api key")
    chaos = store.chaos
    if chaos.api_latency_ms:
        await asyncio.sleep(chaos.api_latency_ms / 1000)
    if random.random() < chaos.api_error_rate:
        raise HTTPException(503, "chaos: provider temporarily unavailable")


def _public(p: dict) -> dict:
    return {k: v for k, v in p.items() if not k.startswith("_")}


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _deliver(url: str, event: dict) -> None:
    """Deliver one webhook with retries and exponential backoff, like real providers."""
    chaos = store.chaos
    lo, hi = chaos.webhook_delay_ms
    await asyncio.sleep(random.uniform(lo, max(lo, hi)) / 1000)
    body = json.dumps(event).encode()
    async with httpx.AsyncClient(timeout=5) as client:
        for attempt in range(6):
            status = None
            try:
                r = await client.post(url, content=body, headers={
                    "Content-Type": "application/json",
                    "X-FakePay-Signature": sign(body),
                })
                status = r.status_code
            except httpx.HTTPError as exc:
                status = f"error: {exc.__class__.__name__}"
            store.webhook_log.append({"event_id": event["id"], "type": event["type"],
                                      "attempt": attempt + 1, "result": status, "at": _now()})
            if isinstance(status, int) and status < 300:
                return
            await asyncio.sleep(min(2 ** attempt, 30))


def _emit(payment: dict, event_type: str) -> None:
    event = {"id": f"evt_{uuid.uuid4().hex}", "type": event_type, "created": _now(),
             "data": _public(payment)}
    chaos = store.chaos
    if random.random() < chaos.drop_rate:
        store.webhook_log.append({"event_id": event["id"], "type": event_type,
                                  "attempt": 0, "result": "dropped by chaos", "at": _now()})
        return
    copies = 2 if random.random() < chaos.duplicate_rate else 1
    for _ in range(copies):
        _spawn(_deliver(payment["_callback_url"], event))


def _transition(payment: dict, status: str, event_type: str) -> dict:
    payment["status"] = status
    payment["updated_at"] = _now()
    _emit(payment, event_type)
    return payment


@app.post("/v1/payments", status_code=201, dependencies=[Depends(require_key)])
async def create_payment(body: CreatePayment, idempotency_key: str = Header()):
    async with store.lock:
        existing = store.by_idempotency.get(idempotency_key)
        if existing:
            return _public(store.payments[existing])
        pid = f"pay_{uuid.uuid4().hex[:20]}"
        payment = {
            "id": pid, "status": "pending", "amount": body.amount, "currency": body.currency,
            "reference": body.reference, "description": body.description,
            "checkout_url": f"{PUBLIC_URL}/checkout/{pid}",
            "created_at": _now(), "updated_at": _now(),
            "_callback_url": str(body.callback_url), "_return_url": str(body.return_url),
        }
        store.payments[pid] = payment
        store.by_idempotency[idempotency_key] = pid
        return _public(payment)


@app.get("/v1/payments", dependencies=[Depends(require_key)])
async def find_payments(reference: str):
    return [_public(p) for p in store.payments.values() if p["reference"] == reference]


@app.get("/v1/payments/{pid}", dependencies=[Depends(require_key)])
async def get_payment(pid: str):
    if pid not in store.payments:
        raise HTTPException(404, "payment not found")
    return _public(store.payments[pid])


@app.post("/v1/payments/{pid}/cancel", dependencies=[Depends(require_key)])
async def cancel_payment(pid: str):
    """Cancel a payment that has not been paid. Returns the current state either way, so
    a caller that races with the customer learns whether the payment actually went through."""
    async with store.lock:
        payment = store.payments.get(pid)
        if payment is None:
            raise HTTPException(404, "payment not found")
        if payment["status"] == "pending":
            _transition(payment, "cancelled", "payment.cancelled")
        return _public(payment)


@app.post("/v1/payments/{pid}/refund", dependencies=[Depends(require_key)])
async def refund_payment(pid: str, idempotency_key: str = Header()):
    async with store.lock:
        payment = store.payments.get(pid)
        if payment is None:
            raise HTTPException(404, "payment not found")
        if payment["status"] == "succeeded":
            payment["refund_idempotency_key"] = idempotency_key
            _transition(payment, "refunded", "payment.refunded")
        elif payment["status"] != "refunded":
            raise HTTPException(409, f"cannot refund a {payment['status']} payment")
        return _public(payment)


@app.post("/v1/payments/{pid}/simulate", dependencies=[Depends(require_key)])
async def simulate(pid: str, outcome: str):
    """Test helper: act as the customer without opening the checkout page."""
    return await _complete(pid, outcome)


async def _complete(pid: str, outcome: str) -> dict:
    if outcome not in ("succeeded", "failed"):
        raise HTTPException(422, "outcome must be succeeded or failed")
    async with store.lock:
        payment = store.payments.get(pid)
        if payment is None:
            raise HTTPException(404, "payment not found")
        if payment["status"] == "pending":
            _transition(payment, outcome, f"payment.{outcome}")
        return _public(payment)


@app.get("/admin/chaos", dependencies=[Depends(require_key)])
async def get_chaos():
    return store.chaos


@app.put("/admin/chaos", dependencies=[Depends(require_key)])
async def set_chaos(chaos: Chaos):
    store.chaos = chaos
    return store.chaos


@app.get("/admin/webhooks", dependencies=[Depends(require_key)])
async def webhook_log(limit: int = 100):
    return store.webhook_log[-limit:]


@app.get("/health")
async def health():
    return {"status": "ok", "payments": len(store.payments)}


# ----- Hosted checkout page ------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FakePay sandbox checkout</title>
<style>
  :root {{ color-scheme: light dark; --bg:#f4f1ea; --card:#fffdf8; --ink:#1d1b16; --muted:#6b6457;
          --line:#e2dccf; --pay:#3b3a8f; --decline:#8f3b3b; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#16150f; --card:#201f18; --ink:#f1ece0;
          --muted:#a59d8c; --line:#38352a; --pay:#8b8af0; --decline:#e08a8a; }} }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg);
         color:var(--ink); font:16px/1.5 ui-sans-serif, system-ui, sans-serif; padding:24px }}
  .card {{ width:min(420px,100%); background:var(--card); border:1px solid var(--line);
          border-radius:14px; padding:28px }}
  .tag {{ display:inline-block; font:600 11px/1 ui-monospace, monospace; letter-spacing:.08em;
         text-transform:uppercase; border:1px dashed var(--muted); color:var(--muted);
         padding:5px 8px; border-radius:6px }}
  h1 {{ font-size:15px; font-weight:600; margin:18px 0 2px; color:var(--muted) }}
  .amount {{ font-size:40px; font-weight:700; letter-spacing:-.02em; margin:0 0 18px;
            font-variant-numeric: tabular-nums }}
  dl {{ display:grid; grid-template-columns:auto 1fr; gap:6px 16px; margin:0 0 24px;
       font-size:14px }}
  dt {{ color:var(--muted) }} dd {{ margin:0; text-align:right; font-family:ui-monospace, monospace;
       overflow-wrap:anywhere }}
  .actions {{ display:grid; gap:10px }}
  button {{ font:600 16px/1 inherit; padding:15px; border-radius:10px; cursor:pointer;
           border:1px solid transparent }}
  .pay {{ background:var(--pay); color:var(--card) }}
  .decline {{ background:transparent; color:var(--decline); border-color:var(--line) }}
  button:focus-visible {{ outline:3px solid var(--pay); outline-offset:2px }}
  .note {{ font-size:13px; color:var(--muted); margin:18px 0 0 }}
  .done {{ font-size:18px; font-weight:600 }}
</style></head>
<body><main class="card">
  <span class="tag">Sandbox · no real money</span>
  <h1>Pay {merchant}</h1>
  <p class="amount">৳{amount}</p>
  <dl><dt>For</dt><dd>{description}</dd><dt>Payment</dt><dd>{pid}</dd></dl>
  {body}
  <p class="note">FakePay simulates a payment provider for testing. Webhooks may arrive late,
  twice, or not at all, depending on the chaos settings.</p>
</main></body></html>"""


@app.get("/checkout/{pid}", response_class=HTMLResponse)
async def checkout_page(pid: str):
    payment = store.payments.get(pid)
    if payment is None:
        raise HTTPException(404, "payment not found")
    if payment["status"] == "pending":
        body = f"""<form method="post" class="actions">
          <button class="pay" name="action" value="succeeded">Pay ৳{payment['amount']:,}</button>
          <button class="decline" name="action" value="failed">Decline payment</button></form>"""
    else:
        body = f'<p class="done">This payment is {html.escape(payment["status"])}.</p>'
    return PAGE.format(merchant="Aashon", amount=f"{payment['amount']:,}",
                       description=html.escape(payment["description"] or payment["reference"]),
                       pid=html.escape(pid), body=body)


@app.post("/checkout/{pid}")
async def checkout_submit(pid: str, action: str = Form()):
    payment = await _complete(pid, action)
    separator = "&" if "?" in store.payments[pid]["_return_url"] else "?"
    return RedirectResponse(
        f"{store.payments[pid]['_return_url']}{separator}status={payment['status']}", 303
    )


@app.middleware("http")
async def no_store(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response

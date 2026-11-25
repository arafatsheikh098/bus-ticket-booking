"""Client for the FakePay provider API, plus webhook signature checks."""

import hashlib
import hmac
import time

import httpx


class ProviderError(Exception):
    """The provider could not be reached or failed. Safe to retry later."""


class ProviderRejected(Exception):
    """The provider refused the request (for example, refunding an unpaid payment)."""


class WebhookSignatureError(Exception):
    pass


def verify_signature(secret: str, header: str | None, body: bytes,
                     tolerance_seconds: int = 300, now: float | None = None) -> None:
    """Check an `X-FakePay-Signature: t=<unix>,v1=<hex hmac>` header.

    The timestamp is part of the signed message, so an attacker cannot replay an old
    webhook with a fresh timestamp.
    """
    if not header:
        raise WebhookSignatureError("missing signature")
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        timestamp, signature = int(parts["t"]), parts["v1"]
    except (KeyError, ValueError):
        raise WebhookSignatureError("malformed signature") from None
    if abs((now or time.time()) - timestamp) > tolerance_seconds:
        raise WebhookSignatureError("signature too old")
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body,
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookSignatureError("signature mismatch")


class FakePayClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 5.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs):
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{method} {path}: {exc.__class__.__name__}") from exc
        if response.status_code == 404:
            return None
        if response.status_code in (409, 422):
            raise ProviderRejected(response.text)
        if response.status_code >= 400:
            raise ProviderError(f"{method} {path}: HTTP {response.status_code}")
        return response.json()

    async def create_payment(self, *, amount: int, reference: str, idempotency_key: str,
                             callback_url: str, return_url: str, description: str) -> dict:
        return await self._request(
            "POST", "/v1/payments",
            headers={"Idempotency-Key": idempotency_key},
            json={"amount": amount, "currency": "BDT", "reference": reference,
                  "callback_url": callback_url, "return_url": return_url,
                  "description": description},
        )

    async def get_payment(self, provider_ref: str) -> dict | None:
        return await self._request("GET", f"/v1/payments/{provider_ref}")

    async def find_by_reference(self, reference: str) -> dict | None:
        found = await self._request("GET", "/v1/payments", params={"reference": reference})
        return found[0] if found else None

    async def cancel(self, provider_ref: str) -> dict | None:
        return await self._request("POST", f"/v1/payments/{provider_ref}/cancel")

    async def refund(self, provider_ref: str, *, idempotency_key: str) -> dict | None:
        return await self._request("POST", f"/v1/payments/{provider_ref}/refund",
                                   headers={"Idempotency-Key": idempotency_key})

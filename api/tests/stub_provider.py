"""In-memory stand-in for FakePay, with switches to simulate outages."""

import uuid

from app.services.provider import ProviderError, ProviderRejected


class StubProvider:
    def __init__(self):
        self.payments: dict[str, dict] = {}
        self.by_key: dict[str, str] = {}
        self.down = False
        self.calls: list[str] = []

    def _check(self, name):
        self.calls.append(name)
        if self.down:
            raise ProviderError("stub provider is down")

    async def create_payment(self, *, amount, reference, idempotency_key, callback_url,
                             return_url, description):
        self._check("create")
        if idempotency_key in self.by_key:
            return dict(self.payments[self.by_key[idempotency_key]])
        pid = f"pay_{uuid.uuid4().hex[:12]}"
        self.payments[pid] = {"id": pid, "status": "pending", "amount": amount,
                              "reference": reference,
                              "checkout_url": f"http://fakepay.test/checkout/{pid}"}
        self.by_key[idempotency_key] = pid
        return dict(self.payments[pid])

    async def get_payment(self, ref):
        self._check("get")
        p = self.payments.get(ref)
        return dict(p) if p else None

    async def find_by_reference(self, reference):
        self._check("find")
        for p in self.payments.values():
            if p["reference"] == reference:
                return dict(p)
        return None

    async def cancel(self, ref):
        self._check("cancel")
        p = self.payments[ref]
        if p["status"] == "pending":
            p["status"] = "cancelled"
        return dict(p)

    async def refund(self, ref, *, idempotency_key):
        self._check("refund")
        p = self.payments[ref]
        if p["status"] == "succeeded":
            p["status"] = "refunded"
        elif p["status"] != "refunded":
            raise ProviderRejected("not paid")
        return dict(p)

    # Test helpers ------------------------------------------------------------------
    def complete(self, ref, outcome="succeeded"):
        p = self.payments[ref]
        if p["status"] == "pending":
            p["status"] = outcome
        return dict(p)

    def ref_for(self, checkout_url: str) -> str:
        return checkout_url.rsplit("/", 1)[1]

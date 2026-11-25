from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from app.deps import get_pool, get_session
from app.ratelimit import limit
from app.services import bookings, payments

router = APIRouter(prefix="/api", tags=["bookings"])


def get_provider(request: Request):
    return request.app.state.provider


class CheckoutRequest(BaseModel):
    name: str = Field(max_length=200)
    phone: str = Field(max_length=30)


class PhoneBody(BaseModel):
    phone: str = Field(max_length=30)


@router.post("/holds/{hold_id}/checkout", dependencies=[limit("checkout:session")])
async def checkout(hold_id: UUID, body: CheckoutRequest, session: str = Depends(get_session),
                   pool: asyncpg.Pool = Depends(get_pool), provider=Depends(get_provider)):
    return await payments.start_checkout(pool, provider, hold_id, session, body.name, body.phone)


@router.post("/webhooks/fakepay")
async def fakepay_webhook(request: Request, x_fakepay_signature: str | None = Header(default=None),
                          pool: asyncpg.Pool = Depends(get_pool), provider=Depends(get_provider)):
    return await payments.handle_webhook(pool, provider, await request.body(), x_fakepay_signature)


@router.get("/bookings/{pnr}", dependencies=[limit("ticket:ip")])
async def get_booking(pnr: str, phone: str, pool: asyncpg.Pool = Depends(get_pool)):
    return await bookings.get_booking(pool, pnr, phone)


@router.post("/bookings/{pnr}/cancel", dependencies=[limit("ticket:ip")])
async def cancel_booking(pnr: str, body: PhoneBody, pool: asyncpg.Pool = Depends(get_pool),
                         provider=Depends(get_provider)):
    return await bookings.cancel_booking(pool, provider, pnr, body.phone)

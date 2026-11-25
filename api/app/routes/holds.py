from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Response
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.deps import get_pool, get_session
from app.ratelimit import limit
from app.services import holds

router = APIRouter(prefix="/api", tags=["holds"])


class HoldRequest(BaseModel):
    seats: list[Annotated[str, StringConstraints(min_length=1, max_length=4)]] = Field(
        default_factory=list, max_length=10)


@router.put("/trips/{trip_id}/hold", dependencies=[limit("hold:session", "hold:ip")])
async def set_hold(trip_id: int, body: HoldRequest, response: Response,
                   session: str = Depends(get_session), pool: asyncpg.Pool = Depends(get_pool)):
    """Set this session's held seats on a trip to exactly `seats`. An empty list releases."""
    hold = await holds.set_hold(pool, trip_id, session, body.seats)
    if hold is None:
        response.status_code = 204
        return None
    return hold


@router.get("/trips/{trip_id}/hold")
async def my_hold(trip_id: int, response: Response,
                  session: str = Depends(get_session), pool: asyncpg.Pool = Depends(get_pool)):
    hold = await holds.get_open_hold(pool, trip_id, session)
    if hold is None:
        response.status_code = 204
        return None
    return hold


@router.get("/holds/{hold_id}")
async def get_hold(hold_id: UUID, session: str = Depends(get_session),
                   pool: asyncpg.Pool = Depends(get_pool)):
    return await holds.get_hold(pool, hold_id, session)


@router.delete("/holds/{hold_id}", status_code=204)
async def release(hold_id: UUID, session: str = Depends(get_session),
                  pool: asyncpg.Pool = Depends(get_pool)):
    await holds.release_hold(pool, hold_id, session)

from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.deps import get_pool
from app.services import trips

router = APIRouter(prefix="/api", tags=["trips"])


@router.get("/cities")
async def cities(pool: asyncpg.Pool = Depends(get_pool)):
    return await trips.list_cities(pool)


@router.get("/trips")
async def search(
    origin: str = Query(alias="from"),
    destination: str = Query(alias="to"),
    day: date = Query(alias="date"),
    pool: asyncpg.Pool = Depends(get_pool),
):
    return await trips.search_trips(pool, origin, destination, day)


@router.get("/trips/{trip_id}")
async def trip_detail(trip_id: int, pool: asyncpg.Pool = Depends(get_pool)):
    return await trips.get_trip(pool, trip_id)

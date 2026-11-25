import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import db
from app.realtime import SeatHub
from app.realtime import router as realtime_router
from app.config import settings
from app.errors import AppError, app_error_handler
from app.routes import holds, trips
from app.seed import ensure_demo_data
from app.services.sweeper import run_sweeper

log = logging.getLogger("aashon")


def create_app(*, pool=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = pool or await db.create_pool(settings.database_url)
        await db.migrate(app.state.pool)
        if settings.seed_demo_data:
            created = await ensure_demo_data(app.state.pool)
            log.info("seeded %d trips", created)
        app.state.hub = SeatHub(settings.database_url)
        await app.state.hub.start()
        tasks = []
        if settings.run_background_tasks:
            tasks.append(asyncio.create_task(run_sweeper(app.state.pool)))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await app.state.hub.stop()
            if pool is None:
                await app.state.pool.close()

    app = FastAPI(title="Aashon API", version="1.0.0", lifespan=lifespan)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(trips.router)
    app.include_router(holds.router)
    app.include_router(realtime_router)

    @app.get("/api/health")
    async def health():
        await app.state.pool.fetchval("SELECT 1")
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = create_app()

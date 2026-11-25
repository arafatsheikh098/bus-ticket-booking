import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.config import settings
from app.errors import AppError, app_error_handler
from app.routes import trips
from app.seed import ensure_demo_data

log = logging.getLogger("aashon")


def create_app(*, pool=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = pool or await db.create_pool(settings.database_url)
        await db.migrate(app.state.pool)
        if settings.seed_demo_data:
            created = await ensure_demo_data(app.state.pool)
            log.info("seeded %d trips", created)
        try:
            yield
        finally:
            if pool is None:
                await app.state.pool.close()

    app = FastAPI(title="Aashon API", version="1.0.0", lifespan=lifespan)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(trips.router)

    @app.get("/api/health")
    async def health():
        await app.state.pool.fetchval("SELECT 1")
        return {"status": "ok"}

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = create_app()

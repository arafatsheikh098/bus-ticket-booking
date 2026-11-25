from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """An error with a stable machine-readable code, returned as JSON."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


class NotFound(AppError):
    def __init__(self, what: str):
        super().__init__(404, "not_found", f"{what} not found")


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.message, **exc.extra}},
    )

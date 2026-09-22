"""FastAPI entry point for the local Harness control center."""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import state_store
from .runs_api import create_router as create_runs_router
from .work_items_api import create_router


STATIC_DIR = Path(__file__).with_name("static")


def _error_response(
    *, http_status: int, code: str, message: str, fields: dict[str, str] | None = None
) -> JSONResponse:
    """Build the stable error envelope consumed by the browser UI."""

    error: dict[str, object] = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return JSONResponse(status_code=http_status, content={"error": error})


def create_app(
    database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
) -> FastAPI:
    """Create a local control-center app bound to one state-store database."""

    selected_database = Path(database_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        state_store.initialize_database(selected_database)
        application.state.database_path = selected_database
        yield

    application = FastAPI(
        title="Harness Control Center",
        description="Local WorkItem visibility and Draft capture for Harness v2.",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.database_path = selected_database
    application.include_router(create_router())
    application.include_router(create_runs_router())
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        fields: dict[str, str] = {}
        for issue in error.errors():
            location = issue.get("loc", ())
            if len(location) >= 2 and location[0] in {"body", "query"}:
                fields[str(location[-1])] = str(issue.get("msg", "입력값을 확인해 주세요."))
        return _error_response(
            http_status=422,
            code="validation_error",
            message="입력값을 확인해 주세요.",
            fields=fields,
        )

    @application.exception_handler(state_store.NotFoundError)
    async def not_found_handler(_request: Request, _error: Exception) -> JSONResponse:
        return _error_response(
            http_status=status.HTTP_404_NOT_FOUND,
            code="not_found",
            message="요청한 대상을 찾을 수 없습니다.",
        )

    @application.exception_handler(state_store.ConflictError)
    async def conflict_handler(_request: Request, error: Exception) -> JSONResponse:
        return _error_response(
            http_status=status.HTTP_409_CONFLICT,
            code="state_conflict",
            message=str(error),
        )

    async def database_error_handler(_request: Request, _error: Exception) -> JSONResponse:
        return _error_response(
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="state_store_unavailable",
            message="상태 저장소를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
        )

    application.add_exception_handler(state_store.StateStoreError, database_error_handler)
    application.add_exception_handler(sqlite3.Error, database_error_handler)

    @application.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, _error: Exception) -> JSONResponse:
        return _error_response(
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            message="예상하지 못한 오류가 발생했습니다.",
        )

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return application


app = create_app()

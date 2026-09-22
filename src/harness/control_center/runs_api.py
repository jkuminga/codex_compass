"""Read-only HTTP API for browsing Harness Runs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request

from .. import state_store


RunStatus = Literal["running", "succeeded", "failed", "interrupted", "cancelled"]


def _database_path(request: Request) -> Path:
    return request.app.state.database_path


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_detail_payload(context: dict[str, object]) -> dict[str, object]:
    run = context["run"]
    assert isinstance(run, dict)
    return {
        "run": {
            key: run.get(key)
            for key in (
                "id",
                "work_item_id",
                "intent",
                "status",
                "started_at",
                "ended_at",
                "summary",
                "termination_reason",
            )
        },
        "work_item": context["work_item"],
        "artifacts": context["artifacts"],
    }


def create_router() -> APIRouter:
    """Create the read-only Run routes mounted under ``/api``."""

    router = APIRouter(prefix="/api/runs", tags=["runs"])

    @router.get("")
    def list_runs(
        request: Request,
        statuses: Annotated[list[RunStatus] | None, Query(alias="status")] = None,
        query: Annotated[str | None, Query(max_length=200)] = None,
        work_item_id: Annotated[str | None, Query(max_length=200)] = None,
        started_from: datetime | None = None,
        started_to: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, object]:
        started_from_text = _utc_text(started_from)
        started_to_text = _utc_text(started_to)
        if started_from_text and started_to_text and started_from_text > started_to_text:
            raise state_store.ConflictError("started_from cannot be later than started_to")
        return state_store.list_runs(
            statuses=statuses or (),
            query=query,
            work_item_id=work_item_id,
            started_from=started_from_text,
            started_to=started_to_text,
            limit=limit,
            offset=offset,
            database_path=_database_path(request),
        )

    @router.get("/{run_id}")
    def get_run(run_id: str, request: Request) -> dict[str, object]:
        context = state_store.get_run_detail(
            run_id, database_path=_database_path(request)
        )
        return _run_detail_payload(context)

    return router

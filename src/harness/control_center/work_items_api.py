"""HTTP API for viewing WorkItems and creating lightweight Draft WorkItems."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import state_store


WorkItemKind = Literal[
    "implementation",
    "bug",
    "research",
    "decision",
    "refactor",
    "migration",
    "verification",
    "maintenance",
]

WorkItemStatusFilter = Literal[
    "draft",
    "backlog",
    "ready",
    "in_progress",
    "blocked",
    "done",
    "cancelled",
]


class DraftWorkItemRequest(BaseModel):
    """User-authored fields accepted when creating one Draft WorkItem."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    kind: WorkItemKind
    goal: str = Field(min_length=1, max_length=2000)
    description: str | None = Field(default=None, max_length=5000)

    @field_validator("description")
    @classmethod
    def empty_description_becomes_none(cls, value: str | None) -> str | None:
        return value or None


def _database_path(request: Request) -> Path:
    """Return the state database selected by the current FastAPI application."""

    return request.app.state.database_path


def _summary(item: dict[str, object]) -> dict[str, object]:
    """Reduce a WorkItem to the fields needed by the list cards."""

    return {
        key: item.get(key)
        for key in (
            "id",
            "title",
            "kind",
            "status",
            "priority",
            "is_draft",
            "feature_id",
            "feature_title",
            "goal",
            "created_at",
        )
    }


def _created_draft(item: dict[str, object]) -> dict[str, object]:
    """Return the stable Draft fields promised by the creation API contract."""

    return {
        key: item.get(key)
        for key in (
            "id",
            "title",
            "kind",
            "goal",
            "description",
            "is_draft",
            "status",
            "priority",
            "feature_id",
            "next_action",
            "created_at",
        )
    }


def _matches_status(item: dict[str, object], filters: set[str]) -> bool:
    """Apply the UI's virtual Draft status without changing the DB status model."""

    if not filters:
        return True
    status_value = item["status"]
    is_draft = bool(item["is_draft"])
    return any(
        (filter_value == "draft" and status_value == "backlog" and is_draft)
        or (filter_value == "backlog" and status_value == "backlog" and not is_draft)
        or (filter_value not in {"draft", "backlog"} and status_value == filter_value)
        for filter_value in filters
    )


def create_router() -> APIRouter:
    """Create the WorkItem routes mounted under the local ``/api`` prefix."""

    router = APIRouter(prefix="/api", tags=["work-items"])

    @router.post("/draft-work-items", status_code=status.HTTP_201_CREATED)
    def create_draft(payload: DraftWorkItemRequest, request: Request) -> dict[str, object]:
        work_item = state_store.create_draft_work_item(
            title=payload.title,
            kind=payload.kind,
            goal=payload.goal,
            description=payload.description,
            actor="web_console",
            database_path=_database_path(request),
        )
        work_item["is_draft"] = bool(work_item["is_draft"])
        return {"work_item": _created_draft(work_item)}

    @router.get("/work-items")
    def list_items(
        request: Request,
        status_filter: Annotated[
            list[WorkItemStatusFilter] | None, Query(alias="status")
        ] = None,
        kind: Annotated[list[WorkItemKind] | None, Query()] = None,
    ) -> dict[str, object]:
        items = state_store.list_work_items(database_path=_database_path(request))
        status_values = set(status_filter or ())
        kind_values = set(kind or ())
        filtered = [
            _summary(item)
            for item in items
            if _matches_status(item, status_values)
            and (not kind_values or item["kind"] in kind_values)
        ]
        return {"work_items": filtered}

    @router.get("/work-items/{work_item_id}")
    def get_item(work_item_id: str, request: Request) -> dict[str, object]:
        context = state_store.get_work_item_context(
            work_item_id, database_path=_database_path(request)
        )
        context["work_item"]["is_draft"] = bool(context["work_item"]["is_draft"])
        return context

    return router

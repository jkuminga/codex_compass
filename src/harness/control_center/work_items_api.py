"""HTTP API for WorkItem management, Draft capture, and user memos."""

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

WorkItemPriority = Literal["urgent", "high", "normal", "low"]
WebStatusTarget = Literal["ready", "done", "cancelled"]
MemoKind = Literal["general", "decision", "problem", "idea", "question", "reference"]
MemoStatus = Literal["open", "closed"]


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


class ReviseWorkItemRequest(BaseModel):
    """Planning fields that the web console may request to revise."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    kind: WorkItemKind | None = None
    goal: str | None = Field(default=None, min_length=1, max_length=2000)
    description: str | None = Field(default=None, max_length=5000)
    priority: WorkItemPriority | None = None
    next_action: str | None = Field(default=None, max_length=2000)

    @field_validator("description", "next_action")
    @classmethod
    def empty_optional_text_becomes_none(cls, value: str | None) -> str | None:
        return value or None


class ChangeWorkItemStatusRequest(BaseModel):
    """A status selected from the web console's deliberately small transition set."""

    model_config = ConfigDict(extra="forbid")
    status: WebStatusTarget


class CreateMemoRequest(BaseModel):
    """User-authored fields for one WorkItem memo."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    kind: MemoKind = "general"
    author: str | None = Field(default=None, max_length=200)
    status: MemoStatus = "open"
    is_pinned: bool = False

    @field_validator("author")
    @classmethod
    def blank_author_becomes_none(cls, value: str | None) -> str | None:
        return value or None


class UpdateMemoRequest(BaseModel):
    """Partial editable fields for an existing memo."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=100_000)
    kind: MemoKind | None = None
    author: str | None = Field(default=None, max_length=200)
    status: MemoStatus | None = None
    is_pinned: bool | None = None

    @field_validator("author")
    @classmethod
    def blank_update_author_becomes_none(cls, value: str | None) -> str | None:
        return value or None


class ReorderMemosRequest(BaseModel):
    """Complete visible-order payload sent after an unfiltered drag."""

    model_config = ConfigDict(extra="forbid")

    memo_ids: list[str] = Field(min_length=1, max_length=500)


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


def _detail(work_item_id: str, database_path: Path) -> dict[str, object]:
    """Build one detail response with server-owned management permissions."""

    context = state_store.get_work_item_context(work_item_id, database_path=database_path)
    context["work_item"]["is_draft"] = bool(context["work_item"]["is_draft"])
    context["capabilities"] = state_store.get_work_item_management_capabilities(
        work_item_id, database_path=database_path
    )
    return context


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
        return _detail(work_item_id, _database_path(request))

    @router.patch("/work-items/{work_item_id}")
    def revise_item(
        work_item_id: str, payload: ReviseWorkItemRequest, request: Request
    ) -> dict[str, object]:
        database_path = _database_path(request)
        changes = payload.model_dump(exclude_unset=True)
        capabilities = state_store.get_work_item_management_capabilities(
            work_item_id, database_path=database_path
        )
        disallowed = sorted(set(changes).difference(capabilities["editable_fields"]))
        if disallowed:
            raise state_store.ConflictError(
                f"현재 WorkItem에서는 수정할 수 없는 필드입니다: {', '.join(disallowed)}"
            )
        if changes:
            state_store.revise_work_item(
                work_item_id,
                actor="web_console",
                database_path=database_path,
                **changes,
            )
        return _detail(work_item_id, database_path)

    @router.patch("/work-items/{work_item_id}/status")
    def change_status(
        work_item_id: str, payload: ChangeWorkItemStatusRequest, request: Request
    ) -> dict[str, object]:
        database_path = _database_path(request)
        current = state_store.get_work_item(work_item_id, database_path=database_path)
        capabilities = state_store.get_work_item_management_capabilities(
            work_item_id, database_path=database_path
        )
        if payload.status not in capabilities["allowed_statuses"]:
            raise state_store.ConflictError(
                f"웹 콘솔에서는 {current['status']} → {payload.status} 상태 변경을 허용하지 않습니다."
            )
        if payload.status == "done":
            state_store.close_work_item(
                work_item_id,
                actor="web_console",
                reason="사용자가 웹 콘솔에서 WorkItem 완료를 확인함",
                database_path=database_path,
            )
        else:
            state_store.change_work_item_status(
                work_item_id,
                payload.status,
                actor="web_console",
                reason="웹 콘솔에서 상태 변경",
                next_action=current["next_action"] if payload.status == "ready" else None,
                block_reason=None,
                database_path=database_path,
            )
        return _detail(work_item_id, database_path)

    @router.delete("/work-items/{work_item_id}")
    def delete_item(work_item_id: str, request: Request) -> dict[str, object]:
        database_path = _database_path(request)
        capabilities = state_store.get_work_item_management_capabilities(
            work_item_id, database_path=database_path
        )
        if not capabilities["can_delete"]:
            raise state_store.ConflictError(str(capabilities["delete_reason"]))
        state_store.delete_backlog_work_item(
            work_item_id,
            actor="web_console",
            reason="웹 콘솔에서 삭제",
            database_path=database_path,
        )
        return {"deleted_work_item_id": work_item_id}

    @router.get("/work-items/{work_item_id}/memos")
    def list_memos(
        work_item_id: str,
        request: Request,
        memo_status: MemoStatus | None = Query(default=None, alias="status"),
        memo_kind: MemoKind | None = Query(default=None, alias="kind"),
    ) -> dict[str, object]:
        memos = state_store.list_work_item_memos(
            work_item_id,
            status=memo_status,
            kind=memo_kind,
            database_path=_database_path(request),
        )
        return {"memos": memos}

    @router.post("/work-items/{work_item_id}/memos", status_code=status.HTTP_201_CREATED)
    def create_memo(
        work_item_id: str, payload: CreateMemoRequest, request: Request
    ) -> dict[str, object]:
        memo = state_store.create_work_item_memo(
            work_item_id,
            title=payload.title,
            content=payload.content,
            kind=payload.kind,
            author=payload.author,
            status=payload.status,
            is_pinned=payload.is_pinned,
            actor="web_console",
            database_path=_database_path(request),
        )
        return {"memo": memo}

    @router.patch("/work-items/{work_item_id}/memos/{memo_id}")
    def update_memo(
        work_item_id: str,
        memo_id: str,
        payload: UpdateMemoRequest,
        request: Request,
    ) -> dict[str, object]:
        changes = payload.model_dump(exclude_unset=True)
        for field in ("title", "content", "kind", "status", "is_pinned"):
            if field in changes and changes[field] is None:
                raise state_store.ConflictError(f"Memo {field} cannot be null")
        memo = state_store.update_work_item_memo(
            work_item_id,
            memo_id,
            database_path=_database_path(request),
            **changes,
        )
        return {"memo": memo}

    @router.delete("/work-items/{work_item_id}/memos/{memo_id}")
    def delete_memo(
        work_item_id: str, memo_id: str, request: Request
    ) -> dict[str, object]:
        state_store.delete_work_item_memo(
            work_item_id, memo_id, database_path=_database_path(request)
        )
        return {"deleted_memo_id": memo_id}

    @router.post("/work-items/{work_item_id}/memos/reorder")
    def reorder_memos(
        work_item_id: str,
        payload: ReorderMemosRequest,
        request: Request,
    ) -> dict[str, object]:
        memos = state_store.reorder_work_item_memos(
            work_item_id,
            payload.memo_ids,
            database_path=_database_path(request),
        )
        return {"memos": memos}

    return router

"""Safe, workflow-oriented access to the Harness v2 SQLite state store.

Callers use the functions in this module instead of issuing SQL directly. Each
write function owns its validation, transaction, and State Event recording.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / ".harness" / "state.db"
SCHEMA_PATH = PROJECT_ROOT / ".harness" / "schema.sql"


class StateStoreError(RuntimeError):
    """Base error for a rejected or failed state-store operation."""


class NotFoundError(StateStoreError):
    """Raised when a requested state-store entity does not exist."""


class ConflictError(StateStoreError):
    """Raised when an operation conflicts with the current state."""


_UNSET = object()


def _utc_now() -> str:
    """Return the current UTC time in a sortable ISO-8601 form."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _generate_id(prefix: str) -> str:
    """Create a compact unique identifier with a domain-specific prefix."""

    return f"{prefix}-{uuid4().hex[:12]}"


def _as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _require_row(
    database: sqlite3.Connection, query: str, parameters: Sequence[Any], label: str
) -> dict[str, Any]:
    row = database.execute(query, parameters).fetchone()
    if row is None:
        raise NotFoundError(f"{label} not found")
    return dict(row)


def open_database(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> sqlite3.Connection:
    """Open the state DB and guarantee that SQLite foreign keys are active."""

    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    enabled = database.execute("PRAGMA foreign_keys").fetchone()[0]
    if enabled != 1:
        database.close()
        raise StateStoreError("SQLite foreign key activation failed")
    return database


def initialize_database(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    schema_path: str | Path = SCHEMA_PATH,
) -> dict[str, Any]:
    """Create or safely reapply the versioned schema to a state database."""

    database = open_database(database_path)
    try:
        database.executescript(Path(schema_path).read_text())
    except (OSError, sqlite3.Error) as error:
        raise StateStoreError(f"database initialization failed: {error}") from error
    finally:
        database.close()
    return check_database_health(database_path)


@contextmanager
def _transaction(database_path: str | Path) -> Iterator[sqlite3.Connection]:
    database = open_database(database_path)
    try:
        database.execute("BEGIN IMMEDIATE")
        yield database
        database.commit()
    except StateStoreError:
        database.rollback()
        raise
    except sqlite3.Error as error:
        database.rollback()
        raise ConflictError(str(error)) from error
    finally:
        database.close()


def _append_state_event(
    database: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: str,
    event_type: str,
    actor: str,
    from_status: str | None = None,
    to_status: str | None = None,
    run_id: str | None = None,
    reason: str | None = None,
    payload: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> None:
    """Append one immutable audit event inside the caller's transaction."""

    database.execute(
        """
        INSERT INTO state_events (
          entity_type, entity_id, event_type, from_status, to_status,
          run_id, actor, reason, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            entity_id,
            event_type,
            from_status,
            to_status,
            run_id,
            actor,
            reason,
            json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            created_at or _utc_now(),
        ),
    )


def check_database_health(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Report SQLite structural integrity and foreign-key violations."""

    database = open_database(database_path)
    try:
        integrity = [row[0] for row in database.execute("PRAGMA integrity_check")]
        foreign_key_violations = [
            dict(row) for row in database.execute("PRAGMA foreign_key_check")
        ]
        return {
            "ok": integrity == ["ok"] and not foreign_key_violations,
            "integrity": integrity,
            "foreign_key_violations": foreign_key_violations,
        }
    finally:
        database.close()


def _require_no_running_run(database: sqlite3.Connection, work_item_id: str) -> None:
    """Reject a state change that would abandon an active Run."""

    if database.execute(
        "SELECT 1 FROM runs WHERE work_item_id = ? AND status = 'running' LIMIT 1",
        (work_item_id,),
    ).fetchone():
        raise ConflictError("running Run must finish before WorkItem status changes")


def _require_valid_evidence(database: sqlite3.Connection, criterion_id: str) -> None:
    """Require passed or non-applicable proof from the Criterion's own WorkItem."""

    valid = database.execute(
        """
        SELECT 1
        FROM acceptance_criteria AS criterion
        JOIN criterion_evidence AS evidence ON evidence.criterion_id = criterion.id
        JOIN artifacts AS artifact ON artifact.id = evidence.artifact_id
        JOIN runs AS evidence_run ON evidence_run.id = artifact.run_id
        WHERE criterion.id = ?
          AND evidence_run.work_item_id = criterion.work_item_id
          AND artifact.verification_status IN ('passed', 'not_applicable')
        LIMIT 1
        """,
        (criterion_id,),
    ).fetchone()
    if valid is None:
        raise ConflictError("valid Evidence is required to pass an Acceptance Criterion")


def _completion_issues(
    database: sqlite3.Connection, work_item_id: str
) -> tuple[dict[str, Any], list[str]]:
    """Calculate the small set of reasons a WorkItem cannot finish yet."""

    counts = _require_row(
        database,
        "SELECT * FROM work_item_verification WHERE work_item_id = ?",
        (work_item_id,),
        "WorkItem verification",
    )
    issues: list[str] = []
    if counts["total_criteria"] == 0:
        issues.append("at least one Acceptance Criterion is required")
    if counts["pending_count"]:
        issues.append(f"{counts['pending_count']} Acceptance Criterion still pending")
    if counts["failed_count"]:
        issues.append(f"{counts['failed_count']} Acceptance Criterion failed")
    if counts["missing_evidence_count"]:
        issues.append(f"{counts['missing_evidence_count']} passed Criterion lacks valid Evidence")
    return counts, issues


def _validate_work_item_completion(
    database: sqlite3.Connection, work_item_id: str
) -> None:
    """Raise a readable error unless every completion requirement is satisfied."""

    _, issues = _completion_issues(database, work_item_id)
    if issues:
        raise ConflictError("WorkItem cannot complete: " + "; ".join(issues))


def create_feature(
    *,
    title: str,
    goal: str,
    actor: str,
    priority: str = "normal",
    status: str = "planned",
    feature_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Create a Feature, the large capability grouping for WorkItems."""

    identifier = feature_id or _generate_id("FEAT")
    now = _utc_now()
    with _transaction(database_path) as database:
        database.execute(
            """
            INSERT INTO features (id, title, goal, status, priority, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (identifier, title, goal, status, priority, now, now),
        )
        _append_state_event(
            database,
            entity_type="feature",
            entity_id=identifier,
            event_type="created",
            actor=actor,
            to_status=status,
            created_at=now,
        )
        return _require_row(database, "SELECT * FROM features WHERE id = ?", (identifier,), "Feature")


def update_feature(
    feature_id: str,
    *,
    actor: str,
    title: str | None = None,
    goal: str | None = None,
    priority: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Revise mutable Feature fields without storing derived progress."""

    now = _utc_now()
    with _transaction(database_path) as database:
        current = _require_row(
            database, "SELECT * FROM features WHERE id = ?", (feature_id,), "Feature"
        )
        changes = {
            key: value
            for key, value in {"title": title, "goal": goal, "priority": priority}.items()
            if value is not None
        }
        if not changes:
            return current
        assignments = ", ".join(f"{field} = ?" for field in changes)
        database.execute(
            f"UPDATE features SET {assignments}, updated_at = ? WHERE id = ?",
            (*changes.values(), now, feature_id),
        )
        _append_state_event(
            database,
            entity_type="feature",
            entity_id=feature_id,
            event_type="updated",
            actor=actor,
            payload={"fields": sorted(changes)},
            created_at=now,
        )
        return _require_row(database, "SELECT * FROM features WHERE id = ?", (feature_id,), "Feature")


def change_feature_status(
    feature_id: str,
    status: str,
    *,
    actor: str,
    reason: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Change a Feature lifecycle state and append its audit event."""

    now = _utc_now()
    with _transaction(database_path) as database:
        current = _require_row(
            database, "SELECT * FROM features WHERE id = ?", (feature_id,), "Feature"
        )
        if current["status"] == status:
            return current
        database.execute(
            "UPDATE features SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, feature_id),
        )
        _append_state_event(
            database,
            entity_type="feature",
            entity_id=feature_id,
            event_type="status_changed",
            actor=actor,
            from_status=current["status"],
            to_status=status,
            reason=reason,
            created_at=now,
        )
        return _require_row(database, "SELECT * FROM features WHERE id = ?", (feature_id,), "Feature")


def get_feature(
    feature_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Return one Feature with its calculated WorkItem progress."""

    database = open_database(database_path)
    try:
        feature = _require_row(
            database, "SELECT * FROM features WHERE id = ?", (feature_id,), "Feature"
        )
        feature["progress"] = _as_dict(
            database.execute(
                "SELECT * FROM feature_progress WHERE feature_id = ?", (feature_id,)
            ).fetchone()
        )
        return feature
    finally:
        database.close()


def list_features(
    *, database_path: str | Path = DEFAULT_DATABASE_PATH
) -> list[dict[str, Any]]:
    """Return all Features with their calculated progress."""

    database = open_database(database_path)
    try:
        features = [dict(row) for row in database.execute("SELECT * FROM features ORDER BY created_at, id")]
        progress = {
            row["feature_id"]: dict(row)
            for row in database.execute("SELECT * FROM feature_progress")
        }
        for feature in features:
            feature["progress"] = progress.get(feature["id"])
        return features
    finally:
        database.close()


def create_work_item(
    *,
    title: str,
    kind: str,
    goal: str,
    actor: str,
    feature_id: str | None = None,
    priority: str = "normal",
    next_action: str | None = None,
    acceptance_criteria: Sequence[str] = (),
    work_item_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Create one executable WorkItem and its initial completion criteria."""

    identifier = work_item_id or _generate_id("WI")
    now = _utc_now()
    with _transaction(database_path) as database:
        database.execute(
            """
            INSERT INTO work_items (
              id, feature_id, title, kind, goal, status, priority, next_action,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'backlog', ?, ?, ?, ?)
            """,
            (identifier, feature_id, title, kind, goal, priority, next_action, now, now),
        )
        _append_state_event(
            database,
            entity_type="work_item",
            entity_id=identifier,
            event_type="created",
            actor=actor,
            to_status="backlog",
            created_at=now,
        )
        for sort_order, description in enumerate(acceptance_criteria, start=1):
            criterion_id = _generate_id("AC")
            database.execute(
                """
                INSERT INTO acceptance_criteria (
                  id, work_item_id, description, status, sort_order
                ) VALUES (?, ?, ?, 'pending', ?)
                """,
                (criterion_id, identifier, description, sort_order),
            )
            _append_state_event(
                database,
                entity_type="acceptance_criterion",
                entity_id=criterion_id,
                event_type="created",
                actor=actor,
                to_status="pending",
                created_at=now,
            )
        return _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (identifier,), "WorkItem"
        )


def revise_work_item(
    work_item_id: str,
    *,
    actor: str,
    title: str | None = None,
    kind: str | None = None,
    goal: str | None = None,
    priority: str | None = None,
    next_action: str | None | object = _UNSET,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Revise a non-terminal WorkItem while preserving its execution history."""

    now = _utc_now()
    with _transaction(database_path) as database:
        current = _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )
        if current["status"] in {"done", "cancelled"}:
            raise ConflictError("terminal WorkItem cannot be revised")
        changes: dict[str, Any] = {
            key: value
            for key, value in {
                "title": title,
                "kind": kind,
                "goal": goal,
                "priority": priority,
            }.items()
            if value is not None
        }
        if next_action is not _UNSET:
            changes["next_action"] = next_action
        if not changes:
            return current
        assignments = ", ".join(f"{field} = ?" for field in changes)
        database.execute(
            f"UPDATE work_items SET {assignments}, updated_at = ? WHERE id = ?",
            (*changes.values(), now, work_item_id),
        )
        _append_state_event(
            database,
            entity_type="work_item",
            entity_id=work_item_id,
            event_type="updated",
            actor=actor,
            payload={"fields": sorted(changes)},
            created_at=now,
        )
        return _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )


def delete_backlog_work_item(
    work_item_id: str,
    *,
    actor: str,
    reason: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> None:
    """Delete only an unstarted backlog candidate; executed work stays as history."""

    now = _utc_now()
    with _transaction(database_path) as database:
        work_item = _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )
        if work_item["status"] != "backlog":
            raise ConflictError("only a backlog WorkItem can be deleted")
        if database.execute(
            "SELECT 1 FROM runs WHERE work_item_id = ? LIMIT 1", (work_item_id,)
        ).fetchone():
            raise ConflictError("WorkItem with Run history cannot be deleted")
        database.execute("DELETE FROM acceptance_criteria WHERE work_item_id = ?", (work_item_id,))
        database.execute("DELETE FROM work_items WHERE id = ?", (work_item_id,))
        _append_state_event(
            database,
            entity_type="work_item",
            entity_id=work_item_id,
            event_type="deleted",
            actor=actor,
            from_status="backlog",
            reason=reason,
            created_at=now,
        )


def get_work_item(
    work_item_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Return one WorkItem's current state."""

    database = open_database(database_path)
    try:
        return _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )
    finally:
        database.close()


def list_next_work_items(
    *,
    limit: int = 20,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """Return ready WorkItems ordered by priority and age."""

    database = open_database(database_path)
    try:
        return [
            dict(row)
            for row in database.execute(
                "SELECT * FROM next_work_items LIMIT ?", (max(0, limit),)
            )
        ]
    finally:
        database.close()


def add_criterion(
    work_item_id: str,
    description: str,
    *,
    actor: str,
    sort_order: int | None = None,
    criterion_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Add one independently tracked Acceptance Criterion to a WorkItem."""

    identifier = criterion_id or _generate_id("AC")
    now = _utc_now()
    with _transaction(database_path) as database:
        work_item = _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )
        if work_item["status"] in {"done", "cancelled"}:
            raise ConflictError("cannot add a Criterion to a terminal WorkItem")
        order = sort_order
        if order is None:
            order = database.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM acceptance_criteria WHERE work_item_id = ?",
                (work_item_id,),
            ).fetchone()[0]
        database.execute(
            """
            INSERT INTO acceptance_criteria (
              id, work_item_id, description, status, sort_order
            ) VALUES (?, ?, ?, 'pending', ?)
            """,
            (identifier, work_item_id, description, order),
        )
        _append_state_event(
            database,
            entity_type="acceptance_criterion",
            entity_id=identifier,
            event_type="created",
            actor=actor,
            to_status="pending",
            created_at=now,
        )
        return _require_row(
            database,
            "SELECT * FROM acceptance_criteria WHERE id = ?",
            (identifier,),
            "Acceptance Criterion",
        )


def revise_criterion(
    criterion_id: str,
    description: str,
    *,
    actor: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Revise a Criterion description only before Evidence is connected."""

    now = _utc_now()
    with _transaction(database_path) as database:
        current = _require_row(
            database,
            "SELECT * FROM acceptance_criteria WHERE id = ?",
            (criterion_id,),
            "Acceptance Criterion",
        )
        if current["status"] in {"passed", "waived"}:
            raise ConflictError("resolved Acceptance Criterion is final")
        database.execute(
            "UPDATE acceptance_criteria SET description = ? WHERE id = ?",
            (description, criterion_id),
        )
        _append_state_event(
            database,
            entity_type="acceptance_criterion",
            entity_id=criterion_id,
            event_type="updated",
            actor=actor,
            payload={"fields": ["description"]},
            created_at=now,
        )
        return _require_row(
            database,
            "SELECT * FROM acceptance_criteria WHERE id = ?",
            (criterion_id,),
            "Acceptance Criterion",
        )


def fail_criterion(
    criterion_id: str,
    *,
    actor: str,
    reason: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Record that current verification did not satisfy a Criterion."""

    return _change_criterion_status(
        criterion_id,
        "failed",
        actor=actor,
        reason=reason,
        database_path=database_path,
    )


def waive_criterion(
    criterion_id: str,
    *,
    actor: str,
    reason: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Explicitly exempt a Criterion while preserving who decided and why."""

    if not reason.strip():
        raise ConflictError("waive reason is required")
    return _change_criterion_status(
        criterion_id,
        "waived",
        actor=actor,
        reason=reason,
        database_path=database_path,
    )


def _change_criterion_status(
    criterion_id: str,
    status: str,
    *,
    actor: str,
    reason: str,
    database_path: str | Path,
) -> dict[str, Any]:
    now = _utc_now()
    with _transaction(database_path) as database:
        current = _require_row(
            database,
            "SELECT * FROM acceptance_criteria WHERE id = ?",
            (criterion_id,),
            "Acceptance Criterion",
        )
        if current["status"] in {"passed", "waived"}:
            raise ConflictError("resolved Acceptance Criterion is final")
        resolved_at = now if status == "waived" else None
        database.execute(
            "UPDATE acceptance_criteria SET status = ?, resolved_at = ? WHERE id = ?",
            (status, resolved_at, criterion_id),
        )
        _append_state_event(
            database,
            entity_type="acceptance_criterion",
            entity_id=criterion_id,
            event_type="status_changed",
            actor=actor,
            from_status=current["status"],
            to_status=status,
            reason=reason,
            created_at=now,
        )
        return _require_row(
            database,
            "SELECT * FROM acceptance_criteria WHERE id = ?",
            (criterion_id,),
            "Acceptance Criterion",
        )


def change_work_item_status(
    work_item_id: str,
    status: str,
    *,
    actor: str,
    reason: str,
    next_action: str | None = None,
    block_reason: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Change a WorkItem outside Run finalization and append its State Event."""

    now = _utc_now()
    with _transaction(database_path) as database:
        current = _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )
        if current["status"] == status:
            return current
        if status in {"ready", "blocked", "done", "cancelled"}:
            _require_no_running_run(database, work_item_id)
        if status == "done":
            _validate_work_item_completion(database, work_item_id)
        closed_at = now if status in {"done", "cancelled"} else None
        database.execute(
            """
            UPDATE work_items
            SET status = ?, next_action = ?, block_reason = ?, closed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, next_action, block_reason, closed_at, now, work_item_id),
        )
        _append_state_event(
            database,
            entity_type="work_item",
            entity_id=work_item_id,
            event_type="status_changed",
            actor=actor,
            from_status=current["status"],
            to_status=status,
            reason=reason,
            created_at=now,
        )
        return _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )


def start_run(
    work_item_id: str,
    *,
    actor: str,
    trace_ref: str | None = None,
    run_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Move a ready WorkItem to in_progress and create its single running Run."""

    identifier = run_id or _generate_id("RUN")
    now = _utc_now()
    with _transaction(database_path) as database:
        work_item = _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )
        if work_item["status"] != "ready":
            raise ConflictError("WorkItem must be ready before starting a Run")
        database.execute(
            """
            UPDATE work_items
            SET status = 'in_progress', started_at = COALESCE(started_at, ?), updated_at = ?
            WHERE id = ?
            """,
            (now, now, work_item_id),
        )
        database.execute(
            """
            INSERT INTO runs (id, work_item_id, status, started_at, trace_ref)
            VALUES (?, ?, 'running', ?, ?)
            """,
            (identifier, work_item_id, now, trace_ref),
        )
        _append_state_event(
            database,
            entity_type="run",
            entity_id=identifier,
            event_type="created",
            actor=actor,
            to_status="running",
            run_id=identifier,
            created_at=now,
        )
        _append_state_event(
            database,
            entity_type="work_item",
            entity_id=work_item_id,
            event_type="status_changed",
            actor=actor,
            from_status="ready",
            to_status="in_progress",
            run_id=identifier,
            reason="Run started",
            created_at=now,
        )
        return _require_row(database, "SELECT * FROM runs WHERE id = ?", (identifier,), "Run")


def create_artifact(
    run_id: str,
    *,
    kind: str,
    uri: str,
    verification_status: str,
    summary: str,
    actor: str,
    artifact_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Register a verifiable Run result without copying its original content."""

    identifier = artifact_id or _generate_id("ART")
    now = _utc_now()
    with _transaction(database_path) as database:
        run = _require_row(database, "SELECT * FROM runs WHERE id = ?", (run_id,), "Run")
        if run["status"] != "running":
            raise ConflictError("Artifact must be registered during a running Run")
        database.execute(
            """
            INSERT INTO artifacts (
              id, run_id, kind, uri, verification_status, summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (identifier, run_id, kind, uri, verification_status, summary, now),
        )
        _append_state_event(
            database,
            entity_type="artifact",
            entity_id=identifier,
            event_type="created",
            actor=actor,
            to_status=verification_status,
            run_id=run_id,
            created_at=now,
        )
        return _require_row(
            database, "SELECT * FROM artifacts WHERE id = ?", (identifier,), "Artifact"
        )


def resolve_artifact(
    artifact_id: str,
    verification_status: str,
    *,
    summary: str,
    actor: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Resolve a pending Artifact to the final passed or failed state."""

    if verification_status not in {"passed", "failed"}:
        raise ConflictError("Artifact must resolve to passed or failed")
    now = _utc_now()
    with _transaction(database_path) as database:
        current = _require_row(
            database, "SELECT * FROM artifacts WHERE id = ?", (artifact_id,), "Artifact"
        )
        if current["verification_status"] != "pending":
            raise ConflictError("only a pending Artifact can be resolved")
        database.execute(
            "UPDATE artifacts SET verification_status = ?, summary = ? WHERE id = ?",
            (verification_status, summary, artifact_id),
        )
        _append_state_event(
            database,
            entity_type="artifact",
            entity_id=artifact_id,
            event_type="status_changed",
            actor=actor,
            from_status="pending",
            to_status=verification_status,
            run_id=current["run_id"],
            created_at=now,
        )
        return _require_row(
            database, "SELECT * FROM artifacts WHERE id = ?", (artifact_id,), "Artifact"
        )


def get_run_artifacts(
    run_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """Return the files, test results, and other Artifacts made by one Run."""

    database = open_database(database_path)
    try:
        _require_row(database, "SELECT id FROM runs WHERE id = ?", (run_id,), "Run")
        return [
            dict(row)
            for row in database.execute(
                "SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at, id",
                (run_id,),
            )
        ]
    finally:
        database.close()


def link_evidence(
    criterion_id: str,
    artifact_id: str,
    *,
    actor: str,
    note: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Connect an Artifact to the Acceptance Criterion it supports."""

    now = _utc_now()
    with _transaction(database_path) as database:
        artifact = _require_row(
            database, "SELECT * FROM artifacts WHERE id = ?", (artifact_id,), "Artifact"
        )
        _require_row(
            database,
            "SELECT * FROM acceptance_criteria WHERE id = ?",
            (criterion_id,),
            "Acceptance Criterion",
        )
        database.execute(
            """
            INSERT INTO criterion_evidence (criterion_id, artifact_id, note, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (criterion_id, artifact_id, note, now),
        )
        _append_state_event(
            database,
            entity_type="acceptance_criterion",
            entity_id=criterion_id,
            event_type="evidence_linked",
            actor=actor,
            run_id=artifact["run_id"],
            payload={"artifact_id": artifact_id},
            created_at=now,
        )
        return {
            "criterion_id": criterion_id,
            "artifact_id": artifact_id,
            "note": note,
            "created_at": now,
        }


def get_criterion_evidence(
    criterion_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """Return every Artifact linked as proof for one Acceptance Criterion."""

    database = open_database(database_path)
    try:
        _require_row(
            database,
            "SELECT id FROM acceptance_criteria WHERE id = ?",
            (criterion_id,),
            "Acceptance Criterion",
        )
        return [
            dict(row)
            for row in database.execute(
                """
                SELECT evidence.criterion_id, evidence.artifact_id, evidence.note,
                       evidence.created_at AS linked_at,
                       artifact.run_id, artifact.kind, artifact.uri,
                       artifact.verification_status, artifact.summary
                FROM criterion_evidence AS evidence
                JOIN artifacts AS artifact ON artifact.id = evidence.artifact_id
                WHERE evidence.criterion_id = ?
                ORDER BY evidence.created_at, evidence.artifact_id
                """,
                (criterion_id,),
            )
        ]
    finally:
        database.close()


def pass_criterion(
    criterion_id: str,
    *,
    actor: str,
    reason: str = "Valid Evidence confirmed",
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Mark a Criterion passed only when valid Evidence exists."""

    now = _utc_now()
    with _transaction(database_path) as database:
        current = _require_row(
            database,
            "SELECT * FROM acceptance_criteria WHERE id = ?",
            (criterion_id,),
            "Acceptance Criterion",
        )
        if current["status"] in {"passed", "waived"}:
            raise ConflictError("resolved Acceptance Criterion is final")
        _require_valid_evidence(database, criterion_id)
        database.execute(
            """
            UPDATE acceptance_criteria
            SET status = 'passed', resolved_at = ?
            WHERE id = ?
            """,
            (now, criterion_id),
        )
        _append_state_event(
            database,
            entity_type="acceptance_criterion",
            entity_id=criterion_id,
            event_type="status_changed",
            actor=actor,
            from_status=current["status"],
            to_status="passed",
            reason=reason,
            created_at=now,
        )
        return _require_row(
            database,
            "SELECT * FROM acceptance_criteria WHERE id = ?",
            (criterion_id,),
            "Acceptance Criterion",
        )


_FINISH_STATUS_PAIRS = {
    ("succeeded", "done"),
    ("succeeded", "ready"),
    ("interrupted", "blocked"),
    ("failed", "ready"),
    ("interrupted", "ready"),
    ("cancelled", "cancelled"),
}


def finish_run(
    run_id: str,
    *,
    run_status: str,
    work_item_status: str,
    summary: str,
    actor: str,
    reason: str,
    termination_reason: str | None = None,
    next_action: str | None = None,
    block_reason: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, dict[str, Any]]:
    """Atomically finish a Run, update its WorkItem, and append both events."""

    if (run_status, work_item_status) not in _FINISH_STATUS_PAIRS:
        raise ConflictError("unsupported Run and WorkItem finish status combination")
    if run_status in {"failed", "interrupted", "cancelled"} and not termination_reason:
        raise ConflictError("termination_reason is required for non-successful Run completion")
    if work_item_status in {"ready", "blocked"} and (
        next_action is None or not next_action.strip()
    ):
        raise ConflictError("next_action is required when a WorkItem remains actionable")
    if work_item_status == "blocked" and (
        block_reason is None or not block_reason.strip()
    ):
        raise ConflictError("block_reason is required for a blocked WorkItem")
    now = _utc_now()
    with _transaction(database_path) as database:
        run = _require_row(database, "SELECT * FROM runs WHERE id = ?", (run_id,), "Run")
        if run["status"] != "running":
            raise ConflictError("only a running Run can be finished")
        work_item = _require_row(
            database,
            "SELECT * FROM work_items WHERE id = ?",
            (run["work_item_id"],),
            "WorkItem",
        )
        if work_item_status == "done":
            _validate_work_item_completion(database, run["work_item_id"])
        database.execute(
            """
            UPDATE runs
            SET status = ?, ended_at = ?, summary = ?, termination_reason = ?
            WHERE id = ?
            """,
            (run_status, now, summary, termination_reason, run_id),
        )
        closed_at = now if work_item_status in {"done", "cancelled"} else None
        database.execute(
            """
            UPDATE work_items
            SET status = ?, next_action = ?, block_reason = ?, closed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                work_item_status,
                next_action,
                block_reason,
                closed_at,
                now,
                run["work_item_id"],
            ),
        )
        _append_state_event(
            database,
            entity_type="run",
            entity_id=run_id,
            event_type="status_changed",
            actor=actor,
            from_status="running",
            to_status=run_status,
            run_id=run_id,
            reason=reason,
            created_at=now,
        )
        _append_state_event(
            database,
            entity_type="work_item",
            entity_id=run["work_item_id"],
            event_type="status_changed",
            actor=actor,
            from_status=work_item["status"],
            to_status=work_item_status,
            run_id=run_id,
            reason=reason,
            created_at=now,
        )
        return {
            "run": _require_row(database, "SELECT * FROM runs WHERE id = ?", (run_id,), "Run"),
            "work_item": _require_row(
                database,
                "SELECT * FROM work_items WHERE id = ?",
                (run["work_item_id"],),
                "WorkItem",
            ),
        }


def get_run(
    run_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Return one Run, a single execution attempt for a WorkItem."""

    database = open_database(database_path)
    try:
        return _require_row(database, "SELECT * FROM runs WHERE id = ?", (run_id,), "Run")
    finally:
        database.close()


def get_running_run(
    work_item_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any] | None:
    """Return the WorkItem's only active Run, or None when it is idle."""

    database = open_database(database_path)
    try:
        _require_row(
            database, "SELECT id FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )
        return _as_dict(
            database.execute(
                "SELECT * FROM runs WHERE work_item_id = ? AND status = 'running'",
                (work_item_id,),
            ).fetchone()
        )
    finally:
        database.close()


def complete_work_item(
    work_item_id: str,
    *,
    summary: str,
    actor: str,
    reason: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, dict[str, Any]]:
    """Finish the active Run successfully after checking all completion proof."""

    verification = get_work_item_verification(
        work_item_id, database_path=database_path
    )
    if not verification["can_complete"]:
        raise ConflictError("WorkItem cannot complete: " + "; ".join(verification["issues"]))
    run = get_running_run(work_item_id, database_path=database_path)
    if run is None:
        raise ConflictError("WorkItem has no running Run to complete")
    return finish_run(
        run["id"],
        run_status="succeeded",
        work_item_status="done",
        summary=summary,
        actor=actor,
        reason=reason,
        database_path=database_path,
    )


def get_work_item_verification(
    work_item_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Explain whether all completion criteria are resolved with valid proof."""

    database = open_database(database_path)
    try:
        _require_row(
            database, "SELECT id FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )
        counts, issues = _completion_issues(database, work_item_id)
        return {**counts, "can_complete": not issues, "issues": issues}
    finally:
        database.close()


def _candidate_from_row(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    """Convert stored candidate JSON into a convenient keyword list."""

    candidate = dict(row)
    candidate["keywords"] = json.loads(candidate.pop("keywords_json"))
    return candidate


def create_candidate(
    run_id: str,
    *,
    proposed_type: str,
    title: str,
    content: str,
    keywords: Sequence[str],
    candidate_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Immediately store one small long-term-memory Candidate discovered by a Run."""

    normalized_keywords: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        cleaned = keyword.strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            normalized_keywords.append(cleaned)
    if not normalized_keywords:
        raise ConflictError("at least one useful Candidate keyword is required")

    identifier = candidate_id or _generate_id("MEMC")
    now = _utc_now()
    with _transaction(database_path) as database:
        run = _require_row(database, "SELECT * FROM runs WHERE id = ?", (run_id,), "Run")
        if run["status"] != "running":
            raise ConflictError("Memory Candidate must be captured during a running Run")
        database.execute(
            """
            INSERT INTO memory_candidates (
              id, run_id, proposed_type, title, content, keywords_json,
              status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                identifier,
                run_id,
                proposed_type,
                title,
                content,
                json.dumps(normalized_keywords, ensure_ascii=False),
                now,
            ),
        )
        row = database.execute(
            "SELECT * FROM memory_candidates WHERE id = ?", (identifier,)
        ).fetchone()
        assert row is not None
        return _candidate_from_row(row)


def list_pending_candidates(
    *,
    run_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """Return memory Candidates that still need promotion or rejection."""

    database = open_database(database_path)
    try:
        if run_id is None:
            rows = database.execute(
                "SELECT * FROM memory_candidates WHERE status = 'pending' ORDER BY created_at, id"
            )
        else:
            rows = database.execute(
                """
                SELECT * FROM memory_candidates
                WHERE run_id = ? AND status = 'pending'
                ORDER BY created_at, id
                """,
                (run_id,),
            )
        return [_candidate_from_row(row) for row in rows]
    finally:
        database.close()


def promote_candidate(
    candidate_id: str,
    *,
    memory_ref: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Mark a reviewed Candidate saved and keep its MemoryGraph reference."""

    if not memory_ref.strip():
        raise ConflictError("memory_ref is required when promoting a Candidate")
    with _transaction(database_path) as database:
        current = _require_row(
            database,
            "SELECT * FROM memory_candidates WHERE id = ?",
            (candidate_id,),
            "Memory Candidate",
        )
        if current["status"] != "pending":
            raise ConflictError("only a pending Memory Candidate can be promoted")
        database.execute(
            "UPDATE memory_candidates SET status = 'promoted', memory_ref = ? WHERE id = ?",
            (memory_ref, candidate_id),
        )
        row = database.execute(
            "SELECT * FROM memory_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        assert row is not None
        return _candidate_from_row(row)


def reject_candidate(
    candidate_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Mark a reviewed Candidate as not worth long-term storage."""

    with _transaction(database_path) as database:
        current = _require_row(
            database,
            "SELECT * FROM memory_candidates WHERE id = ?",
            (candidate_id,),
            "Memory Candidate",
        )
        if current["status"] != "pending":
            raise ConflictError("only a pending Memory Candidate can be rejected")
        database.execute(
            "UPDATE memory_candidates SET status = 'rejected' WHERE id = ?",
            (candidate_id,),
        )
        row = database.execute(
            "SELECT * FROM memory_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        assert row is not None
        return _candidate_from_row(row)


def get_work_item_context(
    work_item_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Return the compact state needed to begin or resume one WorkItem."""

    database = open_database(database_path)
    try:
        work_item = _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )
        feature = (
            _as_dict(
                database.execute(
                    "SELECT * FROM features WHERE id = ?", (work_item["feature_id"],)
                ).fetchone()
            )
            if work_item["feature_id"]
            else None
        )
        criteria = [
            dict(row)
            for row in database.execute(
                """
                SELECT * FROM acceptance_criteria
                WHERE work_item_id = ? ORDER BY sort_order, id
                """,
                (work_item_id,),
            )
        ]
        running_run = _as_dict(
            database.execute(
                "SELECT * FROM runs WHERE work_item_id = ? AND status = 'running'",
                (work_item_id,),
            ).fetchone()
        )
        recent_runs = [
            dict(row)
            for row in database.execute(
                """
                SELECT * FROM runs
                WHERE work_item_id = ? AND status <> 'running'
                ORDER BY started_at DESC, id DESC LIMIT 5
                """,
                (work_item_id,),
            )
        ]
        return {
            "work_item": work_item,
            "feature": feature,
            "acceptance_criteria": criteria,
            "running_run": running_run,
            "recent_runs": recent_runs,
        }
    finally:
        database.close()


def get_preflight_context(
    work_item_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Return the minimal state a hook or agent needs before starting work."""

    context = get_work_item_context(work_item_id, database_path=database_path)
    context["verification"] = get_work_item_verification(
        work_item_id, database_path=database_path
    )
    return context


def get_postflight_status(
    work_item_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Return the compact checklist a postflight hook needs before finalization."""

    context = get_work_item_context(work_item_id, database_path=database_path)
    running_run = context["running_run"]
    artifacts = (
        get_run_artifacts(running_run["id"], database_path=database_path)
        if running_run
        else []
    )
    candidates = (
        list_pending_candidates(run_id=running_run["id"], database_path=database_path)
        if running_run
        else []
    )
    return {
        "work_item": context["work_item"],
        "running_run": running_run,
        "verification": get_work_item_verification(
            work_item_id, database_path=database_path
        ),
        "artifacts": artifacts,
        "pending_memory_candidates": candidates,
    }


def get_project_progress(
    *, database_path: str | Path = DEFAULT_DATABASE_PATH
) -> dict[str, Any]:
    """Return one calculated summary of progress across the local repository."""

    database = open_database(database_path)
    try:
        progress = database.execute("SELECT * FROM project_progress").fetchone()
        if progress is None:
            raise StateStoreError("project progress view is unavailable")
        return dict(progress)
    finally:
        database.close()


def get_recent_activity(
    *,
    limit: int = 50,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """Return recent immutable State Events, newest first."""

    database = open_database(database_path)
    try:
        return [
            dict(row)
            for row in database.execute(
                "SELECT * FROM recent_activity LIMIT ?", (max(0, limit),)
            )
        ]
    finally:
        database.close()

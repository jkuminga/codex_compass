"""Safe, workflow-oriented access to the Harness v2 SQLite state store.

Callers use the functions in this module instead of issuing SQL directly. Each
workflow write function owns its validation, transaction, and State Event
recording; user WorkItem memo writes are deliberately excluded from workflow
events as documented by the memo schema.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / ".harness" / "state.db"
SCHEMA_PATH = PROJECT_ROOT / ".harness" / "schema.sql"
COMPLETION_RECOMMENDED_NEXT_ACTION = (
    "[완료 확인 권장] 현재 AC 기준으로 완료 가능합니다. "
    "결과를 확인해 WI를 완료하거나, 추가 작업을 계속 진행하세요."
)

_WORK_ITEM_STATUS_TRANSITION_TRIGGER_SQL = """
CREATE TRIGGER work_items_validate_status_transition
BEFORE UPDATE OF status ON work_items
WHEN OLD.status <> NEW.status
  AND NOT (
    (OLD.status = 'backlog' AND NEW.status IN ('ready', 'cancelled'))
    OR (OLD.status = 'ready' AND NEW.status IN ('in_progress', 'done', 'cancelled'))
    OR (OLD.status = 'in_progress'
      AND NEW.status IN ('ready', 'blocked', 'cancelled'))
    OR (OLD.status = 'blocked' AND NEW.status IN ('ready', 'cancelled'))
  )
BEGIN
  SELECT RAISE(ABORT, 'invalid work item status transition');
END
"""


class StateStoreError(RuntimeError):
    """Base error for a rejected or failed state-store operation."""


class NotFoundError(StateStoreError):
    """Raised when a requested state-store entity does not exist."""


class ConflictError(StateStoreError):
    """Raised when an operation conflicts with the current state."""


_UNSET = object()
_SEARCHABLE_WORK_ITEM_STATUSES = frozenset({"backlog", "ready", "blocked"})
_MEMO_KINDS = frozenset({"general", "decision", "problem", "idea", "question", "reference"})
_MEMO_STATUSES = frozenset({"open", "closed"})
_EXECUTION_ARTIFACT_KINDS = frozenset({"test_run", "lint_run", "build_run"})
_RUN_TOOL_EVENT_FAMILIES = frozenset(
    {"bash", "file_edit", "mcp", "lifecycle", "other"}
)
_RUN_TOOL_EVENT_STATUSES = frozenset({"succeeded", "failed", "unknown"})
_RUN_TOOL_EVENT_REVIEW_STATUSES = frozenset(
    {"pending", "promoted", "ignored", "error"}
)
_EXECUTION_ARTIFACT_URI = re.compile(
    r"^command:(?P<family>[a-z0-9](?:[a-z0-9-]*[a-z0-9])?):"
    r"(?P<started_at>\d{8}T\d{6}Z)(?:-(?P<suffix>[a-z0-9]{6,12}))?$"
)
_REQUIRED_TABLES = frozenset(
    {
        "features",
        "work_items",
        "acceptance_criteria",
        "runs",
        "run_tool_events",
        "artifacts",
        "criterion_evidence",
        "state_events",
        "memory_candidates",
        "work_item_memos",
    }
)
_REQUIRED_VIEWS = frozenset(
    {
        "feature_progress",
        "project_progress",
        "work_item_verification",
        "next_work_items",
        "recent_activity",
    }
)


def _utc_now() -> str:
    """Return the current UTC time in a sortable ISO-8601 form."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _generate_id(prefix: str) -> str:
    """Create a compact unique identifier with a domain-specific prefix."""

    return f"{prefix}-{uuid4().hex[:12]}"


def _validate_artifact_uri(kind: str, uri: str | None) -> str | None:
    """Validate and normalize the reference that identifies one Artifact result."""

    if uri is None:
        return None
    if not isinstance(uri, str):
        raise ConflictError("Artifact uri must be a string when provided")
    cleaned_uri = uri.strip()
    if not cleaned_uri:
        raise ConflictError("Artifact uri must be non-empty when provided")
    if kind not in _EXECUTION_ARTIFACT_KINDS:
        return cleaned_uri

    match = _EXECUTION_ARTIFACT_URI.fullmatch(cleaned_uri)
    if match is None:
        raise ConflictError(
            "test_run, lint_run, and build_run uri must use "
            "command:<command-family>:<YYYYMMDDTHHMMSSZ> with an optional "
            "6-12 character lowercase suffix"
        )
    try:
        datetime.strptime(match.group("started_at"), "%Y%m%dT%H%M%SZ")
    except ValueError as error:
        raise ConflictError("Artifact uri UTC timestamp is invalid") from error
    return cleaned_uri


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
        # These two migrations run before schema.sql so a legacy artifacts table
        # cannot make the new source_event_id index fail during schema loading.
        _migrate_run_tool_events(database)
        _migrate_artifacts(database)
        database.executescript(Path(schema_path).read_text())
        _migrate_work_item_draft_fields(database)
        _migrate_run_recall_fields(database)
        _migrate_memory_candidate_finalize_fields(database)
        _migrate_work_item_memos(database)
        _migrate_work_item_status_transition(database)
        database.commit()
    except (OSError, sqlite3.Error) as error:
        raise StateStoreError(f"database initialization failed: {error}") from error
    finally:
        database.close()
    return check_database_health(database_path)


def _migrate_run_tool_events(database: sqlite3.Connection) -> None:
    """Create the compact PostToolUse event table for older state databases."""

    database.execute(
        """
        CREATE TABLE IF NOT EXISTS run_tool_events (
          id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
          tool_use_id TEXT NOT NULL CHECK (length(trim(tool_use_id)) > 0),
          run_id TEXT NOT NULL REFERENCES runs(id),
          tool_name TEXT NOT NULL CHECK (length(trim(tool_name)) > 0),
          tool_family TEXT NOT NULL CHECK (tool_family IN (
            'bash', 'file_edit', 'mcp', 'lifecycle', 'other'
          )),
          status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'unknown')),
          exit_code INTEGER,
          input_summary TEXT NOT NULL CHECK (length(trim(input_summary)) <= 512),
          result_summary TEXT NOT NULL CHECK (length(trim(result_summary)) <= 1024),
          review_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (review_status IN ('pending', 'promoted', 'ignored', 'error')),
          created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
          UNIQUE (run_id, tool_use_id)
        )
        """
    )
    database.execute(
        """
        CREATE INDEX IF NOT EXISTS run_tool_events_run_created_at_idx
          ON run_tool_events(run_id, created_at, id)
        """
    )
    database.commit()


def _migrate_artifacts(database: sqlite3.Connection) -> None:
    """Rebuild legacy Artifacts so URI is nullable and events can be linked."""

    table_exists = database.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'artifacts'
        """
    ).fetchone()
    if table_exists is None:
        return

    columns = {
        row["name"]: row
        for row in database.execute("PRAGMA table_info(artifacts)")
    }
    uri_column = columns.get("uri")
    uri_is_required = bool(uri_column["notnull"]) if uri_column is not None else False
    if "source_event_id" in columns and not uri_is_required:
        return

    # SQLite cannot relax NOT NULL in place. Build the replacement while
    # preserving all existing rows and the child foreign keys.
    database.commit()
    foreign_keys_enabled = database.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    database.execute("PRAGMA foreign_keys = OFF")
    try:
        database.execute("BEGIN")
        database.execute(
            """
            CREATE TABLE artifacts_migration_new (
              id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
              run_id TEXT NOT NULL REFERENCES runs(id),
              source_event_id TEXT REFERENCES run_tool_events(id),
              kind TEXT NOT NULL CHECK (kind IN (
                'file', 'commit', 'test_run', 'lint_run', 'build_run', 'pull_request',
                'deployment', 'screenshot', 'report', 'other'
              )),
              uri TEXT CHECK (uri IS NULL OR length(trim(uri)) > 0),
              verification_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (verification_status IN ('not_applicable', 'pending', 'passed', 'failed')),
              summary TEXT NOT NULL CHECK (length(trim(summary)) > 0),
              created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
              CHECK (
                kind NOT IN ('test_run', 'lint_run', 'build_run')
                OR verification_status <> 'not_applicable'
              )
            )
            """
        )
        database.execute(
            """
            INSERT INTO artifacts_migration_new (
              id, run_id, source_event_id, kind, uri,
              verification_status, summary, created_at
            )
            SELECT id, run_id, NULL, kind, uri,
                   verification_status, summary, created_at
            FROM artifacts
            """
        )
        # SQLite keeps triggers on sibling tables while the parent table is
        # replaced. Drop the known Artifact-dependent triggers transactionally;
        # schema.sql recreates their canonical definitions after the swap.
        for trigger_name in (
            "acceptance_criteria_require_valid_evidence_on_pass",
            "work_items_require_completed_criteria_on_done",
            "criterion_evidence_validate_work_item_on_insert",
            "criterion_evidence_validate_work_item_on_update",
            "artifacts_prevent_final_verification_rewrite",
            "artifacts_validate_pending_transition",
        ):
            database.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
        database.execute("DROP VIEW IF EXISTS work_item_verification")
        database.execute("DROP TABLE artifacts")
        database.execute(
            "ALTER TABLE artifacts_migration_new RENAME TO artifacts"
        )
        violations = database.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                f"artifact migration produced foreign-key violations: {violations}"
            )
        database.commit()
    except Exception:
        database.rollback()
        raise
    finally:
        database.execute(
            f"PRAGMA foreign_keys = {'ON' if foreign_keys_enabled else 'OFF'}"
        )


def _migrate_work_item_draft_fields(database: sqlite3.Connection) -> None:
    """Add optional Draft WorkItem fields to databases created before Draft support."""

    columns = {row["name"] for row in database.execute("PRAGMA table_info(work_items)")}
    if "description" not in columns:
        database.execute("ALTER TABLE work_items ADD COLUMN description TEXT")
    if "is_draft" not in columns:
        database.execute(
            "ALTER TABLE work_items ADD COLUMN is_draft INTEGER NOT NULL DEFAULT 0 "
            "CHECK (is_draft IN (0, 1))"
        )


def _migrate_run_recall_fields(database: sqlite3.Connection) -> None:
    """Add Run intent fields to state databases created by older harness versions."""

    columns = {
        row["name"] for row in database.execute("PRAGMA table_info(runs)")
    }
    if "intent" not in columns:
        database.execute(
            "ALTER TABLE runs ADD COLUMN intent TEXT NOT NULL DEFAULT 'legacy run' "
            "CHECK (length(trim(intent)) > 0)"
        )
    if "recall_query" not in columns:
        database.execute(
            "ALTER TABLE runs ADD COLUMN recall_query TEXT NOT NULL DEFAULT 'legacy' "
            "CHECK (length(trim(recall_query)) > 0)"
        )


def _migrate_memory_candidate_finalize_fields(database: sqlite3.Connection) -> None:
    """Add the persisted Storage Plan used to resume Candidate finalization."""

    columns = {
        row["name"] for row in database.execute("PRAGMA table_info(memory_candidates)")
    }
    if "storage_plan_json" not in columns:
        database.execute(
            "ALTER TABLE memory_candidates ADD COLUMN storage_plan_json TEXT "
            "CHECK (storage_plan_json IS NULL OR json_valid(storage_plan_json))"
        )
    if "plan_fingerprint" not in columns:
        database.execute(
            "ALTER TABLE memory_candidates ADD COLUMN plan_fingerprint TEXT "
            "CHECK (plan_fingerprint IS NULL OR length(trim(plan_fingerprint)) = 64)"
        )


def _migrate_work_item_memos(database: sqlite3.Connection) -> None:
    """Create the WorkItem memo table for databases from before memo support."""

    database.execute(
        """
        CREATE TABLE IF NOT EXISTS work_item_memos (
          id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
          work_item_id TEXT NOT NULL REFERENCES work_items(id),
          title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
          content TEXT NOT NULL CHECK (length(trim(content)) > 0),
          kind TEXT NOT NULL CHECK (kind IN (
            'general', 'decision', 'problem', 'idea', 'question', 'reference'
          )),
          author TEXT NOT NULL DEFAULT 'anon' CHECK (length(trim(author)) > 0),
          status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
          is_pinned INTEGER NOT NULL DEFAULT 0 CHECK (is_pinned IN (0, 1)),
          is_model_visible INTEGER NOT NULL DEFAULT 0 CHECK (is_model_visible IN (0, 1)),
          sort_order INTEGER NOT NULL CHECK (sort_order >= 0),
          created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
          updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0),
          UNIQUE (work_item_id, sort_order)
        )
        """
    )
    columns = {
        row["name"] for row in database.execute("PRAGMA table_info(work_item_memos)")
    }
    if "is_model_visible" not in columns:
        database.execute(
            "ALTER TABLE work_item_memos ADD COLUMN is_model_visible INTEGER NOT NULL "
            "DEFAULT 0 CHECK (is_model_visible IN (0, 1))"
        )
    database.execute(
        """
        CREATE INDEX IF NOT EXISTS work_item_memos_work_item_order_idx
          ON work_item_memos(work_item_id, is_pinned DESC, sort_order, id)
        """
    )
    database.execute(
        """
        CREATE INDEX IF NOT EXISTS work_item_memos_work_item_filters_idx
          ON work_item_memos(work_item_id, status, kind)
        """
    )


def _migrate_work_item_status_transition(database: sqlite3.Connection) -> None:
    """Replace the lifecycle Trigger so existing DBs use user-owned completion."""

    database.execute("DROP TRIGGER IF EXISTS work_items_validate_status_transition")
    database.execute(_WORK_ITEM_STATUS_TRANSITION_TRIGGER_SQL)


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
    """Report integrity, foreign-key enforcement, and required DB objects."""

    database = open_database(database_path)
    try:
        integrity = [row[0] for row in database.execute("PRAGMA integrity_check")]
        foreign_keys_enabled = (
            database.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        )
        foreign_key_violations = [
            dict(row) for row in database.execute("PRAGMA foreign_key_check")
        ]
        schema_objects = {
            (row["type"], row["name"])
            for row in database.execute(
                """
                SELECT type, name
                FROM sqlite_master
                WHERE type IN ('table', 'view')
                """
            )
        }
        actual_tables = {
            name for object_type, name in schema_objects if object_type == "table"
        }
        actual_views = {
            name for object_type, name in schema_objects if object_type == "view"
        }
        missing_tables = sorted(_REQUIRED_TABLES - actual_tables)
        missing_views = sorted(_REQUIRED_VIEWS - actual_views)
        return {
            "ok": (
                integrity == ["ok"]
                and foreign_keys_enabled
                and not foreign_key_violations
                and not missing_tables
                and not missing_views
            ),
            "integrity": integrity,
            "foreign_keys_enabled": foreign_keys_enabled,
            "foreign_key_violations": foreign_key_violations,
            "missing_tables": missing_tables,
            "missing_views": missing_views,
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


def create_ready_work_item(
    *,
    title: str,
    kind: str,
    goal: str,
    next_action: str,
    acceptance_criteria: Sequence[str],
    actor: str,
    feature_id: str | None = None,
    priority: str = "normal",
    description: str | None = None,
    work_item_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Create one complete, non-Draft WorkItem atomically in ``ready`` state."""

    cleaned_next_action = next_action.strip()
    cleaned_criteria = [criterion.strip() for criterion in acceptance_criteria]
    if not cleaned_next_action:
        raise ConflictError("ready WorkItem requires next_action")
    if not cleaned_criteria or any(not criterion for criterion in cleaned_criteria):
        raise ConflictError("ready WorkItem requires non-empty Acceptance Criteria")

    identifier = work_item_id or _generate_id("WI")
    now = _utc_now()
    cleaned_description = description.strip() if description and description.strip() else None
    with _transaction(database_path) as database:
        database.execute(
            """
            INSERT INTO work_items (
              id, feature_id, title, kind, goal, description, is_draft, status,
              priority, next_action, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 'ready', ?, ?, ?, ?)
            """,
            (
                identifier,
                feature_id,
                title,
                kind,
                goal,
                cleaned_description,
                priority,
                cleaned_next_action,
                now,
                now,
            ),
        )
        _append_state_event(
            database,
            entity_type="work_item",
            entity_id=identifier,
            event_type="created",
            actor=actor,
            to_status="ready",
            payload={"is_draft": False, "creation_mode": "complete"},
            created_at=now,
        )
        for sort_order, criterion in enumerate(cleaned_criteria, start=1):
            criterion_id = _generate_id("AC")
            database.execute(
                """
                INSERT INTO acceptance_criteria (
                  id, work_item_id, description, status, sort_order
                ) VALUES (?, ?, ?, 'pending', ?)
                """,
                (criterion_id, identifier, criterion, sort_order),
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


def create_draft_work_item(
    *,
    title: str,
    kind: str,
    goal: str,
    actor: str,
    description: str | None = None,
    work_item_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Create a user-authored Draft WorkItem with no executable plan yet."""

    identifier = work_item_id or _generate_id("WI")
    now = _utc_now()
    cleaned_description = description.strip() if description and description.strip() else None
    with _transaction(database_path) as database:
        database.execute(
            """
            INSERT INTO work_items (
              id, title, kind, goal, description, is_draft, status, priority,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, 'backlog', 'normal', ?, ?)
            """,
            (identifier, title, kind, goal, cleaned_description, now, now),
        )
        _append_state_event(
            database,
            entity_type="work_item",
            entity_id=identifier,
            event_type="draft_created",
            actor=actor,
            to_status="backlog",
            payload={"is_draft": True},
            created_at=now,
        )
        return _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (identifier,), "WorkItem"
        )


def refine_draft_work_item(
    work_item_id: str,
    *,
    priority: str,
    next_action: str,
    acceptance_criteria: Sequence[str],
    actor: str,
    feature_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Turn one Draft WorkItem into a ready WorkItem and create its Criteria atomically."""

    cleaned_next_action = next_action.strip()
    cleaned_criteria = [criterion.strip() for criterion in acceptance_criteria]
    if not cleaned_next_action:
        raise ConflictError("Draft refinement requires next_action")
    if not cleaned_criteria or any(not criterion for criterion in cleaned_criteria):
        raise ConflictError("Draft refinement requires non-empty Acceptance Criteria")

    now = _utc_now()
    with _transaction(database_path) as database:
        draft = _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )
        if draft["status"] != "backlog" or not draft["is_draft"]:
            raise ConflictError("only a backlog Draft WorkItem can be refined")
        if database.execute(
            "SELECT 1 FROM acceptance_criteria WHERE work_item_id = ? LIMIT 1", (work_item_id,)
        ).fetchone():
            raise ConflictError("Draft WorkItem already has Acceptance Criteria")

        database.execute(
            """
            UPDATE work_items
            SET feature_id = ?, priority = ?, next_action = ?, is_draft = 0,
                status = 'ready', updated_at = ?
            WHERE id = ?
            """,
            (feature_id, priority, cleaned_next_action, now, work_item_id),
        )
        for sort_order, criterion in enumerate(cleaned_criteria, start=1):
            criterion_id = _generate_id("AC")
            database.execute(
                """
                INSERT INTO acceptance_criteria (id, work_item_id, description, status, sort_order)
                VALUES (?, ?, ?, 'pending', ?)
                """,
                (criterion_id, work_item_id, criterion, sort_order),
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
        _append_state_event(
            database,
            entity_type="work_item",
            entity_id=work_item_id,
            event_type="draft_refined",
            actor=actor,
            from_status="backlog",
            to_status="ready",
            payload={"is_draft": False, "criterion_count": len(cleaned_criteria)},
            created_at=now,
        )
        return _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )


def revise_work_item(
    work_item_id: str,
    *,
    actor: str,
    title: str | None = None,
    kind: str | None = None,
    goal: str | None = None,
    description: str | None | object = _UNSET,
    priority: str | None = None,
    next_action: str | None | object = _UNSET,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Revise allowed WorkItem fields while preserving its execution history."""

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
        if description is not _UNSET:
            changes["description"] = description.strip() or None if isinstance(description, str) else None
        changes = {
            field: value for field, value in changes.items() if current.get(field) != value
        }
        protected_during_run = {"kind", "goal", "next_action"}
        if current["status"] == "in_progress" and protected_during_run.intersection(changes):
            raise ConflictError(
                "in_progress WorkItem cannot change kind, goal, or next_action"
            )
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


def get_work_item_management_capabilities(
    work_item_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Return the edit, web-status, and deletion actions currently safe for one WI."""

    database = open_database(database_path)
    try:
        work_item = _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )
        has_run_history = database.execute(
            "SELECT 1 FROM runs WHERE work_item_id = ? LIMIT 1", (work_item_id,)
        ).fetchone() is not None
    finally:
        database.close()

    status = work_item["status"]
    if work_item["is_draft"]:
        editable_fields = ["title", "kind", "goal", "description"]
        allowed_statuses: list[str] = []
    elif status in {"backlog", "ready", "blocked"}:
        editable_fields = [
            "title", "kind", "goal", "description", "priority", "next_action"
        ]
        allowed_statuses = {
            "backlog": ["cancelled"],
            "ready": ["done", "cancelled"],
            "blocked": ["ready", "cancelled"],
        }[status]
    elif status == "in_progress":
        editable_fields = ["title", "description", "priority"]
        allowed_statuses = []
    else:
        editable_fields = []
        allowed_statuses = []

    can_delete = status == "backlog" and not has_run_history
    if can_delete:
        delete_reason = None
    elif status != "backlog":
        delete_reason = f"{status} WorkItem은 실행 기록 보존을 위해 삭제할 수 없습니다."
    else:
        delete_reason = "Run 기록이 있는 WorkItem은 삭제할 수 없습니다."
    return {
        "can_edit": bool(editable_fields),
        "editable_fields": editable_fields,
        "allowed_statuses": allowed_statuses,
        "can_delete": can_delete,
        "delete_reason": delete_reason,
    }


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
        database.execute("DELETE FROM work_item_memos WHERE work_item_id = ?", (work_item_id,))
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


def list_ready_work_items(
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """Return every ready WorkItem, ordered for an explicit user selection."""

    database = open_database(database_path)
    try:
        return [
            dict(row)
            for row in database.execute("SELECT * FROM next_work_items")
        ]
    finally:
        database.close()


def list_selectable_work_items(
    *, database_path: str | Path = DEFAULT_DATABASE_PATH
) -> list[dict[str, Any]]:
    """Return ready WorkItems plus Drafts for an explicit ``w/`` selection."""

    database = open_database(database_path)
    try:
        items = [
            dict(row)
            for row in database.execute(
                """
                SELECT * FROM work_items
                WHERE status = 'ready' OR (status = 'backlog' AND is_draft = 1)
                ORDER BY
                  CASE WHEN is_draft = 1 THEN 0 ELSE 1 END,
                  CASE priority
                    WHEN 'urgent' THEN 1 WHEN 'high' THEN 2
                    WHEN 'normal' THEN 3 WHEN 'low' THEN 4
                  END,
                  created_at, id
                """
            )
        ]
        for item in items:
            item["is_draft"] = bool(item["is_draft"])
        return items
    finally:
        database.close()


def list_work_items(
    *, database_path: str | Path = DEFAULT_DATABASE_PATH
) -> list[dict[str, Any]]:
    """Return every WorkItem with its optional Feature title for management UIs."""

    database = open_database(database_path)
    try:
        items = [
            dict(row)
            for row in database.execute(
                """
                SELECT work_item.*, feature.title AS feature_title
                FROM work_items AS work_item
                LEFT JOIN features AS feature ON feature.id = work_item.feature_id
                ORDER BY work_item.updated_at DESC, work_item.id DESC
                """
            )
        ]
        for item in items:
            item["is_draft"] = bool(item["is_draft"])
        return items
    finally:
        database.close()


def search_work_items(
    terms: Sequence[str],
    *,
    statuses: Sequence[str] = ("backlog", "ready", "blocked"),
    limit: int = 5,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """Find open WorkItems by weighted title, goal, and next-action matches."""

    normalized_terms: list[str] = []
    seen_terms: set[str] = set()
    for term in terms:
        stripped = term.strip()
        key = stripped.casefold()
        if stripped and key not in seen_terms:
            normalized_terms.append(stripped)
            seen_terms.add(key)
    if not 2 <= len(normalized_terms) <= 5:
        raise ConflictError("search terms must contain between 2 and 5 unique values")

    normalized_statuses = tuple(dict.fromkeys(statuses))
    if not normalized_statuses or not set(normalized_statuses).issubset(
        _SEARCHABLE_WORK_ITEM_STATUSES
    ):
        raise ConflictError("search statuses must be backlog, ready, or blocked")
    if not 1 <= limit <= 5:
        raise ConflictError("search limit must be between 1 and 5")

    score_parts: list[str] = []
    score_parameters: list[str] = []
    for term in normalized_terms:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        score_parts.append(
            """
            CASE WHEN title LIKE ? ESCAPE '\\' THEN 3 ELSE 0 END
            + CASE WHEN goal LIKE ? ESCAPE '\\' THEN 2 ELSE 0 END
            + CASE WHEN COALESCE(next_action, '') LIKE ? ESCAPE '\\' THEN 1 ELSE 0 END
            """
        )
        score_parameters.extend((pattern, pattern, pattern))

    status_placeholders = ", ".join("?" for _ in normalized_statuses)
    score_expression = " + ".join(f"({part})" for part in score_parts)
    database = open_database(database_path)
    try:
        return [
            dict(row)
            for row in database.execute(
                f"""
                SELECT *
                FROM (
                  SELECT work_items.*, ({score_expression}) AS match_score
                  FROM work_items
                  WHERE status IN ({status_placeholders})
                ) AS matches
                WHERE match_score > 0
                ORDER BY
                  match_score DESC,
                  CASE priority
                    WHEN 'urgent' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'normal' THEN 3
                    WHEN 'low' THEN 4
                  END,
                  created_at,
                  id
                LIMIT ?
                """,
                (*score_parameters, *normalized_statuses, limit),
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
        if work_item["is_draft"]:
            raise ConflictError("Draft WorkItem must be refined before adding Criteria")
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

    if status == "done":
        raise ConflictError("done WorkItems must be closed with close_work_item")

    now = _utc_now()
    with _transaction(database_path) as database:
        current = _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )
        if current["status"] == status:
            return current
        if current["is_draft"] and status != "backlog":
            raise ConflictError("Draft WorkItem must be refined before it becomes executable")
        if status in {"ready", "blocked", "cancelled"}:
            _require_no_running_run(database, work_item_id)
        closed_at = now if status == "cancelled" else None
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
    intent: str,
    recall_query: str,
    actor: str,
    trace_ref: str | None = None,
    run_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Create a Run with its human intent and machine recall keywords."""

    if not intent.strip():
        raise ConflictError("Run intent is required")
    if not recall_query.strip():
        raise ConflictError("Run recall_query is required")

    identifier = run_id or _generate_id("RUN")
    now = _utc_now()
    with _transaction(database_path) as database:
        work_item = _require_row(
            database, "SELECT * FROM work_items WHERE id = ?", (work_item_id,), "WorkItem"
        )
        if work_item["status"] != "ready":
            raise ConflictError("WorkItem must be ready before starting a Run")
        if work_item["is_draft"]:
            raise ConflictError("Draft WorkItem must be refined before starting a Run")
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
            INSERT INTO runs (
              id, work_item_id, intent, recall_query, status, started_at, trace_ref
            ) VALUES (?, ?, ?, ?, 'running', ?, ?)
            """,
            (identifier, work_item_id, intent.strip(), recall_query.strip(), now, trace_ref),
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


def _validate_run_tool_event_summary(
    value: str,
    *,
    field: str,
    max_length: int,
) -> str:
    """Normalize one bounded event summary without invoking a model."""

    if not isinstance(value, str):
        raise ConflictError(f"{field} must be a string")
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ConflictError(f"{field} must be non-empty")
    if len(cleaned) > max_length:
        raise ConflictError(f"{field} must be at most {max_length} characters")
    return cleaned


def record_run_tool_event(
    run_id: str,
    *,
    tool_use_id: str,
    tool_name: str,
    tool_family: str,
    status: str,
    exit_code: int | None,
    input_summary: str,
    result_summary: str,
    review_status: str = "pending",
    event_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Store one compact PostToolUse event for an active Run."""

    if tool_family not in _RUN_TOOL_EVENT_FAMILIES:
        raise ConflictError(f"unsupported tool family: {tool_family}")
    if status not in _RUN_TOOL_EVENT_STATUSES:
        raise ConflictError(f"unsupported tool event status: {status}")
    if review_status not in _RUN_TOOL_EVENT_REVIEW_STATUSES:
        raise ConflictError(f"unsupported review status: {review_status}")
    if not isinstance(tool_use_id, str) or not tool_use_id.strip():
        raise ConflictError("tool_use_id must be non-empty")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ConflictError("tool_name must be non-empty")
    if exit_code is not None and not isinstance(exit_code, int):
        raise ConflictError("exit_code must be an integer or null")

    identifier = event_id or _generate_id("EVT")
    normalized_tool_use_id = tool_use_id.strip()
    normalized_tool_name = tool_name.strip()
    normalized_input = _validate_run_tool_event_summary(
        input_summary, field="input_summary", max_length=512
    )
    normalized_result = _validate_run_tool_event_summary(
        result_summary, field="result_summary", max_length=1024
    )
    now = _utc_now()
    with _transaction(database_path) as database:
        run = _require_row(database, "SELECT * FROM runs WHERE id = ?", (run_id,), "Run")
        if run["status"] != "running":
            raise ConflictError("Tool events must be recorded during a running Run")
        database.execute(
            """
            INSERT INTO run_tool_events (
              id, tool_use_id, run_id, tool_name, tool_family, status,
              exit_code, input_summary, result_summary, review_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                normalized_tool_use_id,
                run_id,
                normalized_tool_name,
                tool_family,
                status,
                exit_code,
                normalized_input,
                normalized_result,
                review_status,
                now,
            ),
        )
        return _require_row(
            database,
            "SELECT * FROM run_tool_events WHERE id = ?",
            (identifier,),
            "Run tool event",
        )


def list_run_tool_events(
    run_id: str,
    *,
    review_status: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """List compact tool events for one Run in execution order."""

    if review_status is not None and review_status not in _RUN_TOOL_EVENT_REVIEW_STATUSES:
        raise ConflictError(f"unsupported review status: {review_status}")
    database = open_database(database_path)
    try:
        _require_row(database, "SELECT id FROM runs WHERE id = ?", (run_id,), "Run")
        if review_status is None:
            rows = database.execute(
                """
                SELECT * FROM run_tool_events
                WHERE run_id = ?
                ORDER BY created_at, id
                """,
                (run_id,),
            )
        else:
            rows = database.execute(
                """
                SELECT * FROM run_tool_events
                WHERE run_id = ? AND review_status = ?
                ORDER BY created_at, id
                """,
                (run_id, review_status),
            )
        return [dict(row) for row in rows]
    finally:
        database.close()


def create_artifact(
    run_id: str,
    *,
    kind: str,
    uri: str | None = None,
    source_event_id: str | None = None,
    verification_status: str,
    summary: str,
    actor: str,
    artifact_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Register a verifiable Run result without copying its original content."""

    normalized_uri = _validate_artifact_uri(kind, uri)
    if source_event_id is not None and not isinstance(source_event_id, str):
        raise ConflictError("source_event_id must be a string when provided")
    normalized_source_event_id = source_event_id.strip() if source_event_id else None
    if source_event_id is not None and not normalized_source_event_id:
        raise ConflictError("source_event_id must be non-empty when provided")
    identifier = artifact_id or _generate_id("ART")
    now = _utc_now()
    with _transaction(database_path) as database:
        run = _require_row(database, "SELECT * FROM runs WHERE id = ?", (run_id,), "Run")
        if run["status"] != "running":
            raise ConflictError("Artifact must be registered during a running Run")
        if normalized_source_event_id is not None:
            source_event = _require_row(
                database,
                "SELECT id, run_id FROM run_tool_events WHERE id = ?",
                (normalized_source_event_id,),
                "Run tool event",
            )
            if source_event["run_id"] != run_id:
                raise ConflictError("Artifact source event belongs to another Run")
        database.execute(
            """
            INSERT INTO artifacts (
              id, run_id, source_event_id, kind, uri,
              verification_status, summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                run_id,
                normalized_source_event_id,
                kind,
                normalized_uri,
                verification_status,
                summary,
                now,
            ),
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
    completion_recommended: bool = False,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, dict[str, Any]]:
    """Atomically finish a Run, update its WorkItem, and append both events."""

    if (run_status, work_item_status) not in _FINISH_STATUS_PAIRS:
        raise ConflictError("unsupported Run and WorkItem finish status combination")
    if run_status in {"failed", "interrupted", "cancelled"} and not termination_reason:
        raise ConflictError("termination_reason is required for non-successful Run completion")
    if completion_recommended and (run_status, work_item_status) != ("succeeded", "ready"):
        raise ConflictError("completion_recommended requires a succeeded Run and ready WorkItem")
    if completion_recommended and next_action is not None:
        raise ConflictError("next_action must be omitted when completion_recommended is true")
    if not completion_recommended and work_item_status in {"ready", "blocked"} and (
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
        pending_candidate_count = database.execute(
            """
            SELECT COUNT(*)
            FROM memory_candidates
            WHERE run_id = ? AND status = 'pending'
            """,
            (run_id,),
        ).fetchone()[0]
        if pending_candidate_count:
            raise ConflictError(
                "Run cannot finish while pending Memory Candidates remain"
            )
        work_item = _require_row(
            database,
            "SELECT * FROM work_items WHERE id = ?",
            (run["work_item_id"],),
            "WorkItem",
        )
        resolved_next_action = next_action
        if completion_recommended:
            _validate_work_item_completion(database, run["work_item_id"])
            resolved_next_action = COMPLETION_RECOMMENDED_NEXT_ACTION
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
                resolved_next_action,
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


def validate_active_run(
    work_item_id: str,
    *,
    expected_run_id: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Validate that a Binding still points to the WorkItem's active Run."""

    database = open_database(database_path)
    try:
        row = database.execute(
            """
            SELECT run.*, work_item.status AS work_item_status
            FROM runs AS run
            JOIN work_items AS work_item ON work_item.id = run.work_item_id
            WHERE run.id = ?
              AND run.work_item_id = ?
              AND run.status = 'running'
              AND work_item.status = 'in_progress'
            """,
            (expected_run_id, work_item_id),
        ).fetchone()
        if row is None:
            raise ConflictError(
                "Runtime Binding does not point to an active Run and WorkItem"
            )
        return dict(row)
    finally:
        database.close()


def list_running_runs(
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """Return active Runs with the WorkItem fields needed by UPS matching."""

    database = open_database(database_path)
    try:
        return [
            dict(row)
            for row in database.execute(
                """
                SELECT
                  run.*,
                  work_item.title AS work_item_title,
                  work_item.goal AS work_item_goal,
                  work_item.next_action AS work_item_next_action,
                  work_item.priority AS work_item_priority
                FROM runs AS run
                JOIN work_items AS work_item ON work_item.id = run.work_item_id
                WHERE run.status = 'running'
                ORDER BY run.started_at, run.id
                """
            )
        ]
    finally:
        database.close()


def recover_stale_run(
    work_item_id: str,
    *,
    expected_run_id: str,
    actor: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, dict[str, Any]]:
    """Recover the exact previous-turn Run already verified by the UPS Hook."""

    now = _utc_now()
    with _transaction(database_path) as database:
        work_item = _require_row(
            database,
            "SELECT * FROM work_items WHERE id = ?",
            (work_item_id,),
            "WorkItem",
        )
        run = _require_row(
            database,
            "SELECT * FROM runs WHERE id = ?",
            (expected_run_id,),
            "Run",
        )
        if run["work_item_id"] != work_item_id:
            raise ConflictError("expected Run does not belong to the WorkItem")
        if run["status"] != "running" or work_item["status"] != "in_progress":
            raise ConflictError("expected Run is no longer the active WorkItem Run")
        active_run = database.execute(
            "SELECT id FROM runs WHERE work_item_id = ? AND status = 'running'",
            (work_item_id,),
        ).fetchone()
        if active_run is None or active_run["id"] != expected_run_id:
            raise ConflictError("active Run changed before stale recovery")

        summary = "이전 turn에서 finish_work 없이 종료된 Run을 정리했다."
        termination_reason = "이전 turn에서 finish_work 없이 종료됨"
        database.execute(
            """
            UPDATE runs
            SET status = 'interrupted', ended_at = ?, summary = ?, termination_reason = ?
            WHERE id = ?
            """,
            (now, summary, termination_reason, expected_run_id),
        )
        database.execute(
            """
            UPDATE work_items
            SET status = 'ready', block_reason = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, work_item_id),
        )
        reason = "이전 turn의 미종료 Run을 UserPromptSubmit에서 정리함"
        _append_state_event(
            database,
            entity_type="run",
            entity_id=expected_run_id,
            event_type="recovered",
            actor=actor,
            from_status="running",
            to_status="interrupted",
            run_id=expected_run_id,
            reason=reason,
            created_at=now,
        )
        _append_state_event(
            database,
            entity_type="work_item",
            entity_id=work_item_id,
            event_type="status_changed",
            actor=actor,
            from_status="in_progress",
            to_status="ready",
            run_id=expected_run_id,
            reason=reason,
            created_at=now,
        )
        return {
            "run": _require_row(
                database, "SELECT * FROM runs WHERE id = ?", (expected_run_id,), "Run"
            ),
            "work_item": _require_row(
                database,
                "SELECT * FROM work_items WHERE id = ?",
                (work_item_id,),
                "WorkItem",
            ),
        }


def recover_unbound_run(
    work_item_id: str,
    *,
    expected_run_id: str,
    actor: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, dict[str, Any]]:
    """Compensate for a start_work Run whose Runtime Binding was not saved.

    ``expected_run_id`` is a compare-and-set guard. It ensures the Hook only
    interrupts the exact Run it just created and never a replacement Run.
    """

    now = _utc_now()
    with _transaction(database_path) as database:
        work_item = _require_row(
            database,
            "SELECT * FROM work_items WHERE id = ?",
            (work_item_id,),
            "WorkItem",
        )
        run = _require_row(
            database,
            "SELECT * FROM runs WHERE id = ?",
            (expected_run_id,),
            "Run",
        )
        if run["work_item_id"] != work_item_id:
            raise ConflictError("expected Run does not belong to the WorkItem")
        if run["status"] != "running" or work_item["status"] != "in_progress":
            raise ConflictError("expected Run is no longer the active WorkItem Run")
        active_run = database.execute(
            "SELECT id FROM runs WHERE work_item_id = ? AND status = 'running'",
            (work_item_id,),
        ).fetchone()
        if active_run is None or active_run["id"] != expected_run_id:
            raise ConflictError("active Run changed before Binding compensation")

        summary = "start_work 이후 Runtime Binding 생성에 실패해 Run을 정리했다."
        termination_reason = "Runtime Binding 생성 실패"
        database.execute(
            """
            UPDATE runs
            SET status = 'interrupted', ended_at = ?, summary = ?, termination_reason = ?
            WHERE id = ?
            """,
            (now, summary, termination_reason, expected_run_id),
        )
        database.execute(
            """
            UPDATE work_items
            SET status = 'ready', block_reason = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, work_item_id),
        )
        reason = "Runtime Binding 생성 실패를 보상 처리함"
        _append_state_event(
            database,
            entity_type="run",
            entity_id=expected_run_id,
            event_type="recovered",
            actor=actor,
            from_status="running",
            to_status="interrupted",
            run_id=expected_run_id,
            reason=reason,
            created_at=now,
        )
        _append_state_event(
            database,
            entity_type="work_item",
            entity_id=work_item_id,
            event_type="status_changed",
            actor=actor,
            from_status="in_progress",
            to_status="ready",
            run_id=expected_run_id,
            reason=reason,
            created_at=now,
        )
        return {
            "run": _require_row(
                database,
                "SELECT * FROM runs WHERE id = ?",
                (expected_run_id,),
                "Run",
            ),
            "work_item": _require_row(
                database,
                "SELECT * FROM work_items WHERE id = ?",
                (work_item_id,),
                "WorkItem",
            ),
        }


def recover_abandoned_work(
    work_item_id: str,
    *,
    expected_run_id: str,
    actor: str,
    reason: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, dict[str, Any]]:
    """Recover one user-confirmed abandoned Run without guessing ownership.

    ``expected_run_id`` is a compare-and-set guard: recovery succeeds only
    while that exact Run is still the WorkItem's active Run. The interrupted
    Run is retained as history and the WorkItem keeps its existing next action.
    """

    now = _utc_now()
    with _transaction(database_path) as database:
        work_item = _require_row(
            database,
            "SELECT * FROM work_items WHERE id = ?",
            (work_item_id,),
            "WorkItem",
        )
        run = _require_row(
            database,
            "SELECT * FROM runs WHERE id = ?",
            (expected_run_id,),
            "Run",
        )
        if run["work_item_id"] != work_item_id:
            raise ConflictError("expected Run does not belong to the WorkItem")
        if run["status"] != "running" or work_item["status"] != "in_progress":
            raise ConflictError("expected Run is no longer the active WorkItem Run")
        active_run = database.execute(
            "SELECT id FROM runs WHERE work_item_id = ? AND status = 'running'",
            (work_item_id,),
        ).fetchone()
        if active_run is None or active_run["id"] != expected_run_id:
            raise ConflictError("active Run changed before recovery")

        summary = "사용자 확인으로 중단된 세션의 Run을 복구했다."
        termination_reason = "사용자 확인으로 중단된 세션의 Run을 복구함"
        database.execute(
            """
            UPDATE runs
            SET status = 'interrupted', ended_at = ?, summary = ?, termination_reason = ?
            WHERE id = ?
            """,
            (now, summary, termination_reason, expected_run_id),
        )
        database.execute(
            """
            UPDATE work_items
            SET status = 'ready', block_reason = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, work_item_id),
        )
        _append_state_event(
            database,
            entity_type="run",
            entity_id=expected_run_id,
            event_type="recovered",
            actor=actor,
            from_status="running",
            to_status="interrupted",
            run_id=expected_run_id,
            reason=reason,
            created_at=now,
        )
        _append_state_event(
            database,
            entity_type="work_item",
            entity_id=work_item_id,
            event_type="status_changed",
            actor=actor,
            from_status="in_progress",
            to_status="ready",
            run_id=expected_run_id,
            reason=reason,
            created_at=now,
        )
        return {
            "run": _require_row(
                database, "SELECT * FROM runs WHERE id = ?", (expected_run_id,), "Run"
            ),
            "work_item": _require_row(
                database,
                "SELECT * FROM work_items WHERE id = ?",
                (work_item_id,),
                "WorkItem",
            ),
        }


def close_work_item(
    work_item_id: str,
    *,
    actor: str,
    reason: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Close one ready WorkItem after validating its stored completion proof."""

    now = _utc_now()
    with _transaction(database_path) as database:
        work_item = _require_row(
            database,
            "SELECT * FROM work_items WHERE id = ?",
            (work_item_id,),
            "WorkItem",
        )
        if work_item["status"] != "ready":
            raise ConflictError("only a ready WorkItem can be closed")
        _require_no_running_run(database, work_item_id)
        _validate_work_item_completion(database, work_item_id)
        database.execute(
            """
            UPDATE work_items
            SET status = 'done', next_action = NULL, block_reason = NULL,
                closed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, work_item_id),
        )
        _append_state_event(
            database,
            entity_type="work_item",
            entity_id=work_item_id,
            event_type="status_changed",
            actor=actor,
            from_status="ready",
            to_status="done",
            reason=reason,
            created_at=now,
        )
        return _require_row(
            database,
            "SELECT * FROM work_items WHERE id = ?",
            (work_item_id,),
            "WorkItem",
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
    storage_plan_json = candidate.pop("storage_plan_json", None)
    candidate["storage_plan"] = (
        json.loads(storage_plan_json) if storage_plan_json is not None else None
    )
    candidate.setdefault("plan_fingerprint", None)
    return candidate


def reserve_candidate_finalize_plan(
    candidate_id: str,
    *,
    storage_plan: Mapping[str, Any],
    plan_fingerprint: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Persist the first validated Storage Plan or confirm an identical retry.

    ``storage_plan`` is the canonical MemoryGraph write plan. The 64-character
    ``plan_fingerprint`` is its SHA-256 identity and prevents a retry from
    silently changing the already approved plan.
    """

    fingerprint = plan_fingerprint.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ConflictError("plan_fingerprint must be a SHA-256 hex digest")
    encoded = json.dumps(storage_plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    with _transaction(database_path) as database:
        current = _require_row(
            database,
            "SELECT * FROM memory_candidates WHERE id = ?",
            (candidate_id,),
            "Memory Candidate",
        )
        existing_fingerprint = current.get("plan_fingerprint")
        created = existing_fingerprint is None
        if created:
            if current["status"] != "pending":
                raise ConflictError("only a pending Memory Candidate can reserve a Storage Plan")
            database.execute(
                """
                UPDATE memory_candidates
                SET storage_plan_json = ?, plan_fingerprint = ?
                WHERE id = ?
                """,
                (encoded, fingerprint, candidate_id),
            )
        elif existing_fingerprint != fingerprint:
            raise ConflictError("Memory Candidate already has a different Storage Plan")

        row = database.execute(
            "SELECT * FROM memory_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        assert row is not None
        return {"candidate": _candidate_from_row(row), "created": created}


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


def get_memory_candidate(
    candidate_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Return one Memory Candidate without changing its lifecycle state."""

    identifier = candidate_id.strip()
    if not identifier:
        raise ConflictError("Memory Candidate id is required")
    database = open_database(database_path)
    try:
        row = _require_row(
            database,
            "SELECT * FROM memory_candidates WHERE id = ?",
            (identifier,),
            "Memory Candidate",
        )
        return _candidate_from_row(row)
    finally:
        database.close()


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


def _clean_memo_text(value: str, field: str, *, max_length: int) -> str:
    """Trim and validate one user-authored memo text field."""

    cleaned = value.strip()
    if not cleaned:
        raise ConflictError(f"Memo {field} is required")
    if len(cleaned) > max_length:
        raise ConflictError(f"Memo {field} is too long")
    return cleaned


def _list_work_item_memos_database(
    database: sqlite3.Connection,
    work_item_id: str,
    *,
    status: str | None = None,
    kind: str | None = None,
    model_visible_only: bool = False,
) -> list[dict[str, Any]]:
    """Read memos from an existing connection so detail reads stay compact."""

    clauses = ["work_item_id = ?"]
    parameters: list[Any] = [work_item_id]
    if model_visible_only:
        clauses.append("is_model_visible = 1")
    if status is not None:
        if status not in _MEMO_STATUSES:
            raise ConflictError("Memo status must be open or closed")
        clauses.append("status = ?")
        parameters.append(status)
    if kind is not None:
        if kind not in _MEMO_KINDS:
            raise ConflictError("Memo kind is not supported")
        clauses.append("kind = ?")
        parameters.append(kind)
    return [
        dict(row)
        for row in database.execute(
            "SELECT * FROM work_item_memos WHERE "
            + " AND ".join(clauses)
            + " ORDER BY is_pinned DESC, sort_order, id",
            parameters,
        )
    ]


def create_work_item_memo(
    work_item_id: str,
    *,
    title: str,
    content: str,
    kind: str = "general",
    author: str | None = None,
    status: str = "open",
    is_pinned: bool = False,
    is_model_visible: bool = False,
    actor: str = "web_console",
    memo_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Create one user memo without adding a workflow State Event."""

    cleaned_title = _clean_memo_text(title, "title", max_length=200)
    cleaned_content = _clean_memo_text(content, "content", max_length=100_000)
    cleaned_author = (author or "anon").strip() or "anon"
    if len(cleaned_author) > 200:
        raise ConflictError("Memo author is too long")
    if kind not in _MEMO_KINDS:
        raise ConflictError("Memo kind is not supported")
    if status not in _MEMO_STATUSES:
        raise ConflictError("Memo status must be open or closed")

    identifier = memo_id or _generate_id("MEMO")
    now = _utc_now()
    with _transaction(database_path) as database:
        _require_row(database, "SELECT id FROM work_items WHERE id = ?", (work_item_id,), "WorkItem")
        next_order = database.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM work_item_memos WHERE work_item_id = ?",
            (work_item_id,),
        ).fetchone()[0]
        database.execute(
            """
            INSERT INTO work_item_memos (
              id, work_item_id, title, content, kind, author, status,
              is_pinned, is_model_visible, sort_order, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier, work_item_id, cleaned_title, cleaned_content, kind,
                cleaned_author, status, int(is_pinned), int(is_model_visible),
                next_order, now, now,
            ),
        )
        return _require_row(
            database, "SELECT * FROM work_item_memos WHERE id = ?", (identifier,), "Memo"
        )


def get_work_item_memo(
    work_item_id: str,
    memo_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Return one memo only when it belongs to the requested WorkItem."""

    database = open_database(database_path)
    try:
        _require_row(database, "SELECT id FROM work_items WHERE id = ?", (work_item_id,), "WorkItem")
        return _require_row(
            database,
            "SELECT * FROM work_item_memos WHERE id = ? AND work_item_id = ?",
            (memo_id, work_item_id),
            "Memo",
        )
    finally:
        database.close()


def list_work_item_memos(
    work_item_id: str,
    *,
    status: str | None = None,
    kind: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """List one WorkItem's memos, pinned first and then by saved order."""

    database = open_database(database_path)
    try:
        _require_row(database, "SELECT id FROM work_items WHERE id = ?", (work_item_id,), "WorkItem")
        return _list_work_item_memos_database(database, work_item_id, status=status, kind=kind)
    finally:
        database.close()


def update_work_item_memo(
    work_item_id: str,
    memo_id: str,
    *,
    title: str | object = _UNSET,
    content: str | object = _UNSET,
    kind: str | object = _UNSET,
    author: str | None | object = _UNSET,
    status: str | object = _UNSET,
    is_pinned: bool | object = _UNSET,
    is_model_visible: bool | object = _UNSET,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Update memo fields; pin changes move the memo to its target group end."""

    with _transaction(database_path) as database:
        current = _require_row(
            database,
            "SELECT * FROM work_item_memos WHERE id = ? AND work_item_id = ?",
            (memo_id, work_item_id),
            "Memo",
        )
        changes: dict[str, Any] = {}
        if title is not _UNSET:
            changes["title"] = _clean_memo_text(str(title), "title", max_length=200)
        if content is not _UNSET:
            changes["content"] = _clean_memo_text(str(content), "content", max_length=100_000)
        if kind is not _UNSET:
            if not isinstance(kind, str) or kind not in _MEMO_KINDS:
                raise ConflictError("Memo kind is not supported")
            changes["kind"] = kind
        if author is not _UNSET:
            cleaned_author = (author or "anon").strip() if isinstance(author, str) else "anon"
            changes["author"] = cleaned_author or "anon"
            if len(changes["author"]) > 200:
                raise ConflictError("Memo author is too long")
        if status is not _UNSET:
            if not isinstance(status, str) or status not in _MEMO_STATUSES:
                raise ConflictError("Memo status must be open or closed")
            changes["status"] = status
        pin_changed = is_pinned is not _UNSET and bool(is_pinned) != bool(current["is_pinned"])
        if is_pinned is not _UNSET:
            changes["is_pinned"] = int(bool(is_pinned))
        if is_model_visible is not _UNSET:
            changes["is_model_visible"] = int(bool(is_model_visible))
        changes = {field: value for field, value in changes.items() if current[field] != value}
        if not changes:
            return current
        if pin_changed:
            changes["sort_order"] = database.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM work_item_memos WHERE work_item_id = ?",
                (work_item_id,),
            ).fetchone()[0]
        changes["updated_at"] = _utc_now()
        assignments = ", ".join(f"{field} = ?" for field in changes)
        database.execute(
            f"UPDATE work_item_memos SET {assignments} WHERE id = ? AND work_item_id = ?",
            (*changes.values(), memo_id, work_item_id),
        )
        return _require_row(
            database, "SELECT * FROM work_item_memos WHERE id = ?", (memo_id,), "Memo"
        )


def delete_work_item_memo(
    work_item_id: str,
    memo_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> None:
    """Hard-delete one memo after the caller's confirmation."""

    with _transaction(database_path) as database:
        _require_row(
            database,
            "SELECT id FROM work_item_memos WHERE id = ? AND work_item_id = ?",
            (memo_id, work_item_id),
            "Memo",
        )
        database.execute(
            "DELETE FROM work_item_memos WHERE id = ? AND work_item_id = ?",
            (memo_id, work_item_id),
        )


def reorder_work_item_memos(
    work_item_id: str,
    memo_ids: Sequence[str],
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """Atomically save a complete, same-group-only memo order."""

    submitted = list(memo_ids)
    with _transaction(database_path) as database:
        existing = _list_work_item_memos_database(database, work_item_id)
        existing_ids = [memo["id"] for memo in existing]
        if len(submitted) != len(set(submitted)) or set(submitted) != set(existing_ids):
            raise ConflictError("Memo reorder must contain every memo exactly once")
        pinned_ids = {memo["id"] for memo in existing if memo["is_pinned"]}
        submitted_groups = [memo_id in pinned_ids for memo_id in submitted]
        expected_groups = [True] * len(pinned_ids) + [False] * (len(existing) - len(pinned_ids))
        if submitted_groups != expected_groups:
            raise ConflictError("Pinned and normal memos can only be reordered within their own group")
        temporary_base = database.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM work_item_memos WHERE work_item_id = ?",
            (work_item_id,),
        ).fetchone()[0] + len(existing) + 1
        database.execute(
            "UPDATE work_item_memos SET sort_order = sort_order + ? WHERE work_item_id = ?",
            (temporary_base, work_item_id),
        )
        for order, memo_id in enumerate(submitted):
            database.execute(
                "UPDATE work_item_memos SET sort_order = ? WHERE id = ? AND work_item_id = ?",
                (order, memo_id, work_item_id),
            )
        return _list_work_item_memos_database(database, work_item_id)


def get_work_item_context(
    work_item_id: str,
    *,
    model_visible_memos_only: bool = False,
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
        memos = _list_work_item_memos_database(
            database, work_item_id, model_visible_only=model_visible_memos_only
        )
        return {
            "work_item": work_item,
            "feature": feature,
            "acceptance_criteria": criteria,
            "running_run": running_run,
            "recent_runs": recent_runs,
            "memos": memos,
        }
    finally:
        database.close()


def get_preflight_context(
    work_item_id: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Return the minimal state a hook or agent needs before starting work."""

    context = get_work_item_context(
        work_item_id, model_visible_memos_only=True, database_path=database_path
    )
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

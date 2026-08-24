"""Short-lived, session-scoped pointers from Codex to active Harness Runs."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import state_store


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BINDINGS_DIRECTORY = PROJECT_ROOT / ".harness" / "runtime" / "bindings"

_BINDING_FIELDS = {
    "session_id",
    "turn_id",
    "work_item_id",
    "run_id",
    "updated_at",
}
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


class RuntimeBindingError(RuntimeError):
    """Base error for malformed or unsafe Runtime Binding operations."""


class BindingConflictError(RuntimeBindingError):
    """Raised when a Binding changed or already points at different work."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _validate_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise RuntimeBindingError(f"{label} must be a safe non-empty identifier")
    return value


def _binding_path(session_id: str, bindings_directory: str | Path) -> Path:
    safe_session_id = _validate_identifier(session_id, "session_id")
    return Path(bindings_directory) / f"{safe_session_id}.json"


def _validate_binding(payload: Any, *, expected_session_id: str) -> dict[str, str]:
    if not isinstance(payload, dict) or set(payload) != _BINDING_FIELDS:
        raise RuntimeBindingError("Runtime Binding has an unsupported structure")
    binding: dict[str, str] = {}
    for field in _BINDING_FIELDS:
        value = payload[field]
        if not isinstance(value, str) or not value:
            raise RuntimeBindingError(f"Runtime Binding {field} must be a string")
        binding[field] = value
    for field in ("session_id", "turn_id", "work_item_id", "run_id"):
        _validate_identifier(binding[field], field)
    if binding["session_id"] != expected_session_id:
        raise RuntimeBindingError("Runtime Binding session_id does not match its filename")
    return binding


def save_binding(
    *,
    session_id: str,
    turn_id: str,
    work_item_id: str,
    run_id: str,
    updated_at: str | None = None,
    bindings_directory: str | Path = DEFAULT_BINDINGS_DIRECTORY,
) -> dict[str, str]:
    """Atomically bind one Codex session to one WorkItem Run."""

    for value, label in (
        (session_id, "session_id"),
        (turn_id, "turn_id"),
        (work_item_id, "work_item_id"),
        (run_id, "run_id"),
    ):
        _validate_identifier(value, label)
    directory = Path(bindings_directory)
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    destination = _binding_path(session_id, directory)
    current = load_binding(session_id, bindings_directory=directory)
    if current is not None and (
        current["work_item_id"] != work_item_id or current["run_id"] != run_id
    ):
        raise BindingConflictError("session is already bound to a different Run")

    binding = {
        "session_id": session_id,
        "turn_id": turn_id,
        "work_item_id": work_item_id,
        "run_id": run_id,
        "updated_at": updated_at or _utc_now(),
    }
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{session_id}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(binding, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.chmod(0o600)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return binding


def load_binding(
    session_id: str,
    *,
    bindings_directory: str | Path = DEFAULT_BINDINGS_DIRECTORY,
) -> dict[str, str] | None:
    """Load one session Binding, or return None when the session is idle."""

    path = _binding_path(session_id, bindings_directory)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise RuntimeBindingError("Runtime Binding path must be a regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeBindingError(f"Runtime Binding cannot be read: {error}") from error
    return _validate_binding(payload, expected_session_id=session_id)


def delete_binding(
    session_id: str,
    *,
    expected_run_id: str | None = None,
    bindings_directory: str | Path = DEFAULT_BINDINGS_DIRECTORY,
) -> bool:
    """Delete one Binding, optionally only when it still points at a Run."""

    if expected_run_id is not None:
        _validate_identifier(expected_run_id, "expected_run_id")
    path = _binding_path(session_id, bindings_directory)
    binding = load_binding(session_id, bindings_directory=bindings_directory)
    if binding is None:
        return False
    if expected_run_id is not None and binding["run_id"] != expected_run_id:
        raise BindingConflictError("Runtime Binding changed before deletion")
    path.unlink()
    return True


def find_run_owner(
    run_id: str,
    *,
    bindings_directory: str | Path = DEFAULT_BINDINGS_DIRECTORY,
) -> dict[str, str] | None:
    """Return the session Binding that points at a Run, if one exists."""

    _validate_identifier(run_id, "run_id")
    directory = Path(bindings_directory)
    if not directory.exists():
        return None
    owners: list[dict[str, str]] = []
    for path in sorted(directory.glob("*.json")):
        binding = load_binding(path.stem, bindings_directory=directory)
        if binding is not None and binding["run_id"] == run_id:
            owners.append(binding)
    if len(owners) > 1:
        raise BindingConflictError("Run is referenced by more than one session Binding")
    return owners[0] if owners else None


def validate_active_binding(
    *,
    session_id: str,
    current_turn_id: str,
    database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
    bindings_directory: str | Path = DEFAULT_BINDINGS_DIRECTORY,
) -> dict[str, dict[str, Any]]:
    """Validate the current Turn's Binding against the authoritative DB state."""

    _validate_identifier(current_turn_id, "current_turn_id")
    binding = load_binding(session_id, bindings_directory=bindings_directory)
    if binding is None:
        raise BindingConflictError("current session has no Runtime Binding")
    if binding["turn_id"] != current_turn_id:
        raise BindingConflictError("Runtime Binding belongs to a different Turn")

    active_run = state_store.validate_active_run(
        binding["work_item_id"],
        expected_run_id=binding["run_id"],
        database_path=database_path,
    )
    return {"binding": binding, "active_run": active_run}

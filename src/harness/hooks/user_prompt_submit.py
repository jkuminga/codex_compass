"""Build the deterministic WorkItem selection context for each Codex Turn."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from .. import runtime_binding, state_store


WORK_SELECTION_INSTRUCTION = (
    "아래 상태 패킷을 입력으로 $work-start 스킬을 지금 실행하라. "
    "스킬이 일회성 요청으로 판정하거나 프로젝트 작업의 start_work()를 "
    "완료하기 전에는 프로젝트 변경을 시작하지 마라."
)
TERMINAL_RUN_STATUSES = frozenset(
    {"succeeded", "failed", "interrupted", "cancelled"}
)


class HookInputError(ValueError):
    """Raised when UserPromptSubmit omits a required Codex identifier."""


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise HookInputError(f"{field} must be a non-empty string")
    return value.strip()


def _failure_packet(warning: str) -> dict[str, Any]:
    """Return a fail-closed packet without presenting partial state as valid."""

    return {
        "type": "work_selection_context",
        "schema_version": 1,
        "db_ok": False,
        "recovered_runs": [],
        "active_work_items": [],
        "ready_candidates": [],
        "project_progress": None,
        "warnings": [warning],
    }


def _additional_context(packet: Mapping[str, Any]) -> str:
    compact_packet = json.dumps(
        packet, ensure_ascii=False, separators=(",", ":")
    )
    return (
        f"<work-selection-context>\n{compact_packet}\n"
        f"</work-selection-context>\n\n{WORK_SELECTION_INSTRUCTION}"
    )


def _hook_output(packet: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": _additional_context(packet),
        },
    }


def _active_work_item(
    run: Mapping[str, Any],
    *,
    current_session_id: str,
    bindings_directory: str | Path,
) -> dict[str, Any]:
    """Reduce one running Run to the fields Codex needs for conflict checks."""

    owner = runtime_binding.find_run_owner(
        str(run["id"]), bindings_directory=bindings_directory
    )
    if owner is None:
        ownership = "unknown"
    elif owner["session_id"] == current_session_id:
        ownership = "current_session"
    else:
        ownership = "other_session"
    return {
        "work_item_id": run["work_item_id"],
        "run_id": run["id"],
        "title": run["work_item_title"],
        "goal": run["work_item_goal"],
        "intent": run["intent"],
        "ownership": ownership,
    }


def _ready_candidate(work_item: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the ready WorkItem fields used by the selection decision."""

    return {
        "work_item_id": work_item["id"],
        "title": work_item["title"],
        "goal": work_item["goal"],
        "next_action": work_item["next_action"],
        "priority": work_item["priority"],
    }


def build_work_selection_packet(
    event: Mapping[str, Any],
    *,
    database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
    bindings_directory: str | Path = runtime_binding.DEFAULT_BINDINGS_DIRECTORY,
) -> dict[str, Any]:
    """Run UPS health, recovery, ownership, progress, and candidate checks."""

    session_id = _required_string(event, "session_id")
    current_turn_id = _required_string(event, "turn_id")
    warnings: list[str] = []
    recovered_runs: list[dict[str, str]] = []

    health = state_store.check_database_health(database_path=database_path)
    if not health.get("ok"):
        return _failure_packet("상태 저장소 건강 검사에 실패했습니다.")

    binding = runtime_binding.load_binding(
        session_id, bindings_directory=bindings_directory
    )
    running_runs = state_store.list_running_runs(database_path=database_path)
    running_by_id = {str(run["id"]): run for run in running_runs}

    if binding is not None:
        bound_run = running_by_id.get(binding["run_id"])
        if bound_run is not None and binding["turn_id"] != current_turn_id:
            try:
                state_store.recover_stale_run(
                    str(bound_run["work_item_id"]),
                    expected_run_id=binding["run_id"],
                    actor="user_prompt_submit_hook",
                    database_path=database_path,
                )
                recovered_runs.append(
                    {
                        "run_id": binding["run_id"],
                        "work_item_id": str(bound_run["work_item_id"]),
                    }
                )
            except state_store.StateStoreError as error:
                warnings.append(f"stale_run_recovery_failed:{type(error).__name__}")
            else:
                try:
                    runtime_binding.delete_binding(
                        session_id,
                        expected_run_id=binding["run_id"],
                        bindings_directory=bindings_directory,
                    )
                except runtime_binding.RuntimeBindingError as error:
                    warnings.append(
                        f"stale_binding_cleanup_failed:{type(error).__name__}"
                    )
        elif bound_run is None:
            try:
                persisted_run = state_store.get_run(
                    binding["run_id"], database_path=database_path
                )
            except state_store.NotFoundError:
                warnings.append("binding_run_missing")
                runtime_binding.delete_binding(
                    session_id,
                    expected_run_id=binding["run_id"],
                    bindings_directory=bindings_directory,
                )
            else:
                if persisted_run["status"] in TERMINAL_RUN_STATUSES:
                    runtime_binding.delete_binding(
                        session_id,
                        expected_run_id=binding["run_id"],
                        bindings_directory=bindings_directory,
                    )
                else:
                    warnings.append("binding_run_state_inconsistent")

    running_runs = state_store.list_running_runs(database_path=database_path)
    active_work_items = [
        _active_work_item(
            run,
            current_session_id=session_id,
            bindings_directory=bindings_directory,
        )
        for run in running_runs
    ]
    return {
        "type": "work_selection_context",
        "schema_version": 1,
        "db_ok": True,
        "recovered_runs": recovered_runs,
        "active_work_items": active_work_items,
        "ready_candidates": [
            _ready_candidate(work_item)
            for work_item in state_store.list_next_work_items(
                limit=3, database_path=database_path
            )
        ],
        "project_progress": state_store.get_project_progress(
            database_path=database_path
        ),
        "warnings": warnings,
    }


def dispatch_user_prompt_submit(
    event: Mapping[str, Any],
    *,
    database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
    bindings_directory: str | Path = runtime_binding.DEFAULT_BINDINGS_DIRECTORY,
) -> dict[str, Any]:
    """Convert every caught UPS failure into a model-visible fail-closed packet."""

    try:
        packet = build_work_selection_packet(
            event,
            database_path=database_path,
            bindings_directory=bindings_directory,
        )
    except Exception as error:
        packet = _failure_packet(f"UPS 처리 실패:{type(error).__name__}")
    return _hook_output(packet)


def main() -> int:
    """Read a UserPromptSubmit event from stdin and emit Codex Hook JSON."""

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    result = dispatch_user_prompt_submit(
        payload,
        database_path=os.environ.get("HARNESS_STATE_DATABASE")
        or state_store.DEFAULT_DATABASE_PATH,
        bindings_directory=os.environ.get("HARNESS_BINDINGS_DIRECTORY")
        or runtime_binding.DEFAULT_BINDINGS_DIRECTORY,
    )
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

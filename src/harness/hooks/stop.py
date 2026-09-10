"""Inspect Harness closeout state when Codex attempts to end a Turn."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from .. import runtime_binding, state_store


class StopHookInputError(ValueError):
    """Raised when a Stop Hook event lacks a required Codex identifier."""


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise StopHookInputError(f"{field} must be a non-empty string")
    return value.strip()


def _warning(message: str) -> dict[str, str]:
    """Return a visible warning without blocking Codex termination."""

    return {"systemMessage": f"Harness Stop Hook 경고: {message}"}


def dispatch_stop(
    event: Mapping[str, Any],
    *,
    database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
    bindings_directory: str | Path = runtime_binding.DEFAULT_BINDINGS_DIRECTORY,
) -> dict[str, str] | None:
    """Return one deterministic Stop decision from Binding and SQLite state."""

    if event.get("hook_event_name") != "Stop":
        return None

    try:
        session_id = _required_string(event, "session_id")
        turn_id = _required_string(event, "turn_id")
        binding = runtime_binding.load_binding(
            session_id, bindings_directory=bindings_directory
        )
    except (StopHookInputError, runtime_binding.RuntimeBindingError) as error:
        return _warning(f"Binding을 확인하지 못했습니다 ({type(error).__name__}).")
    except Exception as error:
        return _warning(f"상태 확인에 실패했습니다 ({type(error).__name__}).")

    if binding is None:
        try:
            active_runs = state_store.list_running_runs(database_path=database_path)
        except Exception as error:
            return _warning(f"DB를 조회하지 못했습니다 ({type(error).__name__}).")
        if active_runs:
            return _warning(
                "현재 세션 Binding은 없지만 다른 세션 또는 소유 불명의 활성 Run이 "
                "남아 있습니다. UPS에서 소유권을 확인하세요."
            )
        return None

    try:
        active = runtime_binding.validate_active_binding(
            session_id=session_id,
            current_turn_id=turn_id,
            database_path=database_path,
            bindings_directory=bindings_directory,
        )
        pending_candidates = state_store.list_pending_candidates(
            run_id=active["active_run"]["id"], database_path=database_path
        )
    except (runtime_binding.RuntimeBindingError, state_store.StateStoreError) as error:
        return _warning(
            "Binding과 DB의 활성 Run이 일치하지 않습니다 "
            f"({type(error).__name__}). 상태는 변경하지 않았습니다."
        )
    except Exception as error:
        return _warning(f"DB를 조회하지 못했습니다 ({type(error).__name__}).")

    if event.get("stop_hook_active") is True:
        return _warning(
            "한 차례 마감 재시도 후에도 활성 Run이 남아 있습니다. "
            f"pending Memory Candidate: {len(pending_candidates)}개."
        )

    if pending_candidates:
        reason = (
            "활성 Run에 pending Memory Candidate가 남아 있습니다. "
            "$work-finish를 실행해 후보를 검토·저장하고 Run을 종료하세요."
        )
    else:
        reason = (
            "활성 Run이 아직 마감되지 않았습니다. "
            "$work-finish를 실행해 Run을 종료하세요."
        )
    return {"decision": "block", "reason": reason}


def main() -> int:
    """Read one Codex Stop event and emit an optional JSON decision."""

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        json.dump(
            _warning("입력 JSON을 읽지 못해 종료 상태를 확인하지 못했습니다."),
            sys.stdout,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
        return 0
    if not isinstance(payload, dict):
        return 0

    result = dispatch_stop(
        payload,
        database_path=os.environ.get("HARNESS_STATE_DATABASE")
        or state_store.DEFAULT_DATABASE_PATH,
        bindings_directory=os.environ.get("HARNESS_BINDINGS_DIRECTORY")
        or runtime_binding.DEFAULT_BINDINGS_DIRECTORY,
    )
    if result is not None:
        json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

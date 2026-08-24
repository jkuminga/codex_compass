"""Codex PreToolUse adapter for deterministic Harness authorization."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from .. import hook_policy, runtime_binding, state_store


class HookInputError(ValueError):
    """Raised when a PreToolUse event lacks a required Codex identifier."""


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise HookInputError(f"{field} must be a non-empty string")
    return value.strip()


def _hook_output(decision: hook_policy.ToolDecision) -> dict[str, Any] | None:
    """Map one policy decision to Codex's current PreToolUse output schema."""

    if decision.allowed:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"[{decision.reason_code}] {decision.message}"
            ),
        },
    }


def _fail_closed(reason_code: str, message: str) -> dict[str, Any]:
    output = _hook_output(
        hook_policy.ToolDecision(
            classification="dangerous",
            allowed=False,
            reason_code=reason_code,
            message=message,
        )
    )
    assert output is not None
    return output


def dispatch_pre_tool_use(
    event: Mapping[str, Any],
    *,
    project_root: str | Path = hook_policy.PROJECT_ROOT,
    database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
    bindings_directory: str | Path = runtime_binding.DEFAULT_BINDINGS_DIRECTORY,
) -> dict[str, Any] | None:
    """Classify a tool call and fail closed on every handled policy error."""

    try:
        tool_name = _required_string(event, "tool_name")
        tool_input = event.get("tool_input")
        decision = hook_policy.classify_tool_call(
            tool_name, tool_input, project_root=project_root
        )
        if decision.classification == "run_required":
            decision = hook_policy.authorize_tool_call(
                decision,
                session_id=_required_string(event, "session_id"),
                current_turn_id=_required_string(event, "turn_id"),
                database_path=database_path,
                bindings_directory=bindings_directory,
            )
        return _hook_output(decision)
    except Exception as error:
        return _fail_closed(
            f"pre_tool_use_failed:{type(error).__name__}",
            "PreToolUse 정책을 정상적으로 판정하지 못해 도구 실행을 중단합니다.",
        )


def main() -> int:
    """Read one Hook event and emit only a valid deny; silence means allow."""

    try:
        payload = json.load(sys.stdin)
    except Exception as error:
        result = _fail_closed(
            f"invalid_hook_input:{type(error).__name__}",
            "PreToolUse 입력 JSON을 읽을 수 없어 도구 실행을 중단합니다.",
        )
    else:
        if not isinstance(payload, dict):
            result = _fail_closed(
                "invalid_hook_input:TypeError",
                "PreToolUse 입력은 JSON 객체여야 합니다.",
            )
        else:
            result = dispatch_pre_tool_use(
                payload,
                project_root=os.environ.get("HARNESS_PROJECT_ROOT")
                or hook_policy.PROJECT_ROOT,
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

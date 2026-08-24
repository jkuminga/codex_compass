"""Dispatch successful Codex PostToolUse events to narrow Harness handlers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import runtime_binding, state_store


PROJECT_ROOT = Path(__file__).resolve().parents[3]
START_WORK_TOOL = "mcp__harness_state__start_work"
FINISH_WORK_TOOL = "mcp__harness_state__finish_work"
DEFAULT_RECALL_LIMIT = 5
DEFAULT_RECALL_TIMEOUT_SECONDS = 15

RecallRunner = Callable[[str], dict[str, Any]]
StartedStatusReader = Callable[..., Mapping[str, Any]]


class HookInputError(ValueError):
    """Raised when a matching Hook event lacks its required values."""


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise HookInputError(f"{field} must be a non-empty string")
    return value.strip()


def _structured_tool_response(tool_response: Any, *, tool_name: str) -> Mapping[str, Any]:
    """Extract an MCP tool's structuredContent from the Codex Hook payload."""

    if not isinstance(tool_response, Mapping):
        raise HookInputError("tool_response must be an object")
    if tool_response.get("isError") is True:
        raise HookInputError(f"{tool_name} returned an MCP error")
    structured = tool_response.get("structuredContent")
    if not isinstance(structured, Mapping):
        raise HookInputError("tool_response.structuredContent must be an object")
    return structured


def run_recall(
    recall_query: str,
    *,
    project_root: str | Path = PROJECT_ROOT,
    timeout_seconds: int = DEFAULT_RECALL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Call the local MemoryGraph Wrapper and validate its compact JSON result."""

    command = Path(project_root) / "bin" / "harness-memory"
    try:
        completed = subprocess.run(
            [str(command), "recall", recall_query, "--limit", str(DEFAULT_RECALL_LIMIT)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        response = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"ok": False, "memories": [], "warnings": ["recall_wrapper_failed"]}

    if not isinstance(response, dict):
        return {"ok": False, "memories": [], "warnings": ["invalid_recall_response"]}
    memories = response.get("memories")
    warnings = response.get("warnings")
    if not isinstance(memories, list) or not isinstance(warnings, list):
        return {"ok": False, "memories": [], "warnings": ["invalid_recall_response"]}
    return {
        "ok": bool(response.get("ok")),
        "memories": memories,
        "warnings": [str(warning) for warning in warnings],
    }


def _context_packet(
    *,
    run_id: str,
    intent: str,
    recall_response: Mapping[str, Any],
    extra_warnings: list[str] | None = None,
) -> str:
    """Build the small JSON string injected into the Codex model context."""

    memories: list[dict[str, Any]] = []
    for item in recall_response.get("memories", []):
        if not isinstance(item, Mapping):
            continue
        title = item.get("title")
        summary = item.get("summary")
        matched_keywords = item.get("matched_keywords")
        if not isinstance(title, str) or not isinstance(summary, str):
            continue
        memories.append(
            {
                "title": title,
                "summary": summary,
                "matched_keywords": matched_keywords
                if isinstance(matched_keywords, list)
                else [],
            }
        )

    warnings = [
        str(warning) for warning in recall_response.get("warnings", [])
    ]
    warnings.extend(extra_warnings or [])
    packet = {
        "type": "long_term_memory_context",
        "run_id": run_id,
        "intent": intent,
        "memories": memories,
        "warnings": list(dict.fromkeys(warnings)),
    }
    return json.dumps(packet, ensure_ascii=False, separators=(",", ":"))


def read_started_status(
    work_item_id: str,
    *,
    database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Read the WorkItem label and compact project counts for the start notice."""

    return {
        "work_item": state_store.get_work_item(
            work_item_id, database_path=database_path
        ),
        "progress": state_store.get_project_progress(database_path=database_path),
    }


def _started_system_message(
    *,
    work_item_id: str,
    run_id: str,
    started_status: Mapping[str, Any] | None,
    recall_response: Mapping[str, Any],
    warnings: list[str],
) -> str:
    """Build a small user-visible summary without session or filesystem details."""

    work_item = started_status.get("work_item") if started_status else None
    progress = started_status.get("progress") if started_status else None
    title = work_item.get("title") if isinstance(work_item, Mapping) else None
    work_item_label = work_item_id
    if isinstance(title, str) and title.strip():
        work_item_label = f"{work_item_id} · {title.strip()}"

    lines = [
        "Harness 작업 시작",
        f"WorkItem : {work_item_label}",
        f"Run      : {run_id} · running",
        "Binding  : 연결됨",
    ]
    if isinstance(progress, Mapping):
        lines.append(
            "프로젝트 : "
            f"진행 중 {progress.get('in_progress_count', 0)} / "
            f"준비 {progress.get('ready_count', 0)} / "
            f"완료 {progress.get('done_count', 0)}"
        )

    memories = recall_response.get("memories", [])
    memory_count = len(memories) if isinstance(memories, list) else 0
    unique_warnings = list(dict.fromkeys(warnings))
    memory_status = f"관련 기억 {memory_count}개 불러옴"
    if unique_warnings:
        memory_status += f" · 경고 {len(unique_warnings)}개"
    lines.append(f"장기기억 : {memory_status}")
    return "\n".join(lines)


def dispatch_post_tool_use(
    event: Mapping[str, Any],
    *,
    recall_runner: RecallRunner = run_recall,
    started_status_reader: StartedStatusReader = read_started_status,
    database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
    bindings_directory: str | Path = runtime_binding.DEFAULT_BINDINGS_DIRECTORY,
) -> dict[str, Any] | None:
    """Handle successful start_work and finish_work lifecycle transitions."""

    tool_name = event.get("tool_name")
    if tool_name not in {START_WORK_TOOL, FINISH_WORK_TOOL}:
        return None

    if tool_name == FINISH_WORK_TOOL:
        return _dispatch_finish_work(event, bindings_directory=bindings_directory)

    warnings: list[str] = []
    started_status: Mapping[str, Any] | None = None
    run_id = "unknown"
    intent = "start_work"
    try:
        session_id = _required_string(event, "session_id")
        turn_id = _required_string(event, "turn_id")
        tool_input = event.get("tool_input")
        if not isinstance(tool_input, Mapping):
            raise HookInputError("tool_input must be an object")
        intent = _required_string(tool_input, "intent")
        recall_query = _required_string(tool_input, "recall_query")

        response = _structured_tool_response(
            event.get("tool_response"), tool_name="start_work"
        )
        run_id = _required_string(response, "id")
        work_item_id = _required_string(response, "work_item_id")
    except HookInputError as error:
        recall_response = {"ok": False, "memories": [], "warnings": []}
        warnings.append(f"start_work_post_hook_failed:{type(error).__name__}")
        should_continue = False
    except Exception:
        recall_response = {"ok": False, "memories": [], "warnings": []}
        warnings.append("start_work_post_hook_failed:unexpected_error")
        should_continue = False
    else:
        try:
            runtime_binding.save_binding(
                session_id=session_id,
                turn_id=turn_id,
                work_item_id=work_item_id,
                run_id=run_id,
                bindings_directory=bindings_directory,
            )
        except Exception as binding_error:
            recall_response = {"ok": False, "memories": [], "warnings": []}
            warnings.append(
                f"runtime_binding_save_failed:{type(binding_error).__name__}"
            )
            try:
                state_store.recover_unbound_run(
                    work_item_id,
                    expected_run_id=run_id,
                    actor="start_work_post_tool_use_hook",
                    database_path=database_path,
                )
                warnings.append("unbound_run_recovered")
            except Exception as recovery_error:
                warnings.append(
                    f"unbound_run_recovery_failed:{type(recovery_error).__name__}"
                )
            should_continue = False
        else:
            should_continue = True
            try:
                recall_response = recall_runner(recall_query)
            except Exception:
                recall_response = {"ok": False, "memories": [], "warnings": []}
                warnings.append("memory_recall_failed:unexpected_error")

            try:
                started_status = started_status_reader(
                    work_item_id, database_path=database_path
                )
            except Exception as status_error:
                warnings.append(
                    f"work_start_summary_failed:{type(status_error).__name__}"
                )

    output = {
        "continue": should_continue,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": _context_packet(
                run_id=run_id,
                intent=intent,
                recall_response=recall_response,
                extra_warnings=warnings,
            ),
        },
    }
    if should_continue:
        combined_warnings = [
            str(warning) for warning in recall_response.get("warnings", [])
        ]
        combined_warnings.extend(warnings)
        output["systemMessage"] = _started_system_message(
            work_item_id=work_item_id,
            run_id=run_id,
            started_status=started_status,
            recall_response=recall_response,
            warnings=combined_warnings,
        )
    return output


def _dispatch_finish_work(
    event: Mapping[str, Any],
    *,
    bindings_directory: str | Path,
) -> dict[str, Any]:
    """Delete the current session Binding after its exact Run is finalized."""

    warnings: list[str] = []
    deleted = False
    run_id = "unknown"
    try:
        session_id = _required_string(event, "session_id")
        response = _structured_tool_response(
            event.get("tool_response"), tool_name="finish_work"
        )
        run = response.get("run")
        if not isinstance(run, Mapping):
            raise HookInputError("finish_work response.run must be an object")
        run_id = _required_string(run, "id")
        deleted = runtime_binding.delete_binding(
            session_id,
            expected_run_id=run_id,
            bindings_directory=bindings_directory,
        )
        if not deleted:
            warnings.append("finish_work_binding_missing")
    except (HookInputError, runtime_binding.RuntimeBindingError) as error:
        warnings.append(f"finish_work_binding_cleanup_failed:{type(error).__name__}")
    except Exception:
        warnings.append("finish_work_binding_cleanup_failed:unexpected_error")

    context = json.dumps(
        {
            "type": "work_finish_context",
            "run_id": run_id,
            "binding_deleted": deleted,
            "warnings": warnings,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": context,
        },
    }


def main() -> int:
    """Read one Codex Hook event from stdin and emit its optional response."""

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0

    bindings_directory = os.environ.get("HARNESS_BINDINGS_DIRECTORY")
    result = dispatch_post_tool_use(
        payload,
        database_path=os.environ.get("HARNESS_STATE_DATABASE")
        or state_store.DEFAULT_DATABASE_PATH,
        bindings_directory=bindings_directory
        or runtime_binding.DEFAULT_BINDINGS_DIRECTORY,
    )
    if result is not None:
        json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

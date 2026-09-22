"""Dispatch successful Codex PostToolUse events to narrow Harness handlers."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shlex
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
MAX_INPUT_SUMMARY_LENGTH = 512
MAX_RESULT_SUMMARY_LENGTH = 1024
_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret|authorization|bearer)\b"
    r"(\s*[:=]\s*|\s+)([^\s,;]+)"
)
_PATCH_FILE_PATTERN = re.compile(
    r"^\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*(.+?)\s*$",
    re.MULTILINE,
)
_SHELL_META_PATTERN = re.compile(r"(?:&&|\|\||[|;&<>`$()]|\n)")
_TRIVIAL_READ_COMMANDS = frozenset({"pwd", "ls", "cd", "pushd", "popd"})

RecallRunner = Callable[[str], dict[str, Any]]
StartedStatusReader = Callable[..., Mapping[str, Any]]


class HookInputError(ValueError):
    """Raised when a matching Hook event lacks its required values."""


def _compact_text(value: Any, *, limit: int) -> str:
    """Return a short, whitespace-normalized and secret-redacted string."""

    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            text = str(value)
    text = _SECRET_PATTERN.sub(r"\1=***", text)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: max(0, limit - 1)] + "…"
    return text


def classify_tool_family(tool_name: str) -> str:
    """Classify a Codex tool name without model inference."""

    lowered = tool_name.strip().lower()
    if lowered in {START_WORK_TOOL.lower(), FINISH_WORK_TOOL.lower()}:
        return "lifecycle"
    if lowered in {"bash", "shell", "terminal", "exec", "exec_command"}:
        return "bash"
    if lowered == "apply_patch" or "file_edit" in lowered:
        return "file_edit"
    if lowered.startswith("mcp__"):
        return "mcp"
    return "other"


def _tool_use_id(event: Mapping[str, Any], *, tool_name: str) -> str:
    """Use the Codex tool-call ID, with a stable fallback for old payloads."""

    for field in ("tool_use_id", "tool_call_id", "call_id"):
        value = event.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    fingerprint_input = {
        "session_id": event.get("session_id"),
        "turn_id": event.get("turn_id"),
        "tool_name": tool_name,
        "tool_input": event.get("tool_input"),
        "tool_response": event.get("tool_response"),
    }
    encoded = json.dumps(
        fingerprint_input,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return "hook-" + hashlib.sha256(encoded).hexdigest()[:24]


def summarize_tool_input(tool_name: str, tool_input: Any) -> str:
    """Create a bounded input summary suitable for a state-store event."""

    family = classify_tool_family(tool_name)
    if family == "bash":
        command = tool_input
        if isinstance(tool_input, Mapping):
            command = next(
                (
                    tool_input.get(field)
                    for field in ("cmd", "command", "script")
                    if isinstance(tool_input.get(field), str)
                ),
                tool_input,
            )
        return _compact_text(f"cmd={command}", limit=MAX_INPUT_SUMMARY_LENGTH)
    if family == "file_edit":
        patch = tool_input.get("patch", "") if isinstance(tool_input, Mapping) else tool_input
        patch_text = patch if isinstance(patch, str) else str(patch)
        files = [match.strip() for match in _PATCH_FILE_PATTERN.findall(patch_text)]
        if files:
            unique_files = list(dict.fromkeys(files))
            return _compact_text(
                f"apply_patch files={','.join(unique_files[:20])} ops={len(files)}",
                limit=MAX_INPUT_SUMMARY_LENGTH,
            )
        return "apply_patch files=unknown"
    if family == "mcp":
        if isinstance(tool_input, Mapping):
            keys = ",".join(sorted(str(key) for key in tool_input)[:20]) or "none"
            return _compact_text(
                f"mcp input_keys={keys}", limit=MAX_INPUT_SUMMARY_LENGTH
            )
        return _compact_text("mcp input=none", limit=MAX_INPUT_SUMMARY_LENGTH)
    return _compact_text(
        f"input={tool_input}", limit=MAX_INPUT_SUMMARY_LENGTH
    )


def _shell_command_text(tool_input: Any) -> str | None:
    """Read a shell command from either Codex's ``cmd`` or ``command`` field."""

    if isinstance(tool_input, str):
        return tool_input.strip() or None
    if not isinstance(tool_input, Mapping):
        return None
    for field in ("cmd", "command", "script"):
        value = tool_input.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def is_trivial_read_command(tool_name: str, tool_input: Any) -> bool:
    """Identify only unambiguous, low-value shell navigation/status commands.

    This intentionally uses a narrow allowlist. Compound commands and anything
    that cannot be tokenized safely remain eligible for event storage.
    """

    if classify_tool_family(tool_name) != "bash":
        return False
    command = _shell_command_text(tool_input)
    if command is None or _SHELL_META_PATTERN.search(command):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    if tokens[0] in _TRIVIAL_READ_COMMANDS:
        return True
    # Keep this deliberately narrow: only the plain Git status command and
    # its common display flags are considered navigation noise.
    if tokens[0] == "git" and len(tokens) >= 2 and tokens[1] == "status":
        return all(token.startswith("-") for token in tokens[2:])
    return False


def _response_text(tool_response: Any) -> str:
    """Extract a small useful text preview from common Hook response shapes."""

    if not isinstance(tool_response, Mapping):
        return _compact_text(tool_response, limit=700)
    for field in ("output", "stdout", "stderr", "text"):
        value = tool_response.get(field)
        if isinstance(value, str) and value.strip():
            return _compact_text(value, limit=700)
    content = tool_response.get("content")
    if isinstance(content, list):
        texts = [
            item.get("text")
            for item in content
            if isinstance(item, Mapping) and isinstance(item.get("text"), str)
        ]
        if texts:
            return _compact_text(" ".join(texts), limit=700)
    structured = tool_response.get("structuredContent")
    if structured is not None:
        return _compact_text(structured, limit=700)
    return "no result payload"


def summarize_tool_response(tool_response: Any) -> tuple[str, int | None]:
    """Return a bounded result summary and the optional shell exit code."""

    exit_code: int | None = None
    if isinstance(tool_response, Mapping):
        candidate = tool_response.get("exit_code")
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            exit_code = candidate
        if tool_response.get("isError") is True:
            prefix = "error=true"
        elif tool_response.get("isError") is False:
            prefix = "error=false"
        else:
            prefix = "result"
    else:
        prefix = "result"
    if exit_code is not None:
        prefix = f"exit_code={exit_code}; {prefix}"
    preview = _response_text(tool_response)
    return (
        _compact_text(f"{prefix}; preview={preview}", limit=MAX_RESULT_SUMMARY_LENGTH),
        exit_code,
    )


def tool_event_status(tool_response: Any, exit_code: int | None) -> str:
    """Derive succeeded/failed/unknown from the raw Hook response."""

    if exit_code is not None:
        return "succeeded" if exit_code == 0 else "failed"
    if isinstance(tool_response, Mapping):
        if tool_response.get("isError") is True:
            return "failed"
        if tool_response.get("isError") is False:
            return "succeeded"
    return "unknown"


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


def record_general_tool_event(
    event: Mapping[str, Any],
    *,
    database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
    bindings_directory: str | Path = runtime_binding.DEFAULT_BINDINGS_DIRECTORY,
) -> dict[str, Any] | None:
    """Record one non-lifecycle tool event when this session owns an active Run.

    This path deliberately performs no MCP call, model inference, or user-facing
    denial. A missing/invalid Binding (the temporary session-to-Run pointer) or a
    database failure simply means the event is skipped; the original tool call
    has already completed and must not be blocked by telemetry bookkeeping.
    """

    tool_name = event.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return None
    tool_name = tool_name.strip()
    if tool_name in {START_WORK_TOOL, FINISH_WORK_TOOL}:
        return None
    if is_trivial_read_command(tool_name, event.get("tool_input")):
        return None
    session_id = event.get("session_id")
    turn_id = event.get("turn_id")
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    if not isinstance(turn_id, str) or not turn_id.strip():
        return None
    try:
        active = runtime_binding.validate_active_binding(
            session_id=session_id.strip(),
            current_turn_id=turn_id.strip(),
            database_path=database_path,
            bindings_directory=bindings_directory,
        )
        run_id = active["active_run"]["id"]
        result_summary, exit_code = summarize_tool_response(
            event.get("tool_response")
        )
        return state_store.record_run_tool_event(
            run_id,
            tool_use_id=_tool_use_id(event, tool_name=tool_name),
            tool_name=tool_name,
            tool_family=classify_tool_family(tool_name),
            status=tool_event_status(event.get("tool_response"), exit_code),
            exit_code=exit_code,
            input_summary=summarize_tool_input(tool_name, event.get("tool_input")),
            result_summary=result_summary,
            database_path=database_path,
        )
    except Exception:
        # PostToolUse is observational. State-store issues must never deny or
        # otherwise alter the already-completed tool execution.
        return None


def dispatch_post_tool_use(
    event: Mapping[str, Any],
    *,
    recall_runner: RecallRunner = run_recall,
    started_status_reader: StartedStatusReader = read_started_status,
    database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
    bindings_directory: str | Path = runtime_binding.DEFAULT_BINDINGS_DIRECTORY,
) -> dict[str, Any] | None:
    """Handle lifecycle transitions and record other tools as compact events."""

    tool_name = event.get("tool_name")
    if tool_name not in {START_WORK_TOOL, FINISH_WORK_TOOL}:
        record_general_tool_event(
            event,
            database_path=database_path,
            bindings_directory=bindings_directory,
        )
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

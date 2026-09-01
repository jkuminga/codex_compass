"""Select an explicit WorkItem through an external terminal for ``w/`` prompts."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .. import state_store, work_item_picker


SELECTION_TIMEOUT_SECONDS = 300
SELECTIONS_DIRECTORY_NAME = "selections"
WORK_PREFIX = "w/"


class HookInputError(ValueError):
    """Raised when a requested selection omits a required Hook identifier."""


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise HookInputError(f"{field} must be a non-empty string")
    return value.strip()


def strip_work_prefix(prompt: str) -> str | None:
    """Remove a case-insensitive ``w/`` prefix, leaving the user's request."""

    if not prompt[: len(WORK_PREFIX)].lower() == WORK_PREFIX:
        return None
    return prompt[len(WORK_PREFIX) :].lstrip()


def _selection_directory(database_path: str | Path) -> Path:
    """Locate transient selection files beside the state database."""

    return Path(database_path).resolve().parent / SELECTIONS_DIRECTORY_NAME


def _selection_packet(status: str, **fields: Any) -> dict[str, Any]:
    """Build the compact context Codex receives after a selection attempt."""

    return {"selection_status": status, **fields}


def _additional_context(packet: Mapping[str, Any]) -> str:
    return "<work-selection-context>\n" + json.dumps(
        packet, ensure_ascii=False, separators=(",", ":")
    ) + "\n</work-selection-context>"


def _hook_output(packet: Mapping[str, Any] | None = None) -> dict[str, Any]:
    output: dict[str, Any] = {"continue": True}
    if packet is not None:
        output["hookSpecificOutput"] = {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": _additional_context(packet),
        }
    return output


def write_selection_request(
    *, request_id: str, session_id: str, turn_id: str,
    work_items: list[Mapping[str, Any]], selections_directory: Path,
    timeout_seconds: int = SELECTION_TIMEOUT_SECONDS,
) -> Path:
    """Atomically create one picker request containing every ready WorkItem."""

    created_at = work_item_picker.utc_now()
    request_path = selections_directory / f"selection-{request_id}.request.json"
    payload = {
        "schema_version": work_item_picker.SCHEMA_VERSION,
        "request_id": request_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "created_at": work_item_picker.format_timestamp(created_at),
        "expires_at": work_item_picker.format_timestamp(
            created_at + timedelta(seconds=timeout_seconds)
        ),
        "work_items": [
            {"id": item["id"], "title": item["title"], "goal": item["goal"], "priority": item["priority"]}
            for item in work_items
        ],
    }
    work_item_picker.atomic_write_json(request_path, payload)
    return request_path


def launch_terminal_picker(request_path: Path) -> None:
    """Open the picker in a new macOS Terminal.app tab via AppleScript."""

    project_root = shlex.quote(str(state_store.PROJECT_ROOT))
    request = shlex.quote(str(request_path))
    selections_directory = shlex.quote(str(request_path.parent))
    shell_program = f"""\
show_permission_guidance() {{
  printf '\\n[Harness] WorkItem 선택 창을 열지 못했습니다.\\n\\n'
  printf '원인: Terminal이 프로젝트가 있는 Desktop 폴더에 접근할 수 없습니다.\\n\\n'
  printf '해결:\\n'
  printf '1. macOS 설정 → 개인정보 보호 및 보안 → 파일 및 폴더에서\\n'
  printf '   Terminal의 Desktop 폴더 접근을 허용하세요.\\n'
  printf '2. Codex에서 /stop으로 현재 실행을 종료하세요.\\n'
  printf '3. 같은 w/ 요청을 다시 보내세요.\\n\\n'
  printf '아무 키나 누르면 창을 닫습니다. '
  read -r -k 1
}}

if ! cd {project_root} || ! pwd -P >/dev/null 2>&1; then
  show_permission_guidance
  exit 1
fi

if ! test -r {request} || ! test -w {selections_directory}; then
  show_permission_guidance
  exit 1
fi

uv run python -m src.harness.work_item_picker --request {request}
picker_status=$?
if [ "$picker_status" -ne 0 ]; then
  printf '\\n[Harness] 선택 프로그램이 종료되었습니다 (exit %s).\\n' "$picker_status"
  printf '위 오류를 확인한 뒤 같은 w/ 요청을 다시 보내세요.\\n'
  printf '아무 키나 누르면 창을 닫습니다. '
  read -r -k 1
fi
exit "$picker_status"
"""
    command = f"/bin/zsh -lc {shlex.quote(shell_program)}"
    script = 'on run argv\n tell application "Terminal" to do script (item 1 of argv)\nend run'
    try:
        subprocess.run(["osascript", "-e", script, command], check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise HookInputError("Terminal.app picker could not be launched") from error


def validate_selection_result(
    request_path: Path, result: Mapping[str, Any], *, request_text: str,
) -> dict[str, Any]:
    """Accept only a timely result tied to this exact request and listed WI."""

    request = work_item_picker.read_json_object(request_path)
    if result.get("schema_version") != work_item_picker.SCHEMA_VERSION:
        raise HookInputError("selection result schema_version is invalid")
    for field in ("request_id", "session_id", "turn_id"):
        if result.get(field) != request.get(field):
            raise HookInputError(f"selection result {field} does not match request")
    if work_item_picker.parse_timestamp(result.get("created_at"), "created_at") >= work_item_picker.parse_timestamp(request.get("expires_at"), "expires_at"):
        raise HookInputError("selection result was written after expiry")
    status = result.get("status")
    if status == "cancelled":
        if "work_item_id" in result:
            raise HookInputError("cancelled selection result must not contain work_item_id")
        return _selection_packet("cancelled", request=request_text)
    if status != "selected" or not isinstance(result.get("work_item_id"), str):
        raise HookInputError("selection result status is invalid")
    work_items = request.get("work_items")
    if not isinstance(work_items, list):
        raise HookInputError("selection request work_items is invalid")
    selected = next((item for item in work_items if item.get("id") == result["work_item_id"]), None)
    if selected is None:
        raise HookInputError("selected WorkItem was not in the request")
    return _selection_packet(
        "selected", work_item_id=selected["id"], title=selected["title"], request=request_text
    )


def wait_for_selection_result(
    request_path: Path, *, timeout_seconds: int = SELECTION_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Poll the paired result file only until the selection timeout expires."""

    result_path = work_item_picker.result_path_for(request_path)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if result_path.exists():
            return work_item_picker.read_json_object(result_path)
        time.sleep(0.1)
    return None


def cleanup_selection_files(request_path: Path) -> None:
    """Remove this request/result pair after any completed selection attempt."""

    for path in (request_path, work_item_picker.result_path_for(request_path)):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def cleanup_expired_selection_files(selections_directory: Path) -> None:
    """Remove only expired orphaned files left by an abnormal earlier process."""

    if not selections_directory.exists():
        return
    now = work_item_picker.utc_now()
    for request_path in selections_directory.glob("selection-*.request.json"):
        try:
            request = work_item_picker.read_json_object(request_path)
            if now >= work_item_picker.parse_timestamp(request.get("expires_at"), "expires_at"):
                cleanup_selection_files(request_path)
        except work_item_picker.SelectionFileError:
            continue


def dispatch_user_prompt_submit(
    event: Mapping[str, Any], *, database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Run external WI selection only when the prompt begins with ``w/``."""

    prompt = event.get("prompt")
    if not isinstance(prompt, str):
        return _hook_output()
    request_text = strip_work_prefix(prompt)
    if request_text is None:
        return _hook_output()
    try:
        session_id = _required_string(event, "session_id")
        turn_id = _required_string(event, "turn_id")
        selections_directory = _selection_directory(database_path)
        cleanup_expired_selection_files(selections_directory)
        request_path = write_selection_request(
            request_id=str(uuid4()), session_id=session_id, turn_id=turn_id,
            work_items=state_store.list_ready_work_items(database_path=database_path),
            selections_directory=selections_directory,
        )
    except Exception as error:
        return _hook_output(_selection_packet("error", reason=type(error).__name__))
    try:
        launch_terminal_picker(request_path)
        result = wait_for_selection_result(request_path)
        if result is None:
            return _hook_output(_selection_packet("timed_out"))
        return _hook_output(validate_selection_result(request_path, result, request_text=request_text))
    except Exception as error:
        return _hook_output(_selection_packet("error", reason=type(error).__name__))
    finally:
        cleanup_selection_files(request_path)


def main() -> int:
    """Read Hook JSON from stdin and emit Codex Hook JSON to stdout."""

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = dispatch_user_prompt_submit(
        payload,
        database_path=os.environ.get("HARNESS_STATE_DATABASE") or state_store.DEFAULT_DATABASE_PATH,
    )
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

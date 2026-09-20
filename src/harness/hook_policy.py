"""Deterministic tool classification and authorization for PreToolUse."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from . import runtime_binding, state_store


PROJECT_ROOT = Path(__file__).resolve().parents[2]

ToolClassification = Literal["read", "bootstrap", "run_required", "dangerous"]
BindingValidator = Callable[..., Mapping[str, Any]]

SHELL_TOOL_NAMES = frozenset({"bash", "exec_command", "shell_command"})
READ_ONLY_COMMANDS = frozenset(
    {"pwd", "ls", "rg", "cat", "head", "tail", "wc", "stat", "file", "which"}
)
READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {"status", "diff", "log", "show", "rev-parse"}
)
MUTATING_COMMANDS = frozenset(
    {"mkdir", "touch", "cp", "mv", "chmod", "chown", "rm", "rmdir", "install"}
)

HARNESS_MCP_PREFIX = "mcp__harness_state__"
HARNESS_MEMORY_MCP_PREFIX = "mcp__harness_memory__"
HARNESS_READ_TOOLS = frozenset(
    {
        "get_project_status",
        "get_work_context",
        "search_work_items",
        "get_postflight_status",
        "list_memory_candidates",
    }
)
HARNESS_BOOTSTRAP_TOOLS = frozenset(
    {
        "create_feature",
        "create_work_item",
        "create_ready_work_item",
        "refine_draft_work_item",
        "start_work",
        "close_work_item",
        "recover_abandoned_work",
    }
)
HARNESS_MEMORY_READ_TOOLS = frozenset({"inspect_memory_candidate"})
HARNESS_MEMORY_WRITE_TOOLS = frozenset({"finalize_memory_candidate"})

_COMPLEX_SHELL_OPERATORS = frozenset({">", "<", "|", "&", ";", "\n", "`"})
_PATCH_PATH_PATTERN = re.compile(
    r"^(?:\*\*\* (?:Update|Add|Delete) File:|\*\*\* Move to:|---|\+\+\+)\s+(.+?)\s*$",
    re.MULTILINE,
)
_DANGEROUS_SHELL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "dangerous_rm_recursive_force",
        re.compile(
            r"\brm\s+(?:[^\n;&|]*\s)?(?:-[^\s]*[rR][^\s]*f[^\s]*|"
            r"-[^\s]*f[^\s]*[rR][^\s]*|-[rR]\s+-f|-f\s+-[rR]|"
            r"--recursive\s+--force|--force\s+--recursive)(?:\s|$)"
        ),
    ),
    ("dangerous_git_reset", re.compile(r"\bgit\b[^\n;&|]{0,200}\breset\b")),
    ("dangerous_git_clean", re.compile(r"\bgit\b[^\n;&|]{0,200}\bclean\b")),
    (
        "dangerous_git_force_push",
        re.compile(
            r"\bgit\b[^\n;&|]{0,200}\bpush\b[^\n;&|]{0,200}"
            r"(?:--force(?:-with-lease)?|-f)(?:\s|$)"
        ),
    ),
)


@dataclass(frozen=True)
class ToolDecision:
    """A small, testable result describing why one tool call is allowed or denied."""

    classification: ToolClassification
    allowed: bool
    reason_code: str
    message: str


def normalize_tool_name(tool_name: str) -> str:
    """Normalize only whitespace; MCP names remain exact and auditable."""

    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValueError("tool_name must be a non-empty string")
    return tool_name.strip()


def _decision(
    classification: ToolClassification,
    allowed: bool,
    reason_code: str,
    message: str,
) -> ToolDecision:
    return ToolDecision(classification, allowed, reason_code, message)


def _tool_input_text(tool_input: Any, *fields: str) -> str:
    if isinstance(tool_input, str):
        return tool_input
    if isinstance(tool_input, Mapping):
        for field in fields:
            value = tool_input.get(field)
            if isinstance(value, str):
                return value
    return ""


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _path_violation(path_text: str, *, project_root: Path) -> str | None:
    """Return a reason code for a path outside the repo or in a protected area."""

    candidate_text = path_text.strip().strip("'\"")
    if not candidate_text or candidate_text in {"/dev/null", "dev/null"}:
        return None
    if candidate_text.startswith(("a/", "b/")):
        candidate_text = candidate_text[2:]
    if "://" in candidate_text or candidate_text.startswith("-"):
        return None

    root = project_root.resolve()
    candidate = Path(candidate_text).expanduser()
    resolved = (
        candidate.resolve(strict=False)
        if candidate.is_absolute()
        else (root / candidate).resolve(strict=False)
    )
    if not _is_within(resolved, root):
        return "path_outside_project"

    relative = resolved.relative_to(root).as_posix()
    if relative == ".git" or relative.startswith(".git/"):
        return "protected_git_path"
    if relative == ".harness/state.db" or relative.startswith(".harness/state.db-"):
        return "protected_state_database"
    if relative == ".harness/runtime/bindings" or relative.startswith(
        ".harness/runtime/bindings/"
    ):
        return "protected_runtime_binding"
    return None


def _extract_patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for match in _PATCH_PATH_PATTERN.finditer(patch):
        path = match.group(1).strip()
        if path and path not in paths:
            paths.append(path)
    return paths


def classify_apply_patch(
    tool_input: Any, *, project_root: str | Path = PROJECT_ROOT
) -> ToolDecision:
    """Treat every patch as a project change and reject protected destinations."""

    patch = _tool_input_text(tool_input, "patch", "input", "command")
    root = Path(project_root)
    for path in _extract_patch_paths(patch):
        violation = _path_violation(path, project_root=root)
        if violation is not None:
            return _decision(
                "dangerous",
                False,
                violation,
                f"apply_patch가 허용되지 않는 경로를 대상으로 합니다: {path}",
            )
    return _decision(
        "run_required",
        False,
        "apply_patch_requires_run",
        "파일을 수정하려면 현재 Turn에 연결된 활성 Run이 필요합니다.",
    )


def _contains_complex_shell_syntax(command: str) -> bool:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(command):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote != "'":
            escaped = True
            continue
        if quote == "'":
            if character == quote:
                quote = None
            continue
        if quote == '"':
            if character == quote:
                quote = None
            elif character == "`" or (
                character == "$"
                and index + 1 < len(command)
                and command[index + 1] == "("
            ):
                return True
            continue
        if character in {"'", '"'}:
            quote = character
            continue
        if character in _COMPLEX_SHELL_OPERATORS:
            return True
        if (
            character == "$"
            and index + 1 < len(command)
            and command[index + 1] == "("
        ):
            return True
    return False


def _shell_tokens(command: str) -> list[str] | None:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return None


def _dangerous_shell_reason(command: str) -> str | None:
    for reason_code, pattern in _DANGEROUS_SHELL_PATTERNS:
        if pattern.search(command):
            return reason_code
    return None


def _path_like_tokens(tokens: list[str]) -> list[str]:
    paths: list[str] = []
    for token in tokens:
        cleaned = token.strip("(){}[],:=")
        if not cleaned or cleaned.startswith("-") or "://" in cleaned:
            continue
        if cleaned.startswith(("/", "./", "../", "~")) or "/" in cleaned:
            paths.append(cleaned)
    return paths


def _explicit_output_paths(tokens: list[str]) -> list[str]:
    """Extract common CLI output-file options that turn reads into writes."""

    paths: list[str] = []
    for index, token in enumerate(tokens):
        if token in {"-o", "--output"} and index + 1 < len(tokens):
            paths.append(tokens[index + 1])
        elif token.startswith("--output="):
            paths.append(token.split("=", 1)[1])
        elif token.startswith("-o") and len(token) > 2:
            paths.append(token[2:])
    return paths


def _is_read_only_git(tokens: list[str], *, writes_output: bool) -> bool:
    if len(tokens) < 2 or tokens[0] != "git":
        return False
    if writes_output:
        return False
    subcommand = tokens[1]
    if subcommand in READ_ONLY_GIT_SUBCOMMANDS:
        return True
    return tokens == ["git", "branch", "--show-current"]


def _is_read_only_command(tokens: list[str], *, writes_output: bool) -> bool:
    if not tokens:
        return False
    if writes_output:
        return False
    if tokens[0] == "command":
        return len(tokens) >= 3 and tokens[1] == "-v"
    return tokens[0] in READ_ONLY_COMMANDS or _is_read_only_git(
        tokens, writes_output=writes_output
    )


def _is_directly_mutating(tokens: list[str]) -> bool:
    if not tokens:
        return False
    if tokens[0] in MUTATING_COMMANDS:
        return True
    return tokens[0] == "git" and len(tokens) > 1 and tokens[1] not in (
        READ_ONLY_GIT_SUBCOMMANDS | {"branch"}
    )


def classify_shell_command(
    tool_input: Any, *, project_root: str | Path = PROJECT_ROOT
) -> ToolDecision:
    """Classify a shell string without executing it or inferring script effects."""

    command = _tool_input_text(tool_input, "cmd", "command")
    if not command.strip():
        return _decision(
            "run_required",
            False,
            "shell_input_unknown",
            "Bash 명령 입력을 확인할 수 없어 활성 Run이 필요합니다.",
        )

    dangerous_reason = _dangerous_shell_reason(command)
    if dangerous_reason is not None:
        return _decision(
            "dangerous",
            False,
            dangerous_reason,
            "복구하기 어려운 위험 명령은 활성 Run이 있어도 실행하지 않습니다.",
        )

    complex_syntax = _contains_complex_shell_syntax(command)
    tokens = _shell_tokens(command)
    if tokens is None:
        return _decision(
            "run_required",
            False,
            "shell_parse_failed",
            "Bash 명령 구조를 안전하게 해석할 수 없어 활성 Run이 필요합니다.",
        )

    root = Path(project_root)
    output_paths = _explicit_output_paths(tokens)
    for path in _path_like_tokens(tokens) + output_paths:
        violation = _path_violation(path, project_root=root)
        if violation is None:
            continue
        if violation != "path_outside_project" or _is_directly_mutating(tokens):
            return _decision(
                "dangerous",
                False,
                violation,
                f"Bash 명령이 허용되지 않는 경로를 직접 사용합니다: {path}",
            )
        return _decision(
            "run_required",
            False,
            "outside_read_requires_run",
            "저장소 밖 경로를 읽는 명령은 활성 Run이 필요합니다.",
        )

    if complex_syntax:
        return _decision(
            "run_required",
            False,
            "complex_shell_requires_run",
            "복합 Bash 명령은 활성 Run이 필요합니다.",
        )
    if _is_read_only_command(tokens, writes_output=bool(output_paths)):
        return _decision(
            "read",
            True,
            "read_only_shell",
            "확실한 조회 전용 Bash 명령입니다.",
        )
    return _decision(
        "run_required",
        False,
        "shell_command_requires_run",
        "변경 가능하거나 알 수 없는 Bash 명령은 활성 Run이 필요합니다.",
    )


def classify_mcp_tool(tool_name: str, tool_input: Any) -> ToolDecision:
    """Apply detailed policy only to Harness MCP tools; pass external MCP through."""

    if tool_name.startswith(HARNESS_MEMORY_MCP_PREFIX):
        short_name = tool_name.removeprefix(HARNESS_MEMORY_MCP_PREFIX)
        if short_name in HARNESS_MEMORY_READ_TOOLS:
            return _decision(
                "read",
                True,
                "harness_memory_mcp_read",
                "장기 기억 후보와 기존 기억을 비교하는 조회 도구입니다.",
            )
        if short_name in HARNESS_MEMORY_WRITE_TOOLS:
            return _decision(
                "run_required",
                False,
                "harness_memory_mcp_write_requires_run",
                "장기 기억 저장 도구는 현재 Turn의 활성 Run이 필요합니다.",
            )
        return _decision(
            "run_required",
            False,
            "harness_memory_mcp_requires_run",
            "등록되지 않은 Harness Memory MCP는 활성 Run이 필요합니다.",
        )

    if not tool_name.startswith(HARNESS_MCP_PREFIX):
        return _decision(
            "read",
            True,
            "external_mcp_passthrough",
            "외부 MCP는 Harness Runtime Binding 검사 대상이 아닙니다.",
        )

    short_name = tool_name.removeprefix(HARNESS_MCP_PREFIX)
    if short_name in HARNESS_READ_TOOLS:
        return _decision(
            "read", True, "harness_mcp_read", "상태 저장소 조회 도구입니다."
        )
    if short_name in HARNESS_BOOTSTRAP_TOOLS:
        return _decision(
            "bootstrap",
            True,
            "harness_mcp_bootstrap",
            "WorkItem과 Run을 준비하는 최소 상태 도구입니다.",
        )
    if short_name == "change_work_item_state":
        status = tool_input.get("status") if isinstance(tool_input, Mapping) else None
        if status == "ready":
            return _decision(
                "bootstrap",
                True,
                "work_item_ready_bootstrap",
                "WorkItem을 실행 가능한 ready 상태로 준비합니다.",
            )
    return _decision(
        "run_required",
        False,
        "harness_mcp_requires_run",
        "프로젝트 상태를 변경하는 Harness MCP는 활성 Run이 필요합니다.",
    )


def classify_tool_call(
    tool_name: str,
    tool_input: Any,
    *,
    project_root: str | Path = PROJECT_ROOT,
) -> ToolDecision:
    """Classify one Codex tool call using narrow allowlists and safe defaults."""

    normalized = normalize_tool_name(tool_name)
    lowered = normalized.lower()
    if lowered == "apply_patch":
        return classify_apply_patch(tool_input, project_root=project_root)
    if lowered in SHELL_TOOL_NAMES:
        return classify_shell_command(tool_input, project_root=project_root)
    if lowered.startswith("mcp__"):
        return classify_mcp_tool(lowered, tool_input)
    return _decision(
        "run_required",
        False,
        "unknown_local_tool_requires_run",
        "등록되지 않은 로컬 도구는 활성 Run이 필요합니다.",
    )


def authorize_tool_call(
    decision: ToolDecision,
    *,
    session_id: str,
    current_turn_id: str,
    database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
    bindings_directory: str | Path = runtime_binding.DEFAULT_BINDINGS_DIRECTORY,
    binding_validator: BindingValidator = runtime_binding.validate_active_binding,
) -> ToolDecision:
    """Cross-check run-required calls against the session Binding and SQLite."""

    if decision.classification != "run_required":
        return decision
    try:
        binding_validator(
            session_id=session_id,
            current_turn_id=current_turn_id,
            database_path=database_path,
            bindings_directory=bindings_directory,
        )
    except Exception as error:
        if decision.reason_code == "complex_shell_requires_run":
            return replace(
                decision,
                allowed=False,
                reason_code=(
                    f"warning:complex_command_retry:{type(error).__name__}"
                ),
                message=(
                    "복합 Bash 명령이 안전 정책에 따라 중단되었습니다. "
                    "조회가 목적이라면 읽기 전용 형태로 단순화하여 "
                    "다시 시도하겠습니다."
                ),
            )
        return replace(
            decision,
            allowed=False,
            reason_code=f"active_binding_invalid:{type(error).__name__}",
            message=(
                "현재 Turn의 유효한 Runtime Binding과 활성 Run을 확인할 수 없습니다. "
                "먼저 WorkItem을 선택하고 start_work()를 실행하세요."
            ),
        )
    return replace(
        decision,
        allowed=True,
        reason_code="active_binding_valid",
        message="현재 Turn의 Runtime Binding과 SQLite 활성 Run이 일치합니다.",
    )

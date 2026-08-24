"""Fail-closed process guard for the Codex PreToolUse policy Hook."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from typing import Any


CODEX_BLOCK_EXIT_CODE = 2
DEFAULT_CHILD_TIMEOUT_SECONDS = 7.0


class GuardOutputError(ValueError):
    """Raised when the child Hook did not return the required JSON contract."""


def _block(reason_code: str) -> int:
    """Return Codex's official PreToolUse block signal with a short reason."""

    sys.stderr.write(
        f"PreToolUse guard blocked the tool: [{reason_code}] "
        "the policy Hook did not complete safely.\n"
    )
    return CODEX_BLOCK_EXIT_CODE


def _validated_output(stdout: bytes) -> dict[str, Any] | None:
    """Parse and validate the exact policy response the guard may forward."""

    if not stdout.strip():
        return None
    try:
        payload = json.loads(stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GuardOutputError("child output is not one JSON object") from error

    if not isinstance(payload, dict):
        raise GuardOutputError("child output must be a JSON object")
    specific = payload.get("hookSpecificOutput")
    if not isinstance(specific, dict):
        raise GuardOutputError("hookSpecificOutput must be an object")
    if specific.get("hookEventName") != "PreToolUse":
        raise GuardOutputError("hookEventName must be PreToolUse")
    if specific.get("permissionDecision") != "deny":
        raise GuardOutputError("permissionDecision must be deny")
    reason = specific.get("permissionDecisionReason")
    if not isinstance(reason, str) or not reason.strip():
        raise GuardOutputError("permissionDecisionReason must be non-empty")
    return payload


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Stop the timed-out child and its process group when the OS supports it."""

    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    process.communicate()


def run_guard(
    command: Sequence[str],
    hook_input: bytes,
    *,
    timeout_seconds: float = DEFAULT_CHILD_TIMEOUT_SECONDS,
) -> int:
    """Run one policy Hook and convert every child failure into Codex exit 2."""

    if not command:
        return _block("missing_child_command")
    if timeout_seconds <= 0:
        return _block("invalid_child_timeout")

    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    except OSError as error:
        return _block(f"child_start_failed:{type(error).__name__}")

    try:
        stdout, stderr = process.communicate(hook_input, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _stop_process(process)
        return _block("child_timeout")

    if process.returncode == CODEX_BLOCK_EXIT_CODE:
        reason = stderr.decode("utf-8", errors="replace").strip()
        sys.stderr.write((reason or "PreToolUse policy Hook blocked the tool.") + "\n")
        return CODEX_BLOCK_EXIT_CODE
    if process.returncode != 0:
        return _block(f"child_exit_{process.returncode}")

    try:
        payload = _validated_output(stdout)
    except GuardOutputError as error:
        return _block(f"invalid_child_output:{type(error).__name__}")

    if payload is not None:
        json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Read Hook input, supervise the configured child, and forward safe output."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_CHILD_TIMEOUT_SECONDS,
        help="maximum seconds allowed for the child policy Hook",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    command = arguments.command
    if command[:1] == ["--"]:
        command = command[1:]
    return run_guard(command, sys.stdin.buffer.read(), timeout_seconds=arguments.timeout)


if __name__ == "__main__":
    raise SystemExit(main())

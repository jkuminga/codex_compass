import contextlib
import io
import json
import sys
import time
import unittest

from src.harness.hooks import pre_tool_use_guard


class PreToolUseGuardTests(unittest.TestCase):
    def run_guard(
        self,
        child_source: str,
        *,
        timeout_seconds: float = 1.0,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = pre_tool_use_guard.run_guard(
                [sys.executable, "-c", child_source],
                b'{"hook_event_name":"PreToolUse"}',
                timeout_seconds=timeout_seconds,
            )
        return result, stdout.getvalue(), stderr.getvalue()

    def valid_deny_source(self) -> str:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "test deny",
            }
        }
        return f"import json; print(json.dumps({output!r}))"

    def test_empty_stdout_is_forwarded_as_normal_allow(self) -> None:
        result, stdout, stderr = self.run_guard("pass")

        self.assertEqual(result, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    def test_valid_deny_is_forwarded(self) -> None:
        result, stdout, stderr = self.run_guard(self.valid_deny_source())

        self.assertEqual(result, 0)
        self.assertEqual(
            json.loads(stdout)["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )
        self.assertEqual(stderr, "")

    def test_allow_json_without_rewrite_is_rejected(self) -> None:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "unsupported ordinary allow",
            }
        }
        result, stdout, stderr = self.run_guard(
            f"import json; print(json.dumps({output!r}))"
        )

        self.assertEqual(result, pre_tool_use_guard.CODEX_BLOCK_EXIT_CODE)
        self.assertEqual(stdout, "")
        self.assertIn("invalid_child_output", stderr)

    def test_child_exit_one_becomes_codex_block_exit_two(self) -> None:
        result, stdout, stderr = self.run_guard("raise SystemExit(1)")

        self.assertEqual(result, pre_tool_use_guard.CODEX_BLOCK_EXIT_CODE)
        self.assertEqual(stdout, "")
        self.assertIn("child_exit_1", stderr)

    def test_invalid_json_becomes_codex_block_exit_two(self) -> None:
        result, stdout, stderr = self.run_guard("print('not-json')")

        self.assertEqual(result, pre_tool_use_guard.CODEX_BLOCK_EXIT_CODE)
        self.assertEqual(stdout, "")
        self.assertIn("invalid_child_output", stderr)

    def test_wrong_json_contract_becomes_codex_block_exit_two(self) -> None:
        result, stdout, stderr = self.run_guard("print('{}')")

        self.assertEqual(result, pre_tool_use_guard.CODEX_BLOCK_EXIT_CODE)
        self.assertEqual(stdout, "")
        self.assertIn("invalid_child_output", stderr)

    def test_child_timeout_becomes_codex_block_exit_two(self) -> None:
        started_at = time.monotonic()
        result, stdout, stderr = self.run_guard(
            "import time; time.sleep(5)", timeout_seconds=0.05
        )

        self.assertEqual(result, pre_tool_use_guard.CODEX_BLOCK_EXIT_CODE)
        self.assertEqual(stdout, "")
        self.assertIn("child_timeout", stderr)
        self.assertLess(time.monotonic() - started_at, 1.0)

    def test_explicit_child_exit_two_is_preserved(self) -> None:
        result, stdout, stderr = self.run_guard(
            "import sys; print('policy denied', file=sys.stderr); raise SystemExit(2)"
        )

        self.assertEqual(result, pre_tool_use_guard.CODEX_BLOCK_EXIT_CODE)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "policy denied\n")

    def test_missing_child_command_becomes_codex_block_exit_two(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = pre_tool_use_guard.run_guard([], b"{}")

        self.assertEqual(result, pre_tool_use_guard.CODEX_BLOCK_EXIT_CODE)
        self.assertIn("missing_child_command", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

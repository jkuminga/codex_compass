import tempfile
import unittest
from pathlib import Path

from src.harness import hook_policy


class HookPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name) / "project"
        self.project_root.mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_apply_patch_always_requires_run(self) -> None:
        decision = hook_policy.classify_tool_call(
            "apply_patch",
            {"patch": "*** Update File: src/example.py\n@@\n-old\n+new"},
            project_root=self.project_root,
        )

        self.assertEqual(decision.classification, "run_required")
        self.assertFalse(decision.allowed)

    def test_apply_patch_rejects_protected_and_outside_paths(self) -> None:
        cases = {
            ".git/config": "protected_git_path",
            ".harness/state.db-wal": "protected_state_database",
            ".harness/runtime/bindings/session.json": "protected_runtime_binding",
            "../outside.txt": "path_outside_project",
        }
        for path, reason_code in cases.items():
            with self.subTest(path=path):
                decision = hook_policy.classify_apply_patch(
                    {"patch": f"*** Update File: {path}\n@@\n-old\n+new"},
                    project_root=self.project_root,
                )
                self.assertEqual(decision.classification, "dangerous")
                self.assertEqual(decision.reason_code, reason_code)

    def test_simple_read_commands_accept_command_and_cmd_fields(self) -> None:
        for tool_input in ({"command": "git status"}, {"cmd": "rg TODO src"}):
            with self.subTest(tool_input=tool_input):
                decision = hook_policy.classify_shell_command(
                    tool_input, project_root=self.project_root
                )
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.classification, "read")

    def test_git_read_command_with_output_file_requires_run(self) -> None:
        local_output = hook_policy.classify_shell_command(
            {"command": "git diff --output=report.diff"},
            project_root=self.project_root,
        )
        protected_output = hook_policy.classify_shell_command(
            {"command": "git diff --output=.harness/state.db"},
            project_root=self.project_root,
        )

        self.assertEqual(local_output.classification, "run_required")
        self.assertFalse(local_output.allowed)
        self.assertEqual(protected_output.classification, "dangerous")
        self.assertEqual(protected_output.reason_code, "protected_state_database")

    def test_quoted_pipe_is_read_but_real_pipe_requires_run(self) -> None:
        quoted = hook_policy.classify_shell_command(
            {"command": "rg 'a|b' src"}, project_root=self.project_root
        )
        pipeline = hook_policy.classify_shell_command(
            {"command": "rg TODO src | head"}, project_root=self.project_root
        )

        self.assertEqual(quoted.classification, "read")
        self.assertEqual(pipeline.classification, "run_required")

    def test_command_substitution_inside_double_quotes_requires_run(self) -> None:
        for command in ('rg "$(touch output)" src', 'rg "`touch output`" src'):
            with self.subTest(command=command):
                decision = hook_policy.classify_shell_command(
                    {"command": command}, project_root=self.project_root
                )
                self.assertEqual(decision.classification, "run_required")
                self.assertFalse(decision.allowed)

    def test_unknown_and_script_commands_require_run(self) -> None:
        for command in ("python script.py", "find . -name '*.py'", "mkdir output"):
            with self.subTest(command=command):
                decision = hook_policy.classify_shell_command(
                    {"command": command}, project_root=self.project_root
                )
                self.assertEqual(decision.classification, "run_required")
                self.assertFalse(decision.allowed)

    def test_dangerous_commands_are_denied_even_when_nested(self) -> None:
        commands = (
            "rm -rf build",
            "git reset HEAD~1",
            "git clean -fd",
            "git push origin main --force",
            'sh -c "git reset --hard HEAD"',
        )
        for command in commands:
            with self.subTest(command=command):
                decision = hook_policy.classify_shell_command(
                    {"command": command}, project_root=self.project_root
                )
                self.assertEqual(decision.classification, "dangerous")
                self.assertFalse(decision.allowed)

    def test_mutating_shell_command_rejects_outside_path(self) -> None:
        decision = hook_policy.classify_shell_command(
            {"command": "mkdir ../outside"}, project_root=self.project_root
        )

        self.assertEqual(decision.classification, "dangerous")
        self.assertEqual(decision.reason_code, "path_outside_project")

    def test_harness_mcp_groups_and_external_passthrough(self) -> None:
        cases = (
            ("mcp__harness_state__get_work_context", {}, "read", True),
            ("mcp__harness_state__start_work", {}, "bootstrap", True),
            ("mcp__harness_state__create_ready_work_item", {}, "bootstrap", True),
            (
                "mcp__harness_state__refine_draft_work_item",
                {},
                "bootstrap",
                True,
            ),
            (
                "mcp__harness_state__change_work_item_state",
                {"status": "ready"},
                "bootstrap",
                True,
            ),
            (
                "mcp__harness_state__change_work_item_state",
                {"status": "blocked"},
                "run_required",
                False,
            ),
            ("mcp__harness_state__future_tool", {}, "run_required", False),
            ("mcp__harness_memory__inspect_memory_candidate", {}, "read", True),
            ("mcp__harness_memory__finalize_memory_candidate", {}, "run_required", False),
            ("mcp__harness_memory__future_tool", {}, "run_required", False),
            ("mcp__notion__create_page", {}, "read", True),
        )
        for tool_name, tool_input, classification, allowed in cases:
            with self.subTest(tool_name=tool_name):
                decision = hook_policy.classify_tool_call(tool_name, tool_input)
                self.assertEqual(decision.classification, classification)
                self.assertEqual(decision.allowed, allowed)

    def test_unknown_local_tool_requires_run(self) -> None:
        decision = hook_policy.classify_tool_call("future_local_tool", {})

        self.assertEqual(decision.classification, "run_required")
        self.assertFalse(decision.allowed)


if __name__ == "__main__":
    unittest.main()

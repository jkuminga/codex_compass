import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.harness import runtime_binding, state_store
from src.harness.hooks import pre_tool_use


class PreToolUseHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.project_root = root / "project"
        self.project_root.mkdir()
        self.database_path = root / "state.db"
        self.bindings_directory = root / "bindings"
        state_store.initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def event(
        self,
        tool_name: str,
        tool_input: object,
        *,
        session_id: str = "session-current",
        turn_id: str = "turn-current",
    ) -> dict[str, object]:
        return {
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "turn_id": turn_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": "tool-use-1",
        }

    def permission(self, output: dict[str, object]) -> tuple[str, str]:
        specific = output["hookSpecificOutput"]
        return specific["permissionDecision"], specific["permissionDecisionReason"]

    def create_active_binding(self) -> tuple[dict[str, object], dict[str, object]]:
        work_item = state_store.create_work_item(
            title="PreToolUse 통합 테스트",
            kind="implementation",
            goal="현재 Turn의 프로젝트 변경만 허용한다.",
            next_action="Hook 정책을 검증한다.",
            actor="test",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="Hook 정책을 검증한다.",
            actor="test",
            reason="테스트 준비",
            database_path=self.database_path,
        )
        run = state_store.start_run(
            work_item["id"],
            intent="PreToolUse를 검증한다.",
            recall_query="PreToolUse 통합 테스트",
            actor="test",
            database_path=self.database_path,
        )
        runtime_binding.save_binding(
            session_id="session-current",
            turn_id="turn-current",
            work_item_id=work_item["id"],
            run_id=run["id"],
            bindings_directory=self.bindings_directory,
        )
        return work_item, run

    def dispatch(self, event: dict[str, object]) -> dict[str, object]:
        return pre_tool_use.dispatch_pre_tool_use(
            event,
            project_root=self.project_root,
            database_path=self.database_path,
            bindings_directory=self.bindings_directory,
        )

    def test_read_bootstrap_and_external_mcp_do_not_need_binding(self) -> None:
        cases = (
            self.event("Bash", {"command": "git status"}),
            self.event("mcp__harness_state__start_work", {"work_item_id": "WI-1"}),
            self.event("mcp__github__get_issue", {"issue": 1}),
        )
        for event in cases:
            with self.subTest(tool_name=event["tool_name"]):
                self.assertIsNone(self.dispatch(event))

    def test_project_change_without_binding_is_denied(self) -> None:
        output = self.dispatch(
            self.event(
                "apply_patch",
                {"patch": "*** Update File: src/example.py\n@@\n-old\n+new"},
            )
        )

        permission, reason = self.permission(output)
        self.assertEqual(permission, "deny")
        self.assertIn("start_work()", reason)

    def test_project_change_with_current_active_binding_is_allowed(self) -> None:
        self.create_active_binding()

        output = self.dispatch(
            self.event(
                "apply_patch",
                {"patch": "*** Update File: src/example.py\n@@\n-old\n+new"},
            )
        )

        self.assertIsNone(output)

    def test_different_turn_and_finished_run_are_denied(self) -> None:
        work_item, run = self.create_active_binding()
        different_turn = self.dispatch(
            self.event("Bash", {"command": "python test.py"}, turn_id="turn-other")
        )
        self.assertEqual(self.permission(different_turn)[0], "deny")

        state_store.recover_stale_run(
            work_item["id"],
            expected_run_id=run["id"],
            actor="test",
            database_path=self.database_path,
        )
        finished = self.dispatch(
            self.event("Bash", {"command": "python test.py"})
        )
        self.assertEqual(self.permission(finished)[0], "deny")

    def test_dangerous_command_is_denied_with_valid_binding(self) -> None:
        self.create_active_binding()

        output = self.dispatch(self.event("Bash", {"command": "git clean -fd"}))

        permission, reason = self.permission(output)
        self.assertEqual(permission, "deny")
        self.assertIn("dangerous_git_clean", reason)

    def test_database_failure_does_not_block_read_but_denies_change(self) -> None:
        runtime_binding.save_binding(
            session_id="session-current",
            turn_id="turn-current",
            work_item_id="WI-database-failure",
            run_id="RUN-database-failure",
            bindings_directory=self.bindings_directory,
        )
        invalid_database = self.project_root
        read = pre_tool_use.dispatch_pre_tool_use(
            self.event("Bash", {"command": "ls"}),
            project_root=self.project_root,
            database_path=invalid_database,
            bindings_directory=self.bindings_directory,
        )
        change = pre_tool_use.dispatch_pre_tool_use(
            self.event("apply_patch", {"patch": "*** Add File: hello.txt\n+hi"}),
            project_root=self.project_root,
            database_path=invalid_database,
            bindings_directory=self.bindings_directory,
        )

        self.assertIsNone(read)
        change_permission, change_reason = self.permission(change)
        self.assertEqual(change_permission, "deny")
        self.assertIn("active_binding_invalid:OperationalError", change_reason)

    def test_corrupt_binding_json_is_converted_to_deny(self) -> None:
        self.bindings_directory.mkdir(parents=True)
        binding_path = self.bindings_directory / "session-current.json"
        binding_path.write_text("not-json", encoding="utf-8")

        output = self.dispatch(
            self.event("Bash", {"command": "python test.py"})
        )

        permission, reason = self.permission(output)
        self.assertEqual(permission, "deny")
        self.assertIn("active_binding_invalid:RuntimeBindingError", reason)

    def test_unexpected_policy_exception_is_converted_to_deny(self) -> None:
        output = pre_tool_use.dispatch_pre_tool_use(
            self.event(
                "apply_patch",
                {"patch": "*** Update File: src/example.py\n@@\n-old\n+new"},
            ),
            project_root=object(),
            database_path=self.database_path,
            bindings_directory=self.bindings_directory,
        )

        permission, reason = self.permission(output)
        self.assertEqual(permission, "deny")
        self.assertIn("pre_tool_use_failed:TypeError", reason)

    def test_malformed_event_is_converted_to_valid_deny_output(self) -> None:
        output = self.dispatch({"hook_event_name": "PreToolUse"})

        permission, reason = self.permission(output)
        self.assertEqual(permission, "deny")
        self.assertIn("pre_tool_use_failed:HookInputError", reason)

    def test_cli_invalid_json_fails_closed_with_exit_zero(self) -> None:
        command = Path(__file__).resolve().parents[1] / "bin" / "harness-pre-tool-use"
        completed = subprocess.run(
            [str(command)],
            input="not-json",
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0)
        output = json.loads(completed.stdout)
        self.assertEqual(self.permission(output)[0], "deny")

    def test_cli_internal_binding_failure_returns_valid_deny_json(self) -> None:
        self.bindings_directory.mkdir(parents=True)
        (self.bindings_directory / "session-current.json").write_text(
            "not-json", encoding="utf-8"
        )
        command = Path(__file__).resolve().parents[1] / "bin" / "harness-pre-tool-use"
        environment = os.environ.copy()
        environment.update(
            {
                "HARNESS_PROJECT_ROOT": str(self.project_root),
                "HARNESS_STATE_DATABASE": str(self.database_path),
                "HARNESS_BINDINGS_DIRECTORY": str(self.bindings_directory),
            }
        )
        event = self.event("Bash", {"command": "python test.py"})

        completed = subprocess.run(
            [str(command)],
            input=json.dumps(event),
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )

        self.assertEqual(completed.returncode, 0)
        output = json.loads(completed.stdout)
        permission, reason = self.permission(output)
        self.assertEqual(permission, "deny")
        self.assertIn("active_binding_invalid:RuntimeBindingError", reason)

    def test_cli_read_only_allow_has_empty_stdout(self) -> None:
        command = Path(__file__).resolve().parents[1] / "bin" / "harness-pre-tool-use"
        completed = subprocess.run(
            [str(command)],
            input=json.dumps(self.event("Bash", {"command": "git status"})),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")

    def test_project_hook_registers_one_matcherless_pretooluse_entry(self) -> None:
        hooks_path = Path(__file__).resolve().parents[1] / ".codex" / "hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]

        self.assertEqual(len(hooks["PreToolUse"]), 1)
        entry = hooks["PreToolUse"][0]
        self.assertNotIn("matcher", entry)
        self.assertIn("harness-pre-tool-use", entry["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()

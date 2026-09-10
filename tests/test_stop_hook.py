import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.harness import runtime_binding, state_store
from src.harness.hooks import stop


class StopHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.database_path = root / "state.db"
        self.bindings_directory = root / "bindings"
        state_store.initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def event(self, *, stop_hook_active: bool = False) -> dict[str, object]:
        return {
            "hook_event_name": "Stop",
            "session_id": "session-current",
            "turn_id": "turn-current",
            "stop_hook_active": stop_hook_active,
            "transcript_path": "/tmp/transcript.jsonl",
            "last_assistant_message": "완료했습니다.",
        }

    def start_bound_run(self) -> tuple[dict[str, object], dict[str, object]]:
        work_item = state_store.create_work_item(
            title="Stop Hook 테스트",
            kind="verification",
            goal="마감 누락을 감지한다.",
            next_action="Hook을 검증한다.",
            actor="test",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="Hook을 검증한다.",
            actor="test",
            reason="테스트 준비",
            database_path=self.database_path,
        )
        run = state_store.start_run(
            work_item["id"],
            intent="Stop Hook을 검증한다.",
            recall_query="Stop Hook 테스트",
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

    def dispatch(self, event: dict[str, object]) -> dict[str, str] | None:
        return stop.dispatch_stop(
            event,
            database_path=self.database_path,
            bindings_directory=self.bindings_directory,
        )

    def test_idle_session_allows_stop_silently(self) -> None:
        self.assertIsNone(self.dispatch(self.event()))

    def test_running_run_without_candidates_requests_work_finish(self) -> None:
        self.start_bound_run()

        result = self.dispatch(self.event())

        self.assertEqual(result["decision"], "block")
        self.assertIn("$work-finish", result["reason"])
        self.assertNotIn("pending Memory Candidate", result["reason"])

    def test_pending_candidate_requests_memory_closeout(self) -> None:
        _work_item, run = self.start_bound_run()
        state_store.create_candidate(
            run["id"],
            proposed_type="general",
            title="종료 전 후보 처리",
            content="pending 후보는 Run보다 먼저 처리한다.",
            keywords=["pending", "finish"],
            database_path=self.database_path,
        )

        result = self.dispatch(self.event())

        self.assertEqual(result["decision"], "block")
        self.assertIn("pending Memory Candidate", result["reason"])

    def test_second_stop_warns_without_blocking_again(self) -> None:
        self.start_bound_run()

        result = self.dispatch(self.event(stop_hook_active=True))

        self.assertNotIn("decision", result)
        self.assertIn("한 차례 마감 재시도", result["systemMessage"])

    def test_stale_turn_binding_warns_without_mutating_state(self) -> None:
        work_item, run = self.start_bound_run()
        event = self.event()
        event["turn_id"] = "turn-next"

        result = self.dispatch(event)

        self.assertNotIn("decision", result)
        self.assertIn("일치하지 않습니다", result["systemMessage"])
        self.assertEqual(
            state_store.get_run(run["id"], database_path=self.database_path)["status"],
            "running",
        )
        self.assertEqual(
            state_store.get_work_item(
                work_item["id"], database_path=self.database_path
            )["status"],
            "in_progress",
        )

    def test_unowned_running_run_warns_without_claiming_finish_omission(self) -> None:
        self.start_bound_run()
        runtime_binding.delete_binding(
            "session-current", bindings_directory=self.bindings_directory
        )

        result = self.dispatch(self.event())

        self.assertNotIn("decision", result)
        self.assertIn("다른 세션 또는 소유 불명", result["systemMessage"])

    def test_database_failure_warns_without_blocking(self) -> None:
        result = stop.dispatch_stop(
            self.event(),
            database_path=Path(self.temporary_directory.name) / "missing" / "state.db",
            bindings_directory=self.bindings_directory,
        )

        self.assertNotIn("decision", result)
        self.assertIn("DB를 조회하지 못했습니다", result["systemMessage"])

    def test_cli_emits_block_decision_for_a_bound_running_run(self) -> None:
        self.start_bound_run()
        command = Path(__file__).resolve().parents[1] / "bin" / "harness-stop"
        environment = os.environ.copy()
        environment["HARNESS_STATE_DATABASE"] = str(self.database_path)
        environment["HARNESS_BINDINGS_DIRECTORY"] = str(self.bindings_directory)

        completed = subprocess.run(
            [str(command)],
            input=json.dumps(self.event()),
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)["decision"], "block")
        self.assertEqual(completed.stderr, "")

    def test_project_hook_registers_stop_command(self) -> None:
        hooks_path = Path(__file__).resolve().parents[1] / ".codex" / "hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]

        self.assertEqual(len(hooks["Stop"]), 1)
        entry = hooks["Stop"][0]
        self.assertNotIn("matcher", entry)
        self.assertIn("harness-stop", entry["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()

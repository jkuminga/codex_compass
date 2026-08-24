import json
import tempfile
import unittest
from pathlib import Path

from src.harness import runtime_binding, state_store
from src.harness.hooks import user_prompt_submit


class UserPromptSubmitHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.database_path = root / "state.db"
        self.bindings_directory = root / "bindings"
        state_store.initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def event(self, *, session_id: str = "session-current", turn_id: str = "turn-new") -> dict[str, str]:
        return {
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "turn_id": turn_id,
            "prompt": "UPS Hook을 구현해줘",
        }

    def create_ready_work_item(self, title: str = "UPS Hook 구현") -> dict[str, object]:
        work_item = state_store.create_work_item(
            title=title,
            kind="implementation",
            goal="각 요청 전에 프로젝트 상태를 준비한다.",
            next_action="UserPromptSubmit Hook을 구현한다.",
            acceptance_criteria=["Hook이 최신 상태 패킷을 제공한다."],
            actor="test",
            database_path=self.database_path,
        )
        return state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="UserPromptSubmit Hook을 구현한다.",
            actor="test",
            reason="테스트 준비",
            database_path=self.database_path,
        )

    def start(self, work_item_id: str) -> dict[str, object]:
        return state_store.start_run(
            work_item_id,
            intent="UPS Hook을 구현한다.",
            recall_query="UPS Hook WorkItem",
            actor="test",
            database_path=self.database_path,
        )

    def test_returns_compact_context_and_work_start_instruction(self) -> None:
        work_item = self.create_ready_work_item()

        output = user_prompt_submit.dispatch_user_prompt_submit(
            self.event(),
            database_path=self.database_path,
            bindings_directory=self.bindings_directory,
        )

        context = output["hookSpecificOutput"]["additionalContext"]
        packet_text = context.split("<work-selection-context>\n", 1)[1].split(
            "\n</work-selection-context>", 1
        )[0]
        packet = json.loads(packet_text)
        self.assertTrue(packet["db_ok"])
        self.assertEqual(
            packet["ready_candidates"][0]["work_item_id"], work_item["id"]
        )
        self.assertIn("$work-start", context)

    def test_recovers_only_same_session_binding_from_a_different_turn(self) -> None:
        work_item = self.create_ready_work_item()
        run = self.start(work_item["id"])
        runtime_binding.save_binding(
            session_id="session-current",
            turn_id="turn-old",
            work_item_id=work_item["id"],
            run_id=run["id"],
            bindings_directory=self.bindings_directory,
        )

        packet = user_prompt_submit.build_work_selection_packet(
            self.event(),
            database_path=self.database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertEqual(packet["recovered_runs"][0]["run_id"], run["id"])
        self.assertEqual(
            state_store.get_run(run["id"], database_path=self.database_path)["status"],
            "interrupted",
        )
        self.assertIsNone(
            runtime_binding.load_binding(
                "session-current", bindings_directory=self.bindings_directory
            )
        )

    def test_preserves_and_reports_another_sessions_active_work(self) -> None:
        work_item = self.create_ready_work_item()
        run = self.start(work_item["id"])
        runtime_binding.save_binding(
            session_id="session-other",
            turn_id="turn-other",
            work_item_id=work_item["id"],
            run_id=run["id"],
            bindings_directory=self.bindings_directory,
        )

        packet = user_prompt_submit.build_work_selection_packet(
            self.event(),
            database_path=self.database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertEqual(packet["active_work_items"][0]["ownership"], "other_session")
        self.assertEqual(packet["active_work_items"][0]["run_id"], run["id"])
        self.assertEqual(
            state_store.get_run(run["id"], database_path=self.database_path)["status"],
            "running",
        )

    def test_removes_current_sessions_binding_when_its_run_is_terminal(self) -> None:
        work_item = self.create_ready_work_item()
        run = self.start(work_item["id"])
        runtime_binding.save_binding(
            session_id="session-current",
            turn_id="turn-old",
            work_item_id=work_item["id"],
            run_id=run["id"],
            bindings_directory=self.bindings_directory,
        )
        state_store.recover_stale_run(
            work_item["id"],
            expected_run_id=run["id"],
            actor="test",
            database_path=self.database_path,
        )

        packet = user_prompt_submit.build_work_selection_packet(
            self.event(),
            database_path=self.database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertTrue(packet["db_ok"])
        self.assertIsNone(
            runtime_binding.load_binding(
                "session-current", bindings_directory=self.bindings_directory
            )
        )

    def test_bad_database_returns_a_fail_closed_packet(self) -> None:
        bad_database = Path(self.temporary_directory.name) / "empty.db"

        output = user_prompt_submit.dispatch_user_prompt_submit(
            self.event(),
            database_path=bad_database,
            bindings_directory=self.bindings_directory,
        )
        context = output["hookSpecificOutput"]["additionalContext"]

        self.assertIn('"db_ok":false', context)
        self.assertIn("상태 저장소 건강 검사에 실패", context)


if __name__ == "__main__":
    unittest.main()

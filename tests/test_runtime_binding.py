import tempfile
import unittest
from pathlib import Path

from src.harness import runtime_binding, state_store


class RuntimeBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.bindings_directory = (
            Path(self.temporary_directory.name) / "runtime" / "bindings"
        )
        self.database_path = Path(self.temporary_directory.name) / "state.db"
        state_store.initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_session_binding_can_be_saved_found_and_deleted(self) -> None:
        binding = runtime_binding.save_binding(
            session_id="session-123",
            turn_id="turn-456",
            work_item_id="WI-12",
            run_id="RUN-34",
            bindings_directory=self.bindings_directory,
        )

        self.assertEqual(binding["session_id"], "session-123")
        self.assertEqual(
            runtime_binding.load_binding(
                "session-123", bindings_directory=self.bindings_directory
            ),
            binding,
        )
        self.assertEqual(
            runtime_binding.find_run_owner(
                "RUN-34", bindings_directory=self.bindings_directory
            ),
            binding,
        )
        self.assertTrue(
            runtime_binding.delete_binding(
                "session-123",
                expected_run_id="RUN-34",
                bindings_directory=self.bindings_directory,
            )
        )
        self.assertIsNone(
            runtime_binding.load_binding(
                "session-123", bindings_directory=self.bindings_directory
            )
        )

    def test_binding_guards_against_replacement_and_path_traversal(self) -> None:
        runtime_binding.save_binding(
            session_id="session-123",
            turn_id="turn-1",
            work_item_id="WI-12",
            run_id="RUN-34",
            bindings_directory=self.bindings_directory,
        )

        with self.assertRaises(runtime_binding.BindingConflictError):
            runtime_binding.save_binding(
                session_id="session-123",
                turn_id="turn-2",
                work_item_id="WI-99",
                run_id="RUN-99",
                bindings_directory=self.bindings_directory,
            )
        with self.assertRaises(runtime_binding.BindingConflictError):
            runtime_binding.delete_binding(
                "session-123",
                expected_run_id="RUN-99",
                bindings_directory=self.bindings_directory,
            )
        with self.assertRaises(runtime_binding.RuntimeBindingError):
            runtime_binding.load_binding(
                "../outside", bindings_directory=self.bindings_directory
            )

        binding = runtime_binding.load_binding(
            "session-123", bindings_directory=self.bindings_directory
        )
        self.assertIsNotNone(binding)
        self.assertEqual(binding["run_id"], "RUN-34")

    def test_active_binding_matches_current_turn_and_running_database_state(self) -> None:
        work_item = state_store.create_work_item(
            title="PreToolUse Binding 검증",
            kind="maintenance",
            goal="프로젝트 변경 전에 현재 Run을 확인한다.",
            next_action="Binding 검증 함수를 구현한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="Binding 검증 함수를 구현한다.",
            actor="planner",
            reason="구현 준비 완료",
            database_path=self.database_path,
        )
        run = state_store.start_run(
            work_item["id"],
            intent="현재 Turn의 Binding을 검증한다.",
            recall_query="PreToolUse Binding 검증",
            actor="codex",
            database_path=self.database_path,
        )
        runtime_binding.save_binding(
            session_id="session-123",
            turn_id="turn-current",
            work_item_id=work_item["id"],
            run_id=run["id"],
            bindings_directory=self.bindings_directory,
        )

        validated = runtime_binding.validate_active_binding(
            session_id="session-123",
            current_turn_id="turn-current",
            database_path=self.database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertEqual(validated["binding"]["run_id"], run["id"])
        self.assertEqual(validated["active_run"]["id"], run["id"])
        self.assertEqual(validated["active_run"]["status"], "running")
        self.assertEqual(validated["active_run"]["work_item_status"], "in_progress")

    def test_active_binding_rejects_a_different_turn(self) -> None:
        runtime_binding.save_binding(
            session_id="session-123",
            turn_id="turn-previous",
            work_item_id="WI-12",
            run_id="RUN-34",
            bindings_directory=self.bindings_directory,
        )

        with self.assertRaisesRegex(
            runtime_binding.BindingConflictError, "different Turn"
        ):
            runtime_binding.validate_active_binding(
                session_id="session-123",
                current_turn_id="turn-current",
                database_path=self.database_path,
                bindings_directory=self.bindings_directory,
            )

    def test_active_binding_rejects_a_missing_binding(self) -> None:
        with self.assertRaisesRegex(
            runtime_binding.BindingConflictError, "no Runtime Binding"
        ):
            runtime_binding.validate_active_binding(
                session_id="session-missing",
                current_turn_id="turn-current",
                database_path=self.database_path,
                bindings_directory=self.bindings_directory,
            )

    def test_active_binding_rejects_a_finished_database_run(self) -> None:
        work_item = state_store.create_work_item(
            title="종료 Run 거부",
            kind="maintenance",
            goal="종료된 Run으로 프로젝트를 변경하지 않는다.",
            next_action="종료 상태를 검증한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="종료 상태를 검증한다.",
            actor="planner",
            reason="검증 준비 완료",
            database_path=self.database_path,
        )
        run = state_store.start_run(
            work_item["id"],
            intent="종료된 Run을 거부한다.",
            recall_query="PreToolUse 종료 Run",
            actor="codex",
            database_path=self.database_path,
        )
        runtime_binding.save_binding(
            session_id="session-123",
            turn_id="turn-current",
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

        with self.assertRaisesRegex(
            state_store.ConflictError, "does not point to an active Run"
        ):
            runtime_binding.validate_active_binding(
                session_id="session-123",
                current_turn_id="turn-current",
                database_path=self.database_path,
                bindings_directory=self.bindings_directory,
            )


if __name__ == "__main__":
    unittest.main()

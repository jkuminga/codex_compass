import tempfile
import unittest
from pathlib import Path

from src.harness import runtime_binding, state_store
from src.harness.hooks import ups_recovery


class UpsRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.database_path = root / "state.db"
        self.bindings_directory = root / "bindings"
        state_store.initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_running_run(self, *, title: str = "UPS 복구 대상") -> tuple[dict[str, object], dict[str, object]]:
        work_item = state_store.create_work_item(
            title=title,
            kind="maintenance",
            goal="비정상 종료된 UPS 작업을 안전하게 복구한다.",
            next_action="복구 흐름을 확인한다.",
            actor="test",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="복구 흐름을 확인한다.",
            actor="test",
            reason="복구 테스트 준비",
            database_path=self.database_path,
        )
        run = state_store.start_run(
            work_item["id"],
            intent="UPS 복구를 검증한다.",
            recall_query="UPS 복구",
            actor="test",
            database_path=self.database_path,
        )
        return work_item, run

    def test_previous_turn_binding_is_recovered_and_deleted(self) -> None:
        work_item, run = self.create_running_run()
        runtime_binding.save_binding(
            session_id="session-current",
            turn_id="turn-previous",
            work_item_id=work_item["id"],
            run_id=run["id"],
            bindings_directory=self.bindings_directory,
        )

        report = ups_recovery.reconcile_current_session(
            session_id="session-current",
            current_turn_id="turn-current",
            database_path=self.database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertEqual(report.status, "recovered")
        self.assertEqual(report.recovered_run_id, run["id"])
        self.assertTrue(report.binding_deleted)
        self.assertEqual(
            state_store.get_run(run["id"], database_path=self.database_path)["status"],
            "interrupted",
        )
        self.assertEqual(
            state_store.get_work_item(work_item["id"], database_path=self.database_path)["status"],
            "ready",
        )
        self.assertIsNone(
            runtime_binding.load_binding(
                "session-current", bindings_directory=self.bindings_directory
            )
        )
        self.assertEqual(report.active_work_items, [])

    def test_current_turn_binding_is_preserved_and_reported_as_active(self) -> None:
        work_item, run = self.create_running_run()
        runtime_binding.save_binding(
            session_id="session-current",
            turn_id="turn-current",
            work_item_id=work_item["id"],
            run_id=run["id"],
            bindings_directory=self.bindings_directory,
        )

        report = ups_recovery.reconcile_current_session(
            session_id="session-current",
            current_turn_id="turn-current",
            database_path=self.database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertEqual(report.status, "already_active")
        self.assertFalse(report.binding_deleted)
        self.assertEqual(report.active_work_items[0]["ownership"], "current_session")
        self.assertEqual(
            state_store.get_run(run["id"], database_path=self.database_path)["status"],
            "running",
        )

    def test_other_session_run_is_reported_without_automatic_takeover(self) -> None:
        work_item, run = self.create_running_run(title="다른 세션 작업")
        runtime_binding.save_binding(
            session_id="session-other",
            turn_id="turn-other",
            work_item_id=work_item["id"],
            run_id=run["id"],
            bindings_directory=self.bindings_directory,
        )

        report = ups_recovery.reconcile_current_session(
            session_id="session-current",
            current_turn_id="turn-current",
            database_path=self.database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertEqual(report.status, "warning")
        self.assertIn("other_session_running", report.warnings)
        self.assertEqual(report.active_work_items[0]["ownership"], "other_session")
        self.assertEqual(report.active_work_items[0]["owner_session_id"], "session-other")
        self.assertEqual(
            state_store.get_run(run["id"], database_path=self.database_path)["status"],
            "running",
        )
        self.assertIsNotNone(
            runtime_binding.load_binding(
                "session-other", bindings_directory=self.bindings_directory
            )
        )

    def test_run_without_binding_is_reported_as_unknown_owner(self) -> None:
        work_item, run = self.create_running_run(title="소유자 없는 작업")

        report = ups_recovery.reconcile_current_session(
            session_id="session-current",
            current_turn_id="turn-current",
            database_path=self.database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertEqual(report.status, "warning")
        self.assertIn("run_owner_unknown", report.warnings)
        self.assertEqual(report.active_work_items[0]["ownership"], "unknown")
        self.assertEqual(report.active_work_items[0]["work_item_id"], work_item["id"])
        self.assertEqual(report.active_work_items[0]["run_id"], run["id"])

    def test_finished_run_binding_is_deleted_without_state_change(self) -> None:
        work_item, run = self.create_running_run(title="완료 포인터")
        state_store.finish_run(
            run["id"],
            run_status="failed",
            work_item_status="ready",
            summary="테스트 실패",
            termination_reason="테스트 이유",
            next_action="다시 확인한다.",
            actor="test",
            reason="완료 포인터 준비",
            database_path=self.database_path,
        )
        runtime_binding.save_binding(
            session_id="session-current",
            turn_id="turn-previous",
            work_item_id=work_item["id"],
            run_id=run["id"],
            bindings_directory=self.bindings_directory,
        )

        report = ups_recovery.reconcile_current_session(
            session_id="session-current",
            current_turn_id="turn-current",
            database_path=self.database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertTrue(report.binding_deleted)
        self.assertEqual(report.status, "none")
        self.assertEqual(
            state_store.get_run(run["id"], database_path=self.database_path)["status"],
            "failed",
        )

    def test_missing_run_binding_is_deleted_without_creating_state(self) -> None:
        runtime_binding.save_binding(
            session_id="session-current",
            turn_id="turn-previous",
            work_item_id="WI-missing",
            run_id="RUN-missing",
            bindings_directory=self.bindings_directory,
        )

        report = ups_recovery.reconcile_current_session(
            session_id="session-current",
            current_turn_id="turn-current",
            database_path=self.database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertTrue(report.binding_deleted)
        self.assertIsNone(
            runtime_binding.load_binding(
                "session-current", bindings_directory=self.bindings_directory
            )
        )
        self.assertEqual(report.active_work_items, [])

    def test_unhealthy_database_stops_picker_reconciliation(self) -> None:
        report = ups_recovery.reconcile_current_session(
            session_id="session-current",
            current_turn_id="turn-current",
            database_path=self.temporary_directory.name,
            bindings_directory=self.bindings_directory,
        )

        self.assertFalse(report.database_ok)
        self.assertFalse(report.can_continue)
        self.assertEqual(report.status, "error")
        self.assertIn("database_health_check_failed", report.warnings[0])


if __name__ == "__main__":
    unittest.main()

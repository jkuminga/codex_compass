import tempfile
import unittest
from pathlib import Path

from src.harness import state_store


class StateStoreLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "state.db"
        state_store.initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_work_item_can_run_produce_evidence_and_finish(self) -> None:
        feature = state_store.create_feature(
            title="상태 저장소",
            goal="프로젝트 진행 상태를 관리한다.",
            actor="test",
            database_path=self.database_path,
        )
        work_item = state_store.create_work_item(
            feature_id=feature["id"],
            title="상태 저장 모듈 구현",
            kind="implementation",
            goal="안전한 공개 함수를 제공한다.",
            next_action="상태 저장 모듈을 구현한다.",
            acceptance_criteria=["전체 생명주기 테스트가 통과한다."],
            actor="test",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="상태 저장 모듈을 구현한다.",
            actor="test",
            reason="구현 준비 완료",
            database_path=self.database_path,
        )

        run = state_store.start_run(
            work_item["id"],
            trace_ref="trace://run-1",
            actor="codex",
            database_path=self.database_path,
        )
        artifact = state_store.create_artifact(
            run["id"],
            kind="test_run",
            uri="trace://tests/1",
            verification_status="passed",
            summary="전체 생명주기 테스트 통과",
            actor="codex",
            database_path=self.database_path,
        )
        criterion_id = state_store.get_work_item_context(
            work_item["id"], database_path=self.database_path
        )["acceptance_criteria"][0]["id"]
        state_store.link_evidence(
            criterion_id,
            artifact["id"],
            actor="codex",
            database_path=self.database_path,
        )
        state_store.pass_criterion(
            criterion_id,
            actor="codex",
            database_path=self.database_path,
        )

        result = state_store.complete_work_item(
            work_item["id"],
            summary="구현과 검증을 완료했다.",
            actor="codex",
            reason="모든 완료 조건 충족",
            database_path=self.database_path,
        )

        self.assertEqual(result["run"]["status"], "succeeded")
        self.assertEqual(result["work_item"]["status"], "done")
        self.assertGreaterEqual(
            len(state_store.get_recent_activity(database_path=self.database_path)), 8
        )

    def test_planning_entities_can_be_revised_without_raw_sql(self) -> None:
        feature = state_store.create_feature(
            title="초기 기능",
            goal="초기 목표",
            actor="planner",
            database_path=self.database_path,
        )
        feature = state_store.update_feature(
            feature["id"],
            title="상태 관리 기능",
            priority="high",
            actor="planner",
            database_path=self.database_path,
        )
        feature = state_store.change_feature_status(
            feature["id"],
            "active",
            actor="planner",
            reason="구현 시작",
            database_path=self.database_path,
        )
        self.assertEqual(feature["status"], "active")

        work_item = state_store.create_work_item(
            feature_id=feature["id"],
            title="초기 작업",
            kind="implementation",
            goal="초기 작업 목표",
            actor="planner",
            database_path=self.database_path,
        )
        work_item = state_store.revise_work_item(
            work_item["id"],
            title="상태 모듈 구현",
            next_action="공개 함수를 구현한다.",
            actor="planner",
            database_path=self.database_path,
        )
        criterion = state_store.add_criterion(
            work_item["id"],
            "공개 함수 테스트가 통과한다.",
            actor="planner",
            database_path=self.database_path,
        )
        criterion = state_store.revise_criterion(
            criterion["id"],
            "공개 생명주기 테스트가 통과한다.",
            actor="planner",
            database_path=self.database_path,
        )
        criterion = state_store.fail_criterion(
            criterion["id"],
            actor="codex",
            reason="첫 테스트 실패",
            database_path=self.database_path,
        )
        criterion = state_store.waive_criterion(
            criterion["id"],
            actor="user",
            reason="프로토타입에서는 제외",
            database_path=self.database_path,
        )

        self.assertEqual(work_item["title"], "상태 모듈 구현")
        self.assertEqual(criterion["status"], "waived")
        self.assertEqual(
            state_store.get_feature(feature["id"], database_path=self.database_path)["title"],
            "상태 관리 기능",
        )
        self.assertEqual(
            len(state_store.list_features(database_path=self.database_path)), 1
        )

        disposable = state_store.create_work_item(
            title="삭제할 후보",
            kind="research",
            goal="아직 시작하지 않은 후보",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.delete_backlog_work_item(
            disposable["id"],
            actor="planner",
            reason="더 이상 필요 없음",
            database_path=self.database_path,
        )
        with self.assertRaises(state_store.NotFoundError):
            state_store.get_work_item(
                disposable["id"], database_path=self.database_path
            )

    def test_run_artifacts_and_completion_status_are_queryable(self) -> None:
        work_item = state_store.create_work_item(
            title="검증 흐름",
            kind="verification",
            goal="검증 결과를 명확히 기록한다.",
            next_action="검증을 실행한다.",
            acceptance_criteria=["테스트 결과가 통과한다."],
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="검증을 실행한다.",
            actor="planner",
            reason="검증 준비 완료",
            database_path=self.database_path,
        )
        run = state_store.start_run(
            work_item["id"], actor="codex", database_path=self.database_path
        )
        artifact = state_store.create_artifact(
            run["id"],
            kind="test_run",
            uri="trace://tests/pending",
            verification_status="pending",
            summary="테스트 실행 중",
            actor="codex",
            database_path=self.database_path,
        )
        artifact = state_store.resolve_artifact(
            artifact["id"],
            "passed",
            summary="테스트 12개 통과",
            actor="codex",
            database_path=self.database_path,
        )
        criterion_id = state_store.get_work_item_context(
            work_item["id"], database_path=self.database_path
        )["acceptance_criteria"][0]["id"]
        state_store.link_evidence(
            criterion_id,
            artifact["id"],
            actor="codex",
            database_path=self.database_path,
        )
        state_store.pass_criterion(
            criterion_id, actor="codex", database_path=self.database_path
        )

        self.assertEqual(
            state_store.get_running_run(
                work_item["id"], database_path=self.database_path
            )["id"],
            run["id"],
        )
        self.assertEqual(
            state_store.get_run(run["id"], database_path=self.database_path)["status"],
            "running",
        )
        self.assertEqual(
            state_store.get_run_artifacts(
                run["id"], database_path=self.database_path
            )[0]["verification_status"],
            "passed",
        )
        self.assertEqual(
            state_store.get_criterion_evidence(
                criterion_id, database_path=self.database_path
            )[0]["artifact_id"],
            artifact["id"],
        )
        verification = state_store.get_work_item_verification(
            work_item["id"], database_path=self.database_path
        )
        self.assertTrue(verification["can_complete"])
        self.assertEqual(verification["issues"], [])

    def test_memory_candidates_have_a_small_explicit_lifecycle(self) -> None:
        work_item = state_store.create_work_item(
            title="기억 후보 수집",
            kind="research",
            goal="재사용할 지식을 후보로 남긴다.",
            next_action="후보를 수집한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="후보를 수집한다.",
            actor="planner",
            reason="수집 준비 완료",
            database_path=self.database_path,
        )
        run = state_store.start_run(
            work_item["id"], actor="codex", database_path=self.database_path
        )
        candidate = state_store.create_candidate(
            run["id"],
            proposed_type="command",
            title="전체 상태 저장소 테스트",
            content="python3 -m unittest discover -s tests -v 로 전체 테스트를 실행한다.",
            keywords=["sqlite", "test", "unittest", "test"],
            database_path=self.database_path,
        )
        rejected = state_store.create_candidate(
            run["id"],
            proposed_type="general",
            title="일회성 정보",
            content="이번 실행에만 필요한 정보다.",
            keywords=["temporary"],
            database_path=self.database_path,
        )
        state_store.promote_candidate(
            candidate["id"],
            memory_ref="memory://command/run-tests",
            database_path=self.database_path,
        )
        state_store.reject_candidate(
            rejected["id"], database_path=self.database_path
        )

        self.assertEqual(candidate["keywords"], ["sqlite", "test", "unittest"])
        self.assertEqual(
            state_store.list_pending_candidates(
                run_id=run["id"], database_path=self.database_path
            ),
            [],
        )

    def test_preflight_postflight_and_project_progress_are_compact(self) -> None:
        work_item = state_store.create_work_item(
            title="다음 작업",
            kind="implementation",
            goal="훅 문맥을 제공한다.",
            next_action="훅 문맥을 조회한다.",
            acceptance_criteria=["완료 조건을 확인한다."],
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="훅 문맥을 조회한다.",
            actor="planner",
            reason="실행 가능",
            database_path=self.database_path,
        )

        preflight = state_store.get_preflight_context(
            work_item["id"], database_path=self.database_path
        )
        postflight = state_store.get_postflight_status(
            work_item["id"], database_path=self.database_path
        )
        progress = state_store.get_project_progress(database_path=self.database_path)

        self.assertEqual(preflight["work_item"]["status"], "ready")
        self.assertFalse(postflight["verification"]["can_complete"])
        self.assertEqual(progress["total_work_items"], 1)
        self.assertEqual(
            state_store.list_next_work_items(database_path=self.database_path)[0]["id"],
            work_item["id"],
        )

    def test_failed_finish_rolls_back_run_and_work_item_together(self) -> None:
        work_item = state_store.create_work_item(
            title="원자성 확인",
            kind="implementation",
            goal="부분 저장을 막는다.",
            next_action="구현을 시작한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="구현을 시작한다.",
            actor="planner",
            reason="준비 완료",
            database_path=self.database_path,
        )
        run = state_store.start_run(
            work_item["id"], actor="codex", database_path=self.database_path
        )

        with self.assertRaises(state_store.ConflictError):
            state_store.finish_run(
                run["id"],
                run_status="failed",
                work_item_status="ready",
                summary="검증 실패",
                termination_reason="테스트 실패",
                actor="codex",
                reason="재작업 필요",
                database_path=self.database_path,
            )

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

    def test_successful_run_can_progress_an_unfinished_work_item(self) -> None:
        work_item = state_store.create_work_item(
            title="여러 세션 설계",
            kind="decision",
            goal="여러 Run에서 설계를 확정한다.",
            next_action="첫 번째 설계 쟁점을 검토한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="첫 번째 설계 쟁점을 검토한다.",
            actor="planner",
            reason="설계 시작 준비",
            database_path=self.database_path,
        )
        run = state_store.start_run(
            work_item["id"], actor="codex", database_path=self.database_path
        )

        result = state_store.finish_run(
            run["id"],
            run_status="succeeded",
            work_item_status="ready",
            summary="첫 번째 설계 쟁점을 확정했다.",
            next_action="두 번째 설계 쟁점을 검토한다.",
            actor="codex",
            reason="이번 세션 목표 달성",
            database_path=self.database_path,
        )

        self.assertEqual(result["run"]["status"], "succeeded")
        self.assertEqual(result["work_item"]["status"], "ready")
        self.assertEqual(
            result["work_item"]["next_action"], "두 번째 설계 쟁점을 검토한다."
        )
        next_run = state_store.start_run(
            work_item["id"], actor="codex", database_path=self.database_path
        )
        self.assertEqual(next_run["status"], "running")

    def test_progressed_finish_requires_a_next_action_and_rolls_back(self) -> None:
        work_item = state_store.create_work_item(
            title="다음 행동 검증",
            kind="research",
            goal="진전 종료의 필수 입력을 검증한다.",
            next_action="조사를 시작한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="조사를 시작한다.",
            actor="planner",
            reason="조사 준비 완료",
            database_path=self.database_path,
        )
        run = state_store.start_run(
            work_item["id"], actor="codex", database_path=self.database_path
        )

        with self.assertRaisesRegex(state_store.ConflictError, "next_action"):
            state_store.finish_run(
                run["id"],
                run_status="succeeded",
                work_item_status="ready",
                summary="일부 조사를 마쳤다.",
                actor="codex",
                reason="조사 진전",
                database_path=self.database_path,
            )

        self.assertEqual(
            state_store.get_run(run["id"], database_path=self.database_path)["status"],
            "running",
        )


if __name__ == "__main__":
    unittest.main()

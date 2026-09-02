import sqlite3
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

    def start_run(self, work_item_id: str, **arguments: object) -> dict[str, object]:
        """Start a test Run with the required intent and recall search terms."""

        return state_store.start_run(
            work_item_id,
            intent="테스트 작업을 수행한다.",
            recall_query="테스트 상태 저장소",
            **arguments,
        )

    def test_initialize_database_adds_recall_fields_to_an_existing_runs_table(self) -> None:
        legacy_path = Path(self.temporary_directory.name) / "legacy.db"
        database = sqlite3.connect(legacy_path)
        database.execute(
            "CREATE TABLE runs (id TEXT PRIMARY KEY, work_item_id TEXT NOT NULL, "
            "status TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, "
            "summary TEXT, termination_reason TEXT, trace_ref TEXT)"
        )
        database.commit()
        database.close()

        state_store.initialize_database(legacy_path)

        database = state_store.open_database(legacy_path)
        columns = {
            row["name"]: row for row in database.execute("PRAGMA table_info(runs)")
        }
        database.close()
        self.assertEqual(columns["intent"]["notnull"], 1)
        self.assertEqual(columns["recall_query"]["notnull"], 1)

    def test_database_health_reports_foreign_keys_and_complete_schema(self) -> None:
        health = state_store.check_database_health(self.database_path)

        self.assertTrue(health["ok"])
        self.assertTrue(health["foreign_keys_enabled"])
        self.assertEqual(health["foreign_key_violations"], [])
        self.assertEqual(health["missing_tables"], [])
        self.assertEqual(health["missing_views"], [])

    def test_database_health_rejects_missing_required_tables(self) -> None:
        empty_database_path = Path(self.temporary_directory.name) / "empty.db"

        health = state_store.check_database_health(empty_database_path)

        self.assertFalse(health["ok"])
        self.assertTrue(health["foreign_keys_enabled"])
        self.assertIn("runs", health["missing_tables"])
        self.assertIn("work_items", health["missing_tables"])

    def test_database_health_rejects_missing_required_views(self) -> None:
        database = state_store.open_database(self.database_path)
        database.execute("DROP VIEW project_progress")
        database.commit()
        database.close()

        health = state_store.check_database_health(self.database_path)

        self.assertFalse(health["ok"])
        self.assertEqual(health["missing_tables"], [])
        self.assertEqual(health["missing_views"], ["project_progress"])

    def test_get_memory_candidate_is_read_only_and_decodes_keywords(self) -> None:
        work_item = state_store.create_work_item(
            title="후보 단건 조회",
            kind="verification",
            goal="Memory Candidate를 상태 변경 없이 읽는다.",
            next_action="후보를 생성하고 조회한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"], "ready",
            next_action="후보를 생성하고 조회한다.", actor="planner",
            reason="조회 테스트 준비", database_path=self.database_path,
        )
        run = self.start_run(
            work_item["id"], actor="codex", database_path=self.database_path
        )
        candidate = state_store.create_candidate(
            run["id"], proposed_type="technology", title="SQLite 조회",
            content="후보를 단건 조회한다.", keywords=["SQLite", "조회", "sqlite"],
            database_path=self.database_path,
        )

        loaded = state_store.get_memory_candidate(
            candidate["id"], database_path=self.database_path
        )

        self.assertEqual(loaded["keywords"], ["sqlite", "조회"])
        self.assertEqual(loaded["status"], "pending")

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

        run = self.start_run(
            work_item["id"],
            trace_ref="trace://run-1",
            actor="codex",
            database_path=self.database_path,
        )
        self.assertEqual(run["intent"], "테스트 작업을 수행한다.")
        self.assertEqual(run["recall_query"], "테스트 상태 저장소")
        artifact = state_store.create_artifact(
            run["id"],
            kind="test_run",
            uri="command:unittest:20260827T081800Z",
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

    def test_create_ready_work_item_stores_complete_plan_atomically(self) -> None:
        work_item = state_store.create_ready_work_item(
            title="대화에서 완성 WI 생성",
            kind="implementation",
            goal="현재 대화의 작업을 나중에 바로 실행할 수 있게 등록한다.",
            next_action="등록된 작업의 구현 범위를 검토한다.",
            acceptance_criteria=["WorkItem이 ready 상태로 조회된다.", "AC가 하나 이상 저장된다."],
            priority="high",
            description="대화 맥락에서 만든 완성 WorkItem",
            actor="codex",
            database_path=self.database_path,
        )

        context = state_store.get_work_item_context(
            work_item["id"], database_path=self.database_path
        )
        self.assertEqual(work_item["status"], "ready")
        self.assertEqual(work_item["is_draft"], 0)
        self.assertEqual(work_item["priority"], "high")
        self.assertEqual(work_item["description"], "대화 맥락에서 만든 완성 WorkItem")
        self.assertEqual(len(context["acceptance_criteria"]), 2)
        self.assertIsNone(context["running_run"])

    def test_create_ready_work_item_rejects_an_empty_plan_before_inserting(self) -> None:
        with self.assertRaisesRegex(state_store.ConflictError, "Acceptance Criteria"):
            state_store.create_ready_work_item(
                title="불완전한 WI",
                kind="research",
                goal="저장되지 않아야 한다.",
                next_action="다음 행동",
                acceptance_criteria=[],
                actor="codex",
                database_path=self.database_path,
            )

        self.assertEqual(state_store.list_work_items(database_path=self.database_path), [])

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

    def test_draft_work_item_is_refined_atomically_before_it_can_run(self) -> None:
        draft = state_store.create_draft_work_item(
            title="웹 콘솔 초안 생성",
            kind="implementation",
            goal="사용자가 간단한 메모로 작업을 남긴다.",
            description="설명은 선택 입력이다.",
            actor="web_console",
            database_path=self.database_path,
        )

        self.assertEqual(draft["status"], "backlog")
        self.assertEqual(draft["is_draft"], 1)
        self.assertEqual(draft["description"], "설명은 선택 입력이다.")
        self.assertEqual(
            state_store.list_selectable_work_items(database_path=self.database_path)[0]["id"],
            draft["id"],
        )
        with self.assertRaisesRegex(state_store.ConflictError, "refined"):
            state_store.change_work_item_status(
                draft["id"], "ready", next_action="바로 실행", actor="test",
                reason="Draft를 우회하려는 시도", database_path=self.database_path,
            )
        with self.assertRaisesRegex(state_store.ConflictError, "refined"):
            state_store.add_criterion(
                draft["id"], "초안에 AC를 추가한다.", actor="test",
                database_path=self.database_path,
            )

        refined = state_store.refine_draft_work_item(
            draft["id"],
            priority="high",
            next_action="Draft 생성 API를 구현한다.",
            acceptance_criteria=["제목·종류·목표로 Draft를 저장할 수 있다."],
            actor="codex",
            database_path=self.database_path,
        )

        self.assertEqual(refined["status"], "ready")
        self.assertEqual(refined["is_draft"], 0)
        self.assertEqual(refined["priority"], "high")
        context = state_store.get_work_item_context(
            draft["id"], database_path=self.database_path
        )
        self.assertEqual(len(context["acceptance_criteria"]), 1)
        run = self.start_run(draft["id"], actor="codex", database_path=self.database_path)
        self.assertEqual(run["status"], "running")

    def test_work_items_are_searched_by_weighted_open_fields(self) -> None:
        title_match = state_store.create_work_item(
            title="Recall 정책 결정",
            kind="decision",
            goal="장기 기억을 불러오는 흐름을 정한다.",
            actor="planner",
            database_path=self.database_path,
        )
        goal_match = state_store.create_work_item(
            title="장기 기억 흐름",
            kind="decision",
            goal="Recall 실행 위치를 결정한다.",
            actor="planner",
            database_path=self.database_path,
        )
        action_match = state_store.create_work_item(
            title="기억 조회 준비",
            kind="research",
            goal="관련 문맥을 준비한다.",
            next_action="Recall 결과를 확인한다.",
            actor="planner",
            database_path=self.database_path,
        )
        cancelled_match = state_store.create_work_item(
            title="Recall 폐기 작업",
            kind="maintenance",
            goal="사용하지 않는 작업이다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            cancelled_match["id"],
            "cancelled",
            actor="planner",
            reason="검색 대상에서 제외",
            database_path=self.database_path,
        )

        results = state_store.search_work_items(
            ["recall", "존재하지않는검색어"],
            database_path=self.database_path,
        )

        self.assertEqual(
            [item["id"] for item in results],
            [title_match["id"], goal_match["id"], action_match["id"]],
        )
        self.assertEqual([item["match_score"] for item in results], [3, 2, 1])

    def test_work_item_search_rejects_unbounded_or_unsupported_inputs(self) -> None:
        invalid_calls = (
            ({"terms": ["recall"]}, "between 2 and 5"),
            ({"terms": ["a", "b", "c", "d", "e", "f"]}, "between 2 and 5"),
            ({"terms": ["recall", "memory"], "statuses": ["done"]}, "statuses"),
            ({"terms": ["recall", "memory"], "limit": 6}, "limit"),
        )

        for arguments, message in invalid_calls:
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(state_store.ConflictError, message):
                    state_store.search_work_items(
                        **arguments,
                        database_path=self.database_path,
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
        run = self.start_run(
            work_item["id"], actor="codex", database_path=self.database_path
        )
        artifact = state_store.create_artifact(
            run["id"],
            kind="test_run",
            uri="command:unittest:20260827T081900Z",
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

    def test_execution_artifact_uris_distinguish_retries_and_reject_duplicates(
        self,
    ) -> None:
        work_item = state_store.create_work_item(
            title="반복 검증 결과",
            kind="verification",
            goal="실패와 재실행 성공을 별도 Artifact로 보존한다.",
            next_action="같은 테스트를 수정 전후로 실행한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="같은 테스트를 수정 전후로 실행한다.",
            actor="planner",
            reason="검증 준비 완료",
            database_path=self.database_path,
        )
        run = self.start_run(
            work_item["id"], actor="codex", database_path=self.database_path
        )

        failed = state_store.create_artifact(
            run["id"],
            kind="test_run",
            uri="command:pytest:20260827T082100Z",
            verification_status="failed",
            summary="수정 전 테스트 실패",
            actor="codex",
            database_path=self.database_path,
        )
        passed = state_store.create_artifact(
            run["id"],
            kind="test_run",
            uri="command:pytest:20260827T082130Z",
            verification_status="passed",
            summary="수정 후 테스트 통과",
            actor="codex",
            database_path=self.database_path,
        )

        self.assertNotEqual(failed["id"], passed["id"])
        attempts = {
            artifact["uri"]: artifact["verification_status"]
            for artifact in state_store.get_run_artifacts(
                run["id"], database_path=self.database_path
            )
        }
        self.assertEqual(
            attempts,
            {
                "command:pytest:20260827T082100Z": "failed",
                "command:pytest:20260827T082130Z": "passed",
            },
        )
        with self.assertRaises(state_store.ConflictError):
            state_store.create_artifact(
                run["id"],
                kind="test_run",
                uri="command:pytest:20260827T082130Z",
                verification_status="passed",
                summary="같은 실행 결과 중복",
                actor="codex",
                database_path=self.database_path,
            )

        invalid_uris = (
            "trace://tests/latest",
            "command:pytest:20261327T082130Z",
            "command:Pytest:20260827T082130Z",
        )
        for kind in ("test_run", "lint_run", "build_run"):
            for uri in invalid_uris:
                with self.subTest(kind=kind, uri=uri):
                    with self.assertRaises(state_store.ConflictError):
                        state_store.create_artifact(
                            run["id"],
                            kind=kind,
                            uri=uri,
                            verification_status="failed",
                            summary="잘못된 실행 URI",
                            actor="codex",
                            database_path=self.database_path,
                        )

        suffixed = state_store.create_artifact(
            run["id"],
            kind="lint_run",
            uri="command:ruff-check:20260827T082200Z-a1b2c3",
            verification_status="passed",
            summary="같은 초 실행을 접미사로 구분",
            actor="codex",
            database_path=self.database_path,
        )
        self.assertEqual(
            suffixed["uri"], "command:ruff-check:20260827T082200Z-a1b2c3"
        )

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
        run = self.start_run(
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

    def test_memory_candidate_finalize_plan_is_durable_and_immutable(self) -> None:
        work_item = state_store.create_work_item(
            title="기억 저장 계획", kind="implementation", goal="재시도 계획을 보존한다.",
            next_action="후보를 만든다.", actor="planner", database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"], "ready", next_action="후보를 만든다.", actor="planner",
            reason="준비", database_path=self.database_path,
        )
        run = self.start_run(work_item["id"], actor="codex", database_path=self.database_path)
        candidate = state_store.create_candidate(
            run["id"], proposed_type="solution", title="계획", content="저장한다.",
            keywords=["plan"], database_path=self.database_path,
        )
        plan = {"decision": "reject", "relationships": [], "memory": None, "target_memory_id": None}
        first = state_store.reserve_candidate_finalize_plan(
            candidate["id"], storage_plan=plan, plan_fingerprint="a" * 64,
            database_path=self.database_path,
        )
        retry = state_store.reserve_candidate_finalize_plan(
            candidate["id"], storage_plan=plan, plan_fingerprint="a" * 64,
            database_path=self.database_path,
        )
        self.assertTrue(first["created"])
        self.assertFalse(retry["created"])
        self.assertEqual(retry["candidate"]["storage_plan"], plan)
        with self.assertRaises(state_store.ConflictError):
            state_store.reserve_candidate_finalize_plan(
                candidate["id"], storage_plan=plan, plan_fingerprint="b" * 64,
                database_path=self.database_path,
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
        run = self.start_run(
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
        run = self.start_run(
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
        next_run = self.start_run(
            work_item["id"], actor="codex", database_path=self.database_path
        )
        self.assertEqual(next_run["status"], "running")

    def test_user_confirmed_abandoned_work_can_be_recovered(self) -> None:
        work_item = state_store.create_work_item(
            title="중단 세션 복구",
            kind="maintenance",
            goal="다른 세션이 남긴 실행 잠금을 안전하게 해제한다.",
            next_action="복구 도구를 구현한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="복구 도구를 구현한다.",
            actor="planner",
            reason="구현 준비 완료",
            database_path=self.database_path,
        )
        run = self.start_run(
            work_item["id"], actor="codex", database_path=self.database_path
        )

        result = state_store.recover_abandoned_work(
            work_item["id"],
            expected_run_id=run["id"],
            actor="codex",
            reason="사용자가 다른 세션의 중단을 확인함",
            database_path=self.database_path,
        )

        self.assertEqual(result["run"]["status"], "interrupted")
        self.assertEqual(
            result["run"]["termination_reason"],
            "사용자 확인으로 중단된 세션의 Run을 복구함",
        )
        self.assertEqual(result["work_item"]["status"], "ready")
        self.assertEqual(
            result["work_item"]["next_action"], "복구 도구를 구현한다."
        )

    def test_previous_turn_stale_run_can_be_listed_and_recovered(self) -> None:
        work_item = state_store.create_work_item(
            title="이전 turn 정리",
            kind="maintenance",
            goal="같은 세션의 미종료 Run을 다음 요청 전에 정리한다.",
            next_action="UPS 복구 함수를 구현한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="UPS 복구 함수를 구현한다.",
            actor="planner",
            reason="구현 준비 완료",
            database_path=self.database_path,
        )
        run = self.start_run(
            work_item["id"], actor="codex", database_path=self.database_path
        )

        running = state_store.list_running_runs(database_path=self.database_path)
        self.assertEqual([item["id"] for item in running], [run["id"]])
        self.assertEqual(running[0]["work_item_title"], "이전 turn 정리")
        self.assertEqual(
            running[0]["work_item_goal"],
            "같은 세션의 미종료 Run을 다음 요청 전에 정리한다.",
        )
        self.assertEqual(
            running[0]["work_item_next_action"], "UPS 복구 함수를 구현한다."
        )
        self.assertEqual(running[0]["work_item_priority"], "normal")

        result = state_store.recover_stale_run(
            work_item["id"],
            expected_run_id=run["id"],
            actor="user_prompt_submit_hook",
            database_path=self.database_path,
        )

        self.assertEqual(result["run"]["status"], "interrupted")
        self.assertEqual(
            result["run"]["termination_reason"],
            "이전 turn에서 finish_work 없이 종료됨",
        )
        self.assertEqual(result["work_item"]["status"], "ready")
        self.assertEqual(
            result["work_item"]["next_action"], "UPS 복구 함수를 구현한다."
        )
        self.assertEqual(
            state_store.list_running_runs(database_path=self.database_path), []
        )

    def test_list_running_runs_orders_joined_context_and_excludes_finished_runs(
        self,
    ) -> None:
        specifications = (
            (
                "first",
                "첫 번째 활성 작업",
                "첫 번째 목표를 완료한다.",
                "첫 번째 행동을 수행한다.",
                "high",
            ),
            (
                "second",
                "두 번째 활성 작업",
                "두 번째 목표를 완료한다.",
                "두 번째 행동을 수행한다.",
                "urgent",
            ),
            (
                "finished",
                "종료된 작업",
                "종료된 목표를 완료한다.",
                "종료 전 행동을 수행한다.",
                "low",
            ),
        )
        work_items: dict[str, dict[str, object]] = {}
        runs: dict[str, dict[str, object]] = {}
        for key, title, goal, next_action, priority in specifications:
            work_item = state_store.create_work_item(
                title=title,
                kind="maintenance",
                goal=goal,
                priority=priority,
                next_action=next_action,
                actor="planner",
                database_path=self.database_path,
            )
            state_store.change_work_item_status(
                work_item["id"],
                "ready",
                next_action=next_action,
                actor="planner",
                reason="조회 테스트 준비 완료",
                database_path=self.database_path,
            )
            run = self.start_run(
                work_item["id"],
                run_id=f"RUN-{key}",
                actor="codex",
                database_path=self.database_path,
            )
            work_items[key] = work_item
            runs[key] = run

        state_store.recover_stale_run(
            work_items["finished"]["id"],
            expected_run_id=runs["finished"]["id"],
            actor="user_prompt_submit_hook",
            database_path=self.database_path,
        )
        database = state_store.open_database(self.database_path)
        database.execute(
            "UPDATE runs SET started_at = ? WHERE id = ?",
            ("2026-08-24T00:00:02Z", runs["first"]["id"]),
        )
        database.execute(
            "UPDATE runs SET started_at = ? WHERE id = ?",
            ("2026-08-24T00:00:01Z", runs["second"]["id"]),
        )
        database.commit()
        database.close()

        running = state_store.list_running_runs(database_path=self.database_path)

        self.assertEqual(
            [item["id"] for item in running],
            [runs["second"]["id"], runs["first"]["id"]],
        )
        self.assertEqual(
            [item["work_item_title"] for item in running],
            ["두 번째 활성 작업", "첫 번째 활성 작업"],
        )
        self.assertEqual(
            [item["work_item_goal"] for item in running],
            ["두 번째 목표를 완료한다.", "첫 번째 목표를 완료한다."],
        )
        self.assertEqual(
            [item["work_item_next_action"] for item in running],
            ["두 번째 행동을 수행한다.", "첫 번째 행동을 수행한다."],
        )
        self.assertEqual(
            [item["work_item_priority"] for item in running],
            ["urgent", "high"],
        )

    def test_recover_unbound_run_interrupts_only_the_expected_active_run(self) -> None:
        work_item = state_store.create_work_item(
            title="Binding 실패 보상",
            kind="maintenance",
            goal="소유자 없는 Run을 남기지 않는다.",
            next_action="실패한 Binding 생성을 보상한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="실패한 Binding 생성을 보상한다.",
            actor="planner",
            reason="보상 테스트 준비",
            database_path=self.database_path,
        )
        run = self.start_run(
            work_item["id"], actor="codex", database_path=self.database_path
        )

        result = state_store.recover_unbound_run(
            work_item["id"],
            expected_run_id=run["id"],
            actor="post_tool_use_hook",
            database_path=self.database_path,
        )

        self.assertEqual(result["run"]["status"], "interrupted")
        self.assertEqual(
            result["run"]["termination_reason"], "Runtime Binding 생성 실패"
        )
        self.assertEqual(result["work_item"]["status"], "ready")
        self.assertEqual(
            result["work_item"]["next_action"], "실패한 Binding 생성을 보상한다."
        )

        with self.assertRaises(state_store.ConflictError):
            state_store.recover_unbound_run(
                work_item["id"],
                expected_run_id=run["id"],
                actor="post_tool_use_hook",
                database_path=self.database_path,
            )

    def test_abandoned_work_recovery_rejects_a_stale_run_id(self) -> None:
        work_item = state_store.create_work_item(
            title="복구 대상 검증",
            kind="maintenance",
            goal="잘못된 Run을 복구하지 않는다.",
            next_action="정확한 Run을 확인한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="정확한 Run을 확인한다.",
            actor="planner",
            reason="검증 준비 완료",
            database_path=self.database_path,
        )
        run = self.start_run(
            work_item["id"], actor="codex", database_path=self.database_path
        )

        with self.assertRaises(state_store.NotFoundError):
            state_store.recover_abandoned_work(
                work_item["id"],
                expected_run_id="RUN-stale",
                actor="codex",
                reason="사용자가 복구를 확인함",
                database_path=self.database_path,
            )

        self.assertEqual(
            state_store.get_run(run["id"], database_path=self.database_path)["status"],
            "running",
        )

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
        run = self.start_run(
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

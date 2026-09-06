import sqlite3
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / ".harness" / "schema.sql"


class StateStoreSchemaTests(unittest.TestCase):
    def open_database(self) -> sqlite3.Connection:
        database = sqlite3.connect(":memory:")
        database.row_factory = sqlite3.Row
        database.executescript(SCHEMA_PATH.read_text())
        return database

    def insert_work_item(
        self,
        database: sqlite3.Connection,
        work_item_id: str = "HW-1",
        status: str = "backlog",
        next_action: str | None = None,
    ) -> None:
        database.execute(
            """
            INSERT INTO work_items (
              id, title, kind, goal, status, priority, next_action,
              created_at, updated_at
            ) VALUES (?, '상태 저장소 구현', 'implementation', 'DDL 완성', ?, 'normal', ?,
                      '2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z')
            """,
            (work_item_id, status, next_action),
        )

    def test_schema_builds_the_nine_state_store_tables(self) -> None:
        database = self.open_database()

        table_names = {
            row["name"]
            for row in database.execute(
                """
                SELECT name
                FROM sqlite_schema
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            )
        }

        self.assertEqual(
            table_names,
            {
                "features",
                "work_items",
                "acceptance_criteria",
                "runs",
                "artifacts",
                "criterion_evidence",
                "state_events",
                "memory_candidates",
                "work_item_memos",
            },
        )

    def test_work_item_status_can_only_follow_an_allowed_transition(self) -> None:
        database = self.open_database()
        self.insert_work_item(database)

        with self.assertRaisesRegex(sqlite3.IntegrityError, "invalid work item status transition"):
            database.execute(
                """
                UPDATE work_items
                SET status = 'in_progress', next_action = '구현 시작',
                    updated_at = '2026-08-13T00:01:00Z'
                WHERE id = 'HW-1'
                """
            )

        database.execute(
            """
            UPDATE work_items
            SET status = 'ready', next_action = '구현 시작',
                updated_at = '2026-08-13T00:01:00Z'
            WHERE id = 'HW-1'
            """
        )
        self.assertEqual(
            database.execute("SELECT status FROM work_items WHERE id = 'HW-1'").fetchone()[0],
            "ready",
        )

    def test_running_run_must_finish_before_work_item_leaves_in_progress(self) -> None:
        database = self.open_database()
        self.insert_work_item(database, status="ready", next_action="구현 시작")
        database.execute(
            """
            UPDATE work_items
            SET status = 'in_progress', updated_at = '2026-08-13T00:01:00Z'
            WHERE id = 'HW-1'
            """
        )
        database.execute(
            """
            INSERT INTO runs (id, work_item_id, status, started_at)
            VALUES ('RUN-1', 'HW-1', 'running', '2026-08-13T00:01:00Z')
            """
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "running run must finish first"):
            database.execute(
                """
                UPDATE work_items
                SET status = 'blocked', next_action = '권한 확보',
                    block_reason = '권한 없음', updated_at = '2026-08-13T00:02:00Z'
                WHERE id = 'HW-1'
                """
            )

        database.execute(
            """
            UPDATE runs
            SET status = 'interrupted', ended_at = '2026-08-13T00:02:00Z',
                summary = '권한 부족으로 중단', termination_reason = '외부 권한 필요'
            WHERE id = 'RUN-1'
            """
        )
        database.execute(
            """
            UPDATE work_items
            SET status = 'blocked', next_action = '권한 확보',
                block_reason = '권한 없음', updated_at = '2026-08-13T00:02:00Z'
            WHERE id = 'HW-1'
            """
        )
        self.assertEqual(
            database.execute("SELECT status FROM work_items WHERE id = 'HW-1'").fetchone()[0],
            "blocked",
        )

    def test_successful_run_can_return_unfinished_work_item_to_ready(self) -> None:
        database = self.open_database()
        self.insert_work_item(database, status="ready", next_action="첫 설계 검토")
        database.execute(
            """
            UPDATE work_items
            SET status = 'in_progress', updated_at = '2026-08-13T00:01:00Z'
            WHERE id = 'HW-1'
            """
        )
        database.execute(
            """
            INSERT INTO runs (id, work_item_id, status, started_at)
            VALUES ('RUN-1', 'HW-1', 'running', '2026-08-13T00:01:00Z')
            """
        )

        database.execute(
            """
            UPDATE runs
            SET status = 'succeeded', ended_at = '2026-08-13T00:02:00Z',
                summary = '첫 설계를 확정했다.'
            WHERE id = 'RUN-1'
            """
        )
        database.execute(
            """
            UPDATE work_items
            SET status = 'ready', next_action = '다음 설계를 검토한다.',
                updated_at = '2026-08-13T00:02:00Z'
            WHERE id = 'HW-1'
            """
        )

        self.assertEqual(
            database.execute("SELECT status FROM runs WHERE id = 'RUN-1'").fetchone()[0],
            "succeeded",
        )
        self.assertEqual(
            database.execute("SELECT status FROM work_items WHERE id = 'HW-1'").fetchone()[0],
            "ready",
        )

    def test_criterion_pass_requires_valid_evidence_from_the_same_work_item(self) -> None:
        database = self.open_database()
        self.insert_work_item(database)
        database.execute(
            """
            INSERT INTO acceptance_criteria (
              id, work_item_id, description, status, sort_order
            ) VALUES ('AC-1', 'HW-1', '테스트 통과', 'pending', 1)
            """
        )
        database.execute(
            """
            INSERT INTO runs (
              id, work_item_id, status, started_at, ended_at, summary, termination_reason
            ) VALUES (
              'RUN-1', 'HW-1', 'failed', '2026-08-13T00:00:00Z',
              '2026-08-13T00:01:00Z', '테스트 실패', 'AssertionError'
            )
            """
        )
        database.execute(
            """
            INSERT INTO artifacts (
              id, run_id, kind, uri, verification_status, summary, created_at
            ) VALUES (
              'ART-FAILED', 'RUN-1', 'test_run', 'trace://failed-test',
              'failed', '테스트 실패 결과', '2026-08-13T00:01:00Z'
            )
            """
        )
        database.execute(
            """
            INSERT INTO criterion_evidence (criterion_id, artifact_id, created_at)
            VALUES ('AC-1', 'ART-FAILED', '2026-08-13T00:01:00Z')
            """
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "valid evidence required"):
            database.execute(
                """
                UPDATE acceptance_criteria
                SET status = 'passed', resolved_at = '2026-08-13T00:02:00Z'
                WHERE id = 'AC-1'
                """
            )

        database.execute(
            """
            INSERT INTO artifacts (
              id, run_id, kind, uri, verification_status, summary, created_at
            ) VALUES (
              'ART-PASSED', 'RUN-1', 'test_run', 'trace://passed-test',
              'passed', '재실행 테스트 통과', '2026-08-13T00:02:00Z'
            )
            """
        )
        database.execute(
            """
            INSERT INTO criterion_evidence (criterion_id, artifact_id, created_at)
            VALUES ('AC-1', 'ART-PASSED', '2026-08-13T00:02:00Z')
            """
        )
        database.execute(
            """
            UPDATE acceptance_criteria
            SET status = 'passed', resolved_at = '2026-08-13T00:02:00Z'
            WHERE id = 'AC-1'
            """
        )
        self.assertEqual(
            database.execute(
                "SELECT status FROM acceptance_criteria WHERE id = 'AC-1'"
            ).fetchone()[0],
            "passed",
        )

    def test_work_item_done_requires_at_least_one_resolved_criterion(self) -> None:
        database = self.open_database()
        self.insert_work_item(database, status="ready", next_action="구현 시작")
        database.execute(
            """
            UPDATE work_items
            SET status = 'in_progress', updated_at = '2026-08-13T00:01:00Z'
            WHERE id = 'HW-1'
            """
        )

        with self.assertRaisesRegex(
            sqlite3.IntegrityError, "work item completion requirements not met"
        ):
            database.execute(
                """
                UPDATE work_items
                SET status = 'done', next_action = NULL,
                    closed_at = '2026-08-13T00:02:00Z',
                    updated_at = '2026-08-13T00:02:00Z'
                WHERE id = 'HW-1'
                """
            )

        database.execute(
            """
            INSERT INTO acceptance_criteria (
              id, work_item_id, description, status, sort_order, resolved_at
            ) VALUES (
              'AC-1', 'HW-1', '사용자 승인', 'waived', 1,
              '2026-08-13T00:02:00Z'
            )
            """
        )
        database.execute(
            """
            UPDATE work_items
            SET status = 'done', next_action = NULL,
                closed_at = '2026-08-13T00:02:00Z',
                updated_at = '2026-08-13T00:02:00Z'
            WHERE id = 'HW-1'
            """
        )
        self.assertEqual(
            database.execute("SELECT status FROM work_items WHERE id = 'HW-1'").fetchone()[0],
            "done",
        )

    def test_evidence_must_come_from_the_criterion_work_item(self) -> None:
        database = self.open_database()
        self.insert_work_item(database, work_item_id="HW-1")
        self.insert_work_item(database, work_item_id="HW-2")
        database.execute(
            """
            INSERT INTO acceptance_criteria (
              id, work_item_id, description, status, sort_order
            ) VALUES ('AC-1', 'HW-1', '테스트 통과', 'pending', 1)
            """
        )
        database.execute(
            """
            INSERT INTO runs (
              id, work_item_id, status, started_at, ended_at, summary
            ) VALUES (
              'RUN-2', 'HW-2', 'succeeded', '2026-08-13T00:00:00Z',
              '2026-08-13T00:01:00Z', '다른 작업의 테스트 통과'
            )
            """
        )
        database.execute(
            """
            INSERT INTO artifacts (
              id, run_id, kind, uri, verification_status, summary, created_at
            ) VALUES (
              'ART-2', 'RUN-2', 'test_run', 'trace://other-work-item',
              'passed', '다른 작업의 테스트', '2026-08-13T00:01:00Z'
            )
            """
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "evidence work item mismatch"):
            database.execute(
                """
                INSERT INTO criterion_evidence (criterion_id, artifact_id, created_at)
                VALUES ('AC-1', 'ART-2', '2026-08-13T00:01:00Z')
                """
            )

    def test_final_artifact_verification_cannot_be_rewritten(self) -> None:
        database = self.open_database()
        self.insert_work_item(database)
        database.execute(
            """
            INSERT INTO runs (id, work_item_id, status, started_at)
            VALUES ('RUN-1', 'HW-1', 'running', '2026-08-13T00:00:00Z')
            """
        )
        database.execute(
            """
            INSERT INTO artifacts (
              id, run_id, kind, uri, verification_status, summary, created_at
            ) VALUES (
              'ART-1', 'RUN-1', 'test_run', 'trace://test',
              'pending', '테스트 실행 중', '2026-08-13T00:00:00Z'
            )
            """
        )

        database.execute(
            "UPDATE artifacts SET verification_status = 'passed' WHERE id = 'ART-1'"
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "artifact verification is final"):
            database.execute(
                "UPDATE artifacts SET verification_status = 'failed' WHERE id = 'ART-1'"
            )

    def test_criterion_description_is_locked_after_evidence_is_linked(self) -> None:
        database = self.open_database()
        self.insert_work_item(database)
        database.execute(
            """
            INSERT INTO acceptance_criteria (
              id, work_item_id, description, status, sort_order
            ) VALUES ('AC-1', 'HW-1', '기존 완료 조건', 'pending', 1)
            """
        )
        database.execute(
            """
            INSERT INTO runs (
              id, work_item_id, status, started_at, ended_at, summary
            ) VALUES (
              'RUN-1', 'HW-1', 'succeeded', '2026-08-13T00:00:00Z',
              '2026-08-13T00:01:00Z', '검증 완료'
            )
            """
        )
        database.execute(
            """
            INSERT INTO artifacts (
              id, run_id, kind, uri, verification_status, summary, created_at
            ) VALUES (
              'ART-1', 'RUN-1', 'test_run', 'trace://test',
              'passed', '테스트 통과', '2026-08-13T00:01:00Z'
            )
            """
        )
        database.execute(
            """
            INSERT INTO criterion_evidence (criterion_id, artifact_id, created_at)
            VALUES ('AC-1', 'ART-1', '2026-08-13T00:01:00Z')
            """
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "criterion description is locked"):
            database.execute(
                """
                UPDATE acceptance_criteria
                SET description = '증거와 다른 새 완료 조건'
                WHERE id = 'AC-1'
                """
            )

    def test_state_events_are_append_only(self) -> None:
        database = self.open_database()
        database.execute(
            """
            INSERT INTO state_events (
              entity_type, entity_id, event_type, actor, reason, created_at
            ) VALUES (
              'work_item', 'HW-1', 'created', 'test', '작업 생성',
              '2026-08-13T00:00:00Z'
            )
            """
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "state events are append-only"):
            database.execute("UPDATE state_events SET actor = 'other' WHERE id = 1")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "state events are append-only"):
            database.execute("DELETE FROM state_events WHERE id = 1")

    def test_memory_candidate_keywords_must_all_be_strings(self) -> None:
        database = self.open_database()
        self.insert_work_item(database)
        database.execute(
            """
            INSERT INTO runs (id, work_item_id, status, started_at)
            VALUES ('RUN-1', 'HW-1', 'running', '2026-08-13T00:00:00Z')
            """
        )

        with self.assertRaisesRegex(
            sqlite3.IntegrityError, "memory candidate keywords must be strings"
        ):
            database.execute(
                """
                INSERT INTO memory_candidates (
                  id, run_id, proposed_type, title, content, keywords_json, created_at
                ) VALUES (
                  'MEM-1', 'RUN-1', 'technology', 'SQLite 외래키',
                  '연결마다 활성화한다.', '["sqlite", 123]',
                  '2026-08-13T00:01:00Z'
                )
                """
            )

    def test_work_item_has_one_running_run_and_finished_run_cannot_restart(self) -> None:
        database = self.open_database()
        self.insert_work_item(database)
        database.execute(
            """
            INSERT INTO runs (id, work_item_id, status, started_at)
            VALUES ('RUN-1', 'HW-1', 'running', '2026-08-13T00:00:00Z')
            """
        )

        with self.assertRaises(sqlite3.IntegrityError):
            database.execute(
                """
                INSERT INTO runs (id, work_item_id, status, started_at)
                VALUES ('RUN-2', 'HW-1', 'running', '2026-08-13T00:01:00Z')
                """
            )

        database.execute(
            """
            UPDATE runs
            SET status = 'failed', ended_at = '2026-08-13T00:02:00Z',
                summary = '실행 실패', termination_reason = '테스트 실패'
            WHERE id = 'RUN-1'
            """
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "finished run status is immutable"):
            database.execute(
                """
                UPDATE runs
                SET status = 'running', ended_at = NULL, summary = NULL,
                    termination_reason = NULL
                WHERE id = 'RUN-1'
                """
            )

    def test_schema_exposes_the_five_derived_state_views(self) -> None:
        database = self.open_database()

        view_names = {
            row["name"]
            for row in database.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'view'"
            )
        }

        self.assertEqual(
            view_names,
            {
                "feature_progress",
                "project_progress",
                "work_item_verification",
                "next_work_items",
                "recent_activity",
            },
        )

    def test_schema_is_idempotent_and_enables_foreign_keys(self) -> None:
        database = self.open_database()
        database.executescript(SCHEMA_PATH.read_text())

        self.assertEqual(database.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            database.execute(
                """
                INSERT INTO runs (id, work_item_id, status, started_at)
                VALUES ('RUN-MISSING', 'HW-MISSING', 'running', '2026-08-13T00:00:00Z')
                """
            )

    def test_conditional_fields_reject_incomplete_states(self) -> None:
        database = self.open_database()

        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_work_item(database, status="ready", next_action=None)

        self.insert_work_item(database, status="ready", next_action="구현 시작")
        database.execute(
            """
            UPDATE work_items
            SET status = 'in_progress', updated_at = '2026-08-13T00:01:00Z'
            WHERE id = 'HW-1'
            """
        )
        with self.assertRaises(sqlite3.IntegrityError):
            database.execute(
                """
                INSERT INTO runs (
                  id, work_item_id, status, started_at, ended_at, summary
                ) VALUES (
                  'RUN-1', 'HW-1', 'failed', '2026-08-13T00:00:00Z',
                  '2026-08-13T00:01:00Z', '실패했지만 종료 이유 없음'
                )
                """
            )

    def test_verification_artifacts_cannot_be_not_applicable(self) -> None:
        database = self.open_database()
        self.insert_work_item(database)
        database.execute(
            """
            INSERT INTO runs (id, work_item_id, status, started_at)
            VALUES ('RUN-1', 'HW-1', 'running', '2026-08-13T00:00:00Z')
            """
        )

        with self.assertRaises(sqlite3.IntegrityError):
            database.execute(
                """
                INSERT INTO artifacts (
                  id, run_id, kind, uri, verification_status, summary, created_at
                ) VALUES (
                  'ART-1', 'RUN-1', 'test_run', 'trace://test',
                  'not_applicable', '검증 생략', '2026-08-13T00:01:00Z'
                )
                """
            )

    def test_next_work_items_orders_ready_work_by_priority_then_age(self) -> None:
        database = self.open_database()
        self.insert_work_item(
            database, work_item_id="HW-NORMAL", status="ready", next_action="일반 작업"
        )
        self.insert_work_item(
            database, work_item_id="HW-URGENT", status="ready", next_action="긴급 작업"
        )
        database.execute(
            "UPDATE work_items SET priority = 'urgent' WHERE id = 'HW-URGENT'"
        )

        ordered_ids = [row["id"] for row in database.execute("SELECT id FROM next_work_items")]

        self.assertEqual(ordered_ids, ["HW-URGENT", "HW-NORMAL"])


if __name__ == "__main__":
    unittest.main()

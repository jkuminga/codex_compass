import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.harness import state_store
from src.harness.control_center.app import create_app


class ControlCenterApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "state.db"
        self.client_context = TestClient(create_app(self.database_path))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temporary_directory.cleanup()

    def create_draft(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "title": "웹 콘솔 첫 화면 구현",
            "kind": "implementation",
            "goal": "브라우저에서 Draft를 만들고 확인한다.",
            "description": "목업을 기준으로 구현한다.",
        }
        payload.update(overrides)
        response = self.client.post("/api/draft-work-items", json=payload)
        self.assertEqual(response.status_code, 201)
        return response.json()["work_item"]

    def test_root_serves_the_control_center(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Harness Control Center", response.text)
        self.assertIn("새 Draft WI 생성", response.text)

    def test_create_draft_uses_server_owned_defaults(self) -> None:
        draft = self.create_draft()

        self.assertTrue(draft["is_draft"])
        self.assertEqual(draft["status"], "backlog")
        self.assertEqual(draft["priority"], "normal")
        self.assertIsNone(draft["feature_id"])
        self.assertIsNone(draft["next_action"])

        stored = state_store.get_work_item(
            str(draft["id"]), database_path=self.database_path
        )
        self.assertEqual(stored["description"], "목업을 기준으로 구현한다.")

    def test_create_draft_normalizes_blank_optional_description(self) -> None:
        draft = self.create_draft(description="   ")

        self.assertIsNone(draft["description"])

    def test_validation_error_uses_the_ui_error_envelope(self) -> None:
        response = self.client.post(
            "/api/draft-work-items",
            json={"title": "  ", "kind": "unknown", "goal": ""},
        )

        self.assertEqual(response.status_code, 422)
        error = response.json()["error"]
        self.assertEqual(error["code"], "validation_error")
        self.assertEqual(set(error["fields"]), {"title", "kind", "goal"})

    def test_list_supports_virtual_draft_and_regular_backlog_filters(self) -> None:
        draft = self.create_draft(title="Draft 작업")
        backlog = state_store.create_work_item(
            title="일반 Backlog 작업",
            kind="research",
            goal="필터 분리를 검증한다.",
            actor="test",
            database_path=self.database_path,
        )

        draft_response = self.client.get("/api/work-items?status=draft")
        backlog_response = self.client.get("/api/work-items?status=backlog")

        self.assertEqual(
            [item["id"] for item in draft_response.json()["work_items"]], [draft["id"]]
        )
        self.assertEqual(
            [item["id"] for item in backlog_response.json()["work_items"]],
            [backlog["id"]],
        )

    def test_list_combines_status_and_kind_filters(self) -> None:
        implementation = self.create_draft(title="구현 Draft")
        self.create_draft(title="조사 Draft", kind="research")

        response = self.client.get(
            "/api/work-items?status=draft&kind=implementation"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in response.json()["work_items"]],
            [implementation["id"]],
        )

    def test_detail_returns_empty_draft_execution_fields(self) -> None:
        draft = self.create_draft()

        response = self.client.get(f"/api/work-items/{draft['id']}")

        self.assertEqual(response.status_code, 200)
        context = response.json()
        self.assertTrue(context["work_item"]["is_draft"])
        self.assertEqual(context["acceptance_criteria"], [])
        self.assertIsNone(context["running_run"])
        self.assertEqual(context["recent_runs"], [])
        self.assertTrue(context["capabilities"]["can_edit"])
        self.assertTrue(context["capabilities"]["can_delete"])
        self.assertEqual(context["capabilities"]["allowed_statuses"], [])

    def test_edit_updates_user_managed_fields(self) -> None:
        draft = self.create_draft()

        response = self.client.patch(
            f"/api/work-items/{draft['id']}",
            json={
                "title": "수정된 Draft",
                "goal": "웹에서 계획을 수정한다.",
                "description": None,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["work_item"]["title"], "수정된 Draft")
        self.assertIsNone(response.json()["work_item"]["description"])

    def test_web_api_rejects_backlog_to_ready_even_when_crafted(self) -> None:
        backlog = state_store.create_work_item(
            title="준비 전 작업",
            kind="implementation",
            goal="웹에서 Ready로 우회하지 못한다.",
            actor="test",
            database_path=self.database_path,
        )

        response = self.client.patch(
            f"/api/work-items/{backlog['id']}/status", json={"status": "ready"}
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            state_store.get_work_item(backlog["id"], database_path=self.database_path)["status"],
            "backlog",
        )

    def test_blocked_work_item_can_return_to_ready_from_web(self) -> None:
        work_item = state_store.create_work_item(
            title="차단된 작업",
            kind="bug",
            goal="차단 해제 흐름을 검증한다.",
            next_action="원인을 확인한다.",
            actor="test",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"], "ready", next_action="원인을 확인한다.",
            actor="test", reason="준비", database_path=self.database_path,
        )
        run = state_store.start_run(
            work_item["id"], intent="차단 상태를 만든다.", recall_query="차단 상태",
            actor="test", database_path=self.database_path,
        )
        state_store.finish_run(
            run["id"], run_status="interrupted", work_item_status="blocked",
            summary="권한 부족으로 중단", termination_reason="권한 없음",
            next_action="권한을 기다린다.", block_reason="권한 없음",
            actor="test", reason="차단",
            database_path=self.database_path,
        )

        response = self.client.patch(
            f"/api/work-items/{work_item['id']}/status", json={"status": "ready"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["work_item"]["status"], "ready")
        self.assertEqual(response.json()["work_item"]["next_action"], "권한을 기다린다.")
        self.assertIsNone(response.json()["work_item"]["block_reason"])

    def test_delete_is_limited_to_unstarted_backlog_items(self) -> None:
        draft = self.create_draft()
        response = self.client.delete(f"/api/work-items/{draft['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted_work_item_id"], draft["id"])

        ready = state_store.create_work_item(
            title="삭제하면 안 되는 작업", kind="research", goal="기록을 보존한다.",
            actor="test", database_path=self.database_path,
        )
        state_store.change_work_item_status(
            ready["id"], "ready", next_action="실행한다.", actor="test",
            reason="준비", database_path=self.database_path,
        )
        blocked = self.client.delete(f"/api/work-items/{ready['id']}")
        self.assertEqual(blocked.status_code, 409)

    def test_missing_work_item_returns_not_found_envelope(self) -> None:
        response = self.client.get("/api/work-items/WI-missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")


if __name__ == "__main__":
    unittest.main()

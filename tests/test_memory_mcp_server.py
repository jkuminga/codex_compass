import tempfile
import unittest
from pathlib import Path
from typing import Any

from mcp import Client

from src.harness import state_store
from src.harness.memory_mcp_server import create_server


class MemoryMCPServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "state.db"
        state_store.initialize_database(self.database_path)
        work_item = state_store.create_work_item(
            title="장기 기억 후보 검사",
            kind="implementation",
            goal="후보와 기존 기억을 비교한다.",
            next_action="검사 도구를 실행한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"], "ready",
            next_action="검사 도구를 실행한다.", actor="planner",
            reason="테스트 준비", database_path=self.database_path,
        )
        run = state_store.start_run(
            work_item["id"], intent="장기 기억 후보를 검사한다.",
            recall_query="장기 기억 후보 검사", actor="codex",
            database_path=self.database_path,
        )
        self.candidate = state_store.create_candidate(
            run["id"], proposed_type="solution", title="SQLite 외래키",
            content="연결마다 외래키 검사를 활성화한다.",
            keywords=["SQLite", "외래키"], database_path=self.database_path,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    async def test_tool_reads_candidate_and_forwards_request(self) -> None:
        received: list[dict[str, Any]] = []

        def inspector(payload: dict[str, Any]) -> dict[str, Any]:
            received.append(payload)
            return {
                "ok": True, "candidate": payload["candidate"],
                "search": {"terms": ["sqlite", "외래키", "pragma"],
                           "status": "complete", "attempted": 3, "succeeded": 3},
                "matches": [], "warnings": [],
            }

        async with Client(create_server(self.database_path, inspect_runner=inspector)) as client:
            tools = await client.list_tools()
            result = await client.call_tool(
                "inspect_memory_candidate",
                {"candidate_id": self.candidate["id"],
                 "extra_terms": ["PRAGMA"], "limit": 4},
            )

        self.assertIn("inspect_memory_candidate", {tool.name for tool in tools.tools})
        self.assertFalse(result.is_error)
        self.assertTrue(result.structured_content["ok"])
        self.assertEqual(received[0]["candidate"]["keywords"], ["sqlite", "외래키"])
        self.assertEqual(received[0]["extra_terms"], ["PRAGMA"])
        self.assertEqual(received[0]["limit"], 4)
        self.assertEqual(
            state_store.get_memory_candidate(
                self.candidate["id"], database_path=self.database_path
            )["status"], "pending",
        )

    async def test_missing_candidate_returns_stable_error_without_inspection(self) -> None:
        calls = 0

        def inspector(_: dict[str, Any]) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {}

        async with Client(create_server(self.database_path, inspect_runner=inspector)) as client:
            result = await client.call_tool(
                "inspect_memory_candidate", {"candidate_id": "MEMC-missing"}
            )

        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["error"]["code"], "candidate_not_found")
        self.assertEqual(calls, 0)

    async def test_non_pending_candidate_is_not_searched(self) -> None:
        state_store.promote_candidate(
            self.candidate["id"], memory_ref="memory://existing",
            database_path=self.database_path,
        )
        calls = 0

        def inspector(_: dict[str, Any]) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {}

        async with Client(create_server(self.database_path, inspect_runner=inspector)) as client:
            result = await client.call_tool(
                "inspect_memory_candidate", {"candidate_id": self.candidate["id"]}
            )

        self.assertEqual(result.structured_content["error"]["code"], "candidate_not_pending")
        self.assertEqual(calls, 0)

    def storage_plan(self, decision: str = "create") -> dict[str, Any]:
        return {
            "decision": decision,
            "target_memory_id": None,
            "memory": None if decision == "reject" else {
                "type": "solution", "title": "SQLite 외래키",
                "content": "연결마다 외래키 검사를 활성화한다.",
                "summary": "SQLite 외래키 안전 설정", "tags": ["SQLite"],
                "importance": 0.8, "confidence": 0.9,
            },
            "relationships": [], "reason": "장기 재사용 가능",
        }

    async def test_finalize_promotes_only_after_committed_writer_receipt(self) -> None:
        calls: list[dict[str, Any]] = []

        def finalizer(payload: dict[str, Any]) -> dict[str, Any]:
            calls.append(payload)
            if payload["mode"] == "validate":
                return {"ok": True, "status": "validated", "candidate_id": self.candidate["id"],
                        "decision": "create", "relationships": {"created": [], "skipped": [], "failed": []}, "warnings": []}
            return {"ok": True, "status": "committed", "candidate_id": self.candidate["id"],
                    "decision": "create", "memory_ref": "memory://candidate:test",
                    "node_result": "created", "relationships": {"created": [], "skipped": [], "failed": []}, "warnings": []}

        async with Client(create_server(self.database_path, finalize_runner=finalizer)) as client:
            tools = await client.list_tools()
            result = await client.call_tool("finalize_memory_candidate", {
                "candidate_id": self.candidate["id"], "storage_plan": self.storage_plan(),
            })

        self.assertIn("finalize_memory_candidate", {tool.name for tool in tools.tools})
        self.assertTrue(result.structured_content["ok"])
        self.assertEqual([call["mode"] for call in calls], ["validate", "execute"])
        stored = state_store.get_memory_candidate(self.candidate["id"], database_path=self.database_path)
        self.assertEqual(stored["status"], "promoted")
        self.assertIsNotNone(stored["storage_plan"])
        self.assertEqual(len(stored["plan_fingerprint"]), 64)

    async def test_partial_finalize_stays_pending_and_retry_reuses_saved_plan(self) -> None:
        executions = 0

        def finalizer(payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal executions
            if payload["mode"] == "validate":
                return {"ok": True, "status": "validated", "candidate_id": self.candidate["id"],
                        "decision": "create", "relationships": {"created": [], "skipped": [], "failed": []}, "warnings": []}
            executions += 1
            return {"ok": executions > 1, "status": "committed" if executions > 1 else "partial",
                    "candidate_id": self.candidate["id"], "decision": "create",
                    "memory_ref": "memory://candidate:test", "node_result": "skipped" if executions > 1 else "created",
                    "relationships": {"created": [], "skipped": [], "failed": []}, "warnings": []}

        server = create_server(self.database_path, finalize_runner=finalizer)
        async with Client(server) as client:
            first = await client.call_tool("finalize_memory_candidate", {
                "candidate_id": self.candidate["id"], "storage_plan": self.storage_plan(),
            })
            retry = await client.call_tool("finalize_memory_candidate", {"candidate_id": self.candidate["id"]})

        self.assertEqual(first.structured_content["status"], "partial")
        self.assertEqual(retry.structured_content["status"], "committed")
        self.assertEqual(state_store.get_memory_candidate(
            self.candidate["id"], database_path=self.database_path)["status"], "promoted")

    async def test_finalize_reject_and_plan_conflict_are_stable(self) -> None:
        def finalizer(payload: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "status": "validated", "candidate_id": self.candidate["id"],
                    "decision": payload["storage_plan"]["decision"],
                    "relationships": {"created": [], "skipped": [], "failed": []}, "warnings": []}

        async with Client(create_server(self.database_path, finalize_runner=finalizer)) as client:
            rejected = await client.call_tool("finalize_memory_candidate", {
                "candidate_id": self.candidate["id"], "storage_plan": self.storage_plan("reject"),
            })
            conflict = await client.call_tool("finalize_memory_candidate", {
                "candidate_id": self.candidate["id"], "storage_plan": self.storage_plan("create"),
            })

        self.assertEqual(rejected.structured_content["node_result"], "rejected")
        self.assertEqual(conflict.structured_content["error"]["code"], "plan_conflict")


if __name__ == "__main__":
    unittest.main()

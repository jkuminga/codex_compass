import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import Client, ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.harness import state_store
from src.harness.mcp_server import create_server


class StateStoreMCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "state.db"
        state_store.initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    async def test_codex_can_read_project_status_through_mcp(self) -> None:
        async with Client(create_server(self.database_path)) as client:
            tools = await client.list_tools()
            result = await client.call_tool(
                "get_project_status", {"next_limit": 5, "activity_limit": 5}
            )

        self.assertIn("get_project_status", {tool.name for tool in tools.tools})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["progress"]["total_work_items"], 0)
        self.assertEqual(result.structured_content["next_work_items"], [])
        self.assertEqual(result.structured_content["recent_activity"], [])

    async def test_codex_can_search_open_work_items_through_mcp(self) -> None:
        work_item = state_store.create_work_item(
            title="Recall 검색 도구",
            kind="implementation",
            goal="Memory recall 관련 WorkItem을 검색한다.",
            actor="planner",
            database_path=self.database_path,
        )

        async with Client(create_server(self.database_path)) as client:
            tools = await client.list_tools()
            result = await client.call_tool(
                "search_work_items",
                {"terms": ["recall", "memory"], "limit": 5},
            )

        self.assertIn("search_work_items", {tool.name for tool in tools.tools})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["work_items"][0]["id"], work_item["id"])
        self.assertGreater(result.structured_content["work_items"][0]["match_score"], 0)

    async def test_codex_can_plan_and_prepare_work_through_mcp(self) -> None:
        async with Client(create_server(self.database_path)) as client:
            feature_result = await client.call_tool(
                "create_feature",
                {
                    "title": "MCP 상태 도구",
                    "goal": "Codex가 상태 저장소를 안전하게 사용한다.",
                    "priority": "high",
                },
            )
            feature = feature_result.structured_content
            work_result = await client.call_tool(
                "create_work_item",
                {
                    "feature_id": feature["id"],
                    "title": "MCP 서버 구현",
                    "kind": "implementation",
                    "goal": "상태 저장 도구를 공개한다.",
                    "next_action": "MCP 서버를 구현한다.",
                    "acceptance_criteria": ["MCP Client 통합 테스트가 통과한다."],
                },
            )
            work_item = work_result.structured_content
            ready_result = await client.call_tool(
                "change_work_item_state",
                {
                    "work_item_id": work_item["id"],
                    "status": "ready",
                    "reason": "구현 준비 완료",
                    "next_action": "MCP 서버를 구현한다.",
                },
            )
            context_result = await client.call_tool(
                "get_work_context", {"work_item_id": work_item["id"]}
            )

        self.assertFalse(feature_result.is_error)
        self.assertFalse(work_result.is_error)
        self.assertEqual(ready_result.structured_content["status"], "ready")
        self.assertEqual(
            context_result.structured_content["work_item"]["id"], work_item["id"]
        )
        self.assertEqual(
            context_result.structured_content["acceptance_criteria"][0]["status"],
            "pending",
        )

    async def test_codex_can_execute_verify_and_finish_work_through_mcp(self) -> None:
        work_item = state_store.create_work_item(
            title="MCP 실행 흐름",
            kind="implementation",
            goal="실행과 검증을 MCP 도구로 마친다.",
            next_action="구현을 시작한다.",
            acceptance_criteria=["MCP 실행 테스트가 통과한다."],
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="구현을 시작한다.",
            actor="planner",
            reason="실행 준비 완료",
            database_path=self.database_path,
        )
        criterion_id = state_store.get_work_item_context(
            work_item["id"], database_path=self.database_path
        )["acceptance_criteria"][0]["id"]

        async with Client(create_server(self.database_path)) as client:
            run_result = await client.call_tool(
                "start_work",
                {
                    "work_item_id": work_item["id"],
                    "intent": "MCP 실행 흐름을 구현하고 검증한다.",
                    "recall_query": "MCP 실행 검증",
                    "trace_ref": "trace://mcp/run-1",
                },
            )
            run = run_result.structured_content
            self.assertEqual(run["intent"], "MCP 실행 흐름을 구현하고 검증한다.")
            self.assertEqual(run["recall_query"], "MCP 실행 검증")
            invalid_artifact_result = await client.call_tool(
                "record_artifact",
                {
                    "run_id": run["id"],
                    "kind": "test_run",
                    "uri": "trace://mcp/tests-1",
                    "verification_status": "failed",
                    "summary": "실행 시각이 없는 잘못된 URI",
                },
            )
            artifact_result = await client.call_tool(
                "record_artifact",
                {
                    "run_id": run["id"],
                    "kind": "test_run",
                    "uri": "command:mcp-tests:20260827T082000Z",
                    "verification_status": "passed",
                    "summary": "MCP 실행 테스트 통과",
                },
            )
            artifact = artifact_result.structured_content
            verify_result = await client.call_tool(
                "verify_criterion",
                {
                    "criterion_id": criterion_id,
                    "artifact_id": artifact["id"],
                    "result": "passed",
                    "note": "MCP Client 통합 테스트",
                },
            )
            postflight_result = await client.call_tool(
                "get_postflight_status", {"work_item_id": work_item["id"]}
            )
            finish_result = await client.call_tool(
                "finish_work",
                {
                    "run_id": run["id"],
                    "outcome": "completed",
                    "summary": "MCP 실행과 검증을 완료했다.",
                    "reason": "모든 완료 조건 충족",
                },
            )

        self.assertFalse(run_result.is_error)
        self.assertTrue(invalid_artifact_result.is_error)
        self.assertFalse(artifact_result.is_error)
        self.assertEqual(verify_result.structured_content["criterion"]["status"], "passed")
        self.assertTrue(
            postflight_result.structured_content["verification"]["can_complete"]
        )
        self.assertEqual(finish_result.structured_content["run"]["status"], "succeeded")
        self.assertEqual(finish_result.structured_content["work_item"]["status"], "done")

    async def test_codex_can_revise_planning_entities_through_mcp(self) -> None:
        feature = state_store.create_feature(
            title="초기 기능",
            goal="초기 목표",
            actor="planner",
            database_path=self.database_path,
        )
        work_item = state_store.create_work_item(
            feature_id=feature["id"],
            title="초기 작업",
            kind="implementation",
            goal="초기 작업 목표",
            actor="planner",
            database_path=self.database_path,
        )

        async with Client(create_server(self.database_path)) as client:
            feature_result = await client.call_tool(
                "update_feature",
                {
                    "feature_id": feature["id"],
                    "title": "MCP 기능",
                    "priority": "urgent",
                    "status": "active",
                    "reason": "구현 시작",
                },
            )
            work_result = await client.call_tool(
                "revise_work_item",
                {
                    "work_item_id": work_item["id"],
                    "title": "MCP 도구 구현",
                    "priority": "high",
                    "next_action": "MCP 도구를 구현한다.",
                },
            )
            added_result = await client.call_tool(
                "manage_criterion",
                {
                    "action": "add",
                    "work_item_id": work_item["id"],
                    "description": "MCP 통합 테스트가 통과한다.",
                },
            )
            criterion_id = added_result.structured_content["id"]
            revised_result = await client.call_tool(
                "manage_criterion",
                {
                    "action": "revise",
                    "criterion_id": criterion_id,
                    "description": "전체 MCP 통합 테스트가 통과한다.",
                },
            )
            waived_result = await client.call_tool(
                "manage_criterion",
                {
                    "action": "waive",
                    "criterion_id": criterion_id,
                    "reason": "초기 프로토타입에서는 제외",
                },
            )

        self.assertEqual(feature_result.structured_content["status"], "active")
        self.assertEqual(feature_result.structured_content["priority"], "urgent")
        self.assertEqual(work_result.structured_content["title"], "MCP 도구 구현")
        self.assertEqual(
            revised_result.structured_content["description"],
            "전체 MCP 통합 테스트가 통과한다.",
        )
        self.assertEqual(waived_result.structured_content["status"], "waived")

    async def test_codex_can_progress_work_without_completing_it_through_mcp(self) -> None:
        work_item = state_store.create_work_item(
            title="설계 세션 이어가기",
            kind="decision",
            goal="여러 세션에서 정책을 확정한다.",
            next_action="첫 정책을 검토한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="첫 정책을 검토한다.",
            actor="planner",
            reason="설계 준비 완료",
            database_path=self.database_path,
        )

        async with Client(create_server(self.database_path)) as client:
            run_result = await client.call_tool(
                "start_work",
                {
                    "work_item_id": work_item["id"],
                    "intent": "미완료 작업을 한 단계 진행한다.",
                    "recall_query": "작업 진행 상태",
                },
            )
            finish_result = await client.call_tool(
                "finish_work",
                {
                    "run_id": run_result.structured_content["id"],
                    "outcome": "progressed",
                    "summary": "첫 정책을 확정했다.",
                    "reason": "이번 세션 목표 달성",
                    "next_action": "다음 정책을 검토한다.",
                },
            )

        self.assertFalse(finish_result.is_error)
        self.assertEqual(finish_result.structured_content["run"]["status"], "succeeded")
        self.assertEqual(finish_result.structured_content["work_item"]["status"], "ready")
        self.assertEqual(
            finish_result.structured_content["work_item"]["next_action"],
            "다음 정책을 검토한다.",
        )

    async def test_codex_can_finish_each_non_success_outcome_through_mcp(
        self,
    ) -> None:
        cases = (
            ("retry_needed", "failed", "ready", "다음 Run에서 다시 검증한다.", None),
            (
                "blocked",
                "interrupted",
                "blocked",
                "권한 승인 후 재개한다.",
                "권한 승인 대기",
            ),
            ("interrupted", "interrupted", "ready", "중단 지점부터 재개한다.", None),
            ("cancelled", "cancelled", "cancelled", None, None),
        )

        async with Client(create_server(self.database_path)) as client:
            for index, (
                outcome,
                expected_run_status,
                expected_work_item_status,
                next_action,
                block_reason,
            ) in enumerate(cases, start=1):
                with self.subTest(outcome=outcome):
                    work_item = state_store.create_work_item(
                        title=f"종료 결과 {outcome}",
                        kind="implementation",
                        goal=f"{outcome} 종료 결과를 검증한다.",
                        next_action="종료 결과를 검증한다.",
                        actor="planner",
                        database_path=self.database_path,
                    )
                    state_store.change_work_item_status(
                        work_item["id"],
                        "ready",
                        next_action="종료 결과를 검증한다.",
                        actor="planner",
                        reason="종료 검증 준비 완료",
                        database_path=self.database_path,
                    )
                    run_result = await client.call_tool(
                        "start_work",
                        {
                            "work_item_id": work_item["id"],
                            "intent": f"{outcome} 종료 흐름을 검증한다.",
                            "recall_query": f"종료 결과 {index}",
                        },
                    )
                    finish_result = await client.call_tool(
                        "finish_work",
                        {
                            "run_id": run_result.structured_content["id"],
                            "outcome": outcome,
                            "summary": f"{outcome} 상태로 종료했다.",
                            "reason": f"{outcome} 조건을 확인했다.",
                            "termination_reason": f"{outcome} 종료 사유",
                            "next_action": next_action,
                            "block_reason": block_reason,
                        },
                    )

                    self.assertFalse(finish_result.is_error)
                    self.assertEqual(
                        finish_result.structured_content["run"]["status"],
                        expected_run_status,
                    )
                    self.assertEqual(
                        finish_result.structured_content["run"]["termination_reason"],
                        f"{outcome} 종료 사유",
                    )
                    self.assertEqual(
                        finish_result.structured_content["work_item"]["status"],
                        expected_work_item_status,
                    )
                    self.assertEqual(
                        finish_result.structured_content["work_item"]["next_action"],
                        next_action,
                    )
                    self.assertEqual(
                        finish_result.structured_content["work_item"]["block_reason"],
                        block_reason,
                    )

    async def test_codex_can_recover_user_confirmed_abandoned_work_through_mcp(self) -> None:
        work_item = state_store.create_work_item(
            title="중단 작업 복구",
            kind="maintenance",
            goal="사용자 확인 후 잠긴 작업을 복구한다.",
            next_action="복구 흐름을 구현한다.",
            actor="planner",
            database_path=self.database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="복구 흐름을 구현한다.",
            actor="planner",
            reason="구현 준비 완료",
            database_path=self.database_path,
        )
        run = state_store.start_run(
            work_item["id"],
            intent="중단된 MCP 작업을 시작한다.",
            recall_query="MCP 중단 복구",
            actor="codex",
            database_path=self.database_path,
        )

        async with Client(create_server(self.database_path)) as client:
            tools = await client.list_tools()
            result = await client.call_tool(
                "recover_abandoned_work",
                {
                    "work_item_id": work_item["id"],
                    "expected_run_id": run["id"],
                    "user_confirmation": "confirmed",
                },
            )

        self.assertIn("recover_abandoned_work", {tool.name for tool in tools.tools})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["run"]["status"], "interrupted")
        self.assertEqual(result.structured_content["work_item"]["status"], "ready")

    async def test_codex_can_capture_and_list_memory_candidates_through_mcp(self) -> None:
        work_item = state_store.create_work_item(
            title="기억 후보 MCP",
            kind="research",
            goal="장기 기억 후보를 도구로 관리한다.",
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
            work_item["id"],
            intent="장기 기억 후보를 기록한다.",
            recall_query="장기 기억 후보",
            actor="codex",
            database_path=self.database_path,
        )

        async with Client(create_server(self.database_path)) as client:
            first_source = await client.call_tool(
                "create_memory_candidate",
                {
                    "run_id": run["id"],
                    "proposed_type": "command",
                    "title": "전체 테스트 명령",
                    "content": "uv run python -m unittest discover -s tests -v",
                    "keywords": ["MCP", "test", "mcp"],
                },
            )
            second_source = await client.call_tool(
                "create_memory_candidate",
                {
                    "run_id": run["id"],
                    "proposed_type": "general",
                    "title": "일회성 내용",
                    "content": "이번 실행에서만 필요하다.",
                    "keywords": ["temporary"],
                },
            )
            pending_result = await client.call_tool(
                "list_memory_candidates", {"run_id": run["id"]}
            )

        self.assertEqual(len(pending_result.structured_content["candidates"]), 2)
        self.assertEqual(first_source.structured_content["keywords"], ["mcp", "test"])
        self.assertEqual(first_source.structured_content["status"], "pending")
        self.assertEqual(second_source.structured_content["status"], "pending")

    async def test_stdio_server_exposes_the_agreed_tool_interface(self) -> None:
        state_store.create_work_item(
            title="stdio 확인 작업",
            kind="verification",
            goal="Codex와 같은 전송 방식으로 서버를 확인한다.",
            actor="test",
            database_path=self.database_path,
        )
        environment = dict(os.environ)
        environment["HARNESS_STATE_DB"] = str(self.database_path)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "src.harness.mcp_server"],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
        )

        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                status = await session.call_tool(
                    "get_project_status",
                    {"next_limit": 5, "activity_limit": 5},
                )

        expected_tools = {
            "get_project_status",
            "search_work_items",
            "get_work_context",
            "get_postflight_status",
            "create_feature",
            "update_feature",
            "create_work_item",
            "revise_work_item",
            "change_work_item_state",
            "manage_criterion",
            "start_work",
            "record_artifact",
            "resolve_artifact",
            "verify_criterion",
            "finish_work",
            "recover_abandoned_work",
            "create_memory_candidate",
            "list_memory_candidates",
        }
        self.assertEqual({tool.name for tool in tools.tools}, expected_tools)
        self.assertEqual(status.structured_content["progress"]["total_work_items"], 1)


if __name__ == "__main__":
    unittest.main()

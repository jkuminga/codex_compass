import json
import tempfile
import unittest
from pathlib import Path

from src.harness import runtime_binding, state_store
from src.harness.hooks import post_tool_use


class StartWorkPostToolUseHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.bindings_directory = Path(self.temporary_directory.name) / "bindings"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def event(self) -> dict[str, object]:
        return {
            "hook_event_name": "PostToolUse",
            "session_id": "session-1",
            "turn_id": "turn-7",
            "tool_name": post_tool_use.START_WORK_TOOL,
            "tool_input": {
                "work_item_id": "WI-12",
                "intent": "MemoryGraph Recall Hook을 구현한다.",
                "recall_query": "MemoryGraph Recall Hook",
            },
            "tool_response": {
                "content": [],
                "structuredContent": {
                    "id": "RUN-34",
                    "work_item_id": "WI-12",
                    "intent": "MemoryGraph Recall Hook을 구현한다.",
                    "recall_query": "MemoryGraph Recall Hook",
                    "status": "running",
                },
                "isError": False,
            },
        }

    def test_non_start_work_tool_is_ignored_without_side_effects(self) -> None:
        event = self.event()
        event["tool_name"] = "apply_patch"
        recall_queries: list[str] = []

        result = post_tool_use.dispatch_post_tool_use(
            event,
            recall_runner=lambda query: recall_queries.append(query) or {},
            bindings_directory=self.bindings_directory,
        )

        self.assertIsNone(result)
        self.assertEqual(recall_queries, [])
        self.assertFalse(self.bindings_directory.exists())

    def test_start_work_saves_binding_and_injects_compact_memory_context(self) -> None:
        recall_queries: list[str] = []

        def recall(query: str) -> dict[str, object]:
            recall_queries.append(query)
            return {
                "ok": True,
                "memories": [
                    {
                        "id": "memory-internal-id",
                        "title": "Recall 검색 규칙",
                        "summary": "공백으로 나눈 각 키워드를 검색하고 결과를 합친다.",
                        "matched_keywords": ["Recall", "Hook"],
                    }
                ],
                "warnings": [],
            }

        result = post_tool_use.dispatch_post_tool_use(
            self.event(),
            recall_runner=recall,
            bindings_directory=self.bindings_directory,
        )

        self.assertEqual(recall_queries, ["MemoryGraph Recall Hook"])
        self.assertEqual(
            runtime_binding.load_binding(
                "session-1", bindings_directory=self.bindings_directory
            )["run_id"],
            "RUN-34",
        )
        self.assertTrue(result["continue"])
        context = json.loads(
            result["hookSpecificOutput"]["additionalContext"]
        )
        self.assertEqual(context["run_id"], "RUN-34")
        self.assertEqual(context["memories"][0]["title"], "Recall 검색 규칙")
        self.assertNotIn("id", context["memories"][0])

    def test_recall_failure_does_not_remove_binding_or_block_codex(self) -> None:
        result = post_tool_use.dispatch_post_tool_use(
            self.event(),
            recall_runner=lambda _query: (_ for _ in ()).throw(RuntimeError("offline")),
            bindings_directory=self.bindings_directory,
        )

        binding = runtime_binding.load_binding(
            "session-1", bindings_directory=self.bindings_directory
        )
        context = json.loads(result["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(binding["run_id"], "RUN-34")
        self.assertTrue(result["continue"])
        self.assertEqual(context["memories"], [])
        self.assertIn(
            "memory_recall_failed:unexpected_error", context["warnings"]
        )

    def test_malformed_start_work_response_returns_a_safe_warning(self) -> None:
        event = self.event()
        event["tool_response"] = {"isError": False, "content": []}

        result = post_tool_use.dispatch_post_tool_use(
            event,
            bindings_directory=self.bindings_directory,
        )

        context = json.loads(result["hookSpecificOutput"]["additionalContext"])
        self.assertFalse(result["continue"])
        self.assertIn(
            "start_work_post_hook_failed:HookInputError", context["warnings"]
        )
        self.assertFalse(self.bindings_directory.exists())

    def test_binding_failure_interrupts_the_unbound_run_and_skips_recall(self) -> None:
        database_path = Path(self.temporary_directory.name) / "state.db"
        state_store.initialize_database(database_path)
        work_item = state_store.create_work_item(
            title="Binding 실패 보상",
            kind="implementation",
            goal="소유자 없는 Run을 남기지 않는다.",
            next_action="보상 함수를 실행한다.",
            actor="test",
            database_path=database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="보상 함수를 실행한다.",
            actor="test",
            reason="테스트 준비",
            database_path=database_path,
        )
        run = state_store.start_run(
            work_item["id"],
            intent="Binding 실패를 보상한다.",
            recall_query="Binding 실패 보상",
            actor="test",
            database_path=database_path,
        )
        runtime_binding.save_binding(
            session_id="session-1",
            turn_id="turn-old",
            work_item_id="WI-other",
            run_id="RUN-other",
            bindings_directory=self.bindings_directory,
        )
        event = self.event()
        event["tool_input"]["work_item_id"] = work_item["id"]
        event["tool_response"]["structuredContent"].update(
            {"id": run["id"], "work_item_id": work_item["id"]}
        )
        recall_queries: list[str] = []

        result = post_tool_use.dispatch_post_tool_use(
            event,
            recall_runner=lambda query: recall_queries.append(query) or {},
            database_path=database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertFalse(result["continue"])
        self.assertEqual(recall_queries, [])
        self.assertEqual(
            state_store.get_run(run["id"], database_path=database_path)["status"],
            "interrupted",
        )
        self.assertEqual(
            state_store.get_work_item(
                work_item["id"], database_path=database_path
            )["status"],
            "ready",
        )

    def test_finish_work_deletes_only_the_matching_current_session_binding(self) -> None:
        runtime_binding.save_binding(
            session_id="session-1",
            turn_id="turn-7",
            work_item_id="WI-12",
            run_id="RUN-34",
            bindings_directory=self.bindings_directory,
        )
        event = self.event()
        event["tool_name"] = post_tool_use.FINISH_WORK_TOOL
        event["tool_response"] = {
            "isError": False,
            "content": [],
            "structuredContent": {
                "run": {"id": "RUN-34", "status": "succeeded"},
                "work_item": {"id": "WI-12", "status": "ready"},
            },
        }

        result = post_tool_use.dispatch_post_tool_use(
            event, bindings_directory=self.bindings_directory
        )

        self.assertTrue(result["continue"])
        self.assertIsNone(
            runtime_binding.load_binding(
                "session-1", bindings_directory=self.bindings_directory
            )
        )


if __name__ == "__main__":
    unittest.main()

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

    def active_run(self) -> tuple[Path, dict[str, object]]:
        database_path = Path(self.temporary_directory.name) / "state.db"
        state_store.initialize_database(database_path)
        work_item = state_store.create_work_item(
            title="일반 도구 이벤트 기록",
            kind="verification",
            goal="PostToolUse 이벤트를 저장한다.",
            next_action="이벤트를 검증한다.",
            actor="test",
            database_path=database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="이벤트를 검증한다.",
            actor="test",
            reason="테스트 준비",
            database_path=database_path,
        )
        run = state_store.start_run(
            work_item["id"],
            intent="일반 도구 이벤트를 저장한다.",
            recall_query="PostToolUse 이벤트",
            actor="test",
            database_path=database_path,
        )
        runtime_binding.save_binding(
            session_id="session-1",
            turn_id="turn-7",
            work_item_id=work_item["id"],
            run_id=run["id"],
            bindings_directory=self.bindings_directory,
        )
        return database_path, run

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

    def test_general_tool_event_is_compactly_saved_for_active_binding(self) -> None:
        database_path, run = self.active_run()
        event = {
            "hook_event_name": "PostToolUse",
            "session_id": "session-1",
            "turn_id": "turn-7",
            "tool_use_id": "tool-use-1",
            "tool_name": "bash",
            "tool_input": {"cmd": "pytest tests/test_state_store.py"},
            "tool_response": {
                "exit_code": 0,
                "output": "42 passed",
            },
        }

        saved = post_tool_use.record_general_tool_event(
            event,
            database_path=database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertIsNotNone(saved)
        self.assertEqual(saved["run_id"], run["id"])
        self.assertEqual(saved["tool_family"], "bash")
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(saved["exit_code"], 0)
        self.assertIn("pytest tests/test_state_store.py", saved["input_summary"])
        self.assertIn("42 passed", saved["result_summary"])
        self.assertEqual(
            state_store.list_run_tool_events(
                run["id"], database_path=database_path
            ),
            [saved],
        )

    def test_trivial_navigation_and_status_commands_are_not_saved(self) -> None:
        database_path, run = self.active_run()
        commands = ["pwd", "ls -la", "cd src", "git status --short"]

        for index, command in enumerate(commands):
            with self.subTest(command=command):
                event = {
                    "session_id": "session-1",
                    "turn_id": "turn-7",
                    "tool_use_id": f"noise-{index}",
                    "tool_name": "bash",
                    "tool_input": {"cmd": command},
                    "tool_response": {"exit_code": 0, "output": "noise"},
                }
                self.assertIsNone(
                    post_tool_use.record_general_tool_event(
                        event,
                        database_path=database_path,
                        bindings_directory=self.bindings_directory,
                    )
                )

        self.assertEqual(
            state_store.list_run_tool_events(
                run["id"], database_path=database_path
            ),
            [],
        )

    def test_compound_navigation_command_is_kept_for_review(self) -> None:
        database_path, run = self.active_run()
        event = {
            "session_id": "session-1",
            "turn_id": "turn-7",
            "tool_use_id": "compound-1",
            "tool_name": "bash",
            "tool_input": {"cmd": "cd src && pytest"},
            "tool_response": {"exit_code": 0, "output": "42 passed"},
        }

        saved = post_tool_use.record_general_tool_event(
            event,
            database_path=database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertIsNotNone(saved)
        self.assertIn("cd src && pytest", saved["input_summary"])
        self.assertEqual(
            len(
                state_store.list_run_tool_events(
                    run["id"], database_path=database_path
                )
            ),
            1,
        )

    def test_general_tool_event_failure_and_apply_patch_summary_are_deterministic(self) -> None:
        database_path, _run = self.active_run()
        event = {
            "hook_event_name": "PostToolUse",
            "session_id": "session-1",
            "turn_id": "turn-7",
            "tool_use_id": "tool-use-2",
            "tool_name": "apply_patch",
            "tool_input": {
                "patch": "*** Begin Patch\n*** Update File: src/example.py\n@@\n-old\n+new\n*** End Patch"
            },
            "tool_response": {"exit_code": 1, "output": "failed"},
        }

        saved = post_tool_use.record_general_tool_event(
            event,
            database_path=database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertEqual(saved["tool_family"], "file_edit")
        self.assertEqual(saved["status"], "failed")
        self.assertIn("files=src/example.py", saved["input_summary"])
        self.assertNotIn("old", saved["input_summary"])

    def test_general_tool_event_without_active_binding_is_skipped(self) -> None:
        database_path = Path(self.temporary_directory.name) / "state.db"
        state_store.initialize_database(database_path)
        event = {
            "session_id": "session-1",
            "turn_id": "turn-7",
            "tool_name": "bash",
            "tool_input": {"cmd": "pwd"},
            "tool_response": {"exit_code": 0, "output": "/tmp"},
        }

        self.assertIsNone(
            post_tool_use.record_general_tool_event(
                event,
                database_path=database_path,
                bindings_directory=self.bindings_directory,
            )
        )

    def test_lifecycle_tools_are_not_recorded_as_general_events(self) -> None:
        database_path, run = self.active_run()
        result = post_tool_use.record_general_tool_event(
            self.event(),
            database_path=database_path,
            bindings_directory=self.bindings_directory,
        )

        self.assertIsNone(result)
        self.assertEqual(
            state_store.list_run_tool_events(
                run["id"], database_path=database_path
            ),
            [],
        )

    def test_project_hook_registers_one_matcherless_post_tool_use_entry(self) -> None:
        hooks_path = Path(__file__).resolve().parents[1] / ".codex" / "hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]

        self.assertEqual(len(hooks["PostToolUse"]), 1)
        entry = hooks["PostToolUse"][0]
        self.assertNotIn("matcher", entry)
        self.assertIn("harness-post-tool-use", entry["hooks"][0]["command"])

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
            started_status_reader=lambda _work_item_id, **_kwargs: {
                "work_item": {
                    "id": "WI-12",
                    "title": "Recall Hook 구현",
                    "status": "in_progress",
                },
                "progress": {
                    "in_progress_count": 1,
                    "ready_count": 2,
                    "done_count": 7,
                },
            },
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
        message = result["systemMessage"]
        self.assertIn("WI-12 · Recall Hook 구현", message)
        self.assertIn("RUN-34 · running", message)
        self.assertIn("Binding  : 연결됨", message)
        self.assertIn("진행 중 1 / 준비 2 / 완료 7", message)
        self.assertIn("관련 기억 1개 불러옴", message)
        self.assertNotIn("session-1", message)
        self.assertNotIn(str(self.bindings_directory), message)

    def test_recall_failure_does_not_remove_binding_or_block_codex(self) -> None:
        result = post_tool_use.dispatch_post_tool_use(
            self.event(),
            recall_runner=lambda _query: (_ for _ in ()).throw(RuntimeError("offline")),
            started_status_reader=lambda _work_item_id, **_kwargs: {
                "work_item": {"title": "Recall 실패 허용"},
                "progress": {
                    "in_progress_count": 1,
                    "ready_count": 0,
                    "done_count": 0,
                },
            },
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
        self.assertIn("관련 기억 0개 불러옴 · 경고 1개", result["systemMessage"])

    def test_started_status_reader_uses_the_state_store(self) -> None:
        database_path = Path(self.temporary_directory.name) / "status.db"
        state_store.initialize_database(database_path)
        work_item = state_store.create_work_item(
            title="사용자 시작 알림",
            kind="implementation",
            goal="현재 작업 상태를 짧게 보여준다.",
            next_action="시작 알림을 만든다.",
            actor="test",
            database_path=database_path,
        )
        state_store.change_work_item_status(
            work_item["id"],
            "ready",
            next_action="시작 알림을 만든다.",
            actor="test",
            reason="테스트 준비",
            database_path=database_path,
        )
        state_store.start_run(
            work_item["id"],
            intent="시작 상태를 표시한다.",
            recall_query="시작 상태 표시",
            actor="test",
            database_path=database_path,
        )

        status = post_tool_use.read_started_status(
            work_item["id"], database_path=database_path
        )

        self.assertEqual(status["work_item"]["title"], "사용자 시작 알림")
        self.assertEqual(status["progress"]["in_progress_count"], 1)
        self.assertEqual(status["progress"]["ready_count"], 0)

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

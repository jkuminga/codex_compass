import json
import signal
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from src.harness import runtime_binding, state_store, work_item_picker
from src.harness.hooks import user_prompt_submit


class UserPromptSubmitHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "state.db"
        state_store.initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def event(self, prompt: str = "w/ 로그인 구현") -> dict[str, str]:
        return {"hook_event_name": "UserPromptSubmit", "session_id": "session-current", "turn_id": "turn-new", "prompt": prompt}

    def create_ready_work_item(self, title: str = "로그인 구현") -> dict[str, object]:
        work_item = state_store.create_work_item(
            title=title, kind="implementation", goal="로그인을 적용한다.", next_action="로그인 화면을 구현한다.",
            acceptance_criteria=["로그인할 수 있다."], actor="test", database_path=self.database_path,
        )
        return state_store.change_work_item_status(
            work_item["id"], "ready", next_action="로그인 화면을 구현한다.", actor="test", reason="테스트 준비", database_path=self.database_path,
        )

    def test_non_work_prompt_does_not_query_the_database_or_add_context(self) -> None:
        with patch.object(state_store, "list_ready_work_items") as list_ready:
            output = user_prompt_submit.dispatch_user_prompt_submit(self.event("일반 질문"), database_path=self.database_path)
        self.assertEqual(output, {"continue": True})
        list_ready.assert_not_called()

    def test_work_prefix_is_case_insensitive(self) -> None:
        self.assertEqual(user_prompt_submit.strip_work_prefix("w/ 로그인 구현"), "로그인 구현")
        self.assertEqual(user_prompt_submit.strip_work_prefix("W/ 로그인 구현"), "로그인 구현")
        self.assertIsNone(user_prompt_submit.strip_work_prefix("work/ 로그인 구현"))

    def test_work_prompt_passes_the_selected_work_item_only(self) -> None:
        work_item = self.create_ready_work_item()
        second_work_item = self.create_ready_work_item("비밀번호 재설정")

        def write_selected_result(request_path: Path) -> None:
            request = work_item_picker.read_json_object(request_path)
            self.assertEqual(
                {item["id"] for item in request["work_items"]},
                {work_item["id"], second_work_item["id"]},
            )
            selected = next(
                item for item in request["work_items"] if item["id"] == work_item["id"]
            )
            self.assertEqual(selected["next_action"], "로그인 화면을 구현한다.")
            work_item_picker.atomic_write_json(work_item_picker.result_path_for(request_path), {
                "schema_version": 1, "request_id": request["request_id"], "session_id": request["session_id"],
                "turn_id": request["turn_id"], "created_at": work_item_picker.format_timestamp(work_item_picker.utc_now()),
                "status": "selected", "work_item_id": work_item["id"],
            })

        with patch.object(user_prompt_submit, "launch_terminal_picker", side_effect=write_selected_result):
            output = user_prompt_submit.dispatch_user_prompt_submit(self.event(), database_path=self.database_path)

        context = output["hookSpecificOutput"]["additionalContext"]
        packet = json.loads(context.split("\n", 1)[1].rsplit("\n", 1)[0])
        self.assertEqual(
            packet,
            {
                "selection_status": "selected",
                "work_item_id": work_item["id"],
                "title": work_item["title"],
                "is_draft": False,
                "request": "로그인 구현",
                "recovery": {
                    "status": "none",
                    "run_id": None,
                    "binding_deleted": False,
                    "database_ok": True,
                    "active_work_items": [],
                    "warnings": [],
                },
            },
        )
        selections_directory = self.root / "selections"
        self.assertFalse(selections_directory.exists() and list(selections_directory.iterdir()))

    def test_work_prompt_recovers_previous_turn_before_building_picker_context(self) -> None:
        work_item = self.create_ready_work_item()
        run = state_store.start_run(
            work_item["id"],
            intent="이전 Turn에서 중단된 작업",
            recall_query="UPS 복구",
            actor="test",
            database_path=self.database_path,
        )
        bindings_directory = self.root / "bindings"
        runtime_binding.save_binding(
            session_id="session-current",
            turn_id="turn-previous",
            work_item_id=work_item["id"],
            run_id=run["id"],
            bindings_directory=bindings_directory,
        )

        def write_selected_result(request_path: Path) -> None:
            request = work_item_picker.read_json_object(request_path)
            self.assertIn(
                work_item["id"], {item["id"] for item in request["work_items"]}
            )
            work_item_picker.atomic_write_json(
                work_item_picker.result_path_for(request_path),
                {
                    "schema_version": 1,
                    "request_id": request["request_id"],
                    "session_id": request["session_id"],
                    "turn_id": request["turn_id"],
                    "created_at": work_item_picker.format_timestamp(work_item_picker.utc_now()),
                    "status": "selected",
                    "work_item_id": work_item["id"],
                },
            )

        with (
            patch.object(user_prompt_submit, "launch_terminal_picker", side_effect=write_selected_result),
            patch.dict("os.environ", {"HARNESS_BINDINGS_DIRECTORY": str(bindings_directory)}),
        ):
            output = user_prompt_submit.dispatch_user_prompt_submit(
                self.event(), database_path=self.database_path
            )

        context = output["hookSpecificOutput"]["additionalContext"]
        packet = json.loads(context.split("\n", 1)[1].rsplit("\n", 1)[0])
        self.assertEqual(packet["recovery"]["status"], "recovered")
        self.assertEqual(packet["recovery"]["run_id"], run["id"])
        self.assertTrue(packet["recovery"]["binding_deleted"])
        self.assertEqual(
            state_store.get_run(run["id"], database_path=self.database_path)["status"],
            "interrupted",
        )

    def test_work_prompt_reports_other_session_without_taking_it_over(self) -> None:
        work_item = self.create_ready_work_item()
        run = state_store.start_run(
            work_item["id"],
            intent="다른 세션의 작업",
            recall_query="세션 충돌",
            actor="test",
            database_path=self.database_path,
        )
        bindings_directory = self.root / "bindings"
        runtime_binding.save_binding(
            session_id="session-other",
            turn_id="turn-other",
            work_item_id=work_item["id"],
            run_id=run["id"],
            bindings_directory=bindings_directory,
        )

        def write_cancelled_result(request_path: Path) -> None:
            request = work_item_picker.read_json_object(request_path)
            work_item_picker.atomic_write_json(
                work_item_picker.result_path_for(request_path),
                {
                    "schema_version": 1,
                    "request_id": request["request_id"],
                    "session_id": request["session_id"],
                    "turn_id": request["turn_id"],
                    "created_at": work_item_picker.format_timestamp(work_item_picker.utc_now()),
                    "status": "cancelled",
                },
            )

        with (
            patch.object(user_prompt_submit, "launch_terminal_picker", side_effect=write_cancelled_result),
            patch.dict("os.environ", {"HARNESS_BINDINGS_DIRECTORY": str(bindings_directory)}),
        ):
            output = user_prompt_submit.dispatch_user_prompt_submit(
                self.event(), database_path=self.database_path
            )

        context = output["hookSpecificOutput"]["additionalContext"]
        packet = json.loads(context.split("\n", 1)[1].rsplit("\n", 1)[0])
        self.assertEqual(packet["selection_status"], "cancelled")
        self.assertEqual(packet["recovery"]["status"], "warning")
        self.assertEqual(
            packet["recovery"]["active_work_items"][0]["ownership"],
            "other_session",
        )
        self.assertEqual(
            state_store.get_run(run["id"], database_path=self.database_path)["status"],
            "running",
        )

    def test_selected_picker_result_is_closed_by_the_hook_after_it_is_read(self) -> None:
        work_item = self.create_ready_work_item()

        def selected_result(request_path: Path) -> dict[str, object]:
            request = work_item_picker.read_json_object(request_path)
            return {
                "schema_version": 1,
                "request_id": request["request_id"],
                "session_id": request["session_id"],
                "turn_id": request["turn_id"],
                "created_at": work_item_picker.format_timestamp(work_item_picker.utc_now()),
                "status": "selected",
                "work_item_id": work_item["id"],
            }

        with (
            patch.object(user_prompt_submit, "launch_terminal_picker"),
            patch.object(user_prompt_submit, "wait_for_selection_result", side_effect=selected_result),
            patch.object(user_prompt_submit, "close_terminal_picker", create=True) as close_picker,
        ):
            user_prompt_submit.dispatch_user_prompt_submit(
                self.event(), database_path=self.database_path
            )

        close_picker.assert_called_once()

    def test_work_prompt_includes_a_draft_and_marks_it_in_the_context_packet(self) -> None:
        draft = state_store.create_draft_work_item(
            title="초안 선택",
            kind="research",
            goal="선택기에서 Draft 표시를 확인한다.",
            actor="test",
            database_path=self.database_path,
        )

        def write_selected_result(request_path: Path) -> None:
            request = work_item_picker.read_json_object(request_path)
            item = next(item for item in request["work_items"] if item["id"] == draft["id"])
            self.assertTrue(item["is_draft"])
            self.assertEqual(item["kind"], "research")
            work_item_picker.atomic_write_json(work_item_picker.result_path_for(request_path), {
                "schema_version": 1, "request_id": request["request_id"], "session_id": request["session_id"],
                "turn_id": request["turn_id"], "created_at": work_item_picker.format_timestamp(work_item_picker.utc_now()),
                "status": "selected", "work_item_id": draft["id"],
            })

        with patch.object(user_prompt_submit, "launch_terminal_picker", side_effect=write_selected_result):
            output = user_prompt_submit.dispatch_user_prompt_submit(self.event(), database_path=self.database_path)

        context = output["hookSpecificOutput"]["additionalContext"]
        packet = json.loads(context.split("\n", 1)[1].rsplit("\n", 1)[0])
        self.assertTrue(packet["is_draft"])
        self.assertEqual(packet["work_item_id"], draft["id"])

    def test_rejects_result_for_an_unlisted_work_item(self) -> None:
        request_path = user_prompt_submit.write_selection_request(
            request_id="request", session_id="session", turn_id="turn", work_items=[], selections_directory=self.root / "selections",
        )
        request = work_item_picker.read_json_object(request_path)
        result = {"schema_version": 1, "request_id": request["request_id"], "session_id": request["session_id"],
                  "turn_id": request["turn_id"], "created_at": work_item_picker.format_timestamp(work_item_picker.utc_now()),
                  "status": "selected", "work_item_id": "WI-other"}
        with self.assertRaises(user_prompt_submit.HookInputError):
            user_prompt_submit.validate_selection_result(request_path, result, request_text="구현")

    def test_expired_orphaned_selection_files_are_removed(self) -> None:
        directory = self.root / "selections"
        request_path = user_prompt_submit.write_selection_request(
            request_id="expired", session_id="session", turn_id="turn", work_items=[], selections_directory=directory, timeout_seconds=-1,
        )
        work_item_picker.atomic_write_json(work_item_picker.result_path_for(request_path), {"unused": True})
        user_prompt_submit.cleanup_expired_selection_files(directory)
        self.assertFalse(request_path.exists())
        self.assertFalse(work_item_picker.result_path_for(request_path).exists())

    def test_picker_writes_a_cancelled_result_for_fzf_cancellation(self) -> None:
        now = work_item_picker.utc_now()
        request_path = self.root / "selection-request.request.json"
        work_item_picker.atomic_write_json(request_path, {
            "schema_version": 1, "request_id": "request", "session_id": "session", "turn_id": "turn",
            "created_at": work_item_picker.format_timestamp(now), "expires_at": work_item_picker.format_timestamp(now + timedelta(minutes=5)),
            "work_items": [{"id": "WI-1", "title": "선택", "goal": "고른다", "priority": "high"}],
        })
        with patch.object(work_item_picker, "run_fzf", return_value=None):
            work_item_picker.run_picker(request_path)
        result = work_item_picker.read_json_object(work_item_picker.result_path_for(request_path))
        self.assertEqual(result["status"], "cancelled")
        self.assertNotIn("work_item_id", result)

    def test_picker_reports_a_missing_fzf_executable(self) -> None:
        with patch.object(work_item_picker.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(work_item_picker.SelectionFileError, "fzf could not be started"):
                work_item_picker.run_fzf(["WI-1\thigh\t선택\t고른다"])

    def test_picker_treats_fzf_escape_as_cancellation(self) -> None:
        completed = work_item_picker.subprocess.CompletedProcess(
            args=["fzf"], returncode=130, stdout=""
        )

        with patch.object(work_item_picker.subprocess, "run", return_value=completed):
            selected = work_item_picker.run_fzf(["WI-1\thigh\t선택\t고른다"])

        self.assertIsNone(selected)

    def test_picker_cli_returns_success_and_writes_cancelled_for_fzf_escape(self) -> None:
        now = work_item_picker.utc_now()
        request_path = self.root / "selection-request.request.json"
        work_item_picker.atomic_write_json(request_path, {
            "schema_version": 1, "request_id": "request", "session_id": "session", "turn_id": "turn",
            "created_at": work_item_picker.format_timestamp(now), "expires_at": work_item_picker.format_timestamp(now + timedelta(minutes=5)),
            "work_items": [{"id": "WI-1", "title": "선택", "goal": "고른다", "priority": "high"}],
        })
        completed = work_item_picker.subprocess.CompletedProcess(
            args=["fzf"], returncode=130, stdout=""
        )

        with (
            patch.object(work_item_picker.subprocess, "run", return_value=completed),
            patch.object(work_item_picker.sys, "argv", ["work-item-picker", "--request", str(request_path)]),
        ):
            exit_code = work_item_picker.main()

        result = work_item_picker.read_json_object(
            work_item_picker.result_path_for(request_path)
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "cancelled")

    def test_picker_configures_fzf_with_labels_and_selection_guidance(self) -> None:
        line = "WI-1\tREADY   | high     | implementation | 로그인 구현 / 로그인을 적용한다."
        completed = work_item_picker.subprocess.CompletedProcess(
            args=["fzf"], returncode=0, stdout=f"{line}\n"
        )

        with patch.object(work_item_picker.subprocess, "run", return_value=completed) as run:
            selected = work_item_picker.run_fzf([line])

        command = run.call_args.args[0]
        self.assertEqual(selected, line)
        self.assertIn("--border-label= Harness · WorkItem 선택 ", command)
        self.assertIn("--with-nth=2", command)
        self.assertIn("--nth=2,6,7", command)
        self.assertIn("--layout=reverse", command)
        self.assertIn("--header-first", command)
        self.assertIn("--header-border=bottom", command)
        self.assertIn("--footer-border=top", command)
        self.assertIn("--info=inline-right", command)
        self.assertIn("--pointer=▶", command)
        header = next(option for option in command if option.startswith("--header="))
        self.assertIn("상태    | 우선순위 | 종류          | 제목 / 목표", header)
        footer = next(option for option in command if option.startswith("--footer="))
        self.assertIn("Enter 선택 · Esc 취소", footer)
        preview = next(option for option in command if option.startswith("--preview="))
        self.assertIn("◆ 제목", preview)
        self.assertIn("◆ 목표", preview)
        self.assertIn("◆ 다음 행동", preview)
        self.assertNotIn("선택한 WorkItem\\n\\n", preview)
        self.assertIn("{6}", preview)
        self.assertIn("{7}", preview)
        self.assertIn("{8}", preview)
        self.assertIn("--preview-window=down:50%,border-top,wrap", command)

    def test_picker_formats_visible_columns_with_fixed_separators(self) -> None:
        line = work_item_picker.format_fzf_line(
            {
                "id": "WI-1",
                "is_draft": False,
                "priority": "high",
                "kind": "bug",
                "title": "로그인\t구현",
                "goal": "로그인한다.\n검증한다.",
                "next_action": "로그인 테스트를 실행한다.",
            }
        )

        self.assertEqual(
            line,
            "WI-1\tREADY   | high     | bug            | 로그인 구현 / 로그인한다. 검증한다."
            "\tREADY\thigh\tbug\t로그인 구현\t로그인한다. 검증한다.\t로그인 테스트를 실행한다.",
        )

    def test_terminal_picker_shows_guidance_when_terminal_cannot_access_desktop(self) -> None:
        request_path = self.root / "selections" / "selection.request.json"
        with (
            patch.object(
                user_prompt_submit,
                "_terminal_process_ids",
                side_effect=[{100}, {100, 200}],
            ),
            patch.object(user_prompt_submit.subprocess, "run"),
        ):
            user_prompt_submit.launch_terminal_picker(request_path)

        launcher = work_item_picker.terminal_launcher_path_for(request_path)
        command = launcher.read_text(encoding="utf-8")
        self.assertIn("Terminal이 프로젝트가 있는 Desktop 폴더에 접근할 수 없습니다.", command)
        self.assertIn("Codex에서 /stop으로 현재 실행을 종료하세요.", command)
        self.assertIn("if ! cd", command)
        self.assertIn("test -r", command)
        self.assertIn("WorkItem 선택기를 준비하는 중", command)
        self.assertIn(".venv/bin/python", command)
        self.assertIn("uv run --quiet", command)

    def test_terminal_picker_opens_a_dedicated_terminal_instance(self) -> None:
        request_path = self.root / "selections" / "selection.request.json"
        with (
            patch.object(
                user_prompt_submit,
                "_terminal_process_ids",
                side_effect=[{100}, {100, 200}],
            ),
            patch.object(user_prompt_submit.subprocess, "run") as run,
        ):
            user_prompt_submit.launch_terminal_picker(request_path)

        launcher = work_item_picker.terminal_launcher_path_for(request_path)
        self.assertEqual(
            run.call_args.args[0],
            ["open", "-n", "-a", "Terminal", str(launcher)],
        )
        self.assertEqual(
            work_item_picker.terminal_process_id_path_for(request_path).read_text(
                encoding="utf-8"
            ),
            "200\n",
        )

    def test_hook_terminates_only_the_recorded_picker_terminal_instance(self) -> None:
        request_path = self.root / "selections" / "selection.request.json"
        process_id_path = work_item_picker.terminal_process_id_path_for(request_path)
        process_id_path.parent.mkdir()
        process_id_path.write_text("4321\n", encoding="utf-8")

        with (
            patch.object(
                user_prompt_submit, "_terminal_process_ids", return_value={4321}
            ),
            patch.object(user_prompt_submit.time, "sleep") as sleep,
            patch.object(user_prompt_submit.os, "kill") as kill,
        ):
            user_prompt_submit.close_terminal_picker(request_path)

        sleep.assert_called_once_with(0.2)
        kill.assert_called_once_with(4321, signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()

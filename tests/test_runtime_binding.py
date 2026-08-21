import tempfile
import unittest
from pathlib import Path

from src.harness import runtime_binding


class RuntimeBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.bindings_directory = (
            Path(self.temporary_directory.name) / "runtime" / "bindings"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_session_binding_can_be_saved_found_and_deleted(self) -> None:
        binding = runtime_binding.save_binding(
            session_id="session-123",
            turn_id="turn-456",
            work_item_id="WI-12",
            run_id="RUN-34",
            bindings_directory=self.bindings_directory,
        )

        self.assertEqual(binding["session_id"], "session-123")
        self.assertEqual(
            runtime_binding.load_binding(
                "session-123", bindings_directory=self.bindings_directory
            ),
            binding,
        )
        self.assertEqual(
            runtime_binding.find_run_owner(
                "RUN-34", bindings_directory=self.bindings_directory
            ),
            binding,
        )
        self.assertTrue(
            runtime_binding.delete_binding(
                "session-123",
                expected_run_id="RUN-34",
                bindings_directory=self.bindings_directory,
            )
        )
        self.assertIsNone(
            runtime_binding.load_binding(
                "session-123", bindings_directory=self.bindings_directory
            )
        )

    def test_binding_guards_against_replacement_and_path_traversal(self) -> None:
        runtime_binding.save_binding(
            session_id="session-123",
            turn_id="turn-1",
            work_item_id="WI-12",
            run_id="RUN-34",
            bindings_directory=self.bindings_directory,
        )

        with self.assertRaises(runtime_binding.BindingConflictError):
            runtime_binding.save_binding(
                session_id="session-123",
                turn_id="turn-2",
                work_item_id="WI-99",
                run_id="RUN-99",
                bindings_directory=self.bindings_directory,
            )
        with self.assertRaises(runtime_binding.BindingConflictError):
            runtime_binding.delete_binding(
                "session-123",
                expected_run_id="RUN-99",
                bindings_directory=self.bindings_directory,
            )
        with self.assertRaises(runtime_binding.RuntimeBindingError):
            runtime_binding.load_binding(
                "../outside", bindings_directory=self.bindings_directory
            )

        binding = runtime_binding.load_binding(
            "session-123", bindings_directory=self.bindings_directory
        )
        self.assertIsNotNone(binding)
        self.assertEqual(binding["run_id"], "RUN-34")


if __name__ == "__main__":
    unittest.main()

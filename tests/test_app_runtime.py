import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class RuntimeTests(unittest.TestCase):
    def runtime(self):
        self.assertIsNotNone(
            importlib.util.find_spec("app_runtime"), "Frozen runtime adapter is missing"
        )
        import app_runtime

        return app_runtime

    def test_source_child_uses_interpreter_and_original_entry_points(self):
        runtime = self.runtime()
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            runtime.child_command(
                "macro", ["play", "example.json"], python="python.exe"
            ),
            ["python.exe", str(root / "macro_tool.py"), "play", "example.json"],
        )
        with self.assertRaises(ValueError):
            runtime.child_command("other", [])

    def test_frozen_children_dispatch_roles_without_opening_gui(self):
        runtime = self.runtime()
        with (
            patch.object(runtime.sys, "frozen", True, create=True),
            patch.object(runtime.sys, "executable", "C:/Tools/BMS.exe"),
        ):
            for role in ("launcher", "server", "macro"):
                self.assertEqual(
                    runtime.child_command(role, ["--test"]),
                    ["C:/Tools/BMSWorker.exe", "--internal-role", role, "--test"],
                )

    def test_frozen_data_is_writable_per_user_and_override_is_explicit(self):
        runtime = self.runtime()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                runtime.project_root(frozen=True, environ={"LOCALAPPDATA": tmp}),
                root / "BMSAutomation",
            )
            self.assertEqual(
                runtime.project_root(
                    frozen=True, environ={"BMS_PROJECT_DIR": str(root)}
                ),
                root,
            )
            with self.assertRaisesRegex(ValueError, "absolute"):
                runtime.project_root(
                    frozen=True, environ={"BMS_PROJECT_DIR": "relative"}
                )

    def test_initialize_only_seeds_public_defaults_and_preserves_private_files(self):
        runtime = self.runtime()
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "data"
            project.mkdir()
            (project / ".env").write_text("# private operator settings\n")
            (project / "targets.json").write_text("[]")
            runtime.initialize_project(project)
            self.assertEqual(
                (project / ".env").read_text(), "# private operator settings\n"
            )
            self.assertEqual((project / "targets.json").read_text(), "[]")
            self.assertTrue((project / "scripts/macros/example.json").is_file())
            self.assertTrue((project / ".env.example").is_file())
            self.assertFalse((project / "assets/login_anchor.png").exists())

    def test_unwritable_data_directory_has_actionable_startup_error(self):
        import app_entry

        with (
            patch(
                "app_entry.initialize_project", side_effect=PermissionError("locked")
            ),
            patch("app_entry.report_startup_error") as report,
        ):
            self.assertEqual(app_entry.main([]), 1)
        self.assertIn("folder permissions", report.call_args.args[0])

    def test_internal_server_dispatch_does_not_import_qt(self):
        self.assertIsNotNone(
            importlib.util.find_spec("app_entry"), "Packaged entry point is missing"
        )
        import app_entry

        with (
            patch("server.main", return_value=0) as run,
            patch("app_entry.initialize_project"),
            patch.dict(os.environ, {"BMS_PROJECT_DIR": tempfile.gettempdir()}),
        ):
            self.assertEqual(app_entry.main(["--internal-role", "server"]), 0)
            run.assert_called_once()


if __name__ == "__main__":
    unittest.main()

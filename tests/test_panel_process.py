import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from config import Settings
from launcher import prepare_configuration, connect_tunnel
from panel_process import ProcessController
from runtime_control import ControlSession


class PanelProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".env").write_text(
            "CHANNEL_ACCESS_TOKEN=private-token\nLINE_CHANNEL_SECRET=private-secret\n"
        )

    def test_sessions_are_isolated_and_invalid_paths_are_rejected(self):
        first, second = ControlSession(self.root), ControlSession(self.root)
        first.publish("online", port=5000)
        first.request_stop()
        self.assertTrue(first.stop_requested())
        self.assertFalse(second.stop_requested())
        self.assertEqual(first.read_status()["port"], 5000)
        self.assertEqual(second.read_status(), {})
        for token in ("../escape", "", "x" * 32):
            with self.subTest(token=token), self.assertRaises(ValueError):
                ControlSession(self.root, token)

    def test_stop_watcher_runs_once_and_joins(self):
        session = ControlSession(self.root)
        calls = []
        stopped = threading.Event()
        with session.watch(lambda: (calls.append(True), stopped.set())):
            session.request_stop()
            self.assertTrue(stopped.wait(2))
        self.assertEqual(calls, [True])

    def test_status_publish_recovers_from_temporary_windows_file_lock(self):
        session = ControlSession(self.root)
        session.publish("preparing")
        replace = os.replace
        attempts = []

        def locked_replace(source, destination):
            attempts.append(True)
            if len(attempts) < 3:
                self.assertEqual(session.read_status()["stage"], "preparing")
                raise PermissionError(13, "Windows temporarily locked the status file")
            return replace(source, destination)

        with patch("runtime_control.os.replace", side_effect=locked_replace):
            session.publish("online", port=5000)
        self.assertEqual(session.read_status()["stage"], "online")
        self.assertEqual(session.read_status()["port"], 5000)
        self.assertEqual(list(session.directory.glob("*.tmp")), [])

    def test_persistent_status_lock_has_actionable_error_and_preserves_previous_status(
        self,
    ):
        session = ControlSession(self.root)
        session.publish("preparing")
        with patch(
            "runtime_control.os.replace", side_effect=PermissionError(13, "locked")
        ):
            with self.assertRaisesRegex(RuntimeError, "OneDrive"):
                session.publish("online")
        self.assertEqual(session.read_status()["stage"], "preparing")
        self.assertEqual(list(session.directory.glob("*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Requires actual Windows file sharing")
    def test_windows_open_status_reader_can_close_while_publisher_retries(self):
        session = ControlSession(self.root)
        session.publish("preparing")
        reader = session.status_path.open("rb")
        release = threading.Timer(0.12, reader.close)
        release.start()
        try:
            session.publish("online")
        finally:
            release.join(timeout=2)
            reader.close()
        self.assertEqual(session.read_status()["stage"], "online")

    def test_noninteractive_setup_reports_missing_credentials_without_prompt(self):
        (self.root / ".env").write_text("")
        with patch("launcher.input") as prompt, patch(
            "launcher.subprocess.Popen"
        ) as editor:
            with self.assertRaises(RuntimeError):
                prepare_configuration(self.root, interactive=False)
        prompt.assert_not_called()
        editor.assert_not_called()

    def test_noninteractive_tunnel_auth_failure_does_not_open_editor(self):
        from ngrok_runner import NgrokStartupError

        with patch(
            "launcher.start_tunnel", side_effect=NgrokStartupError("ERR_NGROK_4018", 1)
        ), patch("launcher.input") as prompt, patch(
            "launcher.webbrowser.open"
        ) as browser:
            with self.assertRaises(NgrokStartupError):
                connect_tunnel(
                    Settings(project_dir=self.root), self.root, interactive=False
                )
        prompt.assert_not_called()
        browser.assert_not_called()

    def test_real_child_start_ready_stop_and_redacted_logs(self):
        # Real subprocess, project-specific control files, and a private value on stdout.
        (self.root / "launcher.py").write_text(
            "import sys,time\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0,{str(Path(__file__).resolve().parents[1])!r})\n"
            "from runtime_control import ControlSession\n"
            "control=ControlSession(Path.cwd(),sys.argv[sys.argv.index('--control-session')+1])\n"
            "print('starting private-token',flush=True)\n"
            "control.publish('online',port=5000,webhook='https://bms.ngrok.app/callback')\n"
            "while not control.stop_requested(): time.sleep(.02)\n"
            "print('stopped',flush=True)\n"
        )
        controller = ProcessController(self.root, python=sys.executable)
        self.addCleanup(controller.close)
        controller.start_bot()
        first_session = controller.session.id
        with self.assertRaises(RuntimeError):
            controller.start_bot()
        self.wait_for(lambda: controller.state == "online")
        self.assertIn("bms.ngrok.app", controller.status["webhook"])
        controller.stop()
        self.wait_for(lambda: not controller.busy)
        self.assertEqual(controller.state, "stopped")
        logs = (self.root / ".runtime" / "control_panel.log").read_text()
        self.assertNotIn("private-token", logs)
        self.assertIn("[hidden]", logs)
        controller.start_bot()
        self.assertNotEqual(controller.session.id, first_session)
        self.wait_for(lambda: controller.state == "online")
        controller.stop()
        self.wait_for(lambda: not controller.busy)

    def test_oversized_output_is_omitted_and_queue_is_bounded(self):
        (self.root / "launcher.py").write_text(
            "print('private-token'*20000,flush=True)\nfor n in range(2000): print('row',n,flush=True)\n"
        )
        controller = ProcessController(self.root, python=sys.executable)
        self.addCleanup(controller.close)
        controller.start_bot()
        self.wait_for(lambda: not controller.busy)
        self.assertLessEqual(controller.events.qsize(), 500)
        log = (self.root / ".runtime" / "control_panel.log").read_text()
        self.assertNotIn("private-token", log)
        self.assertIn("Oversized", log)

    def wait_for(self, condition):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            if condition():
                return
            time.sleep(0.02)
        self.fail("Child process did not reach the expected lifecycle state.")


if __name__ == "__main__":
    unittest.main()

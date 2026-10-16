"""Offscreen Qt checks; desktop automation and LINE are never contacted."""

import importlib.util
import json
import os
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication, QLineEdit

from panel_settings import ENV_FIELDS


class Controller:
    def __init__(self):
        self.events = queue.Queue()
        self.state, self.mode, self.status = "stopped", "bot", {}
        self.starts, self.stops, self.closed = 0, 0, False

    @property
    def busy(self):
        return self.state in {"starting", "online", "stopping", "recording", "playing"}

    def start_bot(self):
        self.starts += 1
        self.state = "starting"

    def start_macro(self, command, name):
        self.mode = command
        self.state = "recording" if command == "record" else "playing"
        self.macro = name

    def stop(self):
        self.stops += 1
        if self.busy:
            self.state = "stopping"

    def finish(self, code=0):
        self.state = "stopped" if not code else "error"
        self.events.put(("finished", {"mode": self.mode, "code": code}))

    def close(self):
        self.closed = True


class QtPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def until(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate(), "Qt condition timed out")

    def setUp(self):
        self.assertIsNotNone(
            importlib.util.find_spec("qt_panel"), "Qt interface is missing"
        )
        from qt_panel import QtControlPanel

        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        source = Path(__file__).resolve().parents[1]
        (self.project / ".env.example").write_bytes(
            (source / ".env.example").read_bytes()
        )
        macros = self.project / "scripts/macros"
        macros.mkdir(parents=True)
        (macros / "example.json").write_text('[{"action":"sleep","seconds":0.1}]')
        (self.project / "targets.json").write_text("[]")
        self.controller = Controller()
        self.panel = QtControlPanel(self.project, controller=self.controller)
        self.panel.show()
        self.until(lambda: self.panel.ready and not self.panel.working)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.controller.finish()
        if not self.panel.disposed:
            self.panel.request_close(confirm=False)
            self.until(lambda: self.panel.disposed)
        self.app.processEvents()
        self.tmp.cleanup()

    def test_pages_and_settings_are_accessible(self):
        self.assertEqual(
            set(self.panel.pages),
            {"Dashboard", "Settings", "Targets", "Macros", "Help"},
        )
        self.assertEqual(set(self.panel.settings.entries), set(ENV_FIELDS))
        for page in self.panel.pages:
            self.panel.show_page(page)
            self.assertEqual(self.panel.current_page, page)
        self.assertEqual(self.panel.settings.tabs.count(), 3)
        self.assertEqual(
            self.panel.settings.entries["BMS_PASSWORD"].echoMode(),
            QLineEdit.EchoMode.Password,
        )
        self.panel.resize(800, 600)
        self.app.processEvents()
        self.assertLessEqual(self.panel.minimumWidth(), 800)

    def test_worker_keeps_event_loop_alive_and_completes_on_gui_thread(self):
        release = threading.Event()
        ticks, callback_threads = [], []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start(5)
        self.addCleanup(timer.stop)
        self.addCleanup(release.set)
        self.panel.background(
            lambda: release.wait(2),
            lambda _: callback_threads.append(QThread.currentThread()),
        )
        self.until(lambda: len(ticks) >= 5)
        self.assertTrue(self.panel.working)
        self.panel.show_page("Help")
        self.assertEqual(self.panel.current_page, "Help")
        release.set()
        self.until(lambda: bool(callback_threads))
        self.assertEqual(callback_threads, [self.app.thread()])

    def test_worker_error_is_reported_and_controls_recover(self):
        with patch.object(self.panel, "show_error") as error:
            self.panel.background(
                lambda: (_ for _ in ()).throw(ValueError("Bad setting"))
            )
            self.until(lambda: not self.panel.working)
            error.assert_called_once()
        self.assertTrue(self.panel.start_button.isEnabled())

    def test_settings_save_and_stale_edits_preserve_external_file(self):
        entry = self.panel.settings.entries["LINE_WEBP_QUALITY"]
        entry.setText("85")
        self.panel.save_settings()
        self.until(lambda: not self.panel.working and not self.panel.settings.dirty)
        self.assertEqual(self.panel.settings.saved["LINE_WEBP_QUALITY"], "85")
        path = self.project / ".env"
        path.write_text(path.read_text() + "\n# external change\n")
        entry.setText("90")
        with patch.object(self.panel, "show_error") as error:
            self.panel.save_settings()
            self.until(lambda: not self.panel.working)
            error.assert_called_once()
        self.assertIn("# external change", path.read_text())
        self.assertTrue(self.panel.settings.dirty)

    def test_dirty_edit_survives_background_refresh(self):
        self.panel.settings.entries["BMS_USERNAME"].setText("unsaved operator")
        self.panel.refresh_setup()
        self.until(lambda: not self.panel.working)
        self.assertEqual(
            self.panel.settings.values()["BMS_USERNAME"], "unsaved operator"
        )

    def test_target_edit_save_and_macro_default(self):
        page = self.panel.targets
        page.target_id.setText("09D")
        page.target_name.setText("Daily report")
        page.target_macro.setCurrentText("example.json")
        page.add_row()
        self.panel.save_targets()
        self.until(lambda: not self.panel.working and not page.dirty)
        self.assertEqual(
            json.loads((self.project / "targets.json").read_text())[0]["id"], "09D"
        )
        self.panel.macros.choose_default("login", "example.json")
        self.assertEqual(
            self.panel.settings.values()["LOGIN_MACRO_SCRIPT"], "example.json"
        )
        self.assertEqual(self.panel.current_page, "Settings")

    def test_macro_start_and_stop_use_existing_controller(self):
        self.panel.start_macro("play", "example.json")
        self.until(lambda: not self.panel.working)
        self.assertEqual(self.controller.macro, "example.json")
        self.panel.stop_bot()
        self.until(lambda: self.controller.state == "stopping")
        self.controller.finish()

    def test_safe_settings_restart_waits_for_child_exit(self):
        self.controller.state = "online"
        self.panel.settings.entries["LINE_WEBP_QUALITY"].setText("86")
        self.panel.save_settings()
        self.until(lambda: self.controller.state == "stopping")
        self.assertEqual(self.controller.starts, 0)
        self.controller.finish()
        self.until(lambda: self.controller.starts == 1)
        self.assertEqual(self.panel.settings.saved["LINE_WEBP_QUALITY"], "86")

    def test_logs_are_bounded_and_process_error_is_visible(self):
        for index in range(1000):
            self.controller.events.put(("log", f"line {index}"))
        self.controller.finish(1)
        self.until(lambda: "line 999" in self.panel.log.toPlainText())
        self.assertLessEqual(self.panel.log.document().blockCount(), 800)
        self.until(lambda: "error" in self.panel.notice.text().lower())

    def test_failed_stop_can_be_retried_after_shutdown_error(self):
        self.controller.state = "online"
        with (
            patch.object(
                self.controller, "stop", side_effect=PermissionError("locked")
            ),
            patch.object(self.panel, "show_error"),
        ):
            self.panel.request_close(confirm=False)
            self.until(lambda: not self.panel.jobs)
        self.assertFalse(self.panel.closing)
        self.assertTrue(self.panel.stop_button.isEnabled())
        self.panel.stop_bot()
        self.until(lambda: self.controller.state == "stopping")
        self.controller.finish()

    def test_minimum_window_keeps_macro_and_target_controls_readable(self):
        self.panel.resize(760, 560)
        for page in ("Macros", "Targets"):
            self.panel.show_page(page)
            self.until(lambda: not self.panel.working)
            self.app.processEvents()
        anchor = self.panel.macros.anchor_button
        self.assertGreaterEqual(anchor.height(), anchor.minimumSizeHint().height())
        from PySide6.QtWidgets import QPushButton

        for control in self.panel.targets.editor.findChildren(QPushButton):
            self.assertGreaterEqual(
                control.height(), control.minimumSizeHint().height()
            )

    def test_malformed_macros_do_not_disable_settings_or_refresh(self):
        directory = self.project / "scripts/macros"
        (directory / "deep.json").write_text("[" * 10000 + "]" * 10000)
        (directory / "huge.json").write_text(
            '[{"action":"sleep","seconds":' + "9" * 400 + "}]"
        )
        path = self.project / ".env"
        path.write_text(path.read_text() + "\nLOGIN_MACRO_SCRIPT=deep.json\n")
        with patch.object(self.panel, "show_error") as error:
            self.panel.refresh_setup()
            self.until(lambda: not self.panel.working)
            error.assert_not_called()
        self.assertTrue(self.panel.ready)
        self.assertTrue(self.panel.settings.save_button.isEnabled())
        statuses = {
            self.panel.macros.table.item(i, 0).text(): self.panel.macros.table.item(
                i, 2
            ).text()
            for i in range(self.panel.macros.table.rowCount())
        }
        self.assertEqual(statuses["deep.json"], "Needs repair")
        self.assertEqual(statuses["huge.json"], "Needs repair")

    def test_gui_never_waits_for_process_launch_lock(self):
        from panel_process import ProcessController

        controller = ProcessController(self.project)
        self.panel.controller = controller
        self.panel.bridge.controller = controller
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def hold_launch_lock():
            with controller.lock:
                entered.set()
                release.wait(0.5)

        holder = threading.Thread(target=hold_launch_lock)
        holder.start()
        self.assertTrue(entered.wait(1))
        started = time.monotonic()
        self.panel.refresh_controls()
        elapsed = time.monotonic() - started
        release.set()
        holder.join(1)
        self.assertLess(elapsed, 0.1, "GUI waited for the process launch lock")

    def test_stop_during_validation_cancels_pending_settings_restart(self):
        self.controller.state = "online"
        self.panel.settings.entries["LINE_WEBP_QUALITY"].setText("88")
        entered, release = threading.Event(), threading.Event()
        original = self.panel.store.validate_settings

        def validate(values):
            entered.set()
            release.wait(2)
            return original(values)

        self.addCleanup(release.set)
        with patch.object(self.panel.store, "validate_settings", side_effect=validate):
            self.panel.save_settings()
            self.until(entered.is_set)
            self.panel.stop_bot()
            release.set()
            self.until(lambda: not self.panel.working)
            self.controller.finish()
            self.until(
                lambda: self.panel.pending_restart is None and not self.panel.working
            )
            self.assertEqual(self.controller.starts, 0)
            self.assertTrue(self.panel.settings.dirty)

    def test_macro_import_and_anchor_use_existing_store(self):
        source = self.project / "import.json"
        source.write_text('[{"action":"sleep","seconds":0.2}]')
        with (
            patch(
                "qt_pages.QFileDialog.getOpenFileName", return_value=(str(source), "")
            ),
            patch(
                "qt_pages.QInputDialog.getText", return_value=("imported.json", True)
            ),
        ):
            self.panel.macros.import_file()
            self.until(lambda: not self.panel.working)
        self.assertTrue((self.project / "scripts/macros/imported.json").is_file())
        from PIL import Image

        image = self.project / "crop.png"
        Image.new("RGB", (10, 10), "blue").save(image)
        with patch(
            "qt_pages.QFileDialog.getOpenFileName", return_value=(str(image), "")
        ):
            self.panel.macros.import_anchor()
            self.until(lambda: not self.panel.working)
        self.assertTrue((self.project / "assets/login_anchor.png").is_file())
        self.assertIn("login_anchor.png", self.panel.macros.anchor_text.text())

    def test_settings_notifications_keep_existing_line_format_without_secrets(self):
        from line_api import LineAPI

        values = self.panel.settings.values() | {
            "CHANNEL_ACCESS_TOKEN": "private-token",
            "GROUP_ID": "group",
            "LINE_WEBP_QUALITY": "87",
        }
        api = Mock(text=LineAPI.text)
        with patch("qt_panel.LineAPI", return_value=api):
            self.panel.background(
                lambda: self.panel._save_settings_and_notify(
                    values, self.panel.settings.version, self.panel.settings.saved
                )
            )
            self.until(lambda: not self.panel.working)
        destination, messages = api.push.call_args.args
        self.assertEqual(destination, "group")
        self.assertIn("WebP quality: 87", messages[0]["text"])
        self.assertNotIn("private-token", messages[0]["text"])

    def test_close_during_save_waits_until_write_finishes(self):
        release, entered = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.panel.store.save_settings

        def save(*args):
            entered.set()
            release.wait(2)
            return original(*args)

        self.panel.settings.entries["LINE_WEBP_QUALITY"].setText("89")
        with patch.object(self.panel.store, "save_settings", side_effect=save):
            self.panel.save_settings()
            self.until(entered.is_set)
            self.panel.request_close(confirm=False)
            self.assertFalse(self.panel.disposed)
            release.set()
            self.until(lambda: self.panel.disposed)
        self.assertIn("89", (self.project / ".env").read_text())

    def test_close_during_launch_waits_for_action_and_owned_child(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def launch():
            release.wait(2)
            self.controller.start_bot()

        self.panel.background(launch)
        self.panel.request_close(confirm=False)
        self.assertFalse(self.panel.disposed)
        release.set()
        self.until(lambda: self.controller.state == "stopping")
        self.assertFalse(self.panel.disposed)
        self.controller.finish()
        self.until(lambda: self.panel.disposed)
        self.assertTrue(self.controller.closed)
        self.assertFalse(self.panel.bridge.isRunning())


if __name__ == "__main__":
    unittest.main()

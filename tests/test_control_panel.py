import gc
import json
import os
import queue
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import tkinter as tk
except ImportError:
    tk = None

if tk is not None:
    from control_panel import ControlPanel


class StubController:
    def __init__(self):
        self.events = queue.Queue()
        self.state = "stopped"
        self.status = {}
        self.mode = "bot"
        self.starts = 0
        self.stops = 0

    @property
    def busy(self):
        return self.state in {"starting", "online", "stopping", "playing", "recording"}

    def start_bot(self):
        self.starts += 1
        self.state = "starting"

    def stop(self):
        if not self.busy:
            return
        self.stops += 1
        self.state = "stopped"
        self.events.put(("finished", {"mode": "bot", "code": 0}))

    def close(self):
        pass


@unittest.skipUnless(
    tk and (os.name == "nt" or os.environ.get("DISPLAY")),
    "Tk window requires a display",
)
class ControlPanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        template = Path(__file__).resolve().parents[1] / ".env.example"
        (self.project / ".env.example").write_bytes(template.read_bytes())
        (self.project / "scripts" / "macros").mkdir(parents=True)
        (self.project / "scripts" / "macros" / "DH09D.json").write_text(
            '{"steps":[{"action":"sleep","seconds":0}]}'
        )
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.destroy_root)
        self.controller = StubController()
        self.app = ControlPanel(self.root, self.project, controller=self.controller)
        self.app.minimize_on_start.set(False)
        self.root.update()

    def destroy_root(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        finally:
            # Collect destroyed Tk graphs on their owner thread before the next
            # test starts workers that might otherwise run their finalizers.
            self.app = None
            self.root = None
            gc.collect()

    def test_invalid_restart_keeps_running_bot_online(self):
        self.controller.state = "online"
        self.app.settings.variables["PORT"].set("invalid")
        with patch("control_panel.messagebox.showerror"):
            self.app.save_settings(restart=True)
        self.assertEqual(self.controller.stops, 0)
        self.assertEqual(self.controller.state, "online")

    def test_user_cannot_edit_fields_while_save_is_in_flight(self):
        import threading

        entered, release = threading.Event(), threading.Event()
        save = self.app.store.save_settings

        def blocked_save(*args):
            entered.set()
            release.wait(2)
            return save(*args)

        self.app.settings.variables["PORT"].set("8000")
        with patch.object(self.app.store, "save_settings", side_effect=blocked_save):
            self.app.settings.save_button.invoke()
            self.pump(entered.is_set)
            try:
                entry = self.app.settings.entries["PORT"]
                entry.delete(0, "end")
                entry.insert(0, "9000")
                self.assertEqual(self.app.settings.variables["PORT"].get(), "8000")
                self.assertIn("disabled", self.app.targets.editor_widgets[0].state())
            finally:
                release.set()
                self.pump(lambda: not self.app.working)

    def test_restart_freezes_form_until_saved_settings_are_applied(self):
        self.controller.state = "online"
        self.app.settings.variables["PORT"].set("8000")
        self.app.save_settings(restart=True)
        entry = self.app.settings.entries["PORT"]
        entry.delete(0, "end")
        entry.insert(0, "9000")
        self.assertEqual(self.app.settings.variables["PORT"].get(), "8000")
        self.pump(lambda: self.controller.starts == 1 and not self.app.working)

    def test_close_during_start_action_stops_child_before_destroying_window(self):
        import threading

        ready = threading.Event()

        def delayed_start():
            ready.wait(2)
            self.controller.start_bot()

        self.app.background(delayed_start)
        self.app.request_close()
        ready.set()
        self.pump(lambda: self.controller.stops == 1)
        self.assertEqual(self.controller.state, "stopped")

    def pump(self, condition=lambda: True):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.root.update()
            if condition():
                return
            time.sleep(0.01)
        self.fail("Interface action did not complete.")

    def test_masked_credentials_and_all_configuration_fields_are_available(self):
        from panel_settings import ENV_FIELDS, SECRET_FIELDS

        self.assertEqual(set(self.app.settings.variables), set(ENV_FIELDS))
        for field in SECRET_FIELDS:
            self.assertEqual(self.app.settings.entries[field].cget("show"), "*")
        self.assertEqual(
            set(self.app.pages), {"Dashboard", "Settings", "Targets", "Macros", "Help"}
        )

    def test_macro_anchor_controls_fit_the_default_window(self):
        self.root.deiconify()
        self.app.show_page("Macros")
        self.root.update()
        button = self.app.macros.anchor_button
        self.assertTrue(
            button.winfo_viewable(), "Login anchor button must remain visible."
        )
        self.assertLessEqual(
            button.winfo_rooty() + button.winfo_height(),
            self.root.winfo_rooty() + self.root.winfo_height() - 30,
        )

    def test_dashboard_log_can_be_reached_at_minimum_window_size(self):
        self.root.deiconify()
        self.root.geometry("940x620")
        self.app.show_page("Dashboard")
        self.root.update()
        page = self.app.pages["Dashboard"]
        page.canvas.yview_moveto(1)
        self.root.update()
        self.assertTrue(self.app.log.winfo_viewable())
        self.assertGreaterEqual(self.app.log.winfo_rooty(), page.canvas.winfo_rooty())
        self.assertLessEqual(
            self.app.log.winfo_rooty() + self.app.log.winfo_height(),
            page.canvas.winfo_rooty() + page.canvas.winfo_height(),
        )

    def test_target_editor_and_save_fit_minimum_window_size(self):
        self.root.deiconify()
        self.root.geometry("940x620")
        self.app.show_page("Targets")
        self.root.update()
        for widget in self.app.targets.editor_widgets + [self.app.targets.save_button]:
            self.assertTrue(widget.winfo_viewable())
            self.assertLessEqual(
                widget.winfo_rooty() + widget.winfo_height(),
                self.root.winfo_rooty() + self.root.winfo_height() - 30,
            )

    def test_save_button_updates_env_and_clears_dirty_state(self):
        self.app.settings.variables["PORT"].set("8000")
        self.assertTrue(self.app.settings.dirty)
        self.app.settings.save_button.invoke()
        self.pump(lambda: not self.app.working)
        from config import Settings

        self.assertEqual(Settings.load(self.project, environ={}).port, 8000)
        self.assertFalse(self.app.settings.dirty)

    def test_invalid_save_preserves_env_and_displays_error(self):
        original = (self.project / ".env").read_bytes()
        self.app.settings.variables["PORT"].set("invalid")
        with patch("control_panel.messagebox.showerror"):
            self.app.settings.save_button.invoke()
            self.pump(lambda: not self.app.working)
        self.assertEqual((self.project / ".env").read_bytes(), original)
        self.assertTrue(self.app.settings.dirty)
        self.assertIn("Could not", self.app.notice.get())

    def test_target_form_writes_target_registry(self):
        targets = self.app.targets
        targets.target_id.set("09D")
        targets.target_name.set("DH09D Generator")
        targets.target_macro.set("DH09D.json")
        targets.add_row()
        targets.save_button.invoke()
        self.pump(lambda: not self.app.working)
        rows = json.loads((self.project / "targets.json").read_text())
        self.assertEqual(
            rows, [{"id": "09D", "name": "DH09D Generator", "macro": "DH09D.json"}]
        )

    def test_start_stop_controls_reflect_child_state(self):
        self.app.start_button.invoke()
        self.pump(lambda: not self.app.working)
        self.assertEqual(self.controller.starts, 1)
        self.assertIn("disabled", self.app.start_button.state())
        self.assertNotIn("disabled", self.app.stop_button.state())
        self.app.stop_button.invoke()
        self.pump(lambda: self.controller.stops == 1)
        self.assertEqual(self.controller.state, "stopped")

    def test_using_macro_selects_settings_without_overwriting_unsaved_edits(self):
        self.app.settings.variables["PORT"].set("8000")
        self.app.macros.choose_default("login", "DH09D.json")
        self.assertEqual(
            self.app.settings.variables["LOGIN_MACRO_SCRIPT"].get(), "DH09D.json"
        )
        self.assertEqual(self.app.settings.variables["PORT"].get(), "8000")
        self.assertTrue(self.app.settings.dirty)
        self.assertEqual(self.app.current_page, "Settings")


if __name__ == "__main__":
    unittest.main()

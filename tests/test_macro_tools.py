import json
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from contextlib import contextmanager, redirect_stdout
from enum import Enum
from types import SimpleNamespace
from unittest.mock import Mock, patch

from macro_player import MacroPlayer
from macro_recorder import RecordedMacro, translate_key, record_macro
from macro_tool import convert_legacy, save_macro, run
from config import Settings
from launcher import bootstrap


class MacroToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = 0.0

    def recorder(self):
        return RecordedMacro(
            3000,
            2000,
            clock=lambda: self.now,
            clipboard=lambda: "อาคาร status",
            name="test",
        )

    def test_legacy_conversion_preserves_coordinates_text_and_post_delays(self):
        source = {
            "name": "DH09D",
            "default_post_delay": 0.3,
            "steps": [
                {
                    "action": "click_coord",
                    "x": 1803,
                    "y": 1255,
                    "button": "left",
                    "post_delay": 0.1,
                },
                {"action": "type_text", "text": "operator", "post_delay": 0.1},
                {"action": "press_key", "key": "enter"},
            ],
        }
        converted = convert_legacy(source)
        self.assertEqual(
            converted["steps"],
            [
                {
                    "action": "click",
                    "x": 1803,
                    "y": 1255,
                    "button": "left",
                    "delay": 0.1,
                },
                {"action": "text", "text": "operator", "delay": 0.1},
                {"action": "press", "key": "enter", "delay": 0.3},
            ],
        )
        self.assertEqual(source["steps"][0]["action"], "click_coord")
        save_macro(self.root, "DH09D.json", converted)
        self.assertEqual(MacroPlayer(self.root).load("DH09D.json"), converted["steps"])

    def test_conversion_rejects_unsupported_actions_without_creating_a_script(self):
        with self.assertRaises(ValueError):
            convert_legacy({"steps": [{"action": "shell", "command": "bad"}]})

    def test_recording_merges_unicode_text_and_preserves_action_gaps(self):
        rec = self.recorder()
        rec.start()
        self.now = 1.0
        rec.click(1783, 1115, "left")
        self.now = 1.2
        rec.key_press("h", text="H")
        self.now = 1.3
        rec.key_press("i", text="i")
        self.now = 1.4
        rec.key_press("อ", text="อ")
        self.now = 2.0
        rec.key_press("enter")
        self.now = 2.3
        rec.stop()
        steps = rec.document()["steps"]
        self.assertEqual([s["action"] for s in steps], ["click", "text", "press"])
        self.assertEqual(steps[1]["text"], "Hiอ")
        self.assertAlmostEqual(steps[0]["delay"], 0.2)
        self.assertAlmostEqual(steps[1]["delay"], 0.6)
        self.assertAlmostEqual(steps[2]["delay"], 0.3)
        self.assertEqual(rec.document()["desktop"], {"width": 3000, "height": 2000})

    def test_ctrl_v_records_clipboard_contents_for_fast_paste_playback(self):
        rec = self.recorder()
        rec.start()
        rec.key_press("ctrl")
        rec.key_press("a", text="a")
        rec.key_release("ctrl")
        self.now = 0.5
        rec.key_press("ctrl")
        rec.key_press("v", text="v")
        rec.key_release("ctrl")
        rec.stop()
        self.assertEqual(rec.document()["steps"][0]["keys"], ["ctrl", "a"])
        self.assertEqual(rec.document()["steps"][1]["text"], "อาคาร status")

    def test_control_character_hotkeys_use_the_windows_virtual_key(self):
        key = Mock(name=None, char="\x16", vk=86)
        self.assertEqual(translate_key(key, {"ctrl"}), ("v", "\x16"))

    def test_f8_repeat_is_ignored_and_recording_controls_are_not_exported(self):
        rec = self.recorder()
        rec.click(10, 10, "left")
        rec.key_press("f8")
        rec.key_press("f8")
        self.assertTrue(rec.recording)
        rec.key_release("f8")
        rec.click(10, 10, "left")
        rec.key_press("f8")
        self.assertTrue(rec.finished)
        self.assertEqual(len(rec.document()["steps"]), 1)
        self.assertEqual(rec.document()["steps"][0]["action"], "click")

    def test_modified_or_outside_clicks_are_rejected(self):
        for modified in (False, True):
            rec = self.recorder()
            rec.start()
            if modified:
                rec.key_press("shift")
            with self.assertRaises(ValueError):
                rec.click(50 if modified else 3000, 50, "left")

    def test_listener_rejects_drag_returning_to_start_and_modifier_released_before_mouse_up(
        self,
    ):
        class Button(Enum):
            left = "left"

        for gesture in ("drag", "modifier"):
            with self.subTest(gesture=gesture):
                mouse_callbacks = {}

                @contextmanager
                def mouse_listener(**callbacks):
                    mouse_callbacks.update(callbacks)
                    yield

                @contextmanager
                def keyboard_listener(**callbacks):
                    callbacks["on_press"](SimpleNamespace(name="f8"))
                    callbacks["on_release"](SimpleNamespace(name="f8"))
                    if gesture == "modifier":
                        callbacks["on_press"](SimpleNamespace(name="ctrl_l"))
                    mouse_callbacks["on_click"](10, 10, Button.left, True)
                    if gesture == "modifier":
                        callbacks["on_release"](SimpleNamespace(name="ctrl_l"))
                    else:
                        move = mouse_callbacks.get("on_move", lambda *args: None)
                        move(100, 100)
                        move(10, 10)
                    mouse_callbacks["on_click"](10, 10, Button.left, False)
                    callbacks["on_press"](SimpleNamespace(name="f8"))
                    yield

                backend = SimpleNamespace(
                    mouse=SimpleNamespace(Listener=mouse_listener),
                    keyboard=SimpleNamespace(Listener=keyboard_listener),
                )
                with patch("macro_recorder.sys.platform", "win32"), patch(
                    "macro_player.enable_dpi_awareness"
                ), patch.dict(
                    "sys.modules",
                    {
                        "pynput": backend,
                        "pyperclip": SimpleNamespace(paste=lambda: "text"),
                    },
                ), redirect_stdout(
                    io.StringIO()
                ):
                    with self.assertRaises(ValueError):
                        record_macro("test", 3000, 2000)

    def test_existing_recording_destination_fails_before_desktop_access(self):
        cfg = Settings(project_dir=self.root)
        cfg.macros_dir.mkdir(parents=True)
        (cfg.macros_dir / "a.json").write_text("private existing script")
        args = SimpleNamespace(command="record", name="a.json", overwrite=False)
        with self.assertRaises(FileExistsError):
            run(args, cfg)
        self.assertEqual(
            (cfg.macros_dir / "a.json").read_text(), "private existing script"
        )

    def test_export_is_private_confined_and_preserves_existing_files(self):
        data = {"steps": [{"action": "text", "text": "private operator text"}]}
        path = save_macro(self.root, "a.json", data)
        self.assertEqual(json.loads(path.read_text())["steps"], data["steps"])
        with self.assertRaises(FileExistsError):
            save_macro(
                self.root, "a.json", {"steps": [{"action": "press", "key": "enter"}]}
            )
        self.assertEqual(json.loads(path.read_text())["steps"], data["steps"])
        with self.assertRaises(ValueError):
            save_macro(self.root, "../escape.json", data)
        with self.assertRaises(ValueError):
            save_macro(
                self.root, "a.json", {"steps": [{"action": "shell"}]}, overwrite=True
            )
        self.assertEqual(json.loads(path.read_text())["steps"], data["steps"])
        self.assertEqual([p.name for p in self.root.iterdir()], ["a.json"])

    def test_playback_uses_recorded_coordinates_without_display_size_checks(self):
        path = self.root / "a.json"
        path.write_text(
            json.dumps(
                {
                    "desktop": {"width": 3000, "height": 2000},
                    "steps": [{"action": "click", "x": 1783, "y": 1255}],
                }
            )
        )
        gui = Mock()
        gui.size.return_value = (1920, 1080)
        MacroPlayer(self.root, gui=gui, sleep=lambda _: None).play("a.json")
        gui.click.assert_called_once_with(
            x=1783, y=1255, clicks=1, button="left", interval=0.05
        )
        gui.size.assert_not_called()

    def test_malformed_desktop_metadata_is_rejected_before_input(self):
        (self.root / "a.json").write_text(
            json.dumps(
                {
                    "desktop": {"width": -1, "height": 0},
                    "steps": [{"action": "press", "key": "enter"}],
                }
            )
        )
        gui = Mock()
        with self.assertRaises(ValueError):
            MacroPlayer(self.root, gui=gui).play("a.json")
        gui.press.assert_not_called()

    def test_launcher_tool_mode_installs_then_starts_the_tool_without_bot(self):
        python = self.root / ".venv-launcher" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.touch()
        info = {
            "version": [3, 14],
            "platform": "win-amd64",
            "bits": 64,
            "free_threaded": False,
        }
        child = Mock(wait=lambda: 0)
        with patch("launcher.interpreter_info", return_value=info), patch(
            "launcher.install_dependencies"
        ) as install, patch("launcher.subprocess.Popen", return_value=child) as popen:
            self.assertEqual(bootstrap(self.root, macro_tool=True), 0)
        install.assert_called_once()
        self.assertEqual(
            popen.call_args.args[0], [str(python), str(self.root / "macro_tool.py")]
        )


if __name__ == "__main__":
    unittest.main()

import json
import io
import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from contextlib import contextmanager, nullcontext, redirect_stdout
from enum import Enum
from types import SimpleNamespace
from unittest.mock import Mock, patch

import macro_tool
from macro_player import MacroPlayer
from macro_recorder import RecordedMacro, translate_key, record_macro
from macro_tool import convert_legacy, menu, parser, save_macro, run
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

    def test_template_builder_changes_only_name_description_and_navigation_url(self):
        template = {
            "name": "Private login template",
            "description": "Existing description",
            "steps": [
                {"action": "text", "text": "private operator text", "delay": 0.1},
                {
                    "action": "text",
                    "text": "https://old.example.invalid/generator",
                    "delay": 0.3,
                },
                {"action": "press", "key": "enter", "delay": 0.7},
            ],
            "desktop": {"width": 3000, "height": 2000},
        }
        original = copy.deepcopy(template)
        builder = getattr(macro_tool, "build_from_template", None)
        self.assertIsNotNone(builder, "macro_tool must provide build_from_template")

        generated = builder(
            template,
            "DH08C",
            "https://10.121.48.14/generator/DH08C",
        )

        self.assertEqual(template, original)
        self.assertEqual(generated["name"], "DH08C")
        self.assertRegex(
            generated["description"], r"^Generated on \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
        )
        self.assertEqual(generated["steps"][0], template["steps"][0])
        self.assertEqual(
            generated["steps"][1],
            {
                "action": "text",
                "text": "https://10.121.48.14/generator/DH08C",
                "delay": 0.3,
            },
        )
        self.assertEqual(generated["steps"][2], template["steps"][2])
        self.assertEqual(generated["desktop"], template["desktop"])

    def test_template_builder_rejects_bad_url_or_template_without_navigation(self):
        builder = getattr(macro_tool, "build_from_template", None)
        self.assertIsNotNone(builder, "macro_tool must provide build_from_template")
        template = {
            "steps": [
                {"action": "text", "text": "private operator text", "delay": 0.1}
            ]
        }
        with self.assertRaisesRegex(ValueError, "complete http"):
            builder(template, "DH08C", "not a URL")
        with self.assertRaisesRegex(ValueError, "navigation URL"):
            builder(template, "DH08C", "https://10.121.48.14/generator/DH08C")

    def test_template_menu_collects_only_name_and_complete_url(self):
        url = "https://10.121.48.14/generator/DH08C"
        with patch(
            "builtins.input", side_effect=["4", "DH08C", url]
        ), redirect_stdout(io.StringIO()):
            args = menu(parser())
        self.assertEqual(args.command, "template")
        self.assertEqual(args.name, "DH08C.json")
        self.assertEqual(args.url, url)

    def test_template_command_uses_fixed_steps_and_private_credentials(self):
        cfg = Settings(
            project_dir=self.root,
            default_login_macro="missing.json",
            bms_username="operator",
            bms_password="private-password",
        )
        url = (
            "https://10.121.48.14/#%2FRYG2-SVBMS%2FGraphics%2FElectrical%2F"
            "Individual%2FRYG2%20-%20GEN%2FGEN-IT-DH08-C-RYG2A-R1"
        )
        args = SimpleNamespace(
            command="template",
            name="DH08C.json",
            url=url,
            overwrite=False,
        )

        with redirect_stdout(io.StringIO()):
            run(args, cfg)

        generated = json.loads((cfg.macros_dir / "DH08C.json").read_text())
        self.assertEqual(generated["name"], "DH08C")
        self.assertRegex(
            generated["description"], r"^Generated on \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
        )
        self.assertEqual(
            generated["steps"],
            [
                {
                    "action": "click",
                    "x": 1783,
                    "y": 1115,
                    "button": "left",
                    "delay": 0.1,
                },
                {"action": "text", "text": "operator", "delay": 0.1},
                {
                    "action": "click",
                    "x": 1803,
                    "y": 1255,
                    "button": "left",
                    "delay": 0.1,
                },
                {"action": "text", "text": "private-password", "delay": 0.1},
                {"action": "press", "key": "enter", "delay": 0.7},
                {
                    "action": "click",
                    "x": 681,
                    "y": 116,
                    "button": "left",
                    "delay": 0.1,
                },
                {"action": "text", "text": url, "delay": 0.3},
                {"action": "press", "key": "enter", "delay": 0.7},
            ],
        )
        self.assertEqual(generated["desktop"], {"width": 3000, "height": 2000})

    def test_template_command_explains_missing_private_credentials(self):
        cfg = Settings(project_dir=self.root)
        args = SimpleNamespace(
            command="template",
            name="DH08C.json",
            url="https://10.121.48.14/generator/DH08C",
            overwrite=False,
        )

        with self.assertRaisesRegex(ValueError, "BMS_USERNAME.*BMS_PASSWORD"):
            run(args, cfg)
        self.assertFalse((cfg.macros_dir / "DH08C.json").exists())

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

    def test_recording_merges_unicode_text_and_uses_uniform_step_delays(self):
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
        self.assertAlmostEqual(steps[1]["delay"], 0.2)
        self.assertAlmostEqual(steps[2]["delay"], 0.2)
        self.assertEqual(rec.document()["desktop"], {"width": 3000, "height": 2000})

    def test_recording_uses_configured_delay_for_every_exported_step(self):
        rec = RecordedMacro(3000, 2000, clock=lambda: self.now, step_delay=0.1)
        rec.start()
        rec.click(10, 10, "left")
        self.now = 5
        rec.key_press("enter")
        self.now = 9
        rec.stop()

        self.assertEqual(
            [step["delay"] for step in rec.document()["steps"]], [0.1, 0.1]
        )

    def test_standalone_playback_uses_three_second_countdown(self):
        cfg = Settings(project_dir=self.root)
        args = SimpleNamespace(command="play", name="a.json")
        player = Mock()
        output = io.StringIO()

        server = SimpleNamespace(single_instance=lambda project: nullcontext())
        with patch.dict("sys.modules", {"server": server}), patch(
            "macro_tool.sys.platform", "win32"
        ), patch("macro_tool.enable_dpi_awareness"), patch(
            "macro_tool.MacroPlayer", return_value=player
        ), patch("macro_tool.time.sleep") as sleep, redirect_stdout(output):
            run(args, cfg)

        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 1, 1])
        self.assertIn("three seconds", output.getvalue())
        player.play.assert_called_once_with("a.json")

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

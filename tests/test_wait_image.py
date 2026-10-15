import tempfile
import json
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

import config
import detector
from automation_errors import AutomationError, AutomationTimeoutError
from macro_player import MacroPlayer


class WaitImageInfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_application_root_uses_executable_folder_when_frozen(self):
        application_root = getattr(config, "application_root", None)
        self.assertTrue(callable(application_root), "application_root must be available")
        executable = self.root / "BMS Bot" / "bot.exe"
        self.assertEqual(
            application_root(
                frozen=True,
                executable=executable,
                module_file=self.root / "bundle" / "config.py",
            ),
            executable.parent.resolve(),
        )

    def test_project_resource_prefers_external_then_uses_bundle_fallback(self):
        resolve = getattr(config, "resolve_project_resource", None)
        self.assertTrue(
            callable(resolve), "resolve_project_resource must be available"
        )
        bundle = self.root / "bundle"
        external = self.root / "project"
        relative = "assets/ready.png"
        (bundle / "assets").mkdir(parents=True)
        bundled = bundle / relative
        bundled.write_bytes(b"bundle")
        self.assertEqual(resolve(external, relative, bundle_dir=bundle), bundled)
        (external / "assets").mkdir(parents=True)
        operator_file = external / relative
        operator_file.write_bytes(b"operator")
        self.assertEqual(resolve(external, relative, bundle_dir=bundle), operator_file)

    def test_project_resource_rejects_parent_path_segments(self):
        with self.assertRaisesRegex(ValueError, "escapes"):
            config.resolve_project_resource(
                self.root / "project",
                "assets/../assets/ready.png",
                bundle_dir=self.root / "bundle",
            )

    def test_generic_image_matcher_reports_visible_and_absent(self):
        image_visible = getattr(detector, "image_visible", None)
        self.assertTrue(callable(image_visible), "image_visible must be available")
        anchor = Image.new("RGB", (5, 5), "red")
        anchor_path = self.root / "anchor.png"
        anchor.save(anchor_path)
        screen = Image.new("RGB", (20, 20), "black")
        self.assertFalse(
            image_visible(anchor_path, confidence=0.9, grab=lambda: screen)
        )
        screen.paste(anchor, (4, 7))
        self.assertTrue(image_visible(anchor_path, confidence=0.9, grab=lambda: screen))


class WaitImageMacroTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.macros = self.root / "scripts" / "macros"
        self.assets = self.root / "assets"
        self.macros.mkdir(parents=True)
        self.assets.mkdir()
        Image.new("RGB", (5, 5), "red").save(self.assets / "ready.png")
        Image.new("RGB", (5, 5), "blue").save(self.assets / "busy.png")
        self.gui = Mock()

    def write(self, steps, name="test.json"):
        path = self.macros / name
        path.write_text(json.dumps({"steps": steps}), encoding="utf-8")
        return path

    def wait_step(self, **changes):
        return {
            "action": "wait_image",
            "image": "assets/ready.png",
            "condition": "appear",
            "timeout": 2,
            "poll_interval": 0.1,
            "confidence": 0.9,
            "consecutive_matches": 2,
            **changes,
        }

    def play_with_results(self, step, results):
        self.write([step, {"action": "click", "x": 1, "y": 2}])
        with patch("detector.image_visible", side_effect=results) as match:
            MacroPlayer(
                self.macros, gui=self.gui, sleep=lambda _: None
            ).play("test.json")
        self.gui.click.assert_called_once()
        return match

    def test_waits_for_image_to_disappear(self):
        match = self.play_with_results(
            self.wait_step(condition="disappear"), [True, False, False]
        )
        self.assertEqual(match.call_count, 3)

    def test_already_present_or_absent_still_requires_confirmations(self):
        cases = [
            (self.wait_step(condition="appear"), [True, True]),
            (self.wait_step(condition="disappear"), [False, False]),
        ]
        for step, results in cases:
            with self.subTest(condition=step["condition"]):
                self.gui.reset_mock()
                match = self.play_with_results(step, results)
                self.assertEqual(match.call_count, 2)

    def test_optional_fields_use_documented_defaults(self):
        self.write(
            [
                {
                    "action": "wait_image",
                    "image": "assets/ready.png",
                    "condition": "appear",
                },
                {"action": "click", "x": 1, "y": 2},
            ]
        )
        sleep = Mock()
        with patch("detector.image_visible", side_effect=[True, True]) as match:
            MacroPlayer(self.macros, gui=self.gui, sleep=sleep).play("test.json")
        self.assertEqual(match.call_count, 2)
        self.assertTrue(
            all(call.kwargs["confidence"] == 0.9 for call in match.call_args_list)
        )
        sleep.assert_any_call(0.5)
        self.gui.click.assert_called_once()

    def test_brief_match_resets_consecutive_confirmation_count(self):
        cases = [
            ("appear", [True, False, True, True]),
            ("disappear", [False, True, False, False]),
        ]
        for condition, results in cases:
            with self.subTest(condition=condition):
                self.gui.reset_mock()
                match = self.play_with_results(
                    self.wait_step(condition=condition), results
                )
                self.assertEqual(match.call_count, 4)

    def test_timeout_reports_image_condition_elapsed_and_check_count(self):
        self.write(
            [
                self.wait_step(timeout=1, poll_interval=0.25),
                {"action": "click", "x": 1, "y": 2},
            ]
        )
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        with patch("macro_player.time.monotonic", side_effect=lambda: clock[0]), patch(
            "detector.image_visible", return_value=False
        ), self.assertLogs("macro_player", level="ERROR") as logs:
            with self.assertRaises(AutomationTimeoutError) as caught:
                MacroPlayer(self.macros, gui=self.gui, sleep=sleep).play("test.json")
        message = str(caught.exception)
        for text in ("assets/ready.png", "appear", "elapsed", "checks 4"):
            self.assertIn(text, message)
        self.assertIn("checks 4", "\n".join(logs.output))
        self.gui.click.assert_not_called()

    def test_missing_corrupt_and_escaping_images_are_rejected_before_input(self):
        cases = [
            self.wait_step(image="assets/missing.png"),
            self.wait_step(image="../outside.png"),
            self.wait_step(image=str((self.root / "absolute.png").resolve())),
        ]
        (self.assets / "corrupt.png").write_text("not an image", encoding="utf-8")
        cases.append(self.wait_step(image="assets/corrupt.png"))
        for index, step in enumerate(cases):
            with self.subTest(image=step["image"]):
                self.write(
                    [{"action": "click", "x": 7, "y": 8}, step],
                    f"bad{index}.json",
                )
                with self.assertRaises(AutomationError):
                    MacroPlayer(self.macros, gui=self.gui).play(f"bad{index}.json")
        self.gui.click.assert_not_called()

    def test_invalid_wait_configuration_is_rejected_before_input(self):
        invalid = [
            {"condition": "visible"},
            {"condition": None},
            {"timeout": 0},
            {"timeout": float("inf")},
            {"poll_interval": -0.1},
            {"confidence": 0},
            {"confidence": 1.1},
            {"consecutive_matches": 0},
            {"consecutive_matches": 1.5},
            {"consecutive_matches": True},
            {"image": ""},
        ]
        for index, change in enumerate(invalid):
            with self.subTest(change=change):
                self.write([self.wait_step(**change)], f"invalid{index}.json")
                with self.assertRaises(AutomationError):
                    MacroPlayer(self.macros, gui=self.gui).play(
                        f"invalid{index}.json"
                    )
        self.gui.click.assert_not_called()

    def test_cancellation_stops_wait_and_remaining_steps(self):
        self.write(
            [self.wait_step(), {"action": "click", "x": 1, "y": 2}]
        )

        class CancelDuringPause:
            def is_set(self):
                return False

            def wait(self, seconds):
                return True

        with patch("detector.image_visible", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                MacroPlayer(self.macros, gui=self.gui).play(
                    "test.json", cancel=CancelDuringPause()
                )
        self.gui.click.assert_not_called()

    def test_detection_error_never_satisfies_disappear(self):
        self.write(
            [
                self.wait_step(condition="disappear"),
                {"action": "click", "x": 1, "y": 2},
            ]
        )
        with patch(
            "detector.image_visible", side_effect=AutomationError("capture failed")
        ), self.assertLogs("macro_player", level="ERROR"):
            with self.assertRaisesRegex(AutomationError, "capture failed"):
                MacroPlayer(self.macros, gui=self.gui).play("test.json")
        self.gui.click.assert_not_called()

    def test_unexpected_detection_error_is_reported_not_treated_as_absence(self):
        self.write(
            [
                self.wait_step(condition="disappear"),
                {"action": "click", "x": 1, "y": 2},
            ]
        )
        with patch(
            "detector.image_visible", side_effect=RuntimeError("screen unavailable")
        ), self.assertLogs("macro_player", level="ERROR"):
            with self.assertRaisesRegex(
                AutomationError, "RuntimeError: screen unavailable"
            ):
                MacroPlayer(self.macros, gui=self.gui).play("test.json")
        self.gui.click.assert_not_called()

    def test_outer_deadline_stops_wait_before_its_own_timeout(self):
        self.write(
            [
                self.wait_step(timeout=60, poll_interval=0.5),
                {"action": "click", "x": 1, "y": 2},
            ]
        )
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        with patch("macro_player.time.monotonic", side_effect=lambda: clock[0]), patch(
            "detector.image_visible", return_value=False
        ):
            with self.assertRaisesRegex(
                AutomationTimeoutError, "LINE reply deadline"
            ):
                MacroPlayer(self.macros, gui=self.gui, sleep=sleep).play(
                    "test.json", deadline=1.1
                )
        self.gui.click.assert_not_called()

    def test_detection_finishing_after_timeout_does_not_falsely_succeed(self):
        self.write(
            [
                self.wait_step(
                    timeout=1,
                    poll_interval=0.25,
                    consecutive_matches=1,
                ),
                {"action": "click", "x": 1, "y": 2},
            ]
        )
        clock = [0.0]

        def slow_visible(*args, **kwargs):
            clock[0] = 1.1
            return True

        with patch("macro_player.time.monotonic", side_effect=lambda: clock[0]), patch(
            "detector.image_visible", side_effect=slow_visible
        ):
            with self.assertRaisesRegex(AutomationTimeoutError, "checks 1"):
                MacroPlayer(
                    self.macros, gui=self.gui, sleep=lambda seconds: None
                ).play("test.json")
        self.gui.click.assert_not_called()

    def test_multiple_waits_resume_once_without_replaying_steps(self):
        self.write(
            [
                {"action": "click", "x": 1, "y": 1},
                self.wait_step(image="assets/ready.png", condition="appear"),
                {"action": "click", "x": 2, "y": 2},
                self.wait_step(image="assets/busy.png", condition="disappear"),
                {"action": "click", "x": 3, "y": 3},
            ]
        )
        with patch(
            "detector.image_visible", side_effect=[True, True, False, False]
        ) as match:
            MacroPlayer(
                self.macros, gui=self.gui, sleep=lambda _: None
            ).play("test.json")
        self.assertEqual(match.call_count, 4)
        self.assertEqual(
            [(call.kwargs["x"], call.kwargs["y"]) for call in self.gui.click.call_args_list],
            [(1, 1), (2, 2), (3, 3)],
        )

    def test_relative_image_is_independent_of_current_working_directory(self):
        self.write([self.wait_step()])
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        previous = Path.cwd()
        try:
            os.chdir(elsewhere)
            with patch("detector.image_visible", side_effect=[True, True]) as match:
                MacroPlayer(
                    self.macros, gui=self.gui, sleep=lambda _: None
                ).play("test.json")
        finally:
            os.chdir(previous)
        expected = (self.assets / "ready.png").resolve()
        self.assertTrue(all(call.args[0] == expected for call in match.call_args_list))

    def test_existing_macro_without_wait_image_never_calls_matcher(self):
        self.write(
            [
                {"action": "press", "key": "enter", "delay": 0},
                {"action": "click", "x": 1, "y": 2},
            ]
        )
        with patch(
            "detector.image_visible",
            side_effect=AssertionError("matcher should not run"),
        ):
            MacroPlayer(self.macros, gui=self.gui).play("test.json")
        self.gui.press.assert_called_once_with("enter")
        self.gui.click.assert_called_once()


if __name__ == "__main__":
    unittest.main()

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from macro_player import MacroPlayer
from panel_settings import StaleConfiguration


class MacroEditorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            importlib.util.find_spec("macro_editor"), "Macro editor model is missing"
        )
        from macro_editor import MacroDocument, MacroRepository

        self.Document = MacroDocument
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        (self.project / ".env").write_text("MAX_MACRO_SECONDS=10\n")
        self.directory = self.project / "scripts/macros"
        self.directory.mkdir(parents=True)
        (self.project / "assets").mkdir()
        Image.new("RGB", (8, 8), "blue").save(self.project / "assets/shared.png")
        self.repo = MacroRepository(self.project)
        self.payload = {
            "name": "Generator",
            "description": "Original",
            "vendor": {"keep": [1, 2]},
            "steps": [
                {
                    "type": "text",
                    "text": "${BMS_PASSWORD}\nprivate",
                    "delay": 0.2,
                    "extra": {"preserve": True},
                },
                {"action": "sleep", "seconds": 0.1},
            ],
        }
        self.path = self.directory / "original.json"
        self.path.write_text(json.dumps(self.payload))

    def test_load_save_preserves_metadata_unknown_fields_and_placeholders(self):
        doc = self.repo.open("original.json")
        doc.update_step(0, {"delay": 0.3})
        self.repo.save(doc.filename, doc.payload, doc.version)
        saved = json.loads(self.path.read_text())
        expected = copy.deepcopy(self.payload)
        expected["steps"][0]["delay"] = 0.3
        self.assertEqual(saved, expected)
        self.assertNotIn("private", doc.summary(0))
        self.assertNotIn("BMS_PASSWORD", doc.summary(0))
        self.assertEqual(
            MacroPlayer(self.directory).load("original.json")[0]["text"],
            "${BMS_PASSWORD}\nprivate",
        )

    def test_step_management_and_wait_conditions(self):
        doc = self.Document.new()
        doc.add("wait_image")
        doc.update_step(
            0,
            {
                "image": "assets/shared.png",
                "condition": "disappear",
                "timeout": 30,
                "on_timeout": "continue",
            },
        )
        self.assertIn("disappears", doc.summary(0))
        self.assertEqual(doc.duplicate(0), 1)
        self.assertEqual(doc.move(1, -1), 0)
        doc.update_step(0, {"condition": "appear"})
        self.assertIn("appears", doc.summary(0))
        doc.delete(1)
        self.repo.save("wait.json", doc.payload, None)
        self.assertEqual(
            MacroPlayer(self.directory).load("wait.json")[0]["on_timeout"], "continue"
        )

    def test_all_existing_actions_can_be_added_and_saved(self):
        doc = self.Document.new()
        for action in ("click", "press", "hotkey", "text", "sleep"):
            doc.add(action)
        self.repo.save("all.json", doc.payload, None)
        self.assertEqual(len(MacroPlayer(self.directory).load("all.json")), 5)

    def test_save_as_leaves_original_unchanged_and_rejects_duplicate(self):
        original = self.path.read_bytes()
        doc = self.repo.open("original.json")
        doc.update_step(1, {"seconds": 0.2})
        self.repo.save("copy.json", doc.payload, None)
        self.assertEqual(self.path.read_bytes(), original)
        with self.assertRaises(StaleConfiguration):
            self.repo.save("copy.json", doc.payload, None)

    def test_external_edit_after_replace_is_not_adopted_as_saved_version(self):
        document = self.repo.open("original.json")
        replace = self.repo._replace

        def external_edit(*args):
            replace(*args)
            self.path.write_text(json.dumps(self.payload | {"external": "new work"}))

        with patch.object(self.repo, "_replace", side_effect=external_edit):
            version = self.repo.save(
                document.filename, document.payload, document.version
            )
        with self.assertRaises(StaleConfiguration):
            self.repo.save(document.filename, document.payload, version)
        self.assertEqual(json.loads(self.path.read_text())["external"], "new work")

    def test_stale_save_keeps_external_changes(self):
        doc = self.repo.open("original.json")
        self.path.write_text(json.dumps(self.payload | {"external": True}))
        with self.assertRaises(StaleConfiguration):
            self.repo.save(doc.filename, doc.payload, doc.version)
        self.assertTrue(json.loads(self.path.read_text())["external"])

    def test_failed_replace_preserves_original_and_cleans_draft(self):
        doc = self.repo.open("original.json")
        original = self.path.read_bytes()
        with patch("panel_settings.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                self.repo.save(doc.filename, doc.payload, doc.version)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(list((self.project / ".runtime").glob("*.json")))

    def test_array_root_and_unknown_steps_are_preserved_without_execution(self):
        raw = [
            {"action": "future_action", "secret": "${TOKEN}", "custom": [1]},
            42,
            {"action": "click", "x": 1, "y": 2, "future": True},
        ]
        self.path.write_text(json.dumps(raw))
        doc = self.repo.open("original.json")
        self.assertEqual(doc.payload, raw)
        self.assertFalse(doc.editable(0))
        self.assertFalse(doc.editable(1))
        doc.update_step(2, {"x": 7})
        self.assertEqual(doc.payload[:2], raw[:2])
        self.assertTrue(doc.steps[2]["future"])
        with self.assertRaisesRegex(ValueError, "Step 1"):
            self.repo.save(doc.filename, doc.payload, doc.version)
        self.assertEqual(json.loads(self.path.read_text()), raw)

    def test_invalid_input_reports_step_number(self):
        for step in (
            {"action": "click", "x": -1, "y": 0},
            {"action": "sleep", "seconds": -1},
            {
                "action": "wait_image",
                "image": "assets/shared.png",
                "condition": "appear",
                "timeout": 0,
            },
            {"action": "hotkey", "keys": []},
        ):
            with self.subTest(step=step), self.assertRaisesRegex(ValueError, "Step 2"):
                self.repo.save(
                    "invalid.json",
                    {"steps": [{"action": "press", "key": "enter"}, step]},
                    None,
                )
        self.assertFalse((self.directory / "invalid.json").exists())

    def test_missing_image_and_invalid_paths_never_report_detection_success(self):
        for image in (
            "assets/missing.png",
            "../outside.png",
            str(self.project / "assets/shared.png"),
        ):
            with self.subTest(image=image), self.assertRaises(ValueError):
                self.repo.test_image(
                    {"action": "wait_image", "image": image, "condition": "disappear"}
                )
        with self.assertRaisesRegex(ValueError, "project"):
            self.repo.relative_image(Path(self.tmp.name).parent / "outside.png")

    def test_snapshot_detection_reuses_matcher_once_without_macro_execution(self):
        step = {
            "action": "wait_image",
            "image": "assets/shared.png",
            "condition": "disappear",
            "confidence": 0.84,
        }
        with (
            patch("detector.image_visible", return_value=False) as match,
            patch.object(MacroPlayer, "play") as play,
        ):
            result = self.repo.test_image(step)
        self.assertFalse(result["present"])
        self.assertTrue(result["satisfied"])
        self.assertNotIn("score", result)
        match.assert_called_once_with(
            self.project / "assets/shared.png", confidence=0.84
        )
        play.assert_not_called()

    def test_preview_and_portable_image_paths(self):
        relative = self.repo.relative_image(self.project / "assets/shared.png")
        self.assertEqual(relative, "assets/shared.png")
        preview = self.repo.preview_image(relative)
        self.assertTrue(preview.startswith(b"\x89PNG"))

    def test_invalid_json_and_missing_steps_are_actionable(self):
        for raw in ("{bad json", '{"name":"Missing steps"}'):
            self.path.write_text(raw)
            with self.assertRaises(ValueError):
                self.repo.open("original.json")


if __name__ == "__main__":
    unittest.main()

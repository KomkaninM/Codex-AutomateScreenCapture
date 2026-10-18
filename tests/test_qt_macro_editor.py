import copy
import json
import threading
import unittest
from unittest.mock import patch

import test_qt_panel as panel_tests
from PIL import Image
from PySide6.QtWidgets import QMessageBox


class QtMacroEditorTests(unittest.TestCase):
    setUpClass = classmethod(panel_tests.QtPanelTests.setUpClass.__func__)
    until = panel_tests.QtPanelTests.until
    cleanup = panel_tests.QtPanelTests.cleanup

    def setUp(self):
        panel_tests.QtPanelTests.setUp(self)
        self.assertTrue(hasattr(self.panel, "editor"), "Macro Editor page is missing")
        self.page = self.panel.editor
        self.panel.show_page("Macro Editor")
        self.until(lambda: not self.panel.working)
        (self.project / "assets").mkdir(exist_ok=True)
        Image.new("RGB", (8, 8), "blue").save(self.project / "assets/shared.png")

    def test_visual_actions_preserve_previous_step_changes_and_selection(self):
        self.page.add_step("click")
        self.page.fields["x"].setText("1783")
        self.page.fields["y"].setText("900")
        self.page.add_step("wait_image")
        self.page.fields["image"].setText("assets/shared.png")
        self.page.fields["condition"].setCurrentText("disappear")
        self.page.fields["on_timeout"].setCurrentText("continue")
        self.page.fields["timeout"].setText("30")
        self.page.add_step("press")
        self.assertEqual(self.page.document.steps[0]["x"], 1783)
        self.assertEqual(self.page.document.steps[1]["condition"], "disappear")
        self.assertEqual(self.page.document.steps[1]["on_timeout"], "continue")
        self.page.move_step(-1)
        self.assertEqual(self.page.steps.currentRow(), 1)
        self.page.duplicate_step()
        self.assertEqual(len(self.page.document.steps), 4)
        with patch.object(self.panel, "confirm", return_value=True):
            self.page.delete_step()
        self.assertEqual(len(self.page.document.steps), 3)
        self.assertTrue(self.page.dirty)

    def test_invalid_draft_stays_selected_and_can_be_repaired(self):
        self.page.add_step("click")
        self.page.add_step("sleep")
        self.page.fields["seconds"].setText("-1")
        self.page.steps.setCurrentRow(0)
        self.assertEqual(self.page.steps.currentRow(), 1)
        self.assertIn("Step 2", self.page.status.text())
        self.page.fields["seconds"].setText(".5")
        self.page.steps.setCurrentRow(0)
        self.assertEqual(self.page.document.steps[1]["seconds"], 0.5)

    def test_save_as_round_trip_with_metadata_and_literal_placeholders(self):
        from macro_editor import MacroDocument

        payload = {
            "name": "Generator",
            "description": "Before",
            "vendor": {"keep": True},
            "steps": [{"type": "text", "text": "${PASSWORD}", "private": "extra"}],
        }
        self.page.set_document(MacroDocument(payload, "old.json", None))
        self.page.description.setText("After")
        with patch(
            "qt_macro_editor.QInputDialog.getText", return_value=("new.json", True)
        ):
            self.page.save(save_as=True)
            self.until(lambda: not self.panel.working)
        expected = copy.deepcopy(payload) | {"description": "After"}
        self.assertEqual(
            json.loads((self.project / "scripts/macros/new.json").read_text()), expected
        )
        self.assertFalse(self.page.dirty)
        self.assertEqual(self.page.document.filename, "new.json")
        self.assertNotIn("PASSWORD", self.page.steps.item(0).text())

    def test_unsaved_cancel_discard_and_save_before_new(self):
        self.page.add_step("sleep")
        with patch.object(
            self.page, "ask_unsaved", return_value=QMessageBox.StandardButton.Cancel
        ):
            self.page.new_macro()
        self.assertEqual(len(self.page.document.steps), 1)
        with (
            patch.object(
                self.page, "ask_unsaved", return_value=QMessageBox.StandardButton.Save
            ),
            patch(
                "qt_macro_editor.QInputDialog.getText",
                return_value=("saved.json", True),
            ),
        ):
            self.page.new_macro()
            self.until(lambda: not self.panel.working)
        self.assertTrue((self.project / "scripts/macros/saved.json").is_file())
        self.assertEqual(self.page.document.steps, [])
        self.page.add_step("click")
        with patch.object(
            self.page, "ask_unsaved", return_value=QMessageBox.StandardButton.Discard
        ):
            self.page.new_macro()
        self.assertFalse(self.page.dirty)

    def test_detection_runs_once_off_thread_and_handles_worker_error(self):
        self.page.add_step("wait_image")
        self.page.fields["image"].setText("assets/shared.png")
        release, entered = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def match(*args, **kwargs):
            entered.set()
            release.wait(2)
            return False

        with (
            patch("detector.image_visible", side_effect=match),
            patch("macro_player.MacroPlayer.play") as play,
        ):
            self.page.test_image()
            self.until(entered.is_set)
            self.assertFalse(self.page.test_button.isEnabled())
            self.panel.show_page("Help")
            self.assertEqual(self.panel.current_page, "Help")
            release.set()
            self.until(lambda: not self.panel.working)
            play.assert_not_called()
        self.assertIn("absent", self.page.detection.text())
        with (
            patch(
                "detector.image_visible",
                side_effect=RuntimeError("desktop unavailable"),
            ),
            patch.object(self.panel, "show_error") as error,
        ):
            self.page.test_image()
            self.until(lambda: not self.panel.working)
            error.assert_called_once()
        self.assertTrue(self.page.test_button.isEnabled())

    def test_draft_does_not_block_adjusting_macro_time_limit(self):
        self.page.add_step("sleep")
        self.page.fields["seconds"].setText("12")
        self.panel.settings.entries["MAX_MACRO_SECONDS"].setText("20")
        self.panel.save_settings()
        self.until(lambda: not self.panel.working)
        self.assertEqual(self.panel.settings.saved["MAX_MACRO_SECONDS"], "20")
        self.assertEqual(self.page.fields["seconds"].text(), "12")
        self.assertTrue(self.page.dirty)

    def test_draft_does_not_change_existing_bot_start_workflow(self):
        self.page.add_step("sleep")
        self.panel.start_bot()
        self.until(lambda: not self.panel.working)
        self.assertEqual(self.controller.starts, 1)
        self.assertTrue(self.page.dirty)
        self.assertFalse(self.page.save_button.isEnabled())

    def test_save_and_close_stops_active_bot_before_writing(self):
        self.controller.state = "online"
        self.page.add_step("sleep")
        with (
            patch.object(
                self.page, "ask_unsaved", return_value=QMessageBox.StandardButton.Save
            ),
            patch(
                "qt_macro_editor.QInputDialog.getText",
                return_value=("after-stop.json", True),
            ),
        ):
            self.panel.request_close()
            self.until(lambda: self.controller.state == "stopping")
            self.assertFalse((self.project / "scripts/macros/after-stop.json").exists())
            self.controller.finish()
            self.until(lambda: self.panel.disposed)
        self.assertTrue((self.project / "scripts/macros/after-stop.json").is_file())

    def test_unsupported_configuration_is_hidden_on_each_step(self):
        from PySide6.QtWidgets import QCheckBox

        from macro_editor import MacroDocument

        payload = [
            {"action": "future", "private": "first"},
            {"action": "other_future", "private": "second"},
        ]
        self.page.set_document(MacroDocument(payload))
        reveal = next(
            w
            for w in self.page.findChildren(QCheckBox)
            if w.text().startswith("Show existing configuration")
        )
        reveal.setChecked(True)
        self.page.steps.setCurrentRow(1)
        self.assertFalse(reveal.isChecked())
        self.assertEqual(self.page.document.payload, payload)

    def test_literal_comma_shortcut_round_trips_and_can_be_edited(self):
        from macro_editor import MacroDocument

        payload = {
            "steps": [
                {"action": "hotkey", "keys": ["ctrl", ","]},
                {"action": "sleep", "seconds": 1},
            ]
        }
        self.page.set_document(MacroDocument(payload))
        self.assertTrue(self.page.commit_step())
        self.page.fields["keys"].setText('shift, ","')
        self.assertTrue(self.page.commit_step())
        self.assertEqual(self.page.document.steps[0]["keys"], ["shift", ","])
        self.page.steps.setCurrentRow(1)
        self.assertEqual(self.page.current_index, 1)

    def test_save_before_open_continues_to_selected_file(self):
        other = self.project / "scripts/macros/other.json"
        other.write_text('[{"action":"press","key":"tab"}]')
        self.page.refresh_files(["other.json"])
        self.page.files.setCurrentText("other.json")
        self.page.add_step("sleep")
        with (
            patch.object(
                self.page, "ask_unsaved", return_value=QMessageBox.StandardButton.Save
            ),
            patch(
                "qt_macro_editor.QInputDialog.getText",
                return_value=("first.json", True),
            ),
        ):
            self.page.open_macro()
            self.until(
                lambda: (
                    self.page.document.filename == "other.json"
                    and not self.panel.working
                )
            )
        self.assertTrue((self.project / "scripts/macros/first.json").is_file())
        self.assertEqual(self.page.document.steps[0]["key"], "tab")

    def test_missing_reference_is_visible_when_opened(self):
        from macro_editor import MacroDocument

        payload = {
            "steps": [
                {
                    "action": "wait_image",
                    "image": "assets/missing.png",
                    "condition": "appear",
                }
            ]
        }
        self.page.set_document(MacroDocument(payload))
        self.until(lambda: "Unable to load" in self.page.preview.text())
        self.assertEqual(self.page.document.payload, payload)

    def test_description_edit_preserves_absent_name_property(self):
        from macro_editor import MacroDocument

        self.page.set_document(
            MacroDocument({"steps": [{"action": "sleep", "seconds": 1}]})
        )
        self.page.description.setText("New description")
        self.assertNotIn("name", self.page.document.payload)

    def test_text_is_hidden_again_when_another_step_is_selected(self):
        from PySide6.QtWidgets import QCheckBox

        self.page.add_step("text")
        self.page.fields["text"].setPlainText("private\n${PASSWORD}")
        reveal = next(
            w
            for w in self.page.findChildren(QCheckBox)
            if w.text() == "Show / edit text"
        )
        reveal.setChecked(True)
        self.page.add_step("text")
        self.assertFalse(reveal.isChecked())

    def test_close_save_finishes_without_extra_discard_prompt(self):
        self.page.add_step("sleep")
        with (
            patch.object(
                self.page, "ask_unsaved", return_value=QMessageBox.StandardButton.Save
            ),
            patch(
                "qt_macro_editor.QInputDialog.getText",
                return_value=("closed.json", True),
            ),
            patch.object(
                self.panel,
                "confirm",
                side_effect=AssertionError("Unexpected discard prompt"),
            ),
            patch.object(self.panel, "show_error") as error,
        ):
            self.panel.request_close()
            self.until(lambda: self.panel.disposed or error.called)
            error.assert_not_called()
        self.assertTrue(self.panel.disposed)
        self.assertTrue((self.project / "scripts/macros/closed.json").is_file())

    def test_cancel_replaces_an_earlier_pending_close_save(self):
        self.page.add_step("sleep")
        release = threading.Event()
        self.addCleanup(release.set)
        self.panel.background(lambda: release.wait(2))
        with (
            patch.object(
                self.page,
                "ask_unsaved",
                side_effect=[
                    QMessageBox.StandardButton.Save,
                    QMessageBox.StandardButton.Cancel,
                ],
            ),
            patch(
                "qt_macro_editor.QInputDialog.getText", return_value=("", False)
            ) as ask_name,
        ):
            self.panel.request_close()
            self.panel.request_close()
            release.set()
            self.until(lambda: not self.panel.working)
            ask_name.assert_not_called()
        self.assertFalse(self.panel.closing)

    def test_close_cancel_retains_editor_and_discard_closes_cleanly(self):
        self.page.add_step("sleep")
        with patch.object(
            self.page, "ask_unsaved", return_value=QMessageBox.StandardButton.Cancel
        ):
            self.panel.request_close()
        self.assertFalse(self.panel.closing)
        self.assertEqual(len(self.page.document.steps), 1)
        with patch.object(
            self.page, "ask_unsaved", return_value=QMessageBox.StandardButton.Discard
        ):
            self.panel.request_close()
        self.until(lambda: self.panel.disposed)


if __name__ == "__main__":
    unittest.main()

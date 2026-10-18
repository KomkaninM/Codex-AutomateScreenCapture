"""Visual macro editing; all disk/image services use the panel's Qt workers."""

from __future__ import annotations

import copy
import csv
import io
import json
import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QDoubleValidator, QIntValidator, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config import macro_name
from macro_editor import DEFAULT_STEPS, MacroDocument, MacroRepository, action_of
from panel_settings import fingerprint
from qt_pages import button, label, scroll, set_combo

ACTIONS = {
    "click": "Mouse Click",
    "wait_image": "Wait Image",
    "press": "Press Key",
    "hotkey": "Keyboard Shortcut",
    "text": "Type Text",
    "sleep": "Delay",
}
FIELDS = {
    "click": [
        ("x", "X coordinate", "int"),
        ("y", "Y coordinate", "int"),
        ("button", "Mouse button", ["left", "right", "middle"]),
        ("clicks", "Click count (1–10)", "int"),
    ],
    "press": [("key", "Key (enter, tab, esc…)", "key")],
    "hotkey": [("keys", "Keys separated by commas", "keys")],
    "text": [("text", "Text (kept private in step list)", "text")],
    "sleep": [("seconds", "Duration (seconds)", "number")],
    "wait_image": [
        ("image", "Reference image", "path"),
        ("condition", "Condition", ["appear", "disappear"]),
        ("timeout", "Timeout (seconds)", "number"),
        ("on_timeout", "On timeout", ["stop", "continue"]),
        ("poll_interval", "Polling interval (seconds)", "number"),
        ("confidence", "Match threshold (0–1)", "number"),
        ("consecutive_matches", "Consecutive matches", "int"),
    ],
}
DEFAULTS = {
    "delay": 0,
    "timeout": 60,
    "poll_interval": 0.5,
    "confidence": 0.9,
    "consecutive_matches": 2,
    "on_timeout": "stop",
    "button": "left",
    "clicks": 1,
    "seconds": 0,
}


class MacroEditorPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.repository = MacroRepository(app.project)
        self.document = MacroDocument.new()
        self.current_index = -1
        self.loading = False
        self.original_fields = {}
        self.fields = {}
        self.pending_preview = None
        self.pending_save = None
        self.form_widgets = {}
        self.action_pages = {}
        self.locked = True
        layout = QVBoxLayout(self)
        file_row = QHBoxLayout()
        file_row.addWidget(label("Macro file:"))
        self.files = QComboBox()
        self.files.setMinimumWidth(100)
        self.files.setAccessibleName("Macro file")
        file_row.addWidget(self.files, 1)
        self.open_button = button("Open", self.open_macro)
        self.new_button = button("New", self.new_macro)
        file_row.addWidget(self.open_button)
        file_row.addWidget(self.new_button)
        layout.addLayout(file_row)
        self.metadata = QWidget()
        form = QFormLayout(self.metadata)
        form.setContentsMargins(0, 0, 0, 0)
        self.name, self.description = QLineEdit(), QLineEdit()
        form.addRow("Name", self.name)
        form.addRow("Description", self.description)
        self.name.textChanged.connect(self.metadata_changed)
        self.description.textChanged.connect(self.metadata_changed)
        layout.addWidget(self.metadata)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter, 1)
        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.addWidget(label("Macro steps"))
        self.steps = QListWidget()
        self.steps.setMinimumWidth(150)
        self.steps.currentRowChanged.connect(self.select_step)
        left_box.addWidget(self.steps, 1)
        self.empty_state = label(
            "No steps yet. Choose an action below and click Add Step.", True
        )
        left_box.addWidget(self.empty_state)
        self.add_action = QComboBox()
        for action, title in ACTIONS.items():
            self.add_action.addItem(title, action)
        left_box.addWidget(self.add_action)
        self.add_button = button(
            "+ Add Step", lambda: self.add_step(self.add_action.currentData())
        )
        left_box.addWidget(self.add_button)
        self.manage_buttons = []
        for names in (
            [("Delete", self.delete_step), ("Duplicate", self.duplicate_step)],
            [
                ("Move Up", lambda: self.move_step(-1)),
                ("Move Down", lambda: self.move_step(1)),
            ],
        ):
            row = QHBoxLayout()
            for title, callback in names:
                item = button(title, callback)
                self.manage_buttons.append(item)
                row.addWidget(item)
            left_box.addLayout(row)
        self.splitter.addWidget(left)
        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.addWidget(label("Step configuration"))
        self.action = QComboBox()
        for key, title in ACTIONS.items():
            self.action.addItem(title, key)
        self.action.currentIndexChanged.connect(self.change_action)
        right_box.addWidget(self.action)
        self.forms = QStackedWidget()
        right_box.addWidget(self.forms, 1)
        for action, definitions in FIELDS.items():
            page = QWidget()
            config = QFormLayout(page)
            config.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
            fields = {}
            for key, title, kind in definitions + [
                ("delay", "After-step delay (seconds)", "number")
            ]:
                if isinstance(kind, list):
                    widget = QComboBox()
                    widget.addItems(kind)
                    widget.currentIndexChanged.connect(self.changed)
                elif kind == "text":
                    widget = QPlainTextEdit()
                    widget.setMaximumHeight(110)
                    widget.textChanged.connect(self.changed)
                    reveal = QCheckBox("Show / edit text")
                    self.text_reveal = reveal
                    reveal.toggled.connect(widget.setVisible)
                    config.addRow(reveal)
                    widget.hide()
                else:
                    widget = QLineEdit()
                    widget.textChanged.connect(self.changed)
                    if kind == "int":
                        widget.setValidator(QIntValidator(0, 2147483647, widget))
                    elif kind == "number":
                        validator = QDoubleValidator(0, 1e100, 10, widget)
                        widget.setValidator(validator)
                    elif kind == "keys":
                        widget.setPlaceholderText(
                            'ctrl, shift, s (comma key: ctrl, ",")'
                        )
                widget.setAccessibleName(title)
                fields[key] = widget
                if kind == "path":
                    path_row = QHBoxLayout()
                    path_row.addWidget(widget, 1)
                    path_row.addWidget(button("Browse…", self.browse_image))
                    config.addRow(title, path_row)
                else:
                    config.addRow(title, widget)
            if action == "wait_image":
                config.addRow(
                    label(
                        "Continue applies only to this wait timeout. Cancel, image errors and LINE deadlines still stop the macro.",
                        True,
                    )
                )
                self.preview = QLabel("Choose an image to preview.")
                self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.preview.setWordWrap(True)
                config.addRow(self.preview)
                self.preview_button = button("Preview image", self.request_preview)
                self.test_button = button("Test Image Detection", self.test_image)
                config.addRow(self.preview_button)
                config.addRow(self.test_button)
                self.detection = label(
                    "A test checks the current primary screen once. It never runs macro actions.",
                    True,
                )
                config.addRow(self.detection)
            wrapped = scroll(page)
            self.forms.addWidget(wrapped)
            self.action_pages[action] = wrapped
            self.form_widgets[action] = fields
        self.raw_page = QWidget()
        raw_box = QVBoxLayout(self.raw_page)
        raw_box.addWidget(
            label(
                "Existing unsupported step — read-only. Its properties will be preserved. The executor must support it before the macro can be saved.",
                True,
            )
        )
        self.raw_reveal = reveal_raw = QCheckBox(
            "Show existing configuration (may contain private values)"
        )
        raw_box.addWidget(reveal_raw)
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        reveal_raw.toggled.connect(self.raw.setVisible)
        self.raw.hide()
        raw_box.addWidget(self.raw)
        self.forms.addWidget(self.raw_page)
        self.splitter.addWidget(right)
        self.splitter.setSizes([240, 420])
        footer = QHBoxLayout()
        self.status = label("New macro. Add a step to begin.", True)
        footer.addWidget(self.status, 1)
        self.save_button = button("Save", self.save, True)
        self.save_as_button = button("Save As", lambda: self.save(save_as=True))
        footer.addWidget(self.save_button)
        footer.addWidget(self.save_as_button)
        layout.addLayout(footer)
        self.set_document(self.document)

    @staticmethod
    def field_text(widget):
        if isinstance(widget, QComboBox):
            return widget.currentText()
        if isinstance(widget, QPlainTextEdit):
            return widget.toPlainText()
        return widget.text()

    @property
    def dirty(self):
        return self.document.dirty or any(
            self.field_text(w) != self.original_fields.get(k)
            for k, w in self.fields.items()
        )

    def changed(self, *_):
        if not self.loading:
            self.status.setText(
                "● Unsaved changes" if self.dirty else "Saved / no changes"
            )
            self.update_controls(self.app.process_busy, self.app.editors_locked)

    def metadata_changed(self, *_):
        if not self.loading and isinstance(self.document.payload, dict):
            for key, widget in [("name", self.name), ("description", self.description)]:
                if widget.isEnabled() and widget is self.sender():
                    self.document.payload[key] = widget.text()
            self.changed()

    def refresh_files(self, names):
        current = self.document.filename or self.files.currentText()
        set_combo(self.files, names, current)
        self.files.setPlaceholderText("Choose a macro")

    def set_document(self, document):
        self.loading = True
        self.document = document
        self.current_index = -1
        self.fields = {}
        for key, widget in [("name", self.name), ("description", self.description)]:
            value = (
                document.payload.get(key, "")
                if isinstance(document.payload, dict)
                else ""
            )
            widget.setText(
                value
                if isinstance(value, str)
                else "[Existing non-text metadata preserved]"
            )
        self.loading = False
        self.refresh_files(self.app.macro_names)
        self.render(0 if document.steps else -1)
        self.status.setText(
            "Loaded " + document.filename
            if document.filename
            else "New macro. Add a step to begin."
        )
        self.detection.setText(
            "A test checks the primary screen once without executing macro actions."
        )

    def render(self, selected):
        self.steps.blockSignals(True)
        self.steps.clear()
        for index in range(len(self.document.steps)):
            self.steps.addItem(f"{index + 1}. {self.document.summary(index)}")
        self.steps.setCurrentRow(selected)
        self.steps.blockSignals(False)
        self.empty_state.setVisible(not self.document.steps)
        self.load_step(selected)
        self.changed()

    def load_step(self, index):
        self.loading = True
        self.current_index = index
        self.fields, self.original_fields = {}, {}
        self.pending_preview = None
        if not 0 <= index < len(self.document.steps):
            self.forms.setVisible(False)
            self.action.setEnabled(False)
        else:
            self.forms.show()
            step = self.document.steps[index]
            if not self.document.editable(index):
                self.forms.setCurrentWidget(self.raw_page)
                self.raw_reveal.setChecked(False)
                self.raw.hide()
                self.raw.setPlainText(json.dumps(step, indent=2, ensure_ascii=False))
                self.action.setEnabled(False)
            else:
                action = action_of(step)
                self.action.blockSignals(True)
                self.action.setCurrentIndex(self.action.findData(action))
                self.action.blockSignals(False)
                self.forms.setCurrentWidget(self.action_pages[action])
                self.fields = self.form_widgets[action]
                for key, widget in self.fields.items():
                    value = step.get(key, DEFAULTS.get(key, ""))
                    if (
                        key == "keys"
                        and isinstance(value, list)
                        and all(isinstance(k, str) for k in value)
                    ):
                        buffer = io.StringIO()
                        csv.writer(buffer, lineterminator="").writerow(value)
                        value = buffer.getvalue()
                    text = str(value)
                    if isinstance(widget, QComboBox):
                        set_combo(
                            widget,
                            list(
                                next(kind for k, _, kind in FIELDS[action] if k == key)
                            ),
                            text,
                        )
                    elif isinstance(widget, QPlainTextEdit):
                        widget.setPlainText(value if isinstance(value, str) else text)
                    else:
                        widget.setText(text)
                    self.original_fields[key] = self.field_text(widget)
                if action == "text":
                    self.text_reveal.setChecked(False)
                    self.fields["text"].hide()
                if action == "wait_image":
                    relative = self.fields["image"].text()
                    if relative:
                        self.pending_preview = (self.document, index, relative)
                        QTimer.singleShot(0, self.advance_pending)
                    self.preview.clear()
                    self.preview.setText(
                        "Use Preview image or Test Image Detection to check this reference."
                    )
        self.loading = False
        self.update_controls(self.app.process_busy, self.app.editors_locked)

    def form_step(self):
        step = copy.deepcopy(self.document.steps[self.current_index])
        action = action_of(step)
        for key, _, kind in FIELDS[action] + [("delay", "", "number")]:
            raw = self.field_text(self.fields[key])
            if raw == self.original_fields[key]:
                continue
            if kind == "int":
                try:
                    value = int(raw)
                except ValueError:
                    raise ValueError(
                        f"{key.replace('_', ' ').title()} must be a nonnegative integer."
                    ) from None
                if (
                    value < 0
                    or key == "clicks"
                    and not 1 <= value <= 10
                    or key == "consecutive_matches"
                    and value == 0
                ):
                    raise ValueError(f"Invalid {key.replace('_', ' ')}.")
            elif kind == "number":
                try:
                    value = float(raw)
                except ValueError:
                    raise ValueError(
                        f"{key.replace('_', ' ').title()} must be a number of seconds."
                        if key != "confidence"
                        else "Match threshold must be a number."
                    ) from None
                if (
                    not math.isfinite(value)
                    or value < 0
                    or key in {"timeout", "poll_interval", "confidence"}
                    and value == 0
                    or key == "confidence"
                    and value > 1
                ):
                    raise ValueError(
                        f"{key.replace('_', ' ').title()} must be positive."
                        if key != "confidence"
                        else "Match threshold must be greater than 0 and at most 1."
                    )
            elif kind == "keys":
                try:
                    value = next(csv.reader([raw], skipinitialspace=True, strict=True))
                except (csv.Error, StopIteration):
                    raise ValueError(
                        'Enter shortcut keys separated by commas; quote a literal comma as ",".'
                    ) from None
                if not 1 <= len(value) <= 8 or any(not key for key in value):
                    raise ValueError(
                        "Enter one to eight shortcut keys separated by commas."
                    )
            else:
                value = raw
                if kind == "key" and not value:
                    raise ValueError("Choose a keyboard key.")
                if kind == "text" and len(value) > 10000:
                    raise ValueError("Text is limited to 10000 characters.")
                if isinstance(kind, list) and value not in kind:
                    raise ValueError(f"Choose a valid {key.replace('_', ' ')}.")
            if raw != self.original_fields[key]:
                step[key] = value
        return step

    def commit_step(self):
        if 0 <= self.current_index < len(
            self.document.steps
        ) and self.document.editable(self.current_index):
            try:
                step = self.form_step()
            except ValueError as error:
                self.status.setText(f"Step {self.current_index + 1}: {error}")
                return False
            self.document.steps[self.current_index] = step
            self.original_fields = {
                key: self.field_text(w) for key, w in self.fields.items()
            }
            self.steps.item(self.current_index).setText(
                f"{self.current_index + 1}. {self.document.summary(self.current_index)}"
            )
        return True

    def select_step(self, index):
        if self.loading:
            return
        if not self.commit_step():
            self.steps.blockSignals(True)
            self.steps.setCurrentRow(self.current_index)
            self.steps.blockSignals(False)
            return
        self.load_step(index)
        self.changed()

    def change_action(self, *_):
        if self.loading or not 0 <= self.current_index < len(self.document.steps):
            return
        action = self.action.currentData()
        if action not in DEFAULT_STEPS:
            return
        if not self.commit_step():
            self.action.blockSignals(True)
            self.action.setCurrentIndex(
                self.action.findData(action_of(self.document.steps[self.current_index]))
            )
            self.action.blockSignals(False)
            return
        step = self.document.steps[self.current_index]
        for key, value in DEFAULT_STEPS[action].items():
            if key != "action":
                step.setdefault(key, copy.deepcopy(value))
        step["action" if "action" in step else "type"] = action
        self.render(self.current_index)

    def add_step(self, action):
        if not self.commit_step():
            return
        try:
            self.render(self.document.add(action))
        except ValueError as error:
            self.status.setText(str(error))

    def duplicate_step(self):
        if self.current_index >= 0 and self.commit_step():
            try:
                self.render(self.document.duplicate(self.current_index))
            except ValueError as error:
                self.status.setText(str(error))

    def delete_step(self):
        if self.current_index >= 0 and self.app.confirm(
            "Delete step",
            f"Delete step {self.current_index + 1}? This changes the current draft.",
        ):
            self.render(self.document.delete(self.current_index))

    def move_step(self, offset):
        if self.current_index >= 0 and self.commit_step():
            self.render(self.document.move(self.current_index, offset))

    def ask_unsaved(self):
        return QMessageBox.question(
            self,
            "Unsaved macro changes",
            "Save your macro changes before continuing?"
            + (
                " Active automation will stop safely before saving."
                if self.app.process_busy
                else ""
            ),
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )

    def guard_unsaved(self, continuation):
        self.pending_save = None
        if not self.dirty:
            continuation()
            return
        choice = self.ask_unsaved()
        if choice == QMessageBox.StandardButton.Discard:
            continuation()
        elif choice == QMessageBox.StandardButton.Save:
            if self.app.working or self.app.process_busy:
                self.pending_save = continuation
                self.status.setText(
                    "Waiting for active work to stop safely, then saving before continuing…"
                )
                if self.app.process_busy:
                    self.app.stop_bot()
            else:
                self.save(done=continuation)

    def new_macro(self):
        self.guard_unsaved(lambda: self.set_document(MacroDocument.new()))

    def open_macro(self):
        name = self.files.currentText()
        if not name:
            self.status.setText("Choose a macro file first.")
            return
        self.guard_unsaved(
            lambda: self.app.background(
                lambda: self.repository.open(name), self.set_document
            )
        )

    def save(self, *, save_as=False, done=None):
        if self.app.process_busy or self.app.working or self.app.closing:
            self.status.setText(
                "Stop the bot/macro and wait for the current action before saving."
            )
            return
        if not self.commit_step():
            return
        document = self.document
        name = document.filename
        if save_as or not name:
            name, accepted = QInputDialog.getText(
                self,
                "Save macro in scripts/macros",
                "Filename (letters, numbers, underscores or hyphens):",
                text=name or "new_macro.json",
            )
            if not accepted or not name.strip():
                return
            name = name.strip()
            if not name.endswith(".json"):
                name += ".json"
        try:
            macro_name(name)
        except ValueError as error:
            self.status.setText(str(error))
            return
        payload = copy.deepcopy(document.payload)

        def write(expected):
            if self.app.closing:
                return

            def saved(version):
                document.filename, document.version, document.saved = (
                    name,
                    version,
                    copy.deepcopy(payload),
                )
                self.refresh_files(self.app.macro_names + [name])
                self.changed()
                self.status.setText(
                    f"Saved {name}. A private backup was kept when replacing an existing file."
                )
                if not self.app.closing and done:
                    done()
                if not self.app.closing:
                    self.app.refresh_setup()

            self.app.background(
                lambda: self.repository.save(name, payload, expected),
                saved,
                failed=self.save_failed,
            )

        if name == document.filename and not save_as:
            write(document.version)
        else:

            def prepared(version):
                if self.app.closing:
                    return
                if version is not None and not self.app.confirm(
                    "Overwrite macro", f"Replace {name}? A private backup will be kept."
                ):
                    return
                write(document.version if name == document.filename else version)

            self.app.background(
                lambda: fingerprint(self.repository.path(name)), prepared
            )

    def save_failed(self, _):
        self.pending_save = None
        self.status.setText(
            "Save failed. Your draft has been kept; check the error and try again."
        )

    def browse_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select reference image from the project",
            str(self.app.project / "assets"),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not path:
            return

        def chosen(relative):
            if not relative.startswith("assets/") and not self.app.confirm(
                "Reference image location",
                "This image is outside assets. Keep it at the same relative location when moving the project. Use it?",
            ):
                return
            self.fields["image"].setText(relative)
            self.request_preview()

        self.app.background(lambda: self.repository.relative_image(path), chosen)

    def request_preview(self):
        if "image" not in self.fields or self.app.working or self.app.closing:
            return
        relative = self.fields["image"].text()
        if not relative:
            self.preview.setText("Choose a reference image.")
            return

        def preview():
            try:
                return self.repository.preview_image(relative), ""
            except (ValueError, OSError) as error:
                return None, str(error)

        def shown(result):
            payload, warning = result
            self.preview.clear()
            if warning:
                self.preview.setText("Unable to load reference image: " + warning)
            else:
                pixmap = QPixmap()
                if pixmap.loadFromData(payload):
                    self.preview.setPixmap(pixmap)
                else:
                    self.preview.setText("Unable to load reference image.")

        self.app.background(preview, shown)

    def test_image(self):
        if self.app.process_busy or self.app.working or self.app.closing:
            return
        if (
            not self.commit_step()
            or self.current_index < 0
            or action_of(self.document.steps[self.current_index]) != "wait_image"
        ):
            self.status.setText("Select a valid Wait Image step first.")
            return
        step = copy.deepcopy(self.document.steps[self.current_index])
        self.detection.setText("Checking the primary screen once…")

        def detected(result):
            presence = "present" if result["present"] else "absent"
            condition = "satisfied" if result["satisfied"] else "not satisfied"
            self.detection.setText(
                f"Image currently {presence}; {step['condition']} condition {condition} for this snapshot. Playback still uses consecutive matches ({step.get('consecutive_matches', 2)})."
            )

        self.app.background(
            lambda: self.repository.test_image(step),
            detected,
            failed=lambda _: self.detection.setText(
                "Detection failed. No result is available; check the reference image and desktop."
            ),
        )

    def advance_pending(self):
        if self.pending_save and not self.app.working and not self.app.closing:
            if self.app.process_busy:
                self.app._request_stop()
                return False
            continuation, self.pending_save = self.pending_save, None
            if self.dirty:
                self.save(done=continuation)
            else:
                continuation()
            return True
        if (
            self.pending_preview
            and not self.app.working
            and not self.app.closing
            and not self.app.process_busy
        ):
            document, index, relative = self.pending_preview
            self.pending_preview = None
            if (
                document is self.document
                and index == self.current_index
                and "image" in self.fields
                and self.fields["image"].text() == relative
            ):
                self.request_preview()
                return True
        return False

    def update_controls(self, busy, locked):
        self.locked = locked
        self.splitter.setEnabled(not locked)
        self.files.setEnabled(not locked)
        self.open_button.setEnabled(not locked)
        self.new_button.setEnabled(not locked)
        if 0 <= self.current_index < len(self.document.steps):
            self.action.setEnabled(
                not locked and self.document.editable(self.current_index)
            )
        else:
            self.action.setEnabled(False)
        for key, widget in [("name", self.name), ("description", self.description)]:
            value = (
                self.document.payload.get(key, "")
                if isinstance(self.document.payload, dict)
                else None
            )
            widget.setEnabled(not locked and isinstance(value, str))
        self.save_button.setEnabled(not busy and not locked)
        self.save_as_button.setEnabled(not busy and not locked)
        selected = 0 <= self.current_index < len(self.document.steps)
        for item in self.manage_buttons:
            item.setEnabled(not locked and selected)
        self.manage_buttons[2].setEnabled(not locked and self.current_index > 0)
        self.manage_buttons[3].setEnabled(
            not locked
            and selected
            and self.current_index < len(self.document.steps) - 1
        )
        self.test_button.setEnabled(
            not locked
            and not busy
            and selected
            and action_of(self.document.steps[self.current_index]) == "wait_image"
        )
        self.preview_button.setEnabled(
            not locked
            and selected
            and action_of(self.document.steps[self.current_index]) == "wait_image"
        )

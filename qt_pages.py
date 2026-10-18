"""Layout-managed Qt views; persistence and automation belong to existing modules."""

from __future__ import annotations

import copy
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config import boolean, macro_name
from gui_content import FORM_SECTIONS, HELP_PARAGRAPHS
from panel_settings import SECRET_FIELDS


def label(text, muted=False):
    widget = QLabel(text)
    widget.setWordWrap(True)
    if muted:
        widget.setProperty("muted", True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text.replace("&", "&&"))
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(lambda checked=False: callback())
    if primary:
        widget.setProperty("primary", True)
    return widget


def scroll(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setWidget(widget)
    return area


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.setAlternatingRowColors(True)
    widget.setMinimumHeight(130)
    return widget


def render_table(widget, rows):
    widget.setUpdatesEnabled(False)
    widget.setRowCount(len(rows))
    for index, row in enumerate(rows):
        for column, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            widget.setItem(index, column, item)
    widget.setUpdatesEnabled(True)


def set_combo(combo, values, current=None):
    current = combo.currentText() if current is None else current
    names = sorted(set(values + ([current] if current else [])))
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("")
    combo.addItems(names)
    combo.setCurrentText(current)
    combo.blockSignals(False)


class SettingsPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app, self.entries = app, {}
        self.saved, self.version = {}, None
        self.loaded = False
        layout = QVBoxLayout(self)
        layout.addWidget(
            label(
                "Save & Apply safely restarts an online bot so changes take effect together.",
                True,
            )
        )
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        for tab_name, groups in FORM_SECTIONS.items():
            body = QWidget()
            cards = QVBoxLayout(body)
            for title, fields in groups:
                card = QGroupBox(title)
                form = QVBoxLayout(card)
                for key, title, hint in fields:
                    form.addWidget(label(title))
                    row = QHBoxLayout()
                    if key in {"ENABLE_AUTO_LOGOUT", "REPLY_UNKNOWN_COMMANDS"}:
                        entry = QCheckBox("Enabled")
                        entry.toggled.connect(app.refresh_controls)
                    elif key in {"LOGIN_MACRO_SCRIPT", "LOGOUT_MACRO_SCRIPT"}:
                        entry = QComboBox()
                        entry.currentTextChanged.connect(app.refresh_controls)
                    else:
                        entry = QLineEdit()
                        entry.textChanged.connect(app.refresh_controls)
                    entry.setObjectName(key)
                    entry.setAccessibleName(title)
                    entry.setToolTip(hint)
                    self.entries[key] = entry
                    row.addWidget(entry, 1)
                    if key in SECRET_FIELDS:
                        entry.setEchoMode(QLineEdit.EchoMode.Password)
                        toggle = button(
                            "Show",
                            lambda e=entry: e.setEchoMode(
                                QLineEdit.EchoMode.Normal
                                if e.echoMode() == QLineEdit.EchoMode.Password
                                else QLineEdit.EchoMode.Password
                            ),
                        )
                        toggle.setCheckable(True)
                        toggle.toggled.connect(
                            lambda on, b=toggle: b.setText("Hide" if on else "Show")
                        )
                        row.addWidget(toggle)
                    elif key == "NGROK_EXE_PATH":
                        row.addWidget(button("Browse…", self.browse_ngrok))
                    form.addLayout(row)
                    form.addWidget(label(hint, True))
                    form.addSpacing(8)
                if title == "ngrok tunnel":
                    form.addWidget(
                        button(
                            "Get my ngrok token ↗",
                            lambda: QDesktopServices.openUrl(
                                QUrl(
                                    "https://dashboard.ngrok.com/get-started/your-authtoken"
                                )
                            ),
                        )
                    )
                cards.addWidget(card)
            cards.addStretch()
            self.tabs.addTab(scroll(body), tab_name.replace("&", "&&"))
        toolbar = QHBoxLayout()
        self.dirty_label = label("Loading settings…", True)
        toolbar.addWidget(self.dirty_label, 1)
        self.reload_button = button("Reload from file", self.reload_prompt)
        self.save_button = button("Save & Apply", app.save_settings, True)
        toolbar.addWidget(self.reload_button)
        toolbar.addWidget(self.save_button)
        layout.addLayout(toolbar)

    def values(self):
        result = {}
        for key, entry in self.entries.items():
            result[key] = (
                str(entry.isChecked())
                if isinstance(entry, QCheckBox)
                else entry.currentText()
                if isinstance(entry, QComboBox)
                else entry.text()
            )
        return result

    @property
    def dirty(self):
        return self.loaded and self.values() != self.saved

    def load(self, values, version):
        self.loaded = False
        self.version = version
        for key, entry in self.entries.items():
            value = values[key]
            if isinstance(entry, QCheckBox):
                try:
                    entry.setChecked(boolean(value))
                except ValueError:
                    entry.setChecked(False)
            elif isinstance(entry, QComboBox):
                set_combo(entry, self.app.macro_names, value)
            else:
                entry.setText(value)
        self.saved = self.values()
        self.loaded = True

    def refresh_macros(self):
        for entry in self.entries.values():
            if isinstance(entry, QComboBox):
                set_combo(entry, self.app.macro_names)

    def browse_ngrok(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select ngrok.exe",
            str(self.app.project),
            "Windows application (*.exe);;All files (*)",
        )
        if path:
            self.entries["NGROK_EXE_PATH"].setText(path)

    def reload_prompt(self):
        if not self.dirty or self.app.confirm(
            "Reload settings", "Discard unsaved settings and reload .env?"
        ):
            self.app.refresh_setup(force_settings=True)

    def update_controls(self, busy, locked):
        self.tabs.setEnabled(not locked and self.loaded)
        self.reload_button.setEnabled(not locked)
        self.save_button.setEnabled(
            self.loaded
            and not locked
            and (
                not busy
                or self.app.controller.mode == "bot"
                and self.app.controller.state == "online"
            )
        )
        self.dirty_label.setText(
            "Unsaved changes"
            if self.dirty
            else "Saved to .env"
            if self.loaded
            else "Loading settings…"
        )


class TargetsPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app, self.rows, self.saved, self.version = app, [], [], None
        layout = QVBoxLayout(self)
        layout.addWidget(
            label(
                "Map a short target ID to a macro. Example: capture 09D runs DH09D.json.",
                True,
            )
        )
        self.table = table(["Target ID", "Description", "Macro script"])
        self.table.itemSelectionChanged.connect(self.select)
        layout.addWidget(self.table, 1)
        self.editor = QGroupBox("Add or edit a target")
        form = QFormLayout(self.editor)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.target_id, self.target_name, self.target_macro = (
            QLineEdit(),
            QLineEdit(),
            QComboBox(),
        )
        for title, entry in [
            ("Target ID", self.target_id),
            ("Description", self.target_name),
            ("Macro script", self.target_macro),
        ]:
            form.addRow(title, entry)
        actions = QGridLayout()
        for index, (title, action) in enumerate(
            [
                ("Add target", self.add_row),
                ("Update selected", self.update_row),
                ("Remove selected", self.remove_row),
                ("Clear form", self.clear_form),
            ]
        ):
            actions.addWidget(button(title, action), index // 2, index % 2)
        form.addRow(actions)
        layout.addWidget(self.editor)
        toolbar = QHBoxLayout()
        self.state_label = label("", True)
        toolbar.addWidget(self.state_label, 1)
        self.reload_button = button("Reload from file", self.reload_prompt)
        self.save_button = button("Save targets", app.save_targets, True)
        toolbar.addWidget(self.reload_button)
        toolbar.addWidget(self.save_button)
        layout.addLayout(toolbar)

    @property
    def dirty(self):
        return self.rows != self.saved

    def load(self, rows, version):
        self.rows, self.saved, self.version = (
            copy.deepcopy(rows),
            copy.deepcopy(rows),
            version,
        )
        self.render()

    def render(self):
        render_table(self.table, [(r["id"], r["name"], r["macro"]) for r in self.rows])
        self.app.refresh_controls()

    def select(self):
        index = self.table.currentRow()
        if self.app.editors_locked or not 0 <= index < len(self.rows):
            return
        row = self.rows[index]
        self.target_id.setText(row["id"])
        self.target_name.setText(row["name"])
        set_combo(self.target_macro, self.app.macro_names, row["macro"])

    def row(self, exclude=None):
        target_id, name = self.target_id.text().strip(), self.target_name.text().strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", target_id) or not name:
            raise ValueError(
                "Enter a target ID using letters/numbers and a description."
            )
        if any(
            r["id"].upper() == target_id.upper()
            for i, r in enumerate(self.rows)
            if i != exclude
        ):
            raise ValueError("This target ID already exists.")
        return {
            "id": target_id,
            "name": name,
            "macro": macro_name(self.target_macro.currentText()),
        }

    def add_row(self):
        try:
            self.rows.append(self.row())
            self.render()
            self.clear_form()
            self.app.set_notice("Target added. Click Save targets to apply it.")
        except ValueError as error:
            self.app.show_error("Could not add target", error)

    def update_row(self):
        index = self.table.currentRow()
        if index < 0:
            self.app.set_notice("Select a target in the table first.")
            return
        try:
            self.rows[index] = self.row(exclude=index)
            self.render()
            self.app.set_notice("Target updated. Click Save targets to apply it.")
        except ValueError as error:
            self.app.show_error("Could not update target", error)

    def remove_row(self):
        index = self.table.currentRow()
        if index >= 0:
            self.rows.pop(index)
            self.render()
            self.clear_form()

    def clear_form(self):
        self.target_id.clear()
        self.target_name.clear()
        self.target_macro.setCurrentIndex(0)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)

    def reload_prompt(self):
        if not self.dirty or self.app.confirm(
            "Reload targets", "Discard unsaved target changes?"
        ):
            self.app.refresh_setup(force_targets=True)

    def update_controls(self, busy, locked):
        self.editor.setEnabled(not locked)
        self.table.setEnabled(not locked)
        self.reload_button.setEnabled(not locked)
        self.save_button.setEnabled(not busy and not locked)
        self.state_label.setText(
            "Stop the bot to save targets."
            if busy
            else "Unsaved target changes"
            if self.dirty
            else "Saved to targets.json"
        )


class MacrosPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        layout = QVBoxLayout(self)
        layout.addWidget(
            label(
                "Record, import and play your private BMS macros. Stop the bot before using these tools.",
                True,
            )
        )
        self.table = table(["Macro filename", "Steps", "Validation", "Last modified"])
        layout.addWidget(self.table, 1)
        toolbar = QGridLayout()
        self.tool_buttons = []
        for index, (title, action) in enumerate(
            [
                ("Record new macro", self.record),
                ("Import JSON", self.import_file),
                ("Play selected", self.play),
                ("Use for login", lambda: self.choose_default("login")),
                ("Use for logout", lambda: self.choose_default("logout")),
                ("Refresh macros", app.refresh_setup),
                ("Open macros folder", lambda: app.open_folder("scripts/macros")),
            ]
        ):
            item = button(title, action)
            toolbar.addWidget(item, index // 3, index % 3)
            if index < 5:
                self.tool_buttons.append(item)
        layout.addLayout(toolbar)
        anchor = QGroupBox("Login-page detection image")
        box = QVBoxLayout(anchor)
        self.anchor_text = label("Not configured yet")
        box.addWidget(self.anchor_text)
        box.addWidget(
            label(
                "Select a distinctive crop of the BMS login page. A logout anchor is not needed.",
                True,
            )
        )
        self.anchor_button = button("Select login anchor image…", self.import_anchor)
        box.addWidget(self.anchor_button)
        layout.addWidget(anchor)
        layout.addWidget(
            label(
                "Recording: F8 starts/stops and saves; F9 cancels. Playback: switch to BMS during the three-second countdown. The app minimizes for both. Passwords in macros remain private local files.",
                True,
            )
        )

    def selected(self):
        row = self.table.currentRow()
        if row < 0 or not self.table.item(row, 0):
            raise ValueError("Select a macro in the table first.")
        return self.table.item(row, 0).text()

    def load(self, rows, timezone, anchor):
        selected = self.table.item(self.table.currentRow(), 0)
        selected = selected.text() if selected else None
        render_table(
            self.table,
            [
                (
                    r["name"],
                    r["steps"],
                    "Ready" if r["valid"] else "Needs repair",
                    datetime.fromtimestamp(r["modified"], ZoneInfo(timezone)).strftime(
                        "%Y-%m-%d %H:%M"
                    )
                    if r["modified"]
                    else "—",
                )
                for r in rows
            ],
        )
        for index, row in enumerate(rows):
            if row["name"] == selected:
                self.table.selectRow(index)
        self.anchor_text.setText(
            "Configured: " + anchor if anchor else "Not configured yet"
        )

    def choose_default(self, kind, name=None):
        try:
            name = name or self.selected()
            self.app.show_page("Settings")
            entry = self.app.settings.entries[
                "LOGIN_MACRO_SCRIPT" if kind == "login" else "LOGOUT_MACRO_SCRIPT"
            ]
            set_combo(entry, self.app.macro_names, name)
            self.app.settings.tabs.setCurrentIndex(1)
            self.app.set_notice(f"Selected {name} for {kind}. Click Save & Apply.")
            self.app.refresh_controls()
        except ValueError as error:
            self.app.show_error("Could not select macro", error)

    def record(self):
        name, ok = QInputDialog.getText(
            self, "Record macro", "New filename, for example DH09D.json:"
        )
        if ok and name:
            self.app.start_macro("record", name)

    def play(self):
        try:
            self.app.start_macro("play", self.selected())
        except ValueError as error:
            self.app.show_error("Could not play macro", error)

    def import_file(self):
        source, _ = QFileDialog.getOpenFileName(
            self,
            "Import current or old macro JSON",
            str(self.app.project),
            "JSON macros (*.json)",
        )
        if not source:
            return
        name, ok = QInputDialog.getText(
            self,
            "Import macro",
            "Save as filename (existing files are preserved):",
            text=Path(source).name,
        )
        if ok and name:
            self.app.background(
                lambda: self.app.store.import_macro(
                    source, self.app.normalize_macro(name)
                ),
                lambda path: self.imported(f"Imported {path.name}."),
            )

    def imported(self, notice):
        self.app.refresh_setup()
        self.app.set_notice(notice)

    def import_anchor(self):
        source, _ = QFileDialog.getOpenFileName(
            self,
            "Choose your login-page crop",
            str(self.app.project),
            "Images (*.png *.jpg *.jpeg *.webp)",
        )
        if source:
            self.app.background(
                lambda: self.app.store.import_anchor(source),
                lambda _: self.imported(
                    "Login anchor saved to assets/login_anchor.png and configured for login-page detection. Previous image backed up if present."
                ),
            )

    def update_controls(self, busy, locked):
        for item in self.tool_buttons + [self.anchor_button]:
            item.setEnabled(not busy and not locked)


class HelpPage(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        for title, text in HELP_PARAGRAPHS:
            card = QGroupBox(title)
            box = QVBoxLayout(card)
            content = label(text)
            content.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            box.addWidget(content)
            layout.addWidget(card)
        for title, url in [
            ("LINE Developers ↗", "https://developers.line.biz/console/"),
            ("ngrok dashboard ↗", "https://dashboard.ngrok.com/"),
        ]:
            layout.addWidget(
                button(title, lambda target=url: QDesktopServices.openUrl(QUrl(target)))
            )
        layout.addStretch()
        self.setWidget(body)

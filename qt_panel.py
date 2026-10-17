"""Responsive PySide6 presentation for the existing BMS automation system."""

from __future__ import annotations

import copy
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config import BASE_DIR, Settings, macro_name
from line_api import LineAPI
from panel_process import ProcessController
from panel_settings import ProjectStore, fingerprint, settings_change_message
from qt_pages import (
    HelpPage,
    MacrosPage,
    SettingsPage,
    TargetsPage,
    button,
    label,
    render_table,
    scroll,
    set_combo,
    table,
)
from qt_workers import Job, ProcessBridge

PAGE_DESCRIPTIONS = {
    "Dashboard": "Start your bot, check its connection, and see what it is doing.",
    "Settings": "LINE, ngrok, BMS login, image quality, and program preferences.",
    "Targets": "Give each BMS screen a short ID and a descriptive name.",
    "Macros": "Manage the recorded steps used to navigate your BMS.",
    "Help": "Setup instructions and everyday commands in one place.",
}
STATE_NAMES = {
    "stopped": "Stopped",
    "starting": "Starting…",
    "online": "Bot online",
    "stopping": "Stopping safely…",
    "recording": "Recording macro",
    "playing": "Playing macro",
    "error": "Needs attention",
}
STYLE = """
QMainWindow, QWidget { background: #f3f5f8; color: #172b45; }
QWidget { font-size: 10pt; }
QWidget#sidebar, QWidget#sidebar QLabel { background: #14263e; color: #dbe7f1; }
QLabel[muted="true"] { color: #586b80; }
QLabel#pageTitle { font-size: 24pt; font-weight: 600; }
QLabel#brand { font-size: 28pt; font-weight: 700; }
QLabel#state { font-size: 18pt; font-weight: 600; }
QGroupBox { background: white; border: 1px solid #d9e1ea; border-radius: 8px; margin-top: 14px; padding: 18px 12px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 5px; }
QGroupBox QLabel, QGroupBox QCheckBox { background: transparent; font-weight: normal; }
QPushButton { background: white; border: 1px solid #cbd6e3; border-radius: 5px; padding: 8px 12px; }
QPushButton:hover { background: #e6edf4; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus { border: 2px solid #087f76; }
QPushButton[primary="true"] { background: #087f76; color: white; border-color: #087f76; font-weight: 600; }
QPushButton[primary="true"]:hover { background: #086b63; }
QPushButton:disabled { background: #e4e9ef; color: #758495; }
QWidget#sidebar QPushButton { background: #14263e; color: #dbe7f1; border: none; text-align: left; padding: 12px; }
QWidget#sidebar QPushButton:checked, QWidget#sidebar QPushButton:hover { background: #294960; }
QLineEdit, QComboBox { background: white; border: 1px solid #cbd6e3; border-radius: 4px; padding: 7px; min-width: 70px; }
QTableWidget { background: white; alternate-background-color: #f6f8fb; border: 1px solid #d9e1ea; gridline-color: #e6edf4; selection-background-color: #d1eae6; selection-color: #172b45; }
QHeaderView::section { background: #e6edf4; padding: 8px; border: none; font-weight: 600; }
QPlainTextEdit { background: #172a40; color: #dae5ee; border-radius: 6px; padding: 10px; }
QTabBar::tab { padding: 10px 18px; background: #e6edf4; color: #586b80; border: 1px solid #cbd6e3; border-top-left-radius: 5px; border-top-right-radius: 5px; margin-right: 4px; }
QTabBar::tab:hover:!selected { background: #d6e3ec; color: #172b45; }
QTabBar::tab:selected { background: #087f76; color: white; border-color: #087f76; font-weight: 600; }
QTabBar::tab:disabled { background: #e4e9ef; color: #758495; }
QTabBar::tab:selected:disabled { background: #5a938d; color: white; border-color: #5a938d; }
QScrollArea { border: none; }
"""


class QtControlPanel(QMainWindow):
    def __init__(self, project=BASE_DIR, *, controller=None):
        super().__init__()
        self.project = Path(project).resolve()
        self.store = ProjectStore(self.project)
        self.controller = controller or ProcessController(self.project)
        self.jobs = set()
        self.working = self.closing = self.disposed = self.ready = False
        self.pending_restart = None
        self.restart_after_refresh = False
        self.refresh_requested = False
        self.force_settings = self.force_targets = False
        self.macro_names = []
        self.operation_generation = 0
        self._last_state = None
        self._stop_pending = self._cleanup_started = self._cleanup_done = False
        self.setWindowTitle("BMS Automation · Control Panel")
        self.setMinimumSize(760, 560)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1180, screen.width() - 40), min(850, screen.height() - 60))
        self.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet(STYLE)
        self._shell()
        self.pages = {"Dashboard": self._dashboard()}
        self.settings, self.targets, self.macros = (
            SettingsPage(self),
            TargetsPage(self),
            MacrosPage(self),
        )
        self.pages.update(
            {
                "Settings": self.settings,
                "Targets": self.targets,
                "Macros": self.macros,
                "Help": HelpPage(),
            }
        )
        self.page_containers = {}
        for name, page in self.pages.items():
            container = scroll(page) if name in {"Targets", "Macros"} else page
            self.page_containers[name] = container
            self.stack.addWidget(container)
        self.show_page("Dashboard")
        self.bridge = ProcessBridge(self.controller, self)
        self.bridge.batch.connect(self._process_batch)
        self.bridge.finished.connect(self._bridge_finished)
        self.bridge.start()
        self.refresh_setup()

    def _shell(self):
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(175)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 24, 14, 20)
        brand = label("BMS")
        brand.setObjectName("brand")
        side.addWidget(brand)
        side.addWidget(label("LINE AUTOMATION"))
        side.addSpacing(24)
        self.navigation = {}
        for name in PAGE_DESCRIPTIONS:
            item = button(name, lambda page=name: self.show_page(page))
            item.setCheckable(True)
            self.navigation[name] = item
            side.addWidget(item)
        side.addStretch()
        side.addWidget(label("LOCAL DESKTOP BOT"))
        self.sidebar_state = label("Stopped")
        side.addWidget(self.sidebar_state)
        side.addWidget(
            button("Open reports folder", lambda: self.open_folder("screenshots"))
        )
        layout.addWidget(sidebar)
        area = QWidget()
        body = QVBoxLayout(area)
        body.setContentsMargins(22, 18, 22, 14)
        self.title_label = label("Dashboard")
        self.title_label.setObjectName("pageTitle")
        self.subtitle = label(PAGE_DESCRIPTIONS["Dashboard"], True)
        body.addWidget(self.title_label)
        body.addWidget(self.subtitle)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        self.notice = label("Welcome. Check your settings, then click Start Bot.", True)
        self.notice.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        body.addWidget(self.notice)
        layout.addWidget(area, 1)
        self.setCentralWidget(central)

    def _dashboard(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        connection = QGroupBox("Bot connection")
        box = QVBoxLayout(connection)
        row = QHBoxLayout()
        self.state_text = label("Stopped")
        self.state_text.setObjectName("state")
        row.addWidget(self.state_text, 1)
        self.start_button = button("Start Bot", self.start_bot, True)
        self.stop_button = button("Stop Bot", self.stop_bot)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        box.addLayout(row)
        fields = QFormLayout()
        fields.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.delivery, self.default_macro = label("Not configured"), label("")
        fields.addRow("Delivery", self.delivery)
        fields.addRow("Login macro", self.default_macro)
        self.webhook = QLineEdit("Available after ngrok connects")
        self.webhook.setReadOnly(True)
        webhook_row = QHBoxLayout()
        webhook_row.addWidget(self.webhook, 1)
        webhook_row.addWidget(button("Copy", self.copy_webhook))
        fields.addRow("Webhook URL", webhook_row)
        box.addLayout(fields)
        self.minimize_on_start = QCheckBox(
            "Minimize this window when the bot goes online"
        )
        self.minimize_on_start.setChecked(True)
        box.addWidget(self.minimize_on_start)
        box.addWidget(
            label(
                "Keep the BMS visible while capturing. Stop finishes active work before closing the bot and its tunnel.",
                True,
            )
        )
        layout.addWidget(connection)
        setup = QGroupBox("Setup checklist")
        setup_layout = QVBoxLayout(setup)
        self.setup_table = table(["Status", "Item", "Details"])
        self.setup_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.setup_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.setup_table.setMinimumHeight(160)
        setup_layout.addWidget(self.setup_table)
        layout.addWidget(setup)
        toolbar = QHBoxLayout()
        toolbar.addWidget(button("Refresh setup", self.refresh_setup))
        self.quota_button = button("Check LINE quota", self.check_quota)
        toolbar.addWidget(self.quota_button)
        toolbar.addStretch()
        toolbar.addWidget(label("Live log", True))
        layout.addLayout(toolbar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(800)
        self.log.setFont(QFont("Consolas", 9))
        self.log.setMinimumHeight(160)
        layout.addWidget(self.log, 1)
        return scroll(body)

    @property
    def process_busy(self):
        # The controller holds its lock across filesystem/Popen work. Reading
        # its published state keeps Qt independent of that blocking lock.
        return self.controller.state in {
            "starting",
            "online",
            "stopping",
            "recording",
            "playing",
        }

    @property
    def editors_locked(self):
        return (
            self.working
            or self.pending_restart is not None
            or self.closing
            or not self.ready
        )

    def show_page(self, name):
        if name not in self.pages:
            return
        self.current_page = name
        self.stack.setCurrentWidget(self.page_containers[name])
        self.title_label.setText(name)
        self.subtitle.setText(PAGE_DESCRIPTIONS[name])
        for title, item in self.navigation.items():
            item.setChecked(title == name)
        if self.ready and name in {"Settings", "Targets", "Macros"}:
            self.refresh_setup()

    def set_notice(self, text):
        self.notice.setText(text)

    def confirm(self, title, text):
        return (
            QMessageBox.question(
                self,
                title,
                text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def show_error(self, title, error):
        if isinstance(
            error, (ValueError, RuntimeError, FileExistsError, FileNotFoundError)
        ):
            text = str(error)
        elif isinstance(error, OSError):
            text = "Check folder permissions or pause OneDrive syncing, then try again."
        else:
            text = "The action could not finish. Check your settings and package installation."
        self.set_notice(title + ": " + text)
        # Nonmodal: reporting an error must not suspend progress or cleanup.
        dialog = QMessageBox(
            QMessageBox.Icon.Critical, title, text, QMessageBox.StandardButton.Ok, self
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()

    def background(self, function, done=None):
        if self.working or self.closing:
            self.set_notice("Please wait for the current action to finish.")
            return False
        self.working = True
        self._job(function, done, action=True)
        self.refresh_controls()
        return True

    def _job(self, function, done=None, *, action=False, failed=None):
        job = Job(function, done, self)
        job.action = action
        job.failed = failed
        self.jobs.add(job)
        job.finished.connect(self._job_done)
        job.start()

    @Slot()
    def _job_done(self):
        job = self.sender()
        self.jobs.remove(job)
        if job.action:
            self.working = False
        if job.error:
            if job.failed:
                job.failed(job.error)
            self.show_error("Could not finish action", job.error)
        elif job.done:
            try:
                job.done(job.result)
            except Exception as error:
                self.show_error("Could not update interface", error)
        job.deleteLater()
        self.refresh_controls()
        self._advance()

    def _snapshot(self):
        values, version = self.store.load_settings()
        warning = ""
        try:
            targets, target_version = self.store.load_targets()
        except (OSError, ValueError):
            targets, target_version = [], fingerprint(self.project / "targets.json")
            warning = "targets.json needs repair. Re-create targets here; saving keeps a backup."
        try:
            cfg = Settings.load(self.project, environ={})
            macros = self.store.list_macros()
            timezone = cfg.timezone
            summary = (
                f"{cfg.delivery_kind.title()} · {cfg.delivery_id}"
                if cfg.delivery_id
                else "Discovery only · send check-id in LINE",
                cfg.default_login_macro,
                cfg.public_tunnel_url + "/callback" if cfg.public_tunnel_url else "",
            )
        except (ValueError, OSError, KeyError, ZoneInfoNotFoundError):
            macros, timezone = [], "Asia/Bangkok"
            summary = ("Correct invalid settings to continue", "", "")
        anchor = next(
            (
                name
                for name in ("login_anchor.png", "login-anchor.png")
                if (self.project / "assets" / name).is_file()
            ),
            "",
        )
        return (
            values,
            version,
            targets,
            target_version,
            macros,
            timezone,
            summary,
            anchor,
            self.store.readiness(),
            warning,
        )

    def refresh_setup(self, *, force_settings=False, force_targets=False):
        if self.closing:
            return
        self.force_settings |= force_settings
        self.force_targets |= force_targets
        if self.working:
            self.refresh_requested = True
            return
        self.refresh_requested = False
        force_s, force_t = self.force_settings, self.force_targets
        self.force_settings = self.force_targets = False
        self.background(
            self._snapshot,
            lambda result: self._snapshot_loaded(result, force_s, force_t),
        )

    def _snapshot_loaded(self, snapshot, force_settings, force_targets):
        (
            values,
            version,
            targets,
            target_version,
            macros,
            timezone,
            summary,
            anchor,
            readiness,
            warning,
        ) = snapshot
        self.macro_names = [row["name"] for row in macros]
        if force_settings or not self.settings.dirty:
            self.settings.load(values, version)
        if force_targets or not self.targets.dirty:
            self.targets.load(targets, target_version)
        self.settings.refresh_macros()
        set_combo(self.targets.target_macro, self.macro_names)
        self.macros.load(macros, timezone, anchor)
        self.delivery.setText(summary[0])
        self.default_macro.setText(summary[1])
        if summary[2]:
            self.webhook.setText(summary[2])
        render_table(
            self.setup_table,
            [
                ("OK" if row["ok"] else "Fix", row["name"], row["detail"])
                for row in sorted(readiness, key=lambda r: r["ok"])
            ],
        )
        self.ready = True
        if warning:
            self.set_notice(warning)
        if self.restart_after_refresh and not self.closing:
            self.restart_after_refresh = False
            self.start_bot()

    def _save_settings_and_notify(self, values, version, previous):
        saved = self.store.save_settings(values, version)
        message = settings_change_message(previous, values)
        if not message:
            return saved, ""
        cfg = Settings.load(self.project, environ={})
        if not cfg.channel_access_token or not cfg.delivery_id:
            return (
                saved,
                "Settings saved; LINE notification was skipped because delivery is not configured.",
            )
        try:
            line = LineAPI(cfg.channel_access_token)
            line.push(cfg.delivery_id, [line.text(message)])
        except Exception:
            return (
                saved,
                "Settings saved, but LINE could not receive the change notification.",
            )
        return saved, ""

    def save_settings(self):
        if self.editors_locked:
            return
        values, version, previous = (
            self.settings.values(),
            self.settings.version,
            dict(self.settings.saved),
        )
        if values == previous:
            self.set_notice("No settings changed.")
            return
        if self.process_busy:
            generation = self.operation_generation
            if self.controller.state != "online" or self.controller.mode != "bot":
                self.set_notice("Stop the bot or macro before applying settings.")
                return
            if self.targets.dirty:
                self.set_notice(
                    "Save or reload Targets before restarting with new settings."
                )
                return

            def validate():
                self.store.validate_settings(values)
                if fingerprint(self.project / ".env") != version:
                    raise ValueError(
                        "Settings changed. Reload from file before saving."
                    )

            def validated(_):
                if self.closing or generation != self.operation_generation:
                    return
                self.pending_restart = (values, version, previous)
                self._request_stop()
                self.set_notice(
                    "Applying settings: stopping safely, saving .env, then restarting…"
                )

            self.background(validate, validated)
        else:
            self._save(values, version, previous, restart=False)

    def _save(self, values, version, previous, *, restart):
        def saved(result):
            self.settings.version, warning = result
            self.settings.saved = dict(values)
            self.restart_after_refresh = restart and not self.closing
            if not self.closing:
                self.refresh_setup(force_settings=True)
                self.set_notice(
                    warning
                    or "Settings saved and applied to .env. A private backup was kept."
                )

        self.background(
            lambda: self._save_settings_and_notify(values, version, previous), saved
        )

    def save_targets(self):
        if self.process_busy or self.editors_locked:
            self.set_notice("Stop the bot before saving targets.")
            return
        rows, version = copy.deepcopy(self.targets.rows), self.targets.version

        def saved(result):
            self.targets.version, self.targets.saved = result, rows
            self.refresh_setup(force_targets=True)
            self.set_notice("Targets saved. Start the bot to use the updated registry.")

        self.background(lambda: self.store.save_targets(rows, version), saved)

    def start_bot(self):
        if self.closing or not self.ready:
            return
        if self.settings.dirty or self.targets.dirty:
            self.set_notice(
                "Save or reload unsaved settings and targets before starting."
            )
            return
        self.show_page("Dashboard")
        self.background(
            self.controller.start_bot,
            lambda _: self.set_notice(
                "Starting the bot. Connection progress appears in the live log."
            ),
        )

    def _request_stop(self):
        if (
            not self._stop_pending
            and self.process_busy
            and self.controller.state != "stopping"
        ):
            self._stop_pending = True

            def stopped(_):
                self._stop_pending = False

            def failed(_):
                self._stop_pending = False
                self.pending_restart = None
                self.restart_after_refresh = False
                self.closing = False

            self._job(self.controller.stop, stopped, failed=failed)

    def stop_bot(self):
        self.operation_generation += 1
        self.pending_restart = None
        self.restart_after_refresh = False
        self._request_stop()
        self.set_notice(
            "Stop requested. Active work will finish before the bot closes."
        )

    @staticmethod
    def normalize_macro(name):
        name = name.strip()
        return macro_name(name if name.endswith(".json") else name + ".json")

    def start_macro(self, command, name):
        if self.settings.dirty:
            self.set_notice("Save or reload settings before recording/playing a macro.")
            return

        def launch():
            normalized = self.normalize_macro(name)
            path = self.project / "scripts/macros" / normalized
            if command == "record" and path.exists():
                raise FileExistsError(
                    "This macro already exists. Choose another filename."
                )
            if command == "play" and not path.is_file():
                raise FileNotFoundError("Select an existing macro.")
            self.controller.start_macro(command, normalized)

        def launched(_):
            if not self.closing:
                self.showMinimized()
                self.set_notice(
                    "Macro tool running. Use F8/F9 for recording or Stop to cancel."
                )

        self.background(launch, launched)

    def check_quota(self):
        def query():
            cfg = Settings.load(self.project, environ={})
            return LineAPI(cfg.channel_access_token).status_text(
                cfg.group_id, cfg.user_id
            )

        def show(text):
            if not self.closing:
                dialog = QMessageBox(
                    QMessageBox.Icon.Information,
                    "LINE messaging quota",
                    text,
                    QMessageBox.StandardButton.Ok,
                    self,
                )
                dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                dialog.open()

        self.background(query, show)

    def copy_webhook(self):
        if self.webhook.text().startswith("https://"):
            QApplication.clipboard().setText(self.webhook.text())
            self.set_notice(
                "Webhook URL copied. Paste into LINE Developers → Messaging API."
            )

    def open_folder(self, relative):
        def prepare():
            folder = self.project / relative
            folder.mkdir(parents=True, exist_ok=True)
            return folder

        self.background(
            prepare,
            lambda folder: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))),
        )

    def refresh_controls(self, *_):
        if not hasattr(self, "settings"):
            return
        busy = self.process_busy
        state = STATE_NAMES.get(self.controller.state, "Stopped")
        self.state_text.setText(state)
        self.sidebar_state.setText(state)
        self.start_button.setEnabled(
            self.ready and not busy and not self.editors_locked
        )
        self.stop_button.setText(
            "Cancel macro" if busy and self.controller.mode != "bot" else "Stop Bot"
        )
        self.stop_button.setEnabled(
            busy and self.controller.state != "stopping" and not self._stop_pending
        )
        self.quota_button.setEnabled(self.ready and not self.editors_locked)
        for page in (self.settings, self.targets, self.macros):
            page.update_controls(busy, self.editors_locked)

    @Slot(object)
    def _process_batch(self, payload):
        try:
            events, (state, _mode, status) = payload
            logs = [value for kind, value in events if kind == "log"]
            if logs:
                self.log.appendPlainText("\n".join(logs))
            for kind, value in events:
                if kind == "finished":
                    self.refresh_setup()
                    if value["code"] not in (0, 130):
                        self.set_notice(
                            "The process stopped with an error. Read the live log and check Settings."
                        )
                    elif self.pending_restart is None and not self.closing:
                        self.set_notice(
                            "Stopped safely. Settings and reports are preserved."
                        )
            if state != self._last_state:
                self._last_state = state
                if state == "online":
                    self.webhook.setText(status.get("webhook", self.webhook.text()))
                    self.refresh_setup()
                    self.set_notice(
                        "Bot online. Send capture in LINE; keep the BMS visible."
                    )
                    if self.minimize_on_start.isChecked() and not self.closing:
                        self.showMinimized()
            self.refresh_controls()
            self._advance()
        finally:
            self.bridge.acknowledged.set()

    def _advance(self):
        if self.closing:
            self._request_stop()
            if not self.jobs and not self.process_busy and not self._cleanup_started:
                self._cleanup_started = True
                self._job(
                    self.controller.close, self._cleaned, failed=self._cleanup_failed
                )
            return
        if self.working:
            return
        if self.pending_restart and not self.process_busy:
            values, version, previous = self.pending_restart
            self.pending_restart = None
            self._save(values, version, previous, restart=True)
        elif self.refresh_requested:
            self.refresh_setup()

    def _cleanup_failed(self, _):
        self._cleanup_started = False
        self.closing = False

    def _cleaned(self, _):
        self._cleanup_done = True
        self.bridge.stop()

    @Slot()
    def _bridge_finished(self):
        if self.closing and self._cleanup_done:
            self.disposed = True
            self.close()

    def request_close(self, *, confirm=True):
        if self.closing:
            return
        dirty = self.settings.dirty or self.targets.dirty
        if confirm and (self.process_busy or self.working or dirty):
            text = "Stop active work and close? Active bot work will finish first."
            if dirty:
                text += " Unsaved settings/target changes will be discarded."
            if not self.confirm("Close control panel", text):
                return
        self.closing = True
        self.operation_generation += 1
        self.pending_restart = None
        self.restart_after_refresh = self.refresh_requested = False
        self.set_notice("Closing after active work finishes…")
        self.refresh_controls()
        self._advance()

    def closeEvent(self, event):
        if self.disposed:
            event.accept()
        else:
            event.ignore()
            self.request_close()


def main():
    from launcher import launcher_lock

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("BMS Control Panel")
    try:
        lock_dir = BASE_DIR / ".runtime" / "panel-instance"
        lock_dir.mkdir(parents=True, exist_ok=True)
        with launcher_lock(lock_dir):
            panel = QtControlPanel()
            panel.show()
            return app.exec()
    except Exception as error:
        text = (
            str(error)
            if isinstance(error, (RuntimeError, ValueError, OSError))
            else "The interface could not open. Launch start_gui.bat and check its setup messages."
        )
        QMessageBox.critical(None, "BMS Control Panel", text)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

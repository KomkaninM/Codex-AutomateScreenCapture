"""Native Windows control panel: python control_panel.py or start_gui.bat."""

from __future__ import annotations

import copy
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont, messagebox, ttk

from config import BASE_DIR, Settings, macro_name
from line_api import LineAPI
from panel_pages import (
    Scrollable,
    HelpPage,
    MacrosPage,
    SettingsPage,
    TargetsPage,
)
from panel_process import ProcessController
from panel_settings import ProjectStore, fingerprint
from panel_theme import configure_ttk_styles, palette, preferred_theme
from panel_widgets import LogViewBuffer, classify_log_line, status_style

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


class ControlPanel:
    def __init__(self, root, project=BASE_DIR, *, controller=None):
        self.root = root
        self.project = Path(project).resolve()
        self.store = ProjectStore(self.project)
        self.controller = controller or ProcessController(self.project)
        self.actions = queue.Queue()
        self.working = False
        self.closing = False
        self.pending_restart = None
        self.current_page = "Dashboard"
        self.last_state = ""
        self.disposed = False
        self.theme_name = preferred_theme()
        self.theme = palette(self.theme_name)
        self.log_buffer = LogViewBuffer(limit=800)
        self.log_query = tk.StringVar(value="")
        self.log_filter = tk.StringVar(value="All activity")
        self.log_autoscroll = tk.BooleanVar(value=True)
        self.log_count = tk.StringVar(value="No activity yet")
        self.minimize_on_start = tk.BooleanVar(value=True)
        self.notice = tk.StringVar(
            value="Welcome. Check your settings, then click Start Bot."
        )
        self.state_text = tk.StringVar(value="Stopped")
        self.webhook = tk.StringVar(value="Available after ngrok connects")
        self.delivery = tk.StringVar(value="Not configured")
        self.default_macro = tk.StringVar()
        root.title("BMS Automation · Control Panel")
        root.geometry(
            f"{min(1180, root.winfo_screenwidth() - 80)}x{min(800, root.winfo_screenheight() - 100)}"
        )
        root.minsize(940, 620)
        root.configure(background=self.theme.background)
        root.protocol("WM_DELETE_WINDOW", self.request_close)
        self._styles()
        self._shell()
        self.pages = {"Dashboard": self._dashboard(self.content)}
        self.settings = SettingsPage(self.content, self)
        self.targets = TargetsPage(self.content, self)
        self.macros = MacrosPage(self.content, self)
        self.pages.update(
            {
                "Settings": self.settings,
                "Targets": self.targets,
                "Macros": self.macros,
                "Help": HelpPage(self.content, self),
            }
        )
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")
        self.refresh_setup()
        self.show_page("Dashboard")
        self._refresh_controls()
        self.poll_id = root.after(100, self._poll)
        root.bind("<Destroy>", self._on_destroy, add="+")

    def _styles(self):
        self.font_family = (
            "Segoe UI"
            if os.name == "nt"
            else tkfont.nametofont("TkDefaultFont").actual("family")
        )
        self.mono_family = "Consolas" if os.name == "nt" else "Courier"
        self.style = ttk.Style(self.root)
        self.style.theme_use("clam")
        configure_ttk_styles(
            self.style, self.theme, self.font_family, self.mono_family
        )

    def _shell(self):
        self.sidebar = tk.Frame(
            self.root, background=self.theme.sidebar, width=210
        )
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.brand_title = tk.Label(
            self.sidebar,
            text="BMS",
            font=(self.font_family, 28, "bold"),
            background=self.theme.sidebar,
            foreground=self.theme.sidebar_text,
            anchor="w",
        )
        self.brand_title.pack(fill="x", padx=24, pady=(26, 0))
        self.brand_subtitle = tk.Label(
            self.sidebar,
            text="LINE AUTOMATION",
            font=(self.font_family, 9),
            background=self.theme.sidebar,
            foreground=self.theme.sidebar_muted,
            anchor="w",
        )
        self.brand_subtitle.pack(fill="x", padx=25, pady=(0, 28))
        self.navigation = {}
        for name in PAGE_DESCRIPTIONS:
            button = tk.Button(
                self.sidebar,
                text=name,
                anchor="w",
                padx=18,
                pady=13,
                font=(self.font_family, 11),
                background=self.theme.sidebar,
                foreground=self.theme.sidebar_text,
                activebackground=self.theme.sidebar_active,
                activeforeground=self.theme.sidebar_text,
                relief="flat",
                borderwidth=0,
                cursor="hand2",
                highlightthickness=2,
                highlightbackground=self.theme.sidebar,
                highlightcolor=self.theme.focus,
                takefocus=True,
                command=lambda page=name: self.show_page(page),
            )
            button.pack(fill="x", padx=12, pady=3)
            self.navigation[name] = button
        self.sidebar_bottom = tk.Frame(self.sidebar, background=self.theme.sidebar)
        self.sidebar_bottom.pack(side="bottom", fill="x", padx=20, pady=24)
        self.sidebar_caption = tk.Label(
            self.sidebar_bottom,
            text="LOCAL DESKTOP BOT",
            background=self.theme.sidebar,
            foreground=self.theme.sidebar_muted,
            font=(self.font_family, 8),
            anchor="w",
        )
        self.sidebar_caption.pack(fill="x")
        self.sidebar_state = tk.Label(
            self.sidebar_bottom,
            textvariable=self.state_text,
            background=self.theme.sidebar,
            foreground=self.theme.sidebar_text,
            font=(self.font_family, 11, "bold"),
            anchor="w",
        )
        self.sidebar_state.pack(fill="x", pady=(6, 12))
        self.reports_button = tk.Button(
            self.sidebar_bottom,
            text="Open reports folder",
            command=lambda: self.open_folder("screenshots"),
            relief="flat",
            background=self.theme.sidebar_active,
            foreground=self.theme.sidebar_text,
            activebackground=self.theme.primary,
            activeforeground=self.theme.on_primary,
            pady=9,
            cursor="hand2",
            highlightthickness=2,
            highlightbackground=self.theme.sidebar,
            highlightcolor=self.theme.focus,
            takefocus=True,
        )
        self.reports_button.pack(fill="x")
        self.area = ttk.Frame(self.root, style="Page.TFrame", padding=24)
        self.area.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(self.area, style="Header.TFrame")
        header.pack(fill="x", pady=(0, 18))
        title_area = ttk.Frame(header, style="Header.TFrame")
        title_area.pack(side="left", fill="x", expand=True)
        self.title_label = ttk.Label(
            title_area, text="Dashboard", style="PageTitle.TLabel"
        )
        self.title_label.pack(anchor="w")
        self.subtitle = ttk.Label(
            title_area,
            text=PAGE_DESCRIPTIONS["Dashboard"],
            style="Muted.TLabel",
            wraplength=650,
        )
        self.subtitle.pack(anchor="w", pady=(5, 0))
        header_actions = ttk.Frame(header, style="Header.TFrame")
        header_actions.pack(side="right", padx=(16, 0))
        self.theme_button = ttk.Button(
            header_actions,
            text="Light theme" if self.theme_name == "dark" else "Dark theme",
            command=self.toggle_theme,
        )
        self.theme_button.pack(side="left", padx=(0, 8))
        self.header_status = ttk.Label(
            header_actions,
            textvariable=self.state_text,
            style=status_style(self.controller.state),
        )
        self.header_status.pack(side="left", padx=(0, 8))
        self.header_action = ttk.Button(
            header_actions,
            text="Start Bot",
            style="Primary.TButton",
            command=self.toggle_bot,
        )
        self.header_action.pack(side="left")
        ttk.Label(
            self.area,
            textvariable=self.notice,
            style="Muted.TLabel",
            wraplength=850,
        ).pack(side="bottom", anchor="w", fill="x", pady=(15, 0))
        self.content = ttk.Frame(self.area, style="Page.TFrame")
        self.content.pack(fill="both", expand=True)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

    def _dashboard(self, parent):
        page = Scrollable(parent)
        page.body.configure(padding=0)
        overview = ttk.Frame(page.body, style="Page.TFrame")
        overview.pack(fill="x", pady=(0, 12))
        for column in range(3):
            overview.columnconfigure(column, weight=1, uniform="health")

        def health_card(column, title, variable, detail):
            frame = ttk.LabelFrame(
                overview, text=title, style="Card.TLabelframe", padding=16
            )
            frame.grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 6, 0 if column == 2 else 6),
            )
            value = ttk.Label(
                frame,
                textvariable=variable,
                style="Field.TLabel",
                font=(self.font_family, 13, "bold"),
                wraplength=240,
            )
            value.pack(anchor="w", fill="x")
            ttk.Label(
                frame,
                text=detail,
                style="Hint.TLabel",
                wraplength=240,
                justify="left",
            ).pack(anchor="w", pady=(6, 0))
            return value

        self.dashboard_status = health_card(
            0, "Bot", self.state_text, "Current local process state"
        )
        health_card(1, "LINE delivery", self.delivery, "Group takes priority over user")
        health_card(2, "Login macro", self.default_macro, "Runs when login is detected")

        summary = ttk.LabelFrame(
            page.body, text="Connection and controls", style="Card.TLabelframe", padding=18
        )
        summary.pack(fill="x")
        row = ttk.Frame(summary, style="Card.TFrame")
        row.pack(fill="x", pady=(0, 12))
        ttk.Label(
            row,
            text="Run the bot and keep the BMS visible for screenshots.",
            style="Field.TLabel",
            font=(self.font_family, 11, "bold"),
        ).pack(side="left")
        self.stop_button = ttk.Button(
            row, text="Stop Bot", style="Danger.TButton", command=self.stop_bot
        )
        self.stop_button.pack(side="right", padx=(10, 0))
        self.start_button = ttk.Button(
            row, text="Start Bot", style="Primary.TButton", command=self.start_bot
        )
        self.start_button.pack(side="right")
        webhook_row = ttk.Frame(summary, style="Card.TFrame")
        webhook_row.pack(fill="x")
        ttk.Label(webhook_row, text="Webhook URL:", style="Hint.TLabel", width=14).pack(
            side="left"
        )
        ttk.Entry(webhook_row, textvariable=self.webhook, state="readonly").pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(webhook_row, text="Copy", command=self.copy_webhook).pack(
            side="left", padx=(8, 0)
        )
        ttk.Checkbutton(
            summary,
            text="Minimize this window when the bot goes online",
            variable=self.minimize_on_start,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=(12, 0))
        ttk.Label(
            summary,
            text="Keep the BMS visible while capturing. Stop finishes active work before closing the bot and its tunnel.",
            style="Hint.TLabel",
            wraplength=800,
        ).pack(anchor="w", pady=(6, 0))
        setup = ttk.LabelFrame(
            page.body, text="Setup readiness", padding=12, style="Card.TLabelframe"
        )
        setup.pack(fill="x", pady=14)
        self.setup_table = ttk.Treeview(
            setup,
            columns=("status", "item", "detail"),
            show="headings",
            height=3,
            selectmode="none",
        )
        for key, title, width in [
            ("status", "", 45),
            ("item", "Item", 145),
            ("detail", "Details", 530),
        ]:
            self.setup_table.heading(key, text=title)
            self.setup_table.column(
                key, width=width, minwidth=40, stretch=key == "detail"
            )
        self.setup_table.tag_configure("warning", foreground=self.theme.warning)
        self.setup_table.tag_configure("ready", foreground=self.theme.success)
        setup_scroll = ttk.Scrollbar(
            setup, orient="vertical", command=self.setup_table.yview
        )
        self.setup_table.configure(yscrollcommand=setup_scroll.set)
        setup_scroll.pack(side="right", fill="y")
        self.setup_table.pack(side="left", fill="x", expand=True)
        toolbar = ttk.Frame(page.body, style="Page.TFrame")
        toolbar.pack(fill="x", pady=(0, 12))
        ttk.Button(toolbar, text="Refresh setup", command=self.refresh_setup).pack(
            side="left"
        )
        self.quota_button = ttk.Button(
            toolbar, text="Check LINE quota", command=self.check_quota
        )
        self.quota_button.pack(side="left", padx=8)
        ttk.Button(
            toolbar,
            text="Open macros folder",
            command=lambda: self.open_folder("scripts/macros"),
        ).pack(side="left")
        ttk.Label(toolbar, text="Live activity", style="SectionTitle.TLabel").pack(
            side="right"
        )
        log_tools = ttk.Frame(page.body, style="Page.TFrame")
        log_tools.pack(fill="x", pady=(0, 8))
        ttk.Label(log_tools, text="Search", style="Muted.TLabel").pack(side="left")
        self.log_search = ttk.Entry(
            log_tools, textvariable=self.log_query, width=24
        )
        self.log_search.pack(side="left", padx=(6, 8))
        self.log_filter_box = ttk.Combobox(
            log_tools,
            textvariable=self.log_filter,
            values=("All activity", "Information", "Warnings", "Errors"),
            state="readonly",
            width=15,
        )
        self.log_filter_box.pack(side="left")
        ttk.Checkbutton(
            log_tools,
            text="Follow new activity",
            variable=self.log_autoscroll,
        ).pack(side="left", padx=10)
        self.clear_log_button = ttk.Button(
            log_tools, text="Clear view", command=self.clear_log_view
        )
        self.clear_log_button.pack(side="right")
        self.copy_log_button = ttk.Button(
            log_tools, text="Copy visible", command=self.copy_log
        )
        self.copy_log_button.pack(side="right", padx=(0, 8))
        log_frame = ttk.Frame(page.body, style="Page.TFrame")
        log_frame.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame)
        self.log = tk.Text(
            log_frame,
            height=8,
            font=(self.mono_family, 9),
            background=self.theme.log_background,
            foreground=self.theme.log_text,
            insertbackground=self.theme.log_text,
            relief="flat",
            padx=12,
            pady=10,
            state="disabled",
            wrap="word",
            yscrollcommand=scrollbar.set,
        )
        scrollbar.configure(command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        ttk.Label(
            page.body, textvariable=self.log_count, style="Muted.TLabel"
        ).pack(anchor="w", pady=(6, 0))
        self.log_query.trace_add("write", lambda *_: self._render_log())
        self.log_filter.trace_add("write", lambda *_: self._render_log())
        return page

    def macro_names(self):
        return sorted(
            path.name
            for path in (self.project / "scripts" / "macros").glob("*.json")
            if re_safe_macro(path.name)
        )

    @property
    def editors_locked(self):
        return self.working or self.pending_restart is not None or self.closing

    def show_page(self, name):
        if name not in self.pages:
            return
        self.current_page = name
        self.pages[name].tkraise()
        self.title_label.configure(text=name)
        self.subtitle.configure(text=PAGE_DESCRIPTIONS[name])
        for page, button in self.navigation.items():
            button.configure(
                background=(
                    self.theme.sidebar_active if page == name else self.theme.sidebar
                ),
                foreground=self.theme.sidebar_text,
            )
        if name == "Settings" and not self.settings.dirty and not self.editors_locked:
            self.settings.reload()
        if name == "Targets" and not self.targets.dirty and not self.editors_locked:
            self.targets.reload()
        if name == "Macros":
            self.macros.refresh()

    def settings_notebook_automation(self):
        for widget in self.settings.winfo_children():
            if isinstance(widget, ttk.Notebook):
                widget.select(1)
                return

    def toggle_bot(self):
        if self.controller.busy:
            self.stop_bot()
        else:
            self.start_bot()

    def toggle_theme(self):
        self.apply_theme("dark" if self.theme_name == "light" else "light")

    def apply_theme(self, name):
        self.theme_name = name
        self.theme = palette(name)
        self.root.configure(background=self.theme.background)
        configure_ttk_styles(
            self.style, self.theme, self.font_family, self.mono_family
        )
        for widget in (
            self.sidebar,
            self.sidebar_bottom,
            self.brand_title,
            self.brand_subtitle,
            self.sidebar_caption,
            self.sidebar_state,
        ):
            widget.configure(background=self.theme.sidebar)
        self.brand_title.configure(foreground=self.theme.sidebar_text)
        self.brand_subtitle.configure(foreground=self.theme.sidebar_muted)
        self.sidebar_caption.configure(foreground=self.theme.sidebar_muted)
        self.sidebar_state.configure(foreground=self.theme.sidebar_text)
        self.reports_button.configure(
            background=self.theme.sidebar_active,
            foreground=self.theme.sidebar_text,
            activebackground=self.theme.primary,
            activeforeground=self.theme.on_primary,
            highlightbackground=self.theme.sidebar,
            highlightcolor=self.theme.focus,
        )
        for page, button in self.navigation.items():
            active = page == self.current_page
            button.configure(
                background=(
                    self.theme.sidebar_active if active else self.theme.sidebar
                ),
                foreground=self.theme.sidebar_text,
                activebackground=self.theme.sidebar_active,
                activeforeground=self.theme.sidebar_text,
                highlightbackground=self.theme.sidebar,
                highlightcolor=self.theme.focus,
            )
        for page in self.pages.values():
            for widget in self._walk_widgets(page):
                if isinstance(widget, tk.Canvas):
                    widget.configure(background=self.theme.background)
        self.log.configure(
            background=self.theme.log_background,
            foreground=self.theme.log_text,
            insertbackground=self.theme.log_text,
        )
        self.setup_table.tag_configure("warning", foreground=self.theme.warning)
        self.setup_table.tag_configure("ready", foreground=self.theme.success)
        self.theme_button.configure(
            text="Light theme" if self.theme_name == "dark" else "Dark theme"
        )
        self._render_log()
        self._refresh_controls()

    @staticmethod
    def _walk_widgets(parent):
        for child in parent.winfo_children():
            yield child
            yield from ControlPanel._walk_widgets(child)

    def set_notice(self, text):
        self.notice.set(text)

    def show_error(self, title, error):
        if isinstance(
            error, (ValueError, RuntimeError, FileExistsError, FileNotFoundError)
        ):
            text = str(error)
        elif isinstance(error, OSError):
            text = "The file could not be written. Check folder permissions or pause OneDrive syncing, then try again."
        else:
            text = "The action could not finish. Check the live log and your settings."
        self.set_notice(title + ": " + text)
        messagebox.showerror(title, text, parent=self.root)

    def background(self, function, done=None):
        if self.working:
            self.set_notice("Please wait for the current action to finish.")
            return
        self.working = True
        self._refresh_controls()

        def run():
            try:
                result = function()
                if self.closing and self.controller.busy:
                    self.controller.stop()
                self.actions.put(("success", result, done))
            except Exception as error:
                self.actions.put(("error", error, None))

        threading.Thread(target=run, name="bms-panel-action", daemon=True).start()

    def save_settings(self, restart=False):
        values = self.settings.values()
        version = self.settings.version
        if restart:
            if self.targets.dirty:
                self.set_notice(
                    "Stop and save your target changes, or reload Targets, before restarting with new settings."
                )
                return
            try:
                self.store.validate_settings(values)
            except (ValueError, OSError) as error:
                self.show_error("Could not restart", error)
                return
            if not self.controller.busy or self.controller.mode != "bot":
                self.set_notice(
                    "Start the bot first, or use Save settings while stopped."
                )
                return
            if fingerprint(self.project / ".env") != version:
                self.show_error(
                    "Could not restart",
                    ValueError("Settings changed. Reload from file before saving."),
                )
                return
            self.pending_restart = (values, version)
            self.controller.stop()
            self.set_notice(
                "Stopping safely, then saving your settings and restarting…"
            )
            self._refresh_controls()
            return
        if self.controller.busy:
            self.set_notice("Stop the bot or use Save & Restart Bot to apply settings.")
            return

        def saved(result):
            self.settings.reload()
            self.refresh_setup()
            self.macros.refresh()
            self.set_notice(
                "Settings saved. They will apply on the next start. A private backup was kept."
            )

        self.background(lambda: self.store.save_settings(values, version), saved)

    def save_targets(self):
        if self.controller.busy:
            self.set_notice("Stop the bot before saving targets.")
            return
        rows, version = copy.deepcopy(self.targets.rows), self.targets.version

        def saved(result):
            self.targets.reload()
            self.set_notice("Targets saved. Start the bot to use the updated registry.")

        self.background(lambda: self.store.save_targets(rows, version), saved)

    def start_bot(self):
        if self.settings.dirty or self.targets.dirty:
            self.set_notice(
                "Save or reload your unsaved settings and targets before starting."
            )
            return
        self.show_page("Dashboard")
        self.background(
            self.controller.start_bot,
            lambda result: self.set_notice(
                "Starting the bot. Setup and connection progress appear in the live log."
            ),
        )

    def stop_bot(self):
        self.pending_restart = None
        self.controller.stop()
        self.set_notice(
            "Stop requested. Active work will finish before the bot closes."
        )
        self._refresh_controls()

    def normalize_macro(self, name):
        name = name.strip()
        return macro_name(name if name.endswith(".json") else name + ".json")

    def start_macro(self, command, name):
        if self.settings.dirty:
            self.set_notice("Save or reload settings before recording/playing a macro.")
            return
        try:
            name = self.normalize_macro(name)
            path = self.project / "scripts" / "macros" / name
            if command == "record" and path.exists():
                raise FileExistsError(
                    "This macro already exists. Choose another filename."
                )
            if command == "play" and not path.is_file():
                raise FileNotFoundError("Select an existing macro.")
        except (ValueError, OSError) as error:
            self.show_error("Could not start macro", error)
            return
        self.background(
            lambda: self.controller.start_macro(command, name),
            lambda result: (
                self.root.iconify(),
                self.set_notice(
                    "Macro tool running. Use F8/F9 for recording or Stop to cancel."
                ),
            ),
        )

    def refresh_setup(self):
        self.setup_table.delete(*self.setup_table.get_children())
        for row in sorted(self.store.readiness(), key=lambda item: item["ok"]):
            self.setup_table.insert(
                "",
                "end",
                values=("OK" if row["ok"] else "Fix", row["name"], row["detail"]),
                tags=("ready",) if row["ok"] else ("warning",),
            )
        try:
            cfg = Settings.load(self.project, environ={})
            self.delivery.set(
                f"{cfg.delivery_kind.title()} · {cfg.delivery_id}"
                if cfg.delivery_id
                else "Discovery only · send check-id in LINE"
            )
            self.default_macro.set(cfg.default_login_macro)
            if cfg.public_tunnel_url:
                self.webhook.set(cfg.public_tunnel_url + "/callback")
        except (ValueError, KeyError):
            self.delivery.set("Correct invalid settings to continue")

    def check_quota(self):
        def query():
            cfg = Settings.load(self.project, environ={})
            return LineAPI(cfg.channel_access_token).status_text(
                cfg.group_id, cfg.user_id
            )

        self.background(
            query,
            lambda text: messagebox.showinfo(
                "LINE messaging quota", text, parent=self.root
            ),
        )

    def copy_webhook(self):
        url = self.webhook.get()
        if url.startswith("https://"):
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            self.set_notice(
                "Webhook URL copied. Paste it into LINE Developers → Messaging API."
            )

    def open_folder(self, relative):
        folder = self.project / relative
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(folder)
            else:
                subprocess.Popen(
                    ["xdg-open", str(folder)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except OSError as error:
            self.show_error("Could not open folder", error)

    def _append_log(self, text):
        self.log_buffer.append(text)
        self._render_log()

    def _selected_log_levels(self):
        return {
            "Information": {"info"},
            "Warnings": {"warning"},
            "Errors": {"error"},
        }.get(self.log_filter.get())

    def _render_log(self):
        visible = self.log_buffer.visible(
            query=self.log_query.get(), levels=self._selected_log_levels()
        )
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.tag_configure("info", foreground=self.theme.log_text)
        self.log.tag_configure("warning", foreground=self.theme.warning)
        self.log.tag_configure("error", foreground=self.theme.danger)
        for line in visible:
            self.log.insert("end", line + "\n", classify_log_line(line))
        if self.log_autoscroll.get():
            self.log.see("end")
        self.log.configure(state="disabled")
        total = len(self.log_buffer.lines)
        self.log_count.set(
            "No activity yet"
            if total == 0
            else f"{len(visible)} shown · {total} session line{'s' if total != 1 else ''}"
        )

    def clear_log_view(self):
        self.log_buffer.clear()
        self._render_log()
        self.set_notice("The visible session log was cleared. The log file is unchanged.")

    def copy_log(self):
        text = self.log.get("1.0", "end-1c")
        if not text:
            self.set_notice("There is no visible activity to copy.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_notice("Visible activity copied to the clipboard.")

    def _refresh_controls(self):
        busy = self.controller.busy
        self.state_text.set(STATE_NAMES.get(self.controller.state, "Stopped"))
        state_style = status_style(self.controller.state)
        self.header_status.configure(style=state_style)
        self.dashboard_status.configure(style=state_style)
        self.start_button.state(["disabled"] if busy or self.working else ["!disabled"])
        self.stop_button.configure(
            text=(
                "Cancel macro" if self.controller.mode != "bot" and busy else "Stop Bot"
            )
        )
        self.stop_button.state(
            ["!disabled"]
            if busy and self.controller.state != "stopping"
            else ["disabled"]
        )
        if busy:
            self.header_action.configure(
                text=(
                    "Cancel macro"
                    if self.controller.mode != "bot"
                    else "Stop Bot"
                ),
                style="Danger.TButton",
            )
            self.header_action.state(
                ["disabled"]
                if self.controller.state == "stopping" or self.working
                else ["!disabled"]
            )
        else:
            self.header_action.configure(text="Start Bot", style="Primary.TButton")
            self.header_action.state(["disabled"] if self.working else ["!disabled"])
        self.quota_button.state(["disabled"] if self.working else ["!disabled"])
        for page in (
            getattr(self, "settings", None),
            getattr(self, "targets", None),
            getattr(self, "macros", None),
        ):
            if page:
                page.update_controls(busy, self.editors_locked)

    def _poll(self):
        try:
            for _ in range(100):
                kind, value = self.controller.events.get_nowait()
                if kind == "log":
                    self._append_log(value)
                elif kind == "finished":
                    self.refresh_setup()
                    self.macros.refresh()
                    if value["code"] not in (0, 130):
                        self.set_notice(
                            "The process stopped with an error. Read the live log above and check Settings."
                        )
                    elif self.pending_restart is None:
                        self.set_notice(
                            "Stopped safely. Settings and reports are preserved."
                        )
        except queue.Empty:
            pass
        try:
            kind, result, done = self.actions.get_nowait()
        except queue.Empty:
            pass
        else:
            self.working = False
            if kind == "error":
                self.show_error("Could not finish action", result)
            elif done and not self.closing:
                done(result)
        if self.controller.state != self.last_state:
            self.last_state = self.controller.state
            if self.controller.state == "online":
                self.webhook.set(
                    self.controller.status.get("webhook", self.webhook.get())
                )
                self.refresh_setup()
                self.set_notice(
                    "Bot online. Send capture in LINE; keep the BMS visible."
                )
                if self.minimize_on_start.get():
                    self.root.iconify()
        if self.pending_restart and not self.controller.busy and not self.working:
            values, version = self.pending_restart
            self.pending_restart = None

            def saved(result):
                self.settings.reload()
                self.refresh_setup()
                self.start_bot()

            self.background(lambda: self.store.save_settings(values, version), saved)
        if (
            self.closing
            and self.controller.busy
            and self.controller.state != "stopping"
        ):
            self.controller.stop()
        self._refresh_controls()
        if self.closing and not self.controller.busy and not self.working:
            self.controller.close()
            self.root.destroy()
            return
        self.poll_id = self.root.after(100, self._poll)

    def _on_destroy(self, event):
        if event.widget == self.root:
            self.disposed = True
            self.closing = True
            self.controller.stop()
            try:
                self.root.after_cancel(self.poll_id)
            except tk.TclError:
                pass

    def request_close(self):
        if self.controller.busy:
            if not messagebox.askyesno(
                "Close control panel",
                "Stop the active bot/macro and close this window? Active bot work will finish first."
                + (
                    " Unsaved settings/target changes will be discarded."
                    if self.settings.dirty or self.targets.dirty
                    else ""
                ),
                parent=self.root,
            ):
                return
        elif (self.settings.dirty or self.targets.dirty) and not messagebox.askyesno(
            "Unsaved changes", "Discard unsaved changes and close?", parent=self.root
        ):
            return
        self.closing = True
        self.pending_restart = None
        self.controller.stop()
        self.set_notice("Closing after active work finishes…")


def re_safe_macro(name):
    try:
        macro_name(name)
        return True
    except ValueError:
        return False


def main():
    from launcher import launcher_lock

    root = tk.Tk()
    root.withdraw()
    try:
        # A separate directory keeps the GUI instance lock independent of the bot lock.
        lock_dir = BASE_DIR / ".runtime" / "panel-instance"
        lock_dir.mkdir(parents=True, exist_ok=True)
        with launcher_lock(lock_dir):
            ControlPanel(root)
            root.deiconify()
            root.mainloop()
    except Exception as error:
        messagebox.showerror(
            "BMS Control Panel",
            (
                str(error)
                if isinstance(error, (RuntimeError, ValueError, OSError))
                else "The interface could not open. Launch start_gui.bat and check its setup messages."
            ),
            parent=root,
        )
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    main()

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
    BACKGROUND,
    TEXT,
    MUTED,
    Scrollable,
    HelpPage,
    MacrosPage,
    SettingsPage,
    TargetsPage,
)
from panel_process import ProcessController
from panel_settings import ProjectStore, fingerprint

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
        root.configure(background=BACKGROUND)
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
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=(self.font_family, 10), foreground=TEXT)
        style.configure("Page.TFrame", background=BACKGROUND)
        style.configure("Card.TFrame", background="white")
        style.configure(
            "Card.TLabelframe",
            background="white",
            bordercolor="#d9e1ea",
            relief="solid",
        )
        style.configure(
            "Card.TLabelframe.Label",
            background="white",
            foreground=TEXT,
            font=(self.font_family, 11, "bold"),
        )
        style.configure("Field.TLabel", background="white", foreground=TEXT)
        style.configure(
            "Hint.TLabel",
            background="white",
            foreground=MUTED,
            font=(self.font_family, 9),
        )
        style.configure(
            "Help.TLabel",
            background="white",
            foreground=TEXT,
            font=(self.font_family, 10),
        )
        style.configure("Muted.TLabel", background=BACKGROUND, foreground=MUTED)
        style.configure(
            "PageTitle.TLabel",
            background=BACKGROUND,
            foreground=TEXT,
            font=(self.font_family, 23, "bold"),
        )
        style.configure(
            "Status.TLabel",
            background=BACKGROUND,
            foreground="#147b63",
            font=(self.font_family, 12, "bold"),
        )
        style.configure("Card.TCheckbutton", background="white")
        style.configure("TCheckbutton", background=BACKGROUND)
        style.configure(
            "TButton",
            padding=(12, 8),
            background="white",
            bordercolor="#cbd6e3",
            focuscolor="",
        )
        style.map("TButton", background=[("active", "#e6edf4")])
        style.configure(
            "Primary.TButton",
            background="#087f76",
            foreground="white",
            bordercolor="#087f76",
            font=(self.font_family, 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("disabled", "#c7d4da"), ("active", "#086b63")],
            foreground=[("disabled", "#647581")],
        )
        style.configure("Danger.TButton", foreground="#ab3a38")
        style.configure("TEntry", fieldbackground="white", padding=7)
        style.configure("TCombobox", fieldbackground="white", padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", "white")])
        style.configure("TNotebook", background=BACKGROUND, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 10))
        style.configure(
            "Treeview",
            rowheight=32,
            fieldbackground="white",
            background="white",
            bordercolor="#d9e1ea",
        )
        style.configure(
            "Treeview.Heading",
            padding=8,
            background="#e6edf4",
            font=(self.font_family, 10, "bold"),
        )

    def _shell(self):
        sidebar = tk.Frame(self.root, background="#14263e", width=196)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(
            sidebar,
            text="BMS",
            font=(self.font_family, 28, "bold"),
            background="#14263e",
            foreground="white",
            anchor="w",
        ).pack(fill="x", padx=24, pady=(26, 0))
        tk.Label(
            sidebar,
            text="LINE AUTOMATION",
            font=(self.font_family, 9),
            background="#14263e",
            foreground="#afc5da",
            anchor="w",
        ).pack(fill="x", padx=25, pady=(0, 28))
        self.navigation = {}
        for name in PAGE_DESCRIPTIONS:
            button = tk.Button(
                sidebar,
                text=name,
                anchor="w",
                padx=18,
                pady=13,
                font=(self.font_family, 11),
                background="#14263e",
                foreground="#c5d4e2",
                activebackground="#27445e",
                activeforeground="white",
                relief="flat",
                borderwidth=0,
                cursor="hand2",
                highlightthickness=0,
                command=lambda page=name: self.show_page(page),
            )
            button.pack(fill="x", padx=12, pady=3)
            self.navigation[name] = button
        bottom = tk.Frame(sidebar, background="#14263e")
        bottom.pack(side="bottom", fill="x", padx=20, pady=24)
        tk.Label(
            bottom,
            text="LOCAL DESKTOP BOT",
            background="#14263e",
            foreground="#8da8bf",
            font=(self.font_family, 8),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            bottom,
            textvariable=self.state_text,
            background="#14263e",
            foreground="white",
            font=(self.font_family, 11, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(6, 12))
        tk.Button(
            bottom,
            text="Open reports folder",
            command=lambda: self.open_folder("screenshots"),
            relief="flat",
            background="#284258",
            foreground="white",
            activebackground="#355a74",
            activeforeground="white",
            pady=9,
            cursor="hand2",
        ).pack(fill="x")
        area = ttk.Frame(self.root, style="Page.TFrame", padding=24)
        area.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(area, style="Page.TFrame")
        header.pack(fill="x", pady=(0, 16))
        self.title_label = ttk.Label(header, text="Dashboard", style="PageTitle.TLabel")
        self.title_label.pack(anchor="w")
        self.subtitle = ttk.Label(
            header,
            text=PAGE_DESCRIPTIONS["Dashboard"],
            style="Muted.TLabel",
            wraplength=820,
        )
        self.subtitle.pack(anchor="w", pady=(5, 0))
        ttk.Label(
            area, textvariable=self.notice, style="Muted.TLabel", wraplength=830
        ).pack(side="bottom", anchor="w", fill="x", pady=(15, 0))
        self.content = ttk.Frame(area, style="Page.TFrame")
        self.content.pack(fill="both", expand=True)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

    def _dashboard(self, parent):
        page = Scrollable(parent)
        page.body.configure(padding=0)
        summary = ttk.LabelFrame(
            page.body, text="Bot connection", style="Card.TLabelframe", padding=18
        )
        summary.pack(fill="x")
        row = ttk.Frame(summary, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(
            row,
            textvariable=self.state_text,
            style="Field.TLabel",
            font=(self.font_family, 18, "bold"),
        ).pack(side="left")
        self.stop_button = ttk.Button(
            row, text="Stop Bot", style="Danger.TButton", command=self.stop_bot
        )
        self.stop_button.pack(side="right", padx=(10, 0))
        self.start_button = ttk.Button(
            row, text="Start Bot", style="Primary.TButton", command=self.start_bot
        )
        self.start_button.pack(side="right")
        for label, variable in [
            ("Delivery", self.delivery),
            ("Login macro", self.default_macro),
        ]:
            line = ttk.Frame(summary, style="Card.TFrame")
            line.pack(fill="x", pady=(9, 0))
            ttk.Label(line, text=label + ":", style="Hint.TLabel", width=14).pack(
                side="left"
            )
            ttk.Label(line, textvariable=variable, style="Field.TLabel").pack(
                side="left"
            )
        webhook_row = ttk.Frame(summary, style="Card.TFrame")
        webhook_row.pack(fill="x", pady=(10, 0))
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
            page.body, text="Setup checklist", padding=12, style="Card.TLabelframe"
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
        self.setup_table.tag_configure("warning", foreground="#9b6426")
        setup_scroll = ttk.Scrollbar(
            setup, orient="vertical", command=self.setup_table.yview
        )
        self.setup_table.configure(yscrollcommand=setup_scroll.set)
        setup_scroll.pack(side="right", fill="y")
        self.setup_table.pack(side="left", fill="x", expand=True)
        toolbar = ttk.Frame(page.body, style="Page.TFrame")
        toolbar.pack(fill="x", pady=(0, 10))
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
        ttk.Label(toolbar, text="Live log", style="Muted.TLabel").pack(side="right")
        log_frame = ttk.Frame(page.body, style="Page.TFrame")
        log_frame.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame)
        self.log = tk.Text(
            log_frame,
            height=8,
            font=(self.mono_family, 9),
            background="#172a40",
            foreground="#dae5ee",
            insertbackground="white",
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
                background="#294960" if page == name else "#14263e",
                foreground="white" if page == name else "#c5d4e2",
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
                tags=() if row["ok"] else ("warning",),
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
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 800:
            self.log.delete("1.0", f"{lines - 800}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _refresh_controls(self):
        busy = self.controller.busy
        self.state_text.set(STATE_NAMES.get(self.controller.state, "Stopped"))
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

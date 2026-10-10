"""Settings, targets, macro tools, and help views for the native control panel."""

from __future__ import annotations

import copy
import re
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog, ttk
from zoneinfo import ZoneInfo

from config import Settings, boolean, macro_name
from panel_settings import SECRET_FIELDS, fingerprint

BACKGROUND = "#f3f5f8"
TEXT = "#172b45"
MUTED = "#586b80"


class Scrollable(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, style="Page.TFrame")
        self.canvas = tk.Canvas(self, background=BACKGROUND, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, style="Page.TFrame", padding=12)
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.body.bind(
            "<Configure>",
            lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self.window, width=event.width),
        )
        self.bind_all("<MouseWheel>", self._wheel, add="+")
        self.bind_all("<Button-4>", self._wheel, add="+")
        self.bind_all("<Button-5>", self._wheel, add="+")

    def _wheel(self, event):
        widget = event.widget
        while widget is not None:
            if widget == self:
                delta = (
                    -1
                    if getattr(event, "num", None) == 4
                    else (
                        1
                        if getattr(event, "num", None) == 5
                        else -int(event.delta / 120)
                    )
                )
                self.canvas.yview_scroll(delta, "units")
                return
            widget = getattr(widget, "master", None)


def card(parent, title, row=0, column=0):
    frame = ttk.LabelFrame(parent, text=title, padding=18, style="Card.TLabelframe")
    frame.grid(row=row, column=column, sticky="new", padx=6, pady=8)
    frame.columnconfigure(0, weight=1)
    return frame


FORM_SECTIONS = {
    "LINE & ngrok": [
        (
            "LINE account and delivery",
            [
                (
                    "CHANNEL_ACCESS_TOKEN",
                    "Channel access token",
                    "LINE Developers → Messaging API → long-lived access token.",
                ),
                (
                    "LINE_CHANNEL_SECRET",
                    "Channel secret",
                    "LINE Developers → Basic settings. Authenticates incoming webhooks.",
                ),
                (
                    "GROUP_ID",
                    "Group ID (optional)",
                    "A configured group takes priority over the private user below.",
                ),
                (
                    "USER_ID",
                    "Private user ID (optional)",
                    "Leave Group ID blank for private chats. Send check-id in LINE to find IDs.",
                ),
            ],
        ),
        (
            "ngrok tunnel",
            [
                (
                    "NGROK_EXE_PATH",
                    "ngrok.exe location",
                    "Browse to your downloaded executable. Spaces in paths are supported.",
                ),
                (
                    "NGROK_DOMAIN",
                    "Public ngrok domain",
                    "Your reserved hostname, such as name.ngrok-free.dev. Blank allows automatic discovery.",
                ),
                (
                    "NGROK_AUTHTOKEN",
                    "ngrok authentication token",
                    "Your ngrok account token. Blank uses existing ngrok authentication.",
                ),
            ],
        ),
    ],
    "Automation": [
        (
            "BMS session",
            [
                (
                    "BMS_USERNAME",
                    "BMS login username",
                    "Used by Macro Tool option 4 when creating a fixed-template macro.",
                ),
                (
                    "BMS_PASSWORD",
                    "BMS login password",
                    "Stored only in your private .env and inserted into locally generated macros.",
                ),
                (
                    "LOGIN_MACRO_SCRIPT",
                    "Default login macro",
                    "Runs once when the login-page anchor is found.",
                ),
                (
                    "LOGOUT_MACRO_SCRIPT",
                    "Logout macro",
                    "Runs only after image delivery is accepted, when auto-logout is enabled.",
                ),
                (
                    "ENABLE_AUTO_LOGOUT",
                    "Log out after image delivery",
                    "Keeps the BMS session open if capture or sending fails.",
                ),
                (
                    "MACRO_SETTLE_DELAY",
                    "Page loading delay (seconds)",
                    "Wait after a login/navigation macro before taking the picture.",
                ),
                (
                    "MAX_MACRO_SECONDS",
                    "Maximum macro time (seconds)",
                    "Interactive delivery must still start within the LINE reply deadline.",
                ),
                (
                    "CONFIDENCE_THRESHOLD",
                    "Login anchor match threshold",
                    "0–1; default 0.8. Higher values require a closer visual match.",
                ),
                (
                    "DETECTOR_INTERVAL_SEC",
                    "Login polling interval (seconds)",
                    "Checks for the login anchor after a macro. Default 0.5; use 0.1 for faster polling.",
                ),
                (
                    "LOGIN_WAIT_SECONDS",
                    "Maximum login wait (seconds)",
                    "Continues immediately when the anchor disappears; timeout uses the capture fallback.",
                ),
            ],
        ),
        (
            "Images and chat",
            [
                (
                    "TIMEZONE",
                    "Schedule and timestamp timezone",
                    "Use an IANA name. Default: Asia/Bangkok.",
                ),
                (
                    "LINE_WEBP_QUALITY",
                    "Full-resolution WebP quality",
                    "1–100; default 90. The image keeps its original resolution.",
                ),
                (
                    "IMAGE_TTL_SECONDS",
                    "Public image link lifetime (seconds)",
                    "0 means no expiration. Report JPEGs are never automatically deleted.",
                ),
                (
                    "REPLY_UNKNOWN_COMMANDS",
                    "Reply to unrecognized messages",
                    "Send help for unknown text. Off avoids responding to ordinary conversation.",
                ),
            ],
        ),
    ],
    "Advanced": [
        (
            "Server and local tools",
            [
                (
                    "PORT",
                    "Server port",
                    "Default 5000. ngrok automatically forwards to this port.",
                ),
                (
                    "INTERNAL_API_TOKEN",
                    "Internal tunnel update token (optional)",
                    "Only needed by external tools calling the tunnel update endpoint.",
                ),
            ],
        ),
        (
            "Recording metadata and compatibility",
            [
                (
                    "DESKTOP_WIDTH",
                    "Imported recording width",
                    "Metadata for converting old macros; does not change click coordinates.",
                ),
                (
                    "DESKTOP_HEIGHT",
                    "Imported recording height",
                    "Metadata only; playback does not check display size or scaling.",
                ),
                (
                    "RECORDED_STEP_DELAY_SECONDS",
                    "Recorded step delay (seconds)",
                    "Every action in a newly recorded macro uses this delay. Default 0.2.",
                ),
            ],
        ),
    ],
}


class SettingsPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, style="Page.TFrame")
        self.app = app
        self.variables, self.entries, self.combos = {}, {}, []
        self.editor_buttons = []
        self.version = None
        self.saved = {}
        ttk.Label(
            self,
            text="Edit your project settings. Save while stopped, or use Save & Restart Bot.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 10))
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        for tab_name, groups in FORM_SECTIONS.items():
            scroll = Scrollable(notebook)
            notebook.add(scroll, text=tab_name)
            cards = []
            for column, (title, fields) in enumerate(groups):
                scroll.body.columnconfigure(column, weight=1, uniform="settings")
                group = card(scroll.body, title, column=column)
                cards.append(group)
                for index, (key, label, hint) in enumerate(fields):
                    self._field(group, index * 4, key, label, hint)
                if title == "ngrok tunnel":
                    ttk.Button(
                        group,
                        text="Get my ngrok token ↗",
                        command=lambda: webbrowser.open(
                            "https://dashboard.ngrok.com/get-started/your-authtoken"
                        ),
                    ).grid(row=len(fields) * 4, column=0, sticky="w", pady=8)
            scroll.canvas.bind(
                "<Configure>",
                lambda event, panel=scroll, items=cards: self._layout_cards(
                    panel, items, event.width
                ),
                add="+",
            )
        bottom = ttk.Frame(self, style="Page.TFrame", padding=(0, 12, 0, 0))
        bottom.pack(fill="x")
        self.dirty_label = ttk.Label(bottom, text="Saved", style="Muted.TLabel")
        self.dirty_label.pack(side="left")
        self.restart_button = ttk.Button(
            bottom,
            text="Save & Restart Bot",
            command=lambda: app.save_settings(restart=True),
        )
        self.restart_button.pack(side="right", padx=(8, 0))
        self.save_button = ttk.Button(
            bottom,
            text="Save settings",
            style="Primary.TButton",
            command=app.save_settings,
        )
        self.save_button.pack(side="right", padx=8)
        reload_button = ttk.Button(
            bottom, text="Reload from file", command=self.reload_prompt
        )
        reload_button.pack(side="right")
        self.editor_buttons.append(reload_button)
        self.reload()

    def _layout_cards(self, panel, cards, width):
        stacked = width < 820
        panel.body.columnconfigure(0, weight=1, uniform="" if stacked else "settings")
        panel.body.columnconfigure(
            1, weight=0 if stacked else 1, uniform="" if stacked else "settings"
        )
        for index, group in enumerate(cards):
            group.grid_configure(
                row=index if stacked else 0, column=0 if stacked else index
            )

    def _field(self, parent, row, key, label, hint):
        is_bool = key in {"ENABLE_AUTO_LOGOUT", "REPLY_UNKNOWN_COMMANDS"}
        variable = tk.BooleanVar(value=False) if is_bool else tk.StringVar()
        self.variables[key] = variable
        ttk.Label(parent, text=label, style="Field.TLabel").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(8, 4)
        )
        if is_bool:
            entry = ttk.Checkbutton(
                parent, text="Enabled", variable=variable, style="Card.TCheckbutton"
            )
        elif key in {"LOGIN_MACRO_SCRIPT", "LOGOUT_MACRO_SCRIPT"}:
            entry = ttk.Combobox(
                parent, textvariable=variable, state="readonly", width=25
            )
            self.combos.append(entry)
        else:
            entry = ttk.Entry(
                parent,
                textvariable=variable,
                show="*" if key in SECRET_FIELDS else "",
                width=25,
            )
        entry.grid(row=row + 1, column=0, sticky="ew")
        self.entries[key] = entry
        if key in SECRET_FIELDS:
            button = ttk.Button(
                parent,
                text="Show",
                width=6,
                command=lambda e=entry: e.configure(show="" if e.cget("show") else "*"),
            )
            button.grid(row=row + 1, column=1, padx=(6, 0))
            self.editor_buttons.append(button)
        elif key == "NGROK_EXE_PATH":
            button = ttk.Button(
                parent, text="Browse", width=7, command=self.browse_ngrok
            )
            button.grid(row=row + 1, column=1, padx=(6, 0))
            self.editor_buttons.append(button)
        ttk.Label(
            parent, text=hint, style="Hint.TLabel", wraplength=330, justify="left"
        ).grid(row=row + 2, column=0, columnspan=2, sticky="w", pady=(4, 10))

    def browse_ngrok(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Select ngrok.exe",
            filetypes=[("Windows application", "*.exe"), ("All files", "*")],
        )
        if path:
            self.variables["NGROK_EXE_PATH"].set(path)

    def values(self):
        return {key: str(variable.get()) for key, variable in self.variables.items()}

    @property
    def dirty(self):
        return self.values() != self.saved

    def reload(self):
        values, self.version = self.app.store.load_settings()
        for key, variable in self.variables.items():
            value = values[key]
            if isinstance(variable, tk.BooleanVar):
                try:
                    value = boolean(value)
                except ValueError:
                    value = False
            variable.set(value)
        self.saved = self.values()
        self.refresh_macros()

    def reload_prompt(self):
        if not self.dirty or messagebox.askyesno(
            "Reload settings",
            "Discard unsaved settings and reload the project .env?",
            parent=self,
        ):
            self.reload()

    def refresh_macros(self):
        names = self.app.macro_names()
        for combo in self.combos:
            current = combo.get()
            combo.configure(values=sorted(set(names + ([current] if current else []))))

    def update_controls(self, busy, working):
        for widget in list(self.entries.values()) + self.editor_buttons:
            widget.state(["disabled"] if working else ["!disabled"])
        self.dirty_label.configure(
            text="Unsaved changes" if self.dirty else "Saved to .env"
        )
        self.save_button.state(["disabled"] if busy or working else ["!disabled"])
        self.restart_button.state(
            ["!disabled"]
            if busy
            and self.app.controller.mode == "bot"
            and self.app.controller.state == "online"
            and not working
            else ["disabled"]
        )


class TargetsPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, style="Page.TFrame")
        self.app = app
        self.editor_widgets = []
        self.rows = []
        self.saved = []
        self.version = None
        self.target_id, self.target_name, self.target_macro = (
            tk.StringVar(),
            tk.StringVar(),
            tk.StringVar(),
        )
        ttk.Label(
            self,
            text="Map a short target ID to a macro. Example: capture 09D runs DH09D.json.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 12))
        self.table = ttk.Treeview(
            self,
            columns=("id", "name", "macro"),
            show="headings",
            selectmode="browse",
            height=10,
        )
        for key, label, width in [
            ("id", "Target ID", 90),
            ("name", "Description", 290),
            ("macro", "Macro script", 190),
        ]:
            self.table.heading(key, text=label)
            self.table.column(key, width=width)
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self._select)
        editor = ttk.LabelFrame(
            self, text="Add or edit a target", padding=16, style="Card.TLabelframe"
        )
        editor.pack(fill="x", pady=16)
        for index, (label, variable) in enumerate(
            [
                ("Target ID", self.target_id),
                ("Description", self.target_name),
                ("Macro script", self.target_macro),
            ]
        ):
            editor.columnconfigure(index, weight=1)
            ttk.Label(editor, text=label, style="Field.TLabel").grid(
                row=0, column=index, sticky="w", padx=6, pady=(0, 6)
            )
            widget = (
                ttk.Combobox(editor, textvariable=variable, state="readonly")
                if index == 2
                else ttk.Entry(editor, textvariable=variable)
            )
            widget.grid(row=1, column=index, sticky="ew", padx=6)
            self.editor_widgets.append(widget)
            if index == 2:
                self.macro_combo = widget
        buttons = ttk.Frame(editor, style="Card.TFrame")
        buttons.grid(row=2, column=0, columnspan=3, sticky="w", pady=(14, 0))
        for label, callback in [
            ("Add target", self.add_row),
            ("Update selected", self.update_row),
            ("Remove selected", self.remove_row),
            ("Clear form", self.clear_form),
        ]:
            button = ttk.Button(buttons, text=label, command=callback)
            button.pack(side="left", padx=(0, 8))
            self.editor_widgets.append(button)
        toolbar = ttk.Frame(self, style="Page.TFrame")
        toolbar.pack(fill="x")
        self.state_label = ttk.Label(toolbar, text="", style="Muted.TLabel")
        self.state_label.pack(side="left")
        self.save_button = ttk.Button(
            toolbar,
            text="Save targets",
            style="Primary.TButton",
            command=app.save_targets,
        )
        self.save_button.pack(side="right", padx=(8, 0))
        reload_button = ttk.Button(
            toolbar, text="Reload from file", command=self.reload_prompt
        )
        reload_button.pack(side="right")
        self.editor_widgets.append(reload_button)
        self.table.pack_forget()
        editor.pack_forget()
        toolbar.pack_forget()
        toolbar.pack(side="bottom", fill="x")
        editor.pack(side="bottom", fill="x", pady=16)
        self.table.pack(fill="both", expand=True)
        self.reload()

    @property
    def dirty(self):
        return self.rows != self.saved

    def reload(self):
        try:
            self.rows, self.version = self.app.store.load_targets()
        except (OSError, ValueError):
            self.rows, self.version = [], fingerprint(self.app.project / "targets.json")
            self.app.set_notice(
                "targets.json needs repair. Re-create targets here; saving keeps a backup."
            )
        self.saved = copy.deepcopy(self.rows)
        self.render()
        self.refresh_macros()

    def reload_prompt(self):
        if not self.dirty or messagebox.askyesno(
            "Reload targets", "Discard unsaved target changes?", parent=self
        ):
            self.reload()

    def render(self):
        self.table.delete(*self.table.get_children())
        for index, row in enumerate(self.rows):
            self.table.insert(
                "", "end", iid=str(index), values=(row["id"], row["name"], row["macro"])
            )

    def _select(self, event=None):
        if self.app.editors_locked:
            return
        selected = self.table.selection()
        if selected:
            row = self.rows[int(selected[0])]
            self.target_id.set(row["id"])
            self.target_name.set(row["name"])
            self.target_macro.set(row["macro"])
            self.refresh_macros()

    def _row(self, exclude=None):
        target_id = self.target_id.get().strip()
        name = self.target_name.get().strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", target_id) or not name:
            raise ValueError(
                "Enter a target ID using letters/numbers and a description."
            )
        if any(
            row["id"].upper() == target_id.upper()
            for index, row in enumerate(self.rows)
            if index != exclude
        ):
            raise ValueError("This target ID already exists.")
        return {
            "id": target_id,
            "name": name,
            "macro": macro_name(self.target_macro.get()),
        }

    def add_row(self):
        try:
            self.rows.append(self._row())
            self.render()
            self.clear_form()
            self.app.set_notice("Target added. Click Save targets to apply it.")
        except ValueError as error:
            self.app.show_error("Could not add target", error)

    def update_row(self):
        selected = self.table.selection()
        if not selected:
            self.app.set_notice("Select a target in the table first.")
            return
        try:
            index = int(selected[0])
            self.rows[index].update(self._row(exclude=index))
            self.render()
            self.app.set_notice("Target updated. Click Save targets to apply it.")
        except ValueError as error:
            self.app.show_error("Could not update target", error)

    def remove_row(self):
        selected = self.table.selection()
        if selected:
            self.rows.pop(int(selected[0]))
            self.render()
            self.clear_form()
            self.app.set_notice("Target removed from the form. Save targets to apply.")

    def clear_form(self):
        self.target_id.set("")
        self.target_name.set("")
        self.target_macro.set("")
        self.table.selection_remove(*self.table.selection())

    def refresh_macros(self):
        names = self.app.macro_names()
        current = self.target_macro.get()
        self.macro_combo.configure(
            values=sorted(set(names + ([current] if current else [])))
        )

    def update_controls(self, busy, working):
        for widget in self.editor_widgets + [self.table]:
            widget.state(["disabled"] if working else ["!disabled"])
        self.save_button.state(["disabled"] if busy or working else ["!disabled"])
        self.state_label.configure(
            text=(
                "Stop the bot to save targets."
                if busy
                else "Unsaved target changes" if self.dirty else "Saved to targets.json"
            )
        )


class MacrosPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, style="Page.TFrame")
        self.app = app
        ttk.Label(
            self,
            text="Record, import, and play your private BMS macros. Stop the bot before using these tools.",
            style="Muted.TLabel",
            wraplength=780,
        ).pack(anchor="w", pady=(0, 12))
        self.table = ttk.Treeview(
            self,
            columns=("name", "steps", "status", "modified"),
            show="headings",
            selectmode="browse",
            height=10,
        )
        for key, title, width in [
            ("name", "Macro filename", 230),
            ("steps", "Steps", 60),
            ("status", "Validation", 130),
            ("modified", "Last modified", 180),
        ]:
            self.table.heading(key, text=title)
            self.table.column(key, width=width)
        self.table.pack(fill="both", expand=True)
        toolbar = ttk.Frame(self, style="Page.TFrame")
        toolbar.pack(fill="x", pady=12)
        self.tool_buttons = []
        for index, (text, action) in enumerate(
            [
                ("Record new macro", self.record),
                ("Import JSON", self.import_file),
                ("Play selected", self.play),
                ("Use for login", lambda: self.choose_default("login")),
                ("Use for logout", lambda: self.choose_default("logout")),
                ("Refresh macros", self.refresh),
            ]
        ):
            button = ttk.Button(toolbar, text=text, command=action)
            button.grid(
                row=index // 3, column=index % 3, sticky="ew", padx=(0, 8), pady=(0, 6)
            )
            if index < 5:
                self.tool_buttons.append(button)
        anchor = ttk.LabelFrame(
            self,
            text="Login-page detection image",
            padding=18,
            style="Card.TLabelframe",
        )
        anchor.pack(fill="x", pady=18)
        self.anchor_text = tk.StringVar()
        ttk.Label(anchor, textvariable=self.anchor_text, style="Field.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            anchor,
            text="Crop a distinctive part of the BMS login page and select that image. A logout anchor is not needed.",
            style="Hint.TLabel",
            wraplength=760,
        ).pack(anchor="w", pady=8)
        self.anchor_button = ttk.Button(
            anchor, text="Select login anchor image…", command=self.import_anchor
        )
        self.anchor_button.pack(anchor="w")
        instructions = ttk.Label(
            self,
            text="Recording: F8 starts/stops and saves; F9 cancels. Playback: switch to the BMS during the three-second countdown. The app minimizes during both operations. Passwords in macros remain private local files.",
            style="Muted.TLabel",
            wraplength=780,
        )
        self.table.pack_forget()
        toolbar.pack_forget()
        anchor.pack_forget()
        instructions.pack(side="bottom", fill="x", anchor="w")
        anchor.pack(side="bottom", fill="x", pady=14)
        toolbar.pack(side="bottom", fill="x", pady=(10, 0))
        self.table.pack(fill="both", expand=True)
        self.refresh()

    def selected(self):
        selected = self.table.selection()
        if not selected:
            raise ValueError("Select a macro in the table first.")
        return self.table.item(selected[0], "values")[0]

    def refresh(self):
        self.table.delete(*self.table.get_children())
        try:
            rows = self.app.store.list_macros()
            timezone = ZoneInfo(Settings.load(self.app.project, environ={}).timezone)
        except (OSError, ValueError, KeyError):
            rows = []
            timezone = ZoneInfo("Asia/Bangkok")
        for row in rows:
            modified = (
                datetime.fromtimestamp(row["modified"], timezone).strftime(
                    "%Y-%m-%d %H:%M"
                )
                if row["modified"]
                else "—"
            )
            self.table.insert(
                "",
                "end",
                values=(
                    row["name"],
                    row["steps"],
                    "Ready" if row["valid"] else "Needs repair",
                    modified,
                ),
            )
        anchor = self.app.project / "assets" / "login_anchor.png"
        alternate = anchor.with_name("login-anchor.png")
        self.anchor_text.set(
            "Configured: " + (anchor.name if anchor.is_file() else alternate.name)
            if anchor.is_file() or alternate.is_file()
            else "Not configured yet"
        )
        if hasattr(self.app, "settings"):
            self.app.settings.refresh_macros()
            self.app.targets.refresh_macros()

    def choose_default(self, kind, name=None):
        try:
            name = name or self.selected()
            self.app.settings.variables[
                "LOGIN_MACRO_SCRIPT" if kind == "login" else "LOGOUT_MACRO_SCRIPT"
            ].set(name)
            self.app.show_page("Settings")
            self.app.settings_notebook_automation()
            self.app.set_notice(
                f"Selected {name} for {kind}. Click Save settings to apply."
            )
        except ValueError as error:
            self.app.show_error("Could not select macro", error)

    def record(self):
        name = simpledialog.askstring(
            "Record macro", "New filename, for example DH09D.json:", parent=self
        )
        if name:
            self.app.start_macro("record", name)

    def play(self):
        try:
            self.app.start_macro("play", self.selected())
        except ValueError as error:
            self.app.show_error("Could not play macro", error)

    def import_file(self):
        source = filedialog.askopenfilename(
            parent=self,
            title="Import current or old macro JSON",
            filetypes=[("JSON macros", "*.json")],
        )
        if not source:
            return
        from pathlib import Path

        name = simpledialog.askstring(
            "Import macro",
            "Save as filename (existing files are preserved):",
            initialvalue=Path(source).name,
            parent=self,
        )
        if name:
            self.app.background(
                lambda: self.app.store.import_macro(
                    source, self.app.normalize_macro(name)
                ),
                lambda result: (
                    self.refresh(),
                    self.app.refresh_setup(),
                    self.app.set_notice(f"Imported {result.name}."),
                ),
            )

    def import_anchor(self):
        source = filedialog.askopenfilename(
            parent=self,
            title="Choose your login-page crop",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp")],
        )
        if source:
            self.app.background(
                lambda: self.app.store.import_anchor(source),
                lambda result: (
                    self.refresh(),
                    self.app.refresh_setup(),
                    self.app.set_notice(
                        "Login anchor saved. Previous image backed up if present."
                    ),
                ),
            )

    def update_controls(self, busy, working):
        for button in self.tool_buttons + [self.anchor_button]:
            button.state(["disabled"] if busy or working else ["!disabled"])


class HelpPage(Scrollable):
    def __init__(self, parent, app):
        super().__init__(parent)
        paragraphs = [
            (
                "Get online in four steps",
                "1. In Settings → LINE & ngrok, enter the LINE access token, channel secret, and your ngrok details. Browse to ngrok.exe. Save settings.\n\n2. Click Start Bot on Dashboard. Copy the webhook URL into LINE Developers → Messaging API, then enable Use webhook. For a reserved ngrok domain this setup is done once.\n\n3. Add the bot as a friend or invite it to your LINE group. Send check-id, stop the bot, enter your User ID or Group ID in Settings, save, and start again. Group ID takes priority; leave it blank for private chats.\n\n4. In Macros, record/import your login macro and select a login-page anchor crop. Choose the default login macro in Settings. Keep the BMS visible and the app minimized during capture.",
            ),
            (
                "Useful LINE commands",
                "capture                         Current screen\ncapture 09D daily report        Target with note\nstart-capture 30m daily report   Recurring capture\ncapture report —starttime 15:30  One-time capture\nstop-capture                    Stop all schedules\ncheck-id                        Find your chat IDs\ncheck-quota                     Show messaging quota\nenable-autologout                Log out after accepted delivery\ndisable-autologout               Keep the BMS session open",
            ),
            (
                "Reports and image delivery",
                "Full-resolution WebP images are sent through ngrok, as requested for your tested setup. Full-resolution quality-100 JPEG reports remain permanently in screenshots/<target-or-macro>/. WebP quality defaults to 90 and can be changed in Settings. LINE officially documents JPEG/PNG; WebP follows your operator-tested setup.\n\nCaptions include the actual capture time and optional note. Each schedule starts at #1 and counts accepted image deliveries. Schedules are stored in memory and must be started again after restarting the bot.",
            ),
            (
                "Safe stopping and editing",
                "Stop Bot waits for an active capture/send/logout to finish, then closes its server and the ngrok process it started. An existing external ngrok tunnel is left running. If another launcher is running, use its own window to stop it.\n\nSave settings and targets while stopped, or use Save & Restart Bot for settings. Private backups are kept in .runtime/backups. If a file changed in LINE or another editor, reload it before saving.",
            ),
            (
                "Macro tools",
                "Stop the bot before recording or playing. F8 starts recording, then F8 stops and exports JSON. F9 cancels. Playback starts after three seconds so you can switch to the BMS; moving the mouse to a corner triggers the fail-safe.\n\nNew recordings use the configured delay for every exported action instead of reproducing the operator's timing gaps. Imports accept current macros and your old click_coord/type_text/press_key scripts. Existing filenames are preserved. The original recorded click coordinates are replayed without display consistency checks.",
            ),
        ]
        for index, (title, text) in enumerate(paragraphs):
            frame = card(self.body, title, row=index)
            ttk.Label(
                frame, text=text, style="Help.TLabel", wraplength=730, justify="left"
            ).pack(anchor="w")
        self.body.columnconfigure(0, weight=1)
        links = ttk.Frame(self.body, style="Page.TFrame")
        links.grid(row=len(paragraphs), column=0, sticky="w", padx=6, pady=12)
        for label, url in [
            ("LINE Developers ↗", "https://developers.line.biz/console/"),
            ("ngrok dashboard ↗", "https://dashboard.ngrok.com/"),
        ]:
            ttk.Button(
                links, text=label, command=lambda target=url: webbrowser.open(target)
            ).pack(side="left", padx=(0, 8))

# PySide6 desktop migration

The requested outcome is a responsive Windows control panel for nontechnical BMS operators, preserving all automation, LINE commands, schedules, JSON formats, settings keys, and existing workflows. Work starts at remote main cd17c23 on feat/pyside6-gui. No live automation, push, or merge is authorized.

## Inspection

The repository is a flat Python application. `control_panel.py` and `panel_pages.py` contain all Tkinter/ttk widgets; there is no CustomTkinter dependency. Five pages provide dashboard/process logs, three settings tabs, target editing, macro recording/import/playback/default selection/login-anchor import, and help. `start_gui.bat` delegates to `start_bot.bat --gui`; `launcher.bootstrap` prepares a virtual environment and starts the panel with pythonw.

`ProjectStore` already validates and atomically saves settings, targets, macros and anchors, rejects stale edits and keeps backups. `ProcessController` launches owned bot/macro subprocesses, redacts bounded logs, monitors control-session status and requests cooperative stop. `launcher.py` owns ngrok/server startup, webhook synchronization and cleanup. `server.py`, `commands.py`, `scheduler.py`, `workflow.py`, `macro_player.py`, `macro_recorder.py`, `capture.py` and `line_api.py` implement the existing business behavior. They use threads, queues, cancellation events and a shared desktop lock. Existing configuration already supports external resources with a bundle fallback. No PyInstaller spec exists.

## Selected approach

Add a parallel Qt presentation layer and retain both original Tkinter files unchanged. A direct replacement would remove the comparison/rollback path. Embedding Tk inside Qt would retain conflicting event loops and poor scaling. The new `qt_panel.py` and `qt_pages.py` reuse ProjectStore and ProcessController instead.

Use a native QMainWindow, sidebar, stacked pages, scroll areas, layout-managed cards/forms, password fields, tables and a bounded plain-text log. Qt supplies native window movement and automatic display scaling. Existing settings metadata/help copy is extracted into a GUI-independent content module; the legacy pages remain unchanged for rollback.

All store reads/validation/imports/saves, LINE requests and subprocess launches run in QThread jobs. A signal bridge batches existing process events every 100 ms. Widgets are touched only on the application thread. Dirty editors are preserved during refresh. Save & Apply retains safe stop/save/notify/restart behavior; closing waits asynchronously for actions and owned subprocess cleanup. No force termination is added to the UI.

## Startup and distribution

The familiar batch entry point selects Qt by default; direct legacy Python startup remains available. Bootstrap checks Qt with an actual QApplication before opening pythonw and prints actionable dependency/plugin failures.

A frozen entry point dispatches internal launcher/server/macro roles to existing main functions, so a packaged application never treats its executable as a Python interpreter. Child roles share the existing control session. `BMS_PROJECT_DIR` explicitly selects writable operator data; source execution continues using the repository. Frozen execution defaults to per-user local application data. Seed only missing public defaults; never bundle private .env, screenshots, logs, anchors or recorded macros. A single PyInstaller onedir spec collects Qt plugins, lazy desktop dependencies and tzdata. Building/distribution is outside this task.

## Verification

Run the existing unittest suite after installing dependencies. Add offscreen Qt tests covering every page/settings field, saving/dirty/stale edits, targets/macros/anchors, worker event-loop responsiveness and exceptions, safe restart, log bounds, cancellation and shutdown while work is active. Verify child command dispatch, writable/bundled paths and bootstrap. Run a real offscreen QApplication smoke launch. Windows batch execution, scaling/dragging, native hooks, BMS input and real LINE/ngrok remain local acceptance checks.

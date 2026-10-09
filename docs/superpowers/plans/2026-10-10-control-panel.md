# Windows Control Panel Implementation Plan

> Execute inline with superpowers:executing-plans; use a focused reviewer after implementation.

**Goal:** One-click native settings and lifecycle management for the existing working bot.

**Architecture:** Tkinter views call a validated local project store and a subprocess controller. The controller launches the existing launcher; a per-run local control session coordinates readiness and graceful shutdown with launcher/server.

**Tech Stack:** Python 3.12/3.14, Tkinter/ttk, existing dotenv/config/macro/LINE modules, standard-library subprocess/threading/JSON.

**Spec:** `docs/superpowers/specs/2026-10-10-control-panel-design.md`

## Global constraints

- Preserve tested capture/LINE/ngrok behavior; no new remote administration API or dependency.
- Blank GROUP_ID supports private delivery; group takes priority.
- Preserve private files and completed reports; redact all credential values in process logs.
- Stop gracefully and affect only owned processes. Keep slow work off the Tk thread.
- Config/target saves are atomic, backed up, validated, and reject stale data or active external processes.

## Review focus

- OneDrive file replacement failures must leave original settings intact.
- A runtime `set-login` change must not be overwritten by a stale GUI form.
- Stop during tests/tunnel connection/capture must not leave an owned child behind.
- A macro recorder must not overlap a running bot or expose its private text.
- A stopped/restarted run must never consume an earlier run's stop request or report false readiness.

## Tasks

1. Create `panel_settings.py` and `tests/test_panel_settings.py`.
   - [x] Write and run failing tests for settings/comment/path preservation, validation, stale edit rejection, target round trips, and private macro import.
   - [x] Implement `ProjectStore.load_settings/save_settings/load_targets/save_targets/readiness/import_macro/list_macros` with atomic writes and private backups.
   - [x] Run the focused tests and existing suite.
2. Create `runtime_control.py`, `panel_process.py`, and `tests/test_panel_process.py`; extend launcher/server.
   - [x] Write failing tests for per-run stop/readiness, noninteractive startup, redacted logs, duplicate starts, and real owned subprocess shutdown.
   - [x] Implement `ControlSession` and `ProcessController`, reuse launcher/ngrok cleanup, and register server graceful-stop monitoring.
   - [x] Run lifecycle and existing launcher tests.
3. Create `control_panel.py`, `panel_pages.py`, `start_gui.bat`, and `tests/test_control_panel.py`.
   - [x] Write Tk interaction tests for forms, target editing, save validation, process controls, and unsaved-state protection.
   - [x] Implement styled sidebar pages, responsive background actions, setup checks, macro tools, and bootstrap entry.
   - [x] Run the UI with a virtual display, inspect screenshots, and document operator usage.
4. Validate and publish.
   - [x] Run full Python 3.12/3.14 suites with Tk tests, formatter, and diff checks.
   - [x] Review lifecycle/storage/UI failure handling and resolve findings.
   - [x] Commit/push to the existing branch and update the attached PR.

Validation evidence: 141 tests passed with an actual Tk display on Python 3.12 and 3.14. Ten repeated GUI suite runs passed on Python 3.14 (130 tests). Black and git diff checks passed. All five pages were rendered with demo data at default and minimum window sizes; clipped controls were fixed and covered by regressions. Review confirmed alias clearing, snapshot consistency, edit freezing, and main-thread test cleanup. Native Windows input, cmd/pythonw, and live LINE/ngrok remain operator deployment checks.

# BMS Control Panel GUI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modernize the existing Tkinter control panel without changing its backend behavior, public interfaces, or configuration formats.

**Architecture:** Add semantic theme and reusable presentation modules, then compose them through the existing `ControlPanel` and page classes. Existing callbacks continue to call `ProjectStore` and `ProcessController`; background work, queues, subprocesses, and application services are unchanged.

**Tech Stack:** Python 3.12/3.14, Tkinter/ttk, standard library, existing unittest suite.

**Spec:** `docs/superpowers/specs/2026-10-10-gui-redesign-design.md`

## Global Constraints

- Do not modify capture, detection, macro execution, scheduling, LINE, ngrok, authentication, quota, cancellation, or delivery behavior.
- Do not change `.env`, `targets.json`, macro, webhook, or API contracts.
- Keep all blocking work off the Tk main thread and preserve the current 100 ms event poll.
- Keep secrets masked and out of status cards, logs, tests, and screenshots.
- Keep Dashboard, Settings, Targets, Macros, Help, and every existing action.
- Use no new runtime dependency and no emoji as structural icons.
- Structured scheduler telemetry is out of scope.

## Review Focus

- Theme changes must update both ttk and classic Tk widgets without hiding text or focus.
- Log search/filter/clear must never alter the underlying process log or block event polling.
- Start, stop, save, restart, record, and play controls must retain their current enablement semantics.
- Minimum-size and high-DPI layouts must keep primary controls reachable.
- Reorganized pages must not expose secrets or overwrite unsaved edits.

---

### Task 1: Semantic theme and presentation utilities

**Files:**
- Create: `panel_theme.py`
- Create: `panel_widgets.py`
- Create: `tests/test_panel_presentation.py`

**Interfaces:**
- Produces: `ThemePalette`, `palette(name)`, `preferred_theme()`, `status_tone(state)`, `classify_log_line(text)`, and `filter_log_lines(lines, query, levels)`.
- Consumes: no application-service interfaces.

- [ ] Write tests for complete light/dark semantic tokens, known and unknown theme names, state-to-tone mapping, case-insensitive log search, severity filtering, and stable input ordering.
- [ ] Run the focused tests and confirm they fail because the modules do not exist.
- [ ] Implement only the tested pure presentation interfaces, plus ttk theme registration and small reusable widget classes needed by later tasks.
- [ ] Run the focused tests and the existing suite.
- [ ] Commit the task.

### Task 2: Accessible shell and operations dashboard

**Files:**
- Modify: `control_panel.py`
- Modify: `tests/test_control_panel.py`

**Interfaces:**
- Consumes: Task 1 theme registration, status mapping, log filtering, and presentation widgets.
- Produces: persistent header process controls, theme switching, dashboard health cards, and enhanced log controls while retaining existing public widget attributes.

- [ ] Add GUI tests for visible focus styles, persistent process status/action controls, theme switching, log search/filter/clear/copy behavior, state labels, and minimum-size reachability.
- [ ] Run the focused GUI tests under an available display and confirm the new assertions fail; record display skips when no display exists.
- [ ] Apply semantic styles and rebuild the shell/dashboard without changing process or store calls.
- [ ] Run focused presentation tests, GUI tests, and the complete suite.
- [ ] Commit the task.

### Task 3: Responsive Settings, Targets, Macros, and Help

**Files:**
- Modify: `panel_pages.py`
- Modify: `tests/test_control_panel.py`

**Interfaces:**
- Consumes: Task 1 semantic styles/widgets and existing `ControlPanel`, `ProjectStore`, and `ProcessController` contracts.
- Produces: responsive page layouts, selection-aware action states, table scrollbars, state badges, and improved help presentation.

- [ ] Add GUI tests for settings save-state feedback, target empty/count/selection states, macro selection controls, scrollbars, and narrow/wide reflow.
- [ ] Run the focused GUI tests and confirm failures for the missing presentation behavior.
- [ ] Redesign the four pages while preserving every callback and public test attribute.
- [ ] Run focused presentation tests, GUI tests, and the complete suite.
- [ ] Commit the task.

### Task 4: Verification and Windows handoff

**Files:**
- Modify only tests or presentation files when a failing verification demonstrates a GUI defect.

**Interfaces:**
- Consumes: Tasks 1-3 completed GUI.
- Produces: verified release candidate and documented platform limits.

- [ ] Run syntax compilation, presentation tests, GUI tests, and the complete Python suite.
- [ ] Inspect the final diff for backend/configuration changes and secret exposure.
- [ ] Review keyboard focus, state contrast, resize behavior, and background-thread boundaries against the spec.
- [ ] Record Cloud limitations and the Windows checks still required.
- [ ] Commit any test-first verification fixes, then complete the branch review.

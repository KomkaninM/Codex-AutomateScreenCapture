# BMS Control Panel GUI Redesign

The existing Tkinter control panel will be redesigned as a restrained Windows operations console while preserving every current workflow and backend interface. The five existing destinations remain Dashboard, Settings, Targets, Macros, and Help. The GUI continues to call `ProjectStore` and `ProcessController`; capture, detection, macro playback, scheduling, LINE delivery, ngrok, configuration formats, and graceful cancellation remain unchanged.

## Visual direction

Use semantic theme tokens instead of colors embedded throughout widgets. Light mode uses a cool neutral background, white surfaces, navy text, trust-blue actions, and green/amber/red status colors. Dark mode uses slate surfaces and accessible lighter state colors. The initial theme follows Windows when available and can be changed for the current GUI session without adding an `.env` field.

Use Segoe UI (or the platform Tk default) for the interface and Consolas (or Courier) for logs and tabular values. Follow a 4/8-pixel spacing rhythm, clear type hierarchy, visible keyboard focus, text labels for every status, flat surfaces with restrained borders, and no decorative animation or emoji icons.

## Application shell

Keep the fixed sidebar and five navigation destinations. Add a persistent header status badge and contextual Start/Stop action so process state remains visible on every page. Navigation has a visible selected state and keyboard focus. The bottom sidebar retains the reports-folder shortcut and state summary.

## Dashboard

Present Bot, Delivery, Webhook, and Login readiness as scannable health cards. Preserve Start/Stop, minimize-on-start, webhook copy, setup refresh, LINE quota, macros folder, setup checks, and the live log. The log gains presentation-only search, severity filtering, copy, clear-view, and autoscroll; the underlying bounded process log and file logging do not change.

## Settings

Keep the existing LINE & ngrok, Automation, and Advanced groups and all current fields. Improve grouping, required/optional cues, save-state feedback, error visibility, and button hierarchy. `ProjectStore` remains the sole save and validation authority. Secrets stay masked by default.

## Targets

Use a table/editor split at wide widths and a stacked layout at narrow widths. Preserve Add, Update, Remove, Clear, Save, and Reload behavior. Add scrollbars, flexible columns, row count, empty-state text, selection-aware actions, and clear destructive styling.

## Macros and Help

Keep every macro action, macro metadata, anchor import, privacy rule, and stop-before-use requirement. Use status badges and selection-aware actions. Reorganize Help into scannable cards and copy-friendly command examples without changing documentation meaning.

## Boundaries

Only `control_panel.py`, `panel_pages.py`, and GUI tests are modified. `panel_theme.py` and `panel_widgets.py` are added for presentation concerns. Backend, adapter, configuration, automation, and integration modules remain untouched. The GUI must continue processing slow work through its existing background threads, subprocess monitor, queues, and 100 ms Tk event poll.

Structured scheduler-job progress is excluded because the GUI has no scheduler telemetry interface. Adding it requires a separate approved backend change.

## Verification

Preserve existing GUI widget contracts used by tests. Add tests for semantic theme data, log filtering, status mapping, theme switching, focus visibility, responsive table/editor layouts, and existing interactions. Run the complete Python suite. Native Windows verification covers startup, resize behavior, keyboard navigation, light/dark themes, 100/125/150 percent display scaling, process lifecycle, and macro tools.

# Windows control panel

The operator wants a beginner-friendly desktop application for the existing, working LINE bot: run/stop, edit the project `.env`, manage `targets.json`, and choose/record/play macros. The control panel must preserve the existing desktop capture, WebP delivery, quota, login-anchor, and post-acceptance logout behavior.

Use Python's bundled Tkinter/ttk for a native Windows window with no additional product dependencies. A sidebar opens Dashboard, Settings, Targets, Macros, and Help. Dashboard shows lifecycle status, delivery mode, webhook URL, setup checks, and bounded redacted live logs. Start launches the tested launcher; Stop requests graceful shutdown, drains active work, and stops only the ngrok process it owns. Automatically minimize when the bot becomes ready so the BMS remains visible.

Settings use labeled forms and inline descriptions, masked secret entries, macro dropdowns, ngrok executable browsing, and an advanced section. Preserve existing comments, unknown settings, and Windows paths. Validate before atomic replacement, keep a private backup, and reject stale edits. Changes are saved while stopped; Save & Restart handles an owned running bot. Group ID remains optional and takes priority over User ID. Defaults and validation come from `config.Settings`.

Targets use a table and Add/Update/Remove form, editable IDs/names and a macro selector. Validate using the server's existing registry contract. Preserve extra row fields, reject duplicates and unsafe filenames, and save atomically with a private backup. Settings and targets require restart and cannot be written while another bot/launcher owns the project.

Macros show private local filenames, validation state, step count, and modification time without revealing recorded text. Import current or legacy JSON through the existing converter/validator. Record and Play use the existing macro tool and the same cross-process desktop lock. Minimize during recording/playback; explain F8/F9 and the five-second playback countdown. Recording/playback are unavailable while the bot runs. Choose login/logout macros through the settings forms.

Help includes first-run LINE/ngrok steps, command examples, screenshot archive location, and existing report/WebP behavior. Credentials and macro contents never go into screenshots, logs, or documentation. Configuration backups stay under ignored `.runtime`.

The launcher needs a noninteractive GUI mode, not a second implementation of ngrok setup. Project-local, per-run random control files request shutdown and publish readiness; no network administration endpoint is added. Existing command-line startup and Ctrl+C remain supported. A dedicated one-click `start_gui.bat` reuses bootstrap and opens the panel using the prepared Python environment.

Validation covers real configuration round trips, concurrent-edit rejection, target validation, real subprocess start/readiness/stop, bounded secret-redacted logs, existing launcher regressions, actual Tk widgets under a virtual display, and the complete Python 3.12/3.14 suites. Native Windows input and live LINE/ngrok remain deployment checks.

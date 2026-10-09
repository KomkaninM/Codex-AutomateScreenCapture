# BMS Automation LINE Bot

A desktop BMS bridge with signed LINE webhooks, serialized automation, screenshot archival, and scheduled group or private-chat delivery. Python 3.11+ is required; the application tests cover Python 3.12 and 3.14. On Windows use the standard **64-bit x64 Python build** (not ARM64 or free-threaded Python). Run one bot process in the logged-in Windows desktop session; this is not a Windows service that runs on the noninteractive service desktop.

## Install and configure

### One-click Windows launcher

Extract the complete downloaded ZIP into a normal folder, then double-click **`start_bot.bat`**. Install standard Windows x64 Python 3.14 (or 3.12) and download the official ngrok CLI once beforehand. Set `NGROK_EXE_PATH` in `.env` to its full path, put ngrok on PATH, or place `ngrok.exe` beside the launcher. No administrator privileges or PowerShell execution-policy changes are needed.

The launcher creates its own `.venv-launcher`, installs the pinned packages using prebuilt native dependencies, checks the installation, runs the full test suite, starts/reuses ngrok, saves the detected hostname as `NGROK_DOMAIN`, and starts `server.py`. The bot builds its HTTPS image URLs from that domain automatically. Later launches reuse installed dependencies unless `requirements.txt` changes or the package check fails. The console stays open to show status and errors. A launcher lock prevents two double-clicks from running setup concurrently.

On the first launch it copies `.env.example` to `.env` only if `.env` does not already exist, opens Notepad, and waits for you to save your LINE credentials and ngrok settings. Use `NGROK_AUTHTOKEN` in your private `.env` or ngrok's existing authenticated configuration. Get your account token from [the official ngrok dashboard](https://dashboard.ngrok.com/get-started/your-authtoken). A verified ngrok account and valid token are required for a new installation. The launcher automatically writes a project-local `.runtime/ngrok.yml` from `NGROK_AUTHTOKEN`; it does not overwrite your global ngrok configuration or put the token in command-line arguments. With a blank token it uses your existing ngrok authentication. Existing settings, operator macros, reference images, and screenshots are preserved. Missing BMS files are listed; the server can still start for `check-id` setup, while capture remains blocked until the recorded files are supplied.

For example, add these ngrok settings to your private `.env`, replacing the example path and domain with your own:

```dotenv
NGROK_EXE_PATH='C:\Users\HWTHR\Downloads\ngrok.exe'
NGROK_AUTHTOKEN=your-account-authtoken
NGROK_DOMAIN=your-domain.ngrok-free.app
IMAGE_TTL_SECONDS=0
```

Use **single quotes** around Windows paths so backslashes are preserved; spaces in folder names are supported. A relative executable path is resolved from the project folder. A configured path is used before PATH or the local fallback. If it points to a missing file, the launcher reports the setting to fix. Once configured, double-clicking `start_bot.bat` runs `ngrok.exe http <PORT> --domain=<NGROK_DOMAIN>` automatically. With a blank domain, ngrok can allocate one and the launcher records the detected hostname.

`LINE_CHANNEL_SECRET` is required for this bot. Find it in **LINE Developers → your Messaging API channel → Basic settings → Channel secret**. The access token authorizes outgoing messages; the channel secret verifies that incoming webhooks came from LINE. Keep both in your private `.env`. `PUBLIC_TUNNEL_URL` is unnecessary when `NGROK_DOMAIN` is set. Older configurations containing it still work as a fallback; the domain takes precedence when both are present.

When upgrading an existing installation, edit your existing `.env` to add `NGROK_EXE_PATH` and change `IMAGE_TTL_SECONDS=604800` to `IMAGE_TTL_SECONDS=0` for image links without an expiration. The launcher preserves your file rather than replacing it with the new template. You can remove old `PUBLIC_TUNNEL_URL` and `CLOSE_MENU_MACRO` entries. All saved screenshots remain on disk for reporting regardless of the link expiry setting.

The launcher prints the exact webhook URL to enter in LINE Developers. Enable Use webhook and Webhook redelivery there. For group use, invite the bot to your group; for private use, add the bot as a LINE friend. With both IDs blank, send `check-id` in either chat, copy the desired group ID or user ID into `.env`, then relaunch. It does not change LINE account settings automatically. Keep the BMS desktop at 100% scaling, awake, and unlocked, and record your BMS macros/reference images as described below.

Ngrok output is captured in the launcher window and saved to `.runtime/ngrok.log`; there is no separate console that disappears on failure. Tokens are redacted from diagnostics. On a recognized missing/invalid-token error, the launcher opens the official ngrok dashboard and your `.env` in Notepad, waits for you to save `NGROK_AUTHTOKEN`, and retries once. Domain conflicts, another running agent, and network errors remain visible with their error code. Never share your token or `.runtime/ngrok.yml`.

Keep the launcher window open while using the bot. Press Ctrl+C to stop; it allows the bot to finish its active transaction and stops only the ngrok process it started. An already-running matching ngrok tunnel is reused and left running. If another ngrok agent uses port 4040 for a different port/domain, the launcher asks you to configure or stop it yourself. A private `.env` change takes effect on the next launch. `start_bot.bat` and the launcher flow require final validation on an actual Windows PC; automated tests on Linux exercise the helper behavior with process adapters.

### Manual setup

From the repository directory on Windows:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --only-binary=numpy,opencv-python-headless,pillow -r requirements.txt
Copy-Item .env.example .env
```

Python 3.12 also works: substitute `py -3.12` when creating the environment. If the older download fails while compiling NumPy on Python 3.14, download the updated requirements, upgrade pip, and rerun the installation command above in your existing Python 3.14 environment. The binary-only option applies to the three native image dependencies; PyAutoGUI and its pure-Python helpers may still build wheels locally. No Visual Studio compiler is required for these native dependencies on Windows x64.

Edit `.env` locally. Set `CHANNEL_ACCESS_TOKEN`, `LINE_CHANNEL_SECRET`, and your ngrok settings (`NGROK_DOMAIN` is a hostname only). Choose a chat mode:

- **Group:** set `GROUP_ID`. It takes priority over `USER_ID`; only that group can issue commands and scheduled captures go there. Any member can run macros and configuration commands, so use a trusted operator group.
- **Private:** leave `GROUP_ID` blank and set `USER_ID` to the ID returned by `check-id` in your private chat. Only that user can issue commands and scheduled captures go to that user. The bot must be a friend and unblocked for pushes.
- **Discovery:** leave both blank. Only `check-id` works, from a private or group chat; desktop commands remain blocked until an ID is configured.

For private mode, your `.env` contains:

```dotenv
GROUP_ID=
USER_ID=Uyour-user-id-from-check-id
```

The template follows the variable names and defaults in the supplied Python configuration loader. `LINE_CHANNEL_ACCESS_TOKEN` and `DEFAULT_LOGIN_MACRO` are supported aliases; `CHANNEL_ACCESS_TOKEN` and `LOGIN_MACRO_SCRIPT` take precedence when nonempty. Process environment values override `.env`. `set-login` and auto-logout toggles are synchronized and persisted to `.env`; external process-level overrides still take precedence after restart. Keep `.env`, desktop macros, and screenshots private; they are ignored by Git. No token values are printed at startup.

1. Set Windows display scaling to **100%** and the primary display resolution to `DESKTOP_WIDTH` × `DESKTOP_HEIGHT` (the supplied template uses your 3000 × 2000 display). Keep the BMS on the primary monitor and its window position fixed. Per-monitor DPI awareness is enabled before capture/input; primary system DPI other than 96 and resolution mismatch block automation. If upgrading an existing `.env`, change `DESKTOP_WIDTH=3000` and `DESKTOP_HEIGHT=2000` there as well.
2. Configure Windows power and security policies to keep the display awake and desktop unlocked during operation. A disconnected/locked RDP session can remove or change the graphical desktop; validate your chosen console/session arrangement. Run under a dedicated operator account with the permissions BMS needs.
3. Create `scripts/macros/login_bms.json`, `DH07C.json`, and `DH07D.json` using your BMS coordinates. Create `logout.json` only if you enable auto-logout. Adjust `targets.json` to match your deployment. Target macros must work from both the logged-out screen and the logged-in screen: when logged out, they replace the default login and navigation entirely. Ad-hoc macros also validate the session, running the default login first when necessary. No BMS coordinates are fabricated in this repository.
4. Save a tight, distinctive crop of a control on the BMS login page as `assets/login_anchor.png`, recorded at the same scaling/resolution as your desktop. This is the only reference image required. When it appears after a session timeout, the bot treats the session as logged out and runs the default login macro, or the requested target macro directly. When the anchor disappears, the bot assumes the session is active and can capture. Missing/unreadable reference files and an anchor larger than the captured screen block automation. Keep the BMS visible on the primary desktop: another application covering the login page can hide the anchor and be mistaken for an active BMS session. Set `CONFIDENCE_THRESHOLD` after testing both the login page and your normal BMS screen. `DETECTOR_INTERVAL_SEC` is retained as a legacy configuration value; there is no unattended idle polling that could interfere with the UI. Session checks occur before each transaction and ten seconds before scheduled captures.
5. For manual startup, launch ngrok separately, for example `ngrok http --domain=<your-reserved-domain> 5000`, using its official CLI and your securely configured authentication. `server.py` does not start ngrok; the one-click launcher does. Enable webhooks and disable LINE Official Account automatic responses that conflict with this bot. Configure LINE's webhook URL as `https://<your-domain>/callback`.
6. Start the bot:

```powershell
.\.venv\Scripts\python.exe server.py
```

`server.py` uses Waitress on `0.0.0.0:PORT`, prints the configuration dashboard, immediately requests quota/consumption/group-member metrics, and starts the scheduler. An OS file lock rejects a second bot instance. Do not launch multiple WSGI processes or use Flask's reload mode: each process has its own desktop lock and schedule state. `GET /health` checks HTTP availability only, not BMS or LINE readiness. Ctrl+C drains active desktop work and cancels pending schedules.

## Commands

| Command | Behavior |
| --- | --- |
| `capture` | Capture the current screen, logging in with the default macro if needed. |
| `capture 07C [note]` | Use the target macro, save under `screenshots/07C`, and reply with an image. |
| `capture [target] [note] --starttime 15:30:00` | Acknowledge now; one-time scheduled push at the next occurrence of this time. |
| `start-capture 30m [target] [note] [--starttime 15:30]` | Recurring scheduled pushes; otherwise first capture is one interval from now. |
| `stop-capture` | Cancel all schedules, including queued capture/precheck work. |
| `set-login DH07A.json` | Validate a local macro, update the default, and persist to `.env`. |
| `login` | Validate/recover the BMS session with the current login macro. |
| `macro name.json` | Replay a validated local macro. |
| `enable-autologout` / `disable-autologout` | Persist the post-capture logout setting. |
| `check-id` | Reply with the event's group and user IDs. |
| `check-quota` | Reply with monthly limit, consumption, remaining quota, and recipient reach information. |
| `help` | Show command usage. |

Quote notes when needed. Macro names can omit `.json`; path traversal and arbitrary shell commands are rejected. Intervals use `s`, `m`, or `h` and must be between ten seconds and 365 days. Local schedule times use `TIMEZONE` (default `Asia/Bangkok`); a time already passed means tomorrow. Schedules are **in memory** and must be recreated after restart. Missed intervals are skipped rather than replayed as a backlog. Prechecks run ten seconds early on a best-effort basis; a busy desktop can delay them and the capture. Capture waits for its own precheck to finish and rechecks the session. A session timeout during navigation receives one relogin attempt; unknown states remain blocked. `stop-capture` cancels immediately at authenticated ingress and invalidates older queued schedule registrations. A push already sent to LINE cannot be recalled. If the acknowledgement queue is saturated, cancellation still succeeds but its reply may be omitted; the completed stop remains deduplicated.

All interactive commands use `replyToken`, including `login` and `macro`, following the strict quota invariant. Scheduled deliveries have no reply token and use `pushMessage`; group recipient reach scales quota use. Multiple message objects in one delivery are sent in a single API request. LINE replies do not count toward monthly push quota. Failed/expired replies **never** fall back to a paid push. Keep complete capture workflows, including auto-logout, below 45 seconds; queued interactive work expires after 45 seconds to leave margin for LINE token validity. For longer workflows use scheduling, or shorten macros. Expired commands may be dropped and require the operator to retry.

Closing a menu collapses a navigation panel; logout ends the BMS session. There is no `close-menu` command or required close-menu macro. `ENABLE_AUTO_LOGOUT=True` runs `LOGOUT_MACRO_SCRIPT` after capture and confirms that `assets/login_anchor.png` appears again; leave it `False` if you want the BMS session to stay open. No `logout_anchor.png` is needed.

LINE exposes group **member count**, not each member's block status. The dashboard labels membership as an upper bound instead of inventing an unblocked target reach. The wrapper also supports daily follower insights and eligible-account follower ID enumeration; follower reach is not interchangeable with group reach. Metrics unavailable for the account or group are displayed as unavailable, and an unlimited quota is displayed explicitly.

## Record, convert, and play macros

Double-click **`macro_tool.bat`** to open the macro tool. It installs/reuses the launcher environment and opens a numbered menu; it does not start the bot or ngrok. Stop the bot and close its launcher before recording or playing, because both tools share its desktop instance lock.

To record:

1. Choose **1 — Record**, then enter a filename such as `DH09D.json`.
2. Switch to the BMS on the primary monitor. Press **F8** to start.
3. Perform the mouse clicks, type text, paste text, and use your keyboard shortcuts.
4. Press **F8** again to stop and export to `scripts/macros/DH09D.json`. **F9** cancels without saving. F8/F9 are reserved and are not recorded.

The recorder merges ordinary typing into text steps; playback pastes those strings with Ctrl+V. A recorded Ctrl+V or Shift+Insert saves the clipboard text at that moment, rather than depending on future clipboard contents. Pauses between actions and after the last action are retained. The recording stores the actual primary display size, and playback refuses a different resolution. The macro tool prints the matching `DESKTOP_WIDTH`/`DESKTOP_HEIGHT` settings to put in `.env`.

Record short sequences: `MAX_MACRO_SECONDS` defaults to 35 seconds and also bounds exported playback. Typing is accelerated, but click/keypress overhead still counts toward validation. Unsupported drags, scrolling, modifier-clicks, or clicks outside the primary monitor stop recording with an explanation; they are never silently converted into different actions. The tool listens only while its explicit recording session is open. Passwords typed or pasted during that session are stored in the private JSON, so keep these files out of Git and back them up securely.

To play, choose **2 — Play**, enter the filename, and switch to the correct BMS starting screen during the five-second countdown. Playback uses the same validated macro interpreter as the bot. Moving the mouse to a screen corner triggers PyAutoGUI's fail-safe. Standalone playback replays the sequence as recorded; it does not run the bot's login guard or send LINE messages.

To convert your old DH09D macro on your PC, copy its **complete JSON** from the chat, choose **3 — Convert old JSON**, enter `DH09D.json`, and leave the source-path prompt blank to read the copied JSON. Alternatively select a local JSON file. Conversion preserves coordinates, text, the navigation URL, and every `post_delay`; `default_post_delay` supplies missing delays. The output uses `click`, `text`, `press`, and `delay` and records the configured desktop dimensions. Existing files are preserved by default; choose another filename or explicitly use `--overwrite` from the command line.

The supplied DH09D script contains a complete login followed by navigation. Start standalone playback on the login page. To use it for session-timeout recovery, set `LOGIN_MACRO_SCRIPT=DH09D.json` in `.env` or send `set-login DH09D.json`, then use untargeted `capture`. Its URL was preserved exactly and contains `DH09-C`, despite the DH09D filename. Recorded/login scripts with credentials are ignored by Git and are not included in the GitHub ZIP; create/import them locally using the tool.

For direct Python execution after installing requirements:

```powershell
.\.venv-launcher\Scripts\python.exe macro_tool.py record DH09D.json
.\.venv-launcher\Scripts\python.exe macro_tool.py play DH09D.json
.\.venv-launcher\Scripts\python.exe macro_tool.py convert DH09D.json --source "C:\path\old_DH09D.json"
# Read complete JSON copied to the clipboard:
.\.venv-launcher\Scripts\python.exe macro_tool.py convert DH09D.json
```

Live recording uses the Windows-only `pynput` dependency; the recorder/converter tests run without desktop hooks on Linux. Actual Windows hooks, keyboard layout, and BMS input behavior require a local check.

## Macro contract

A JSON array of steps or an object containing `steps` is accepted. The following shows the supported primitives; replace the coordinates and text with your recorded BMS inputs before use:

```json
{
  "steps": [
    {"action": "click", "x": 120, "y": 250, "clicks": 1, "button": "left", "delay": 0.2},
    {"action": "text", "text": "operator", "delay": 0.2},
    {"action": "hotkey", "keys": ["ctrl", "a"]},
    {"action": "press", "key": "enter"},
    {"action": "sleep", "seconds": 2.0}
  ]
}
```

The entire script is validated before any input. Optional `desktop` metadata has positive integer `width` and `height` fields and must match the current display before playback. Coordinates must be on the primary desktop; macro files must remain under `scripts/macros` even when symlinked. Text uses `pyperclip` and Ctrl+V and restores the previous clipboard contents. PyAutoGUI's corner fail-safe stays enabled. Each macro has a `MAX_MACRO_SECONDS` execution bound; no executable commands or Python expressions are supported. `scripts/macros/example.json` is an executable delay-only schema example, not a BMS login macro.

## Images and tunnel updates

Each capture saves a quality-100 full-resolution archival JPEG (JPEG is not mathematically lossless), compressed WebP, LINE JPEG up to 2560 pixels, and 240-pixel preview JPEG. LINE image messages support **JPEG/PNG**, not WebP, so sending the WebP URL would fail. WebP is retained for bandwidth-friendly downloads; the compatible JPEG URLs are sent to LINE. Filenames contain milliseconds and a 128-bit random token. Partitions use the requested target or current default macro name.

Only generated delivery/preview filenames are served under `/images/<path>`. Archival JPEGs and arbitrary files are private. Paths and symlinks cannot escape screenshot storage. All image files are retained permanently under `screenshots/<target-or-macro>/`; no background cleanup deletes them. Back up that folder for your reports. `IMAGE_TTL_SECONDS=0` is the default and disables public link expiration. A positive value optionally limits public access by file age, without deleting the file. Public URLs act as bearer capabilities: anyone possessing a URL can retrieve that image while it is available. Image links still require the bot, its ngrok domain, and the saved files to remain available; LINE's own storage lifetime is outside this bot's control.

Set a strong `INTERNAL_API_TOKEN` to enable live tunnel updates. Send an authenticated JSON request to the local service:

```text
POST /internal/update-tunnel
Authorization: Bearer <your-private-update-token>
Content-Type: application/json

{"url": "https://your-new-domain.ngrok.app"}
```

The hostname is persisted as `NGROK_DOMAIN` in `.env` and its HTTPS origin is applied in memory; no restart is needed. The endpoint is disabled when the token is blank. The one-click launcher does not need this endpoint or token: it records the tunnel before starting the server. Only HTTPS origins without userinfo, paths, or query strings are accepted. Restrict this endpoint at the tunnel/reverse proxy as an additional host policy. Previously sent image URLs still require the old domain to remain reachable; updating a URL cannot migrate LINE messages already sent.

## Reliability and validation

The webhook verifies the exact raw body using the channel secret before parsing. It returns promptly after bounded enqueueing; queue saturation returns 503 so LINE can redeliver. Enable LINE webhook redelivery. A SQLite ledger in `.runtime` suppresses accepted event IDs for seven days, including across restarts. It stores hashes, not tokens or message text. This is an at-most-once acceptance policy: a process crash after claiming an event can lose that workflow, so the operator must retry it. Exactly-once physical desktop input cannot be guaranteed by webhook retries. Scheduled push retries use one LINE retry key per delivery; replies are single-attempt. Requests have explicit connect/read timeouts and retain TLS verification.

For Linux/cloud development without a BMS desktop:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

Tests substitute hardware and LINE HTTP transport while exercising real Flask routing, signature checks, session guards, synchronization, scheduling, image encoding, file serving, configuration persistence, and restart deduplication. Desktop libraries are loaded lazily, allowing service tests on a headless machine. Linux desktop operation additionally needs an active X display and a working clipboard provider; Windows is the deployment target.

Before unattended deployment, validate a real logged-out capture for each target, an already-logged-in capture, logout confirmation, simultaneous commands, stop/restart scheduling, ngrok image retrieval by LINE, and actual quota changes for scheduled pushes. No live LINE token or BMS desktop is available in the cloud checkout, so those deployment checks are not represented as completed.

# BMS Automation LINE Bot

A desktop BMS bridge with signed LINE webhooks, serialized automation, screenshot archival, and scheduled group delivery. Python 3.11+ is required. Run one bot process in the logged-in Windows desktop session; this is not a Windows service that runs on the noninteractive service desktop.

## Install and configure

From the repository directory on Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` locally. Set `CHANNEL_ACCESS_TOKEN`, `LINE_CHANNEL_SECRET`, `GROUP_ID`, and `NGROK_DOMAIN` (hostname only) or `PUBLIC_TUNNEL_URL` (HTTPS origin). `USER_ID` is retained for compatibility; this bot delivers scheduled messages only to `GROUP_ID`. A blank group permits only `check-id` discovery from group chats; once the group is configured, events from other groups are ignored. Any member of the configured group can issue commands, including macros and configuration changes. Use a trusted operator group.

The template follows the variable names and defaults in the supplied Python configuration loader. `LINE_CHANNEL_ACCESS_TOKEN` and `DEFAULT_LOGIN_MACRO` are supported aliases; `CHANNEL_ACCESS_TOKEN` and `LOGIN_MACRO_SCRIPT` take precedence when nonempty. Process environment values override `.env`. `set-login` and auto-logout toggles are synchronized and persisted to `.env`; external process-level overrides still take precedence after restart. Keep `.env`, desktop macros, and screenshots private; they are ignored by Git. No token values are printed at startup.

1. Set Windows display scaling to **100%** and the primary display resolution to `DESKTOP_WIDTH` × `DESKTOP_HEIGHT` (default 1920 × 1080). Keep the BMS on the primary monitor and its window position fixed. Per-monitor DPI awareness is enabled before capture/input; primary system DPI other than 96 and resolution mismatch block automation.
2. Configure Windows power and security policies to keep the display awake and desktop unlocked during operation. A disconnected/locked RDP session can remove or change the graphical desktop; validate your chosen console/session arrangement. Run under a dedicated operator account with the permissions BMS needs.
3. Create `scripts/macros/login_bms.json`, `logout.json`, `close_menu.json`, `DH07C.json`, and `DH07D.json` using your BMS coordinates. Adjust `targets.json` to match your deployment. Target macros must work from both the logged-out screen and the logged-in screen: when logged out, they replace the default login and navigation entirely. Ad-hoc macros and `close-menu` also validate the session, running the default login first when necessary. No BMS coordinates are fabricated in this repository.
4. Save a tight, distinctive crop of a control visible only while logged in as `assets/logout_anchor.png` (for example, the logout button). Save a crop visible only while logged out as `assets/login_anchor.png`. Both must be recorded at the same scaling/resolution. Recognition checks a fresh frame; missing images, neither match, or both matching block automation. Set `CONFIDENCE_THRESHOLD` after testing both states. `DETECTOR_INTERVAL_SEC` is retained as a legacy configuration value; there is no unattended idle polling that could interfere with the UI. Session checks occur before each transaction and ten seconds before scheduled captures.
5. Launch ngrok separately, for example `ngrok http --domain=<your-reserved-domain> 5000`, using its official CLI and your securely configured authentication. `NGROK_AUTHTOKEN` is retained for launcher compatibility; the Python process does not start ngrok. Enable webhooks and disable LINE Official Account automatic responses that conflict with this bot. Configure LINE's webhook URL as `https://<your-domain>/callback`.
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
| `close-menu` | Replay `CLOSE_MENU_MACRO` using the same desktop lock. |
| `enable-autologout` / `disable-autologout` | Persist the post-capture logout setting. |
| `check-id` | Reply with the event's group and user IDs. |
| `check-quota` | Reply with monthly limit, consumption, remaining quota, and group-member upper bound. |
| `help` | Show command usage. |

Quote notes when needed. Macro names can omit `.json`; path traversal and arbitrary shell commands are rejected. Intervals use `s`, `m`, or `h` and must be between ten seconds and 365 days. Local schedule times use `TIMEZONE` (default `Asia/Bangkok`); a time already passed means tomorrow. Schedules are **in memory** and must be recreated after restart. Missed intervals are skipped rather than replayed as a backlog. Prechecks run ten seconds early on a best-effort basis; a busy desktop can delay them and the capture. Capture waits for its own precheck to finish and rechecks the session. A session timeout during navigation receives one relogin attempt; unknown states remain blocked. `stop-capture` cancels immediately at authenticated ingress and invalidates older queued schedule registrations. A push already sent to LINE cannot be recalled. If the acknowledgement queue is saturated, cancellation still succeeds but its reply may be omitted; the completed stop remains deduplicated.

All interactive commands use `replyToken`, including `login`, `macro`, and `close-menu`, following the strict quota invariant. The original command table's push entries for those interactive commands conflict with that invariant. Scheduled deliveries have no reply token and use `pushMessage`; group recipient reach scales quota use. Multiple message objects in one delivery are sent in a single API request. LINE replies do not count toward monthly push quota. Failed/expired replies **never** fall back to a paid push. Keep complete capture workflows, including auto-logout, below 45 seconds; queued interactive work expires after 45 seconds to leave margin for LINE token validity. For longer workflows use scheduling, or shorten macros. Expired commands may be dropped and require the operator to retry.

LINE exposes group **member count**, not each member's block status. The dashboard labels membership as an upper bound instead of inventing an unblocked target reach. The wrapper also supports daily follower insights and eligible-account follower ID enumeration; follower reach is not interchangeable with group reach. Metrics unavailable for the account or group are displayed as unavailable, and an unlimited quota is displayed explicitly.

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

The entire script is validated before any input. Coordinates must be on the primary desktop; macro files must remain under `scripts/macros` even when symlinked. Text uses `pyperclip` and Ctrl+V and restores the previous clipboard contents. PyAutoGUI's corner fail-safe stays enabled. Each macro has a `MAX_MACRO_SECONDS` execution bound; no executable commands or Python expressions are supported. `scripts/macros/example.json` is an executable delay-only schema example, not a BMS login macro.

## Images and tunnel updates

Each capture saves a quality-100 full-resolution archival JPEG (JPEG is not mathematically lossless), compressed WebP, LINE JPEG up to 2560 pixels, and 240-pixel preview JPEG. LINE image messages support **JPEG/PNG**, not WebP, so sending the WebP URL would fail. WebP is retained for bandwidth-friendly downloads; the compatible JPEG URLs are sent to LINE. Filenames contain milliseconds and a 128-bit random token. Partitions use the requested target or current default macro name.

Only generated delivery/preview filenames are served under `/images/<path>`. Archival JPEGs and arbitrary files are private. Paths and symlinks cannot escape screenshot storage. Public URLs act as bearer capabilities: anyone possessing a URL can retrieve that image until `IMAGE_TTL_SECONDS` (default seven days) elapses. LINE needs unauthenticated HTTPS retrieval; protect sensitive BMS data accordingly. Files remain on disk for archival reporting. Set a host disk-retention policy appropriate to your compliance requirements and available storage.

Set a strong `INTERNAL_API_TOKEN` to enable live tunnel updates. Send an authenticated JSON request to the local service:

```text
POST /internal/update-tunnel
Authorization: Bearer <your-private-update-token>
Content-Type: application/json

{"url": "https://your-new-domain.ngrok.app"}
```

The update is persisted to `.env` and applied in memory; no restart is needed. The endpoint is disabled when the token is blank. Only HTTPS origins without userinfo, paths, or query strings are accepted. Restrict this endpoint at the tunnel/reverse proxy as an additional host policy. Previously sent image URLs still require the old domain to remain reachable; updating a URL cannot migrate LINE messages already sent.

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

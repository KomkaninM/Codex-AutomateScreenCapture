"""Existing settings metadata and help copy, without Tkinter imports."""

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
                    "LINE Developers → Basic settings. Authenticates incoming "
                    "webhooks.",
                ),
                (
                    "GROUP_ID",
                    "Group ID (optional)",
                    "A configured group takes priority over the private user below.",
                ),
                (
                    "USER_ID",
                    "Private user ID (optional)",
                    "Leave Group ID blank for private chats. Send check-id in LINE to "
                    "find IDs.",
                ),
            ],
        ),
        (
            "ngrok tunnel",
            [
                (
                    "NGROK_EXE_PATH",
                    "ngrok.exe location",
                    "Browse to your downloaded executable. Spaces in paths are "
                    "supported.",
                ),
                (
                    "NGROK_DOMAIN",
                    "Public ngrok domain",
                    "Your reserved hostname, such as name.ngrok-free.dev. Blank "
                    "allows automatic discovery.",
                ),
                (
                    "NGROK_AUTHTOKEN",
                    "ngrok authentication token",
                    "Your ngrok account token. Blank uses existing ngrok "
                    "authentication.",
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
                    "Stored only in your private .env and inserted into locally "
                    "generated macros.",
                ),
                (
                    "LOGIN_MACRO_SCRIPT",
                    "Default login macro",
                    "Runs once when the login-page anchor is found.",
                ),
                (
                    "LOGOUT_MACRO_SCRIPT",
                    "Logout macro",
                    "Runs only after image delivery is accepted, when auto-logout is "
                    "enabled.",
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
                    "Bounds input and fixed delays. wait_image uses its JSON timeout; "
                    "LINE deadlines still apply.",
                ),
                (
                    "CONFIDENCE_THRESHOLD",
                    "Login anchor match threshold",
                    "0–1; used only for the pre-macro login check. wait_image has its "
                    "own confidence.",
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
                    "1–100; default 80. The image keeps its original resolution.",
                ),
                (
                    "IMAGE_TTL_SECONDS",
                    "Public image link lifetime (seconds)",
                    "0 means no expiration. Report JPEGs are never automatically "
                    "deleted.",
                ),
                (
                    "REPLY_UNKNOWN_COMMANDS",
                    "Reply to unrecognized messages",
                    "Send help for unknown text. Off avoids responding to ordinary "
                    "conversation.",
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
                    "Metadata for converting old macros; does not change click "
                    "coordinates.",
                ),
                (
                    "DESKTOP_HEIGHT",
                    "Imported recording height",
                    "Metadata only; playback does not check display size or scaling.",
                ),
                (
                    "RECORDED_STEP_DELAY_SECONDS",
                    "Recorded step delay (seconds)",
                    "Every action in a newly recorded macro uses this delay. Default "
                    "0.2.",
                ),
            ],
        ),
    ],
}

HELP_PARAGRAPHS = [
    (
        "Get online in four steps",
        "1. In Settings → LINE & ngrok, enter the LINE access token, channel secret, and "
        "your ngrok details. Browse to ngrok.exe. Click Save & Apply.\n"
        "\n"
        "2. Click Start Bot on Dashboard. Copy the webhook URL into LINE Developers → "
        "Messaging API, then enable Use webhook. For a reserved ngrok domain this setup is "
        "done once.\n"
        "\n"
        "3. Add the bot as a friend or invite it to your LINE group. Send check-id, enter "
        "your User ID or Group ID in Settings, then click Save & Apply. Group ID takes "
        "priority; leave it blank for private chats.\n"
        "\n"
        "4. In Macros, record/import your login macro and select a login-page anchor crop. "
        "Use Macro Editor to add Wait Image steps wherever loading must pause, then choose the default "
        "login macro in Settings. Keep the BMS visible and the app minimized during "
        "capture.",
    ),
    (
        "Useful LINE commands",
        "capture                                  Current screen\n"
        "capture 09D daily report                 Target with note\n"
        "start-capture 30m daily report            Recurring capture with immediate #0\n"
        "start-capture 30m report -s 15:30         Start at 15:30 with #1\n"
        "start-capture 30m report -m 10            Stop after 10 successful pictures\n"
        "start-capture 30m report -s 15:30 -m 10   Combine start time and limit\n"
        "capture report -s 15:30                   One-time capture\n"
        "stop-capture                              Stop all schedules\n"
        "check-id                                  Find your chat IDs\n"
        "check-quota                               Show messaging quota\n"
        "enable-autologout                          Log out after accepted delivery\n"
        "disable-autologout                         Keep the BMS session open\n"
        "\n"
        "Long aliases --starttime and --max are also supported.",
    ),
    (
        "Reports and image delivery",
        "Full-resolution WebP images are sent through ngrok, as requested for your tested "
        "setup. Full-resolution quality-100 JPEG reports remain permanently in "
        "screenshots/<target-or-macro>/. WebP quality defaults to 80 and can be changed in "
        "Settings. LINE officially documents JPEG/PNG; WebP follows your operator-tested "
        "setup.\n"
        "\n"
        "Captions include the actual capture time and optional note. Target captures show "
        "their macro. Recurring schedules without -s reply with an immediate #0 capture, "
        "then push #1, #2, and so on. Use -m to stop after a number of successful pictures; "
        "normal capture messages stay unchanged and one completion message is sent at the "
        "end. Schedules are stored in memory and must be started again after restarting the "
        "bot.",
    ),
    (
        "Safe stopping and editing",
        "Stop Bot waits for an active capture/send/logout to finish, then closes its server "
        "and the ngrok process it started. An existing external ngrok tunnel is left "
        "running. If another launcher is running, use its own window to stop it.\n"
        "\n"
        "Save & Apply automatically performs a safe restart when the bot is online and sends "
        "a LINE summary containing only non-sensitive operational changes. Private backups "
        "are kept in .runtime/backups. If a file changed in LINE or another editor, reload "
        "it before saving.",
    ),
    (
        "Macro tools",
        "Stop the bot before recording or playing. F8 starts recording, then F8 stops and "
        "exports JSON. F9 cancels. Playback starts after three seconds so you can switch to "
        "the BMS; moving the mouse to a corner triggers the fail-safe.\n"
        "\n"
        "New recordings use the configured delay for every exported action instead of "
        "reproducing the operator's timing gaps. Imports accept current macros, wait_image, "
        "and your old click_coord/type_text/press_key scripts. Existing filenames are "
        "preserved. The original recorded click coordinates are replayed without display "
        "consistency checks. Image waiting occurs only at explicit wait_image steps; there "
        "is no automatic wait after a macro.",
    ),
]

HELP_PARAGRAPHS.append(
    (
        "Visual Macro Editor",
        "Open a macro or choose New. Pick an action and click Add Step, then select a numbered step to edit it. Use Duplicate or Move Up/Down to organize the sequence. Save As keeps the original macro and does not change target mappings.\n\nFor Wait Image, choose a reference inside the project, select appear or disappear, and set a timeout. Stop is the default timeout behavior; Continue explicitly permits the next step after that wait expires. Cancellation, image errors and LINE deadlines still stop the macro. Preview checks the file; Test Image Detection checks the current primary screen once without clicking or typing. A snapshot result does not replace playback's consecutive-match checks.\n\nUnsaved changes are protected by Save / Discard / Cancel prompts. Save waits for active automation to stop safely. Text and existing unsupported configurations are hidden until explicitly revealed; unsupported steps are preserved but must be supported by the executor before saving.",
    )
)

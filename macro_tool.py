"""Record, generate, convert, export, and play private BMS JSON macros."""

from __future__ import annotations

import argparse
import _thread
import copy
import json
import os
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path
from contextlib import nullcontext
from urllib.parse import urlsplit

from config import BASE_DIR, Settings, macro_name
from macro_player import MacroPlayer, enable_dpi_awareness
from automation_errors import AutomationTimeoutError

BMS_GENERATOR_TEMPLATE = {
    "name": "DH08C",
    "description": "Generated template",
    "steps": [
        {
            "action": "click",
            "x": 1783,
            "y": 1115,
            "button": "left",
            "delay": 0.1,
        },
        {"action": "text", "text": "__BMS_USERNAME__", "delay": 0.1},
        {
            "action": "click",
            "x": 1803,
            "y": 1255,
            "button": "left",
            "delay": 0.1,
        },
        {"action": "text", "text": "__BMS_PASSWORD__", "delay": 0.1},
        {"action": "press", "key": "enter", "delay": 0.7},
        {
            "action": "click",
            "x": 681,
            "y": 116,
            "button": "left",
            "delay": 0.1,
        },
        {
            "action": "text",
            "text": "https://bms.invalid/generator",
            "delay": 0.3,
        },
        {"action": "press", "key": "enter", "delay": 0.7},
    ],
    "desktop": {"width": 3000, "height": 2000},
}


def convert_legacy(data):
    if isinstance(data, list):
        data = {"steps": data}
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        raise ValueError("Macro JSON must contain a steps array.")
    result = copy.deepcopy(data)
    default = result.pop("default_post_delay", 0)
    aliases = {"click_coord": "click", "type_text": "text", "press_key": "press"}
    steps = []
    for original in result["steps"]:
        if not isinstance(original, dict):
            raise ValueError("Each macro step must be an object.")
        step = dict(original)
        action = step.pop("action", step.pop("type", None))
        action = aliases.get(action, action)
        if action not in ("click", "text", "press", "hotkey", "sleep"):
            raise ValueError("Unsupported action in source macro.")
        delay = step.pop("post_delay", default)
        step.setdefault("delay", delay)
        steps.append({"action": action, **step})
    result["steps"] = steps
    return result


def build_from_template(template, name, url):
    """Copy a private login macro and replace its label and navigation URL."""
    if not isinstance(template, dict) or not isinstance(template.get("steps"), list):
        raise ValueError("Template macro JSON must contain a steps array.")
    url = url.strip() if isinstance(url, str) else ""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Enter the complete http or https generator URL.")
    result = copy.deepcopy(template)
    navigation = next(
        (
            step
            for step in reversed(result["steps"])
            if isinstance(step, dict)
            and step.get("action") == "text"
            and isinstance(step.get("text"), str)
            and step["text"].startswith(("http://", "https://"))
        ),
        None,
    )
    if navigation is None:
        raise ValueError("The template macro does not contain a navigation URL step.")
    result["name"] = name
    result["description"] = "Generated on " + datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    navigation["text"] = url
    return result


def build_fixed_template(name, url, username, password):
    """Build the standard eight-step generator macro with private credentials."""
    if not username or not password:
        raise ValueError(
            "Set BMS_USERNAME and BMS_PASSWORD in .env before using option 4."
        )
    template = copy.deepcopy(BMS_GENERATOR_TEMPLATE)
    template["steps"][1]["text"] = username
    template["steps"][3]["text"] = password
    return build_from_template(template, name, url)


def save_macro(directory, name, data, *, max_seconds=35, overwrite=False):
    directory = Path(directory).resolve()
    destination = directory / macro_name(name)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / ("validate_" + secrets.token_hex(16) + ".json")
    claimed = False
    try:
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(payload)
        MacroPlayer(directory, max_seconds=max_seconds).load(temporary.name)
        if not overwrite:
            with destination.open("x", encoding="utf-8"):
                pass
            claimed = True
        os.replace(temporary, destination)
        claimed = False
        return destination
    finally:
        temporary.unlink(missing_ok=True)
        if claimed:
            destination.unlink(missing_ok=True)


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    modes = command.add_subparsers(dest="command")
    record = modes.add_parser(
        "record", help="Record using F8 to start/stop and F9 to cancel."
    )
    record.add_argument("name", help="Output filename, e.g. DH09D.json")
    record.add_argument("--overwrite", action="store_true")
    play = modes.add_parser(
        "play", help="Play an existing macro after a three-second countdown."
    )
    play.add_argument("name")
    convert = modes.add_parser(
        "convert", help="Convert old JSON from a file or your clipboard."
    )
    convert.add_argument("name", help="Output filename, e.g. DH09D.json")
    convert.add_argument(
        "--source", type=Path, help="Old JSON file; omitted reads copied JSON text."
    )
    convert.add_argument("--overwrite", action="store_true")
    template = modes.add_parser(
        "template", help="Create a macro from the fixed BMS template."
    )
    template.add_argument("name", help="Output filename, e.g. DH08C.json")
    template.add_argument("url", help="Complete BMS generator URL")
    template.set_defaults(overwrite=False)
    return command


def menu(command):
    print(
        "\nBMS MACRO TOOL\n1. Record a new macro\n2. Play a saved macro\n3. Convert old JSON\n4. Create from fixed template\n5. Exit"
    )
    choice = input("Choose 1–5: ").strip()
    if choice == "5":
        return None
    if choice not in ("1", "2", "3", "4"):
        raise ValueError("Choose 1, 2, 3, 4, or 5.")
    name = input("Macro filename (example: DH09D.json): ").strip()
    if name and not name.endswith(".json"):
        name += ".json"
    args = [
        {"1": "record", "2": "play", "3": "convert", "4": "template"}[
            choice
        ],
        macro_name(name),
    ]
    if choice == "3":
        source = (
            input("Old JSON file path, or leave blank after copying the JSON: ")
            .strip()
            .strip('"')
        )
        if source:
            args += ["--source", source]
    elif choice == "4":
        args.append(input("Complete BMS generator URL: ").strip())
    return command.parse_args(args)


def run(args, cfg):
    name = macro_name(args.name if args.name.endswith(".json") else args.name + ".json")
    destination = cfg.macros_dir / name
    if (
        args.command == "record"
        and not args.overwrite
        and (destination.exists() or destination.is_symlink())
    ):
        raise FileExistsError("Recording destination already exists.")
    if args.command == "convert":
        if args.source:
            if args.source.stat().st_size > 1_000_000:
                raise ValueError("Source macro exceeds 1 MB.")
            source = args.source.read_text(encoding="utf-8-sig")
        else:
            import pyperclip

            source = pyperclip.paste()
            if len(source) > 1_000_000:
                raise ValueError("Copied JSON exceeds 1 MB.")
        data = convert_legacy(json.loads(source))
        data.setdefault(
            "desktop", {"width": cfg.expected_width, "height": cfg.expected_height}
        )
    elif args.command == "template":
        data = build_fixed_template(
            Path(name).stem, args.url, cfg.bms_username, cfg.bms_password
        )
    else:
        if sys.platform != "win32":
            raise RuntimeError("Recording and playback require your Windows desktop.")
        from server import single_instance
        from macro_recorder import record_macro

        # The bot and every macro tool share the same cross-process desktop lock.
        with single_instance(cfg.project_dir):
            enable_dpi_awareness()
            if args.command == "record":
                import pyautogui

                width, height = pyautogui.size()
                print(
                    f"Recorded desktop: {width} × {height} (informational). Playback uses the recorded coordinates directly."
                )
                data = record_macro(
                    Path(name).stem,
                    width,
                    height,
                    max_seconds=cfg.max_macro_seconds,
                    step_delay=cfg.recorded_step_delay_seconds,
                )
            else:
                player = MacroPlayer(
                    cfg.macros_dir,
                    max_seconds=cfg.max_macro_seconds,
                )
                player.load(name)
                print(
                    "Switch to the BMS. Playback starts in three seconds; move the mouse to a screen corner to abort."
                )
                for remaining in range(3, 0, -1):
                    print(remaining, flush=True)
                    time.sleep(1)
                player.play(name)
                print("Playback completed.")
                return
    path = save_macro(
        cfg.macros_dir,
        name,
        data,
        max_seconds=cfg.max_macro_seconds,
        overwrite=args.overwrite,
    )
    print(
        f"Saved {len(data['steps'])} steps to scripts/macros/{path.name}. Keep macros with passwords private."
    )


def main(argv=None):
    command = parser()
    args = command.parse_args(argv)
    try:
        if args.command is None:
            args = menu(command)
            if args is None:
                return 0
        from launcher import ensure_env_file

        ensure_env_file(BASE_DIR)
        from runtime_control import environment_control

        control = environment_control(BASE_DIR)
        with control.watch(_thread.interrupt_main) if control else nullcontext():
            if control and control.stop_requested():
                raise KeyboardInterrupt
            run(args, Settings.load())
        return 0
    except KeyboardInterrupt:
        print("\nMacro tool stopped.")
        return 130
    except FileExistsError:
        print(
            "A macro with that filename already exists. Choose another name, or explicitly use --overwrite."
        )
        return 1
    except Exception as exc:
        # JSON decoder messages and GUI errors may contain private input; expose only safe errors.
        if isinstance(exc, json.JSONDecodeError):
            print(
                "Invalid JSON. Copy the complete old macro object or select a valid JSON file."
            )
        elif isinstance(
            exc, (ValueError, RuntimeError, FileNotFoundError, AutomationTimeoutError)
        ):
            print("Macro tool: " + str(exc))
        else:
            print(
                "Macro tool failed ("
                + type(exc).__name__
                + "). Check the desktop, clipboard, and package installation."
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

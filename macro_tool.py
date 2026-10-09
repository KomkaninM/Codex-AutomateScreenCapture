"""Record, convert, export, and play private BMS JSON macros."""

from __future__ import annotations

import argparse
import copy
import json
import os
import secrets
import sys
import time
from pathlib import Path

from config import BASE_DIR, Settings, macro_name
from macro_player import MacroPlayer, enable_dpi_awareness
from automation_errors import AutomationTimeoutError


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
        "play", help="Play an existing macro after a five-second countdown."
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
    return command


def menu(command):
    print(
        "\nBMS MACRO TOOL\n1. Record a new macro\n2. Play a saved macro\n3. Convert old JSON\n4. Exit"
    )
    choice = input("Choose 1–4: ").strip()
    if choice == "4":
        return None
    if choice not in ("1", "2", "3"):
        raise ValueError("Choose 1, 2, 3, or 4.")
    name = input("Macro filename (example: DH09D.json): ").strip()
    if name and not name.endswith(".json"):
        name += ".json"
    args = [{"1": "record", "2": "play", "3": "convert"}[choice], macro_name(name)]
    if choice == "3":
        source = (
            input("Old JSON file path, or leave blank after copying the JSON: ")
            .strip()
            .strip('"')
        )
        if source:
            args += ["--source", source]
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
                    Path(name).stem, width, height, max_seconds=cfg.max_macro_seconds
                )
            else:
                player = MacroPlayer(
                    cfg.macros_dir,
                    max_seconds=cfg.max_macro_seconds,
                )
                player.load(name)
                print(
                    "Switch to the BMS. Playback starts in five seconds; move the mouse to a screen corner to abort."
                )
                for remaining in range(5, 0, -1):
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

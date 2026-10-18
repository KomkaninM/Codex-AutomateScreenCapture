import base64
import hashlib
import hmac
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import Future
from unittest.mock import Mock, patch
from PIL import Image

from config import Settings, RuntimeConfig
from macro_player import MacroPlayer, enable_dpi_awareness
from capture import CaptureEngine
from detector import SessionGuard, SessionState
from event_ledger import EventLedger
from scheduler import Scheduler, parse_interval
from line_api import LineAPI
from commands import HELP, parse_command
from workflow import Workflow
from server import create_app, Dispatcher


class CoreTests(unittest.TestCase):
    def test_webhook_database_connections_close_after_transactions(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = EventLedger(Path(directory) / ".runtime")
            for fail in (False, True):
                with self.subTest(failed_transaction=fail):
                    try:
                        with ledger._connect() as connection:
                            connection.execute("SELECT 1")
                            if fail:
                                raise ValueError("transaction failed")
                    except ValueError:
                        pass
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connection.execute("SELECT 1")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings = Settings(
            project_dir=self.root,
            channel_secret="secret",
            channel_access_token="token",
            group_id="group",
            public_tunnel_url="https://bms.ngrok.app",
            internal_api_token="local-secret",
            settle_delay=0,
        )
        self.runtime = RuntimeConfig(self.settings)

    def test_project_env_and_boolean_validation(self):
        (self.root / ".env").write_text("PORT=8000\nENABLE_AUTO_LOGOUT=false\n")
        cfg = Settings.load(self.root, environ={})
        self.assertEqual(cfg.port, 8000)
        self.assertFalse(cfg.auto_logout)
        (self.root / ".env").write_text("ENABLE_AUTO_LOGOUT=maybe\n")
        with self.assertRaises(ValueError):
            Settings.load(self.root, environ={})

    def test_bms_credentials_load_from_private_environment(self):
        (self.root / ".env").write_text(
            "BMS_USERNAME=operator\nBMS_PASSWORD=private-password\n"
        )

        cfg = Settings.load(self.root, environ={})

        self.assertEqual(getattr(cfg, "bms_username", None), "operator")
        self.assertEqual(getattr(cfg, "bms_password", None), "private-password")

    def test_operator_timing_and_image_defaults(self):
        cfg = Settings.load(self.root, environ={})

        self.assertEqual(cfg.max_macro_seconds, 10)
        self.assertFalse(hasattr(cfg, "login_wait_seconds"))
        self.assertFalse(hasattr(cfg, "detector_interval"))
        self.assertEqual(cfg.line_webp_quality, 80)

    def test_obsolete_login_wait_settings_are_ignored_and_recording_delay_validates(self):
        (self.root / ".env").write_text(
            "DETECTOR_INTERVAL_SEC=0.1\nRECORDED_STEP_DELAY_SECONDS=0.2\n"
        )
        cfg = Settings.load(self.root, environ={})
        self.assertFalse(hasattr(cfg, "detector_interval"))
        self.assertEqual(getattr(cfg, "recorded_step_delay_seconds", None), 0.2)

        (self.root / ".env").write_text("RECORDED_STEP_DELAY_SECONDS=-0.1\n")
        with self.assertRaises(ValueError):
            Settings.load(self.root, environ={})

    def test_runtime_persistence_and_path_confinement(self):
        self.runtime.set_login("DH07A.json")
        self.assertIn("LOGIN_MACRO_SCRIPT", (self.root / ".env").read_text())
        self.assertEqual(self.runtime.snapshot().default_login_macro, "DH07A.json")
        with self.assertRaises(ValueError):
            self.runtime.set_login("../escape.json")

    def test_ngrok_domain_is_the_single_public_address_setting(self):
        (self.root / ".env").write_text(
            "NGROK_DOMAIN=bms.ngrok.app\nPUBLIC_TUNNEL_URL=https://old.ngrok.app\n"
        )
        cfg = Settings.load(self.root, environ={})
        self.assertEqual(cfg.public_tunnel_url, "https://bms.ngrok.app")
        self.runtime.set_tunnel("https://new.ngrok.app")
        self.assertEqual(self.runtime.snapshot().ngrok_domain, "new.ngrok.app")
        self.assertEqual(
            Settings.load(self.root, environ={}).public_tunnel_url,
            "https://new.ngrok.app",
        )
        self.assertIn("NGROK_DOMAIN='new.ngrok.app'", (self.root / ".env").read_text())
        self.assertIn(
            "PUBLIC_TUNNEL_URL=https://old.ngrok.app", (self.root / ".env").read_text()
        )

    def test_image_links_default_to_no_expiry_and_zero_is_valid(self):
        self.assertEqual(Settings.load(self.root, environ={}).image_ttl_seconds, 0)
        (self.root / ".env").write_text("IMAGE_TTL_SECONDS=0\n")
        self.assertEqual(Settings.load(self.root, environ={}).image_ttl_seconds, 0)
        (self.root / ".env").write_text("IMAGE_TTL_SECONDS=-1\n")
        with self.assertRaises(ValueError):
            Settings.load(self.root, environ={})

    def test_macro_pastes_unicode_and_restores_clipboard(self):
        folder = self.root / "macros"
        folder.mkdir()
        (folder / "a.json").write_text(
            json.dumps(
                {
                    "steps": [
                        {"action": "text", "text": "อาคาร"},
                        {"action": "hotkey", "keys": ["ctrl", "a"]},
                        {"action": "click", "x": 12, "y": 20},
                    ]
                }
            )
        )
        gui = Mock()
        gui.size.return_value = (1920, 1080)
        clip = Mock()
        clip.paste.return_value = "original"
        MacroPlayer(folder, gui=gui, clipboard=clip, sleep=lambda _: None).play(
            "a.json"
        )
        self.assertEqual(clip.copy.call_args_list[0].args, ("อาคาร",))
        gui.hotkey.assert_any_call("ctrl", "v")
        self.assertEqual(clip.copy.call_args_list[-1].args, ("original",))

    def test_macro_validates_entire_script_before_input(self):
        folder = self.root / "macros"
        folder.mkdir()
        (folder / "bad.json").write_text(
            json.dumps(
                [
                    {"action": "click", "x": 1, "y": 1},
                    {"action": "shell", "command": "evil"},
                ]
            )
        )
        gui = Mock()
        gui.size.return_value = (1920, 1080)
        with self.assertRaises(ValueError):
            MacroPlayer(folder, gui=gui).play("bad.json")
        gui.click.assert_not_called()

    def test_macro_timeout_reports_step_and_macro_limit_without_text(self):
        folder = self.root / "macros"
        folder.mkdir()
        (folder / "a.json").write_text(
            json.dumps(
                {"steps": [{"action": "text", "text": "private-input", "delay": 0.5}]}
            )
        )
        clock = [0.0]
        gui = Mock()
        gui.hotkey.side_effect = lambda *keys: clock.__setitem__(0, 0.8)
        player = MacroPlayer(folder, gui=gui, clipboard=Mock(), max_seconds=1)
        player.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        with patch("macro_player.time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(TimeoutError) as caught:
                player.play("a.json")
        message = str(caught.exception)
        self.assertIn("step 1", message)
        self.assertIn("MAX_MACRO_SECONDS", message)
        self.assertIn("elapsed", message)
        self.assertNotIn("private-input", message)

    def test_macro_does_not_start_when_remaining_reply_time_cannot_fit_waits(self):
        folder = self.root / "macros"
        folder.mkdir()
        (folder / "a.json").write_text(
            json.dumps({"steps": [{"action": "click", "x": 10, "y": 20, "delay": 1}]})
        )
        gui = Mock()
        with patch("macro_player.time.monotonic", return_value=0):
            with self.assertRaises(TimeoutError) as caught:
                MacroPlayer(folder, gui=gui).play("a.json", deadline=0.5)
        self.assertIn("LINE reply deadline", str(caught.exception))
        self.assertIn("before input", str(caught.exception))
        gui.click.assert_not_called()

    def test_windows_dpi_awareness_does_not_require_specific_scaling(self):
        win_api = Mock()
        win_api.user32.GetDpiForSystem.return_value = 144
        with patch("sys.platform", "win32"), patch(
            "ctypes.windll", win_api, create=True
        ):
            enable_dpi_awareness()
        win_api.user32.SetProcessDpiAwarenessContext.assert_called_once()
        win_api.user32.GetDpiForSystem.assert_not_called()

    def test_capture_writes_real_dual_formats_and_webp_delivery(self):
        engine = CaptureEngine(
            self.root / "shots", grab=lambda: Image.new("RGB", (200, 100), "red")
        )
        shot = engine.capture("07C")
        for name, fmt in [
            ("archive", "JPEG"),
            ("webp", "WEBP"),
            ("original", "WEBP"),
            ("preview", "WEBP"),
        ]:
            path = getattr(shot, name)
            self.assertEqual(path.parents[1].name, "07C")
            self.assertEqual(
                path.parent.name, "reports" if name == "archive" else "line"
            )
            with Image.open(path) as img:
                self.assertEqual(img.format, fmt)
        self.assertNotEqual(shot.archive, engine.capture("07C").archive)
        with self.assertRaises(ValueError):
            engine.capture("../escape")

    def test_detector_unknown_blocks_and_target_replaces_default_login(self):
        player = Mock()
        detector = Mock(
            state=Mock(side_effect=[SessionState.LOGGED_OUT, SessionState.LOGGED_IN])
        )
        guard = SessionGuard(detector, player)
        used = guard.ensure("login.json", "DH07C.json")
        self.assertTrue(used)
        player.play.assert_called_once_with("DH07C.json")
        detector.state.assert_called_once_with()
        guard = SessionGuard(Mock(state=lambda: SessionState.UNKNOWN), player)
        with self.assertRaises(RuntimeError):
            guard.ensure("login.json")

    def test_login_guard_does_not_rescan_after_the_login_macro(self):
        detector = Mock(
            state=Mock(side_effect=[SessionState.LOGGED_OUT, SessionState.LOGGED_IN])
        )
        player = Mock()
        guard = SessionGuard(detector, player)

        self.assertTrue(guard.ensure("login.json"))

        player.play.assert_called_once_with("login.json")
        detector.state.assert_called_once_with()

    def test_macro_wait_image_appears_then_continues_remaining_steps(self):
        macros = self.root / "scripts" / "macros"
        assets = self.root / "assets"
        macros.mkdir(parents=True)
        assets.mkdir()
        Image.new("RGB", (4, 4), "red").save(assets / "ready.png")
        (macros / "a.json").write_text(
            json.dumps(
                {
                    "steps": [
                        {
                            "action": "wait_image",
                            "image": "assets/ready.png",
                            "condition": "appear",
                            "timeout": 5,
                            "poll_interval": 0.1,
                            "confidence": 0.9,
                            "consecutive_matches": 2,
                        },
                        {"action": "click", "x": 10, "y": 20},
                    ]
                }
            )
        )
        gui = Mock()
        with patch(
            "detector.image_visible", side_effect=[False, True, True], create=True
        ) as match:
            try:
                MacroPlayer(macros, gui=gui, sleep=lambda _: None).play("a.json")
            except ValueError as exc:
                self.fail(f"wait_image should be supported: {exc}")

        self.assertEqual(match.call_count, 3)
        gui.click.assert_called_once()

    def test_wait_image_uses_its_own_timeout_outside_macro_input_budget(self):
        macros = self.root / "scripts" / "macros"
        assets = self.root / "assets"
        macros.mkdir(parents=True)
        assets.mkdir()
        Image.new("RGB", (4, 4), "red").save(assets / "ready.png")
        (macros / "a.json").write_text(
            json.dumps(
                {
                    "steps": [
                        {
                            "action": "wait_image",
                            "image": "assets/ready.png",
                            "condition": "appear",
                            "timeout": 60,
                            "poll_interval": 0.5,
                            "consecutive_matches": 2,
                        },
                        {"action": "click", "x": 1, "y": 2},
                    ]
                }
            )
        )
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        gui = Mock()
        visible = [False, False, False, True, True]
        with patch("macro_player.time.monotonic", side_effect=lambda: clock[0]), patch(
            "detector.image_visible", side_effect=visible, create=True
        ):
            try:
                MacroPlayer(
                    macros, gui=gui, sleep=sleep, max_seconds=1
                ).play("a.json")
            except ValueError as exc:
                self.fail(f"wait_image should be supported: {exc}")

        self.assertGreater(clock[0], 1)
        gui.click.assert_called_once()

    def test_workflow_serializes_and_leaves_session_open_on_capture_failure(self):
        active = []
        overlaps = []

        class Guard:
            def ensure(inner, *args):
                active.append(1)
                overlaps.append(len(active))
                time.sleep(0.02)
                return False

        class Capture:
            def capture(inner, *args):
                active.pop()
                return "shot"

        player = Mock()
        wf = Workflow(self.runtime, player, Guard(), Capture())
        threads = [threading.Thread(target=wf.capture) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(max(overlaps), 1)
        self.runtime.set_autologout(True)
        wf.capture_engine = Mock(
            capture=Mock(side_effect=RuntimeError("capture failed"))
        )
        with self.assertRaises(RuntimeError):
            wf.capture()
        player.play.assert_not_called()

    def test_workflow_keeps_page_settle_delay_after_macro_completion(self):
        runtime = RuntimeConfig(Settings(project_dir=self.root, settle_delay=2))
        order = []
        player = Mock()
        player._wait.side_effect = lambda *args: order.append("settle")
        guard = Mock()
        guard.ensure.side_effect = lambda *args, **kwargs: order.append("macro") or True
        capture = Mock()
        capture.capture.side_effect = lambda *args: order.append("capture") or "shot"
        workflow = Workflow(runtime, player, guard, capture)

        self.assertEqual(workflow.capture(), "shot")

        self.assertEqual(order, ["macro", "settle", "capture"])

    def test_command_parser_schedule_and_target_note(self):
        cmd = parse_command(
            "capture 07C generator status --starttime 15:30:00", {"07C": {}}
        )
        self.assertEqual(
            (cmd.target, cmd.note, cmd.starttime),
            ("07C", "generator status", "15:30:00"),
        )
        self.assertEqual(parse_interval("30m"), 1800)
        for bad in ["0m", "nan", "-1h", "1x"]:
            with self.assertRaises(ValueError):
                parse_interval(bad)
        with self.assertRaises(ValueError):
            parse_command("capture --starttime 25:90", {})

    def test_force_capture_parser_keeps_multiword_note_and_rejects_options(self):
        command = parse_command("force-capture loading failure screen", {"07C": {}})

        self.assertEqual(command.name, "force-capture")
        self.assertEqual(command.note, "loading failure screen")
        self.assertIsNone(command.target)
        for text in (
            "force-capture --starttime 11:30",
            "force-capture -s 11:30",
            "force-capture --max 2",
            "force-capture -m 2",
        ):
            with self.subTest(text=text), self.assertRaisesRegex(
                ValueError, "does not accept options"
            ):
                parse_command(text, {})

    def test_start_capture_short_and_long_options_are_equivalent(self):
        commands = (
            "start-capture 30m generator room -s 11:30 -m 10",
            "start-capture 30m generator room -m 10 -s 11:30",
            "start-capture 30m generator room --starttime 11:30 --max 10",
            "start-capture 30m generator room --max 10 --starttime 11:30:00",
            "start-capture 30m generator room -s 11:30:00 --max 10",
            "start-capture 30m generator room --starttime 11:30 -m 10",
        )

        for text in commands:
            with self.subTest(text=text):
                command = parse_command(text, {})
                self.assertEqual(command.interval, 1800)
                self.assertEqual(command.note, "generator room")
                self.assertEqual(command.starttime, "11:30:00")
                self.assertEqual(getattr(command, "max_captures", None), 10)

    def test_start_capture_without_options_remains_unlimited(self):
        command = parse_command("start-capture 30m generator room", {})

        self.assertEqual(command.note, "generator room")
        self.assertIsNone(command.starttime)
        self.assertIsNone(getattr(command, "max_captures", None))

    def test_existing_single_dash_note_words_remain_compatible(self):
        command = parse_command("start-capture 30m generator -daily", {})

        self.assertEqual(command.note, "generator -daily")

    def test_start_capture_rejects_invalid_or_missing_option_values(self):
        invalid = {
            "start-capture 30m note -m": "maximum",
            "start-capture 30m note --max": "maximum",
            "start-capture 30m note -m 0": "positive integer",
            "start-capture 30m note -m -5": "positive integer",
            "start-capture 30m note -m abc": "positive integer",
            "start-capture 30m note -m 1.5": "positive integer",
            "start-capture 30m note -s": "start time",
            "start-capture 30m note --starttime": "start time",
            "start-capture 30m note -s 25:99": "HH:MM",
            "start-capture 30m note -s 11:30 -s 12:30": "once",
            "start-capture 30m note -m 2 --max 3": "once",
        }

        for text, message in invalid.items():
            with self.subTest(text=text), self.assertRaisesRegex(
                ValueError, "(?i)" + message
            ):
                parse_command(text, {})

    def test_line_help_prefers_short_schedule_options_and_documents_long_aliases(self):
        self.assertIn("force-capture [note]", HELP)
        self.assertIn("start-capture 30m [note] [-s HH:MM[:SS]] [-m count]", HELP)
        self.assertIn("--starttime", HELP)
        self.assertIn("--max", HELP)

    def test_scheduler_precheck_ten_seconds_early_and_stop_cancels_dispatch(self):
        now = datetime.fromisoformat("2026-10-09T15:00:00+07:00")
        calls = []
        pending = []
        futures = []

        def dispatch(fn):
            pending.append(fn)
            future = Future()
            futures.append(future)
            return future

        scheduler = Scheduler(dispatch, clock=lambda: now)
        scheduler.add(
            30,
            now + timedelta(seconds=30),
            lambda cancel: calls.append("capture"),
            lambda cancel: calls.append("precheck"),
        )
        scheduler.tick(now + timedelta(seconds=19))
        self.assertEqual(len(pending), 0)
        scheduler.tick(now + timedelta(seconds=20))
        futures.pop(0).set_result(pending.pop(0)())
        self.assertEqual(calls, ["precheck"])
        scheduler.tick(now + timedelta(seconds=30))
        self.assertEqual(len(pending), 1)
        scheduler.stop_all()
        pending.pop(0)()
        self.assertEqual(calls, ["precheck"])

    def test_scheduler_can_cancel_one_job_without_stopping_others(self):
        now = datetime.fromisoformat("2026-10-09T15:00:00+07:00")
        scheduler = Scheduler(Mock(), clock=lambda: now)
        first = scheduler.add(30, now + timedelta(seconds=30), Mock(), Mock())
        second = scheduler.add(60, now + timedelta(seconds=60), Mock(), Mock())

        self.assertTrue(scheduler.cancel(first))

        self.assertNotIn(first, scheduler.jobs)
        self.assertIn(second, scheduler.jobs)
        self.assertFalse(scheduler.cancel(first))

    def test_scheduler_paused_job_waits_until_resumed(self):
        now = datetime.fromisoformat("2026-10-09T15:00:00+07:00")
        pending = []

        def dispatch(fn):
            pending.append(fn)
            return Future()

        scheduler = Scheduler(dispatch, clock=lambda: now)
        job_id = scheduler.add(
            30,
            now + timedelta(seconds=30),
            Mock(),
            Mock(),
            paused=True,
        )

        scheduler.tick(now + timedelta(seconds=30))
        self.assertEqual(pending, [])
        self.assertTrue(scheduler.resume(job_id))
        scheduler.tick(now + timedelta(seconds=30))
        self.assertEqual(len(pending), 1)
        self.assertFalse(scheduler.resume("missing"))

    def test_line_reply_and_push_are_separate_and_images_use_jpeg(self):
        transport = Mock()
        transport.request.return_value = Mock(
            status_code=200, json=lambda: {}, headers={}
        )
        api = LineAPI("token", session=transport)
        api.reply("reply-token", [api.text("ok")])
        api.push("group", [api.text("scheduled")])
        first, second = transport.request.call_args_list
        self.assertTrue(first.args[1].endswith("/message/reply"))
        self.assertEqual(first.kwargs["json"]["replyToken"], "reply-token")
        self.assertNotIn("X-Line-Retry-Key", first.kwargs["headers"])
        self.assertTrue(second.args[1].endswith("/message/push"))
        self.assertIn("X-Line-Retry-Key", second.kwargs["headers"])

    def test_webhook_signature_group_filter_dedup_and_nonblocking(self):
        gate = threading.Event()
        worker = Dispatcher(workers=2, capacity=8)
        self.addCleanup(worker.close)
        bot = Mock()
        bot.handle.side_effect = lambda *args, **kwargs: gate.wait(2)
        app = create_app(self.runtime, bot=bot, dispatcher=worker)
        body = json.dumps(
            {
                "events": [
                    {
                        "type": "message",
                        "webhookEventId": "one",
                        "replyToken": "reply",
                        "source": {"type": "group", "groupId": "group"},
                        "message": {"type": "text", "text": "capture"},
                    }
                ]
            }
        ).encode()
        signature = base64.b64encode(
            hmac.new(b"secret", body, hashlib.sha256).digest()
        ).decode()
        with app.test_client() as client:
            self.assertEqual(client.post("/callback", data=body).status_code, 401)
            start = time.monotonic()
            self.assertEqual(
                client.post(
                    "/callback", data=body, headers={"X-Line-Signature": signature}
                ).status_code,
                200,
            )
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertEqual(
                client.post(
                    "/callback", data=body, headers={"X-Line-Signature": signature}
                ).status_code,
                200,
            )
        gate.set()
        worker.close()
        self.assertEqual(bot.handle.call_count, 1)

    def test_tunnel_update_authentication_and_image_traversal(self):
        worker = Dispatcher()
        self.addCleanup(worker.close)
        app = create_app(self.runtime, bot=Mock(), dispatcher=worker)
        with app.test_client() as client:
            self.assertEqual(
                client.post(
                    "/internal/update-tunnel", json={"url": "https://new.ngrok.app"}
                ).status_code,
                401,
            )
            self.assertEqual(
                client.post(
                    "/internal/update-tunnel",
                    json={"url": "http://localhost"},
                    headers={"Authorization": "Bearer local-secret"},
                ).status_code,
                400,
            )
            self.assertEqual(
                client.post(
                    "/internal/update-tunnel",
                    json={"url": "https://new.ngrok.app"},
                    headers={"Authorization": "Bearer local-secret"},
                ).status_code,
                200,
            )
            self.assertEqual(client.get("/images/../.env").status_code, 404)


if __name__ == "__main__":
    unittest.main()

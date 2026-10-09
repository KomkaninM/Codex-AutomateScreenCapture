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
from commands import parse_command
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

    def test_windows_scaling_must_be_one_hundred_percent(self):
        win_api = Mock()
        win_api.user32.GetDpiForSystem.return_value = 144
        with patch("sys.platform", "win32"), patch(
            "ctypes.windll", win_api, create=True
        ):
            with self.assertRaisesRegex(RuntimeError, "100%"):
                enable_dpi_awareness()
            win_api.user32.GetDpiForSystem.return_value = 96
            enable_dpi_awareness()

    def test_capture_writes_real_dual_formats_and_line_jpeg(self):
        engine = CaptureEngine(
            self.root / "shots", grab=lambda: Image.new("RGB", (200, 100), "red")
        )
        shot = engine.capture("07C")
        for name, fmt in [
            ("archive", "JPEG"),
            ("webp", "WEBP"),
            ("original", "JPEG"),
            ("preview", "JPEG"),
        ]:
            path = getattr(shot, name)
            self.assertEqual(path.parent.name, "07C")
            with Image.open(path) as img:
                self.assertEqual(img.format, fmt)
        self.assertNotEqual(shot.archive, engine.capture("07C").archive)
        with self.assertRaises(ValueError):
            engine.capture("../escape")

    def test_detector_unknown_blocks_and_target_replaces_default_login(self):
        player = Mock()
        states = iter([SessionState.LOGGED_OUT, SessionState.LOGGED_IN])
        guard = SessionGuard(Mock(state=lambda: next(states)), player, settle_delay=0)
        used = guard.ensure("login.json", "DH07C.json")
        self.assertTrue(used)
        player.play.assert_called_once_with("DH07C.json")
        guard = SessionGuard(
            Mock(state=lambda: SessionState.UNKNOWN), player, settle_delay=0
        )
        with self.assertRaises(RuntimeError):
            guard.ensure("login.json")

    def test_workflow_serializes_and_autologout_on_capture_failure(self):
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
        player.play.assert_called_with("logout.json")

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

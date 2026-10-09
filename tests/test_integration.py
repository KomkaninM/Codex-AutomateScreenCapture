import base64
import hashlib
import hmac
import io
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import requests
from PIL import Image

from capture import CaptureEngine
from commands import Bot
from config import RuntimeConfig, Settings
from detector import VisualDetector, SessionState, SessionGuard
from line_api import LineAPI, LineAPIError
from scheduler import Scheduler
from server import create_app, Dispatcher, startup_banner, single_instance
from workflow import Workflow


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = RuntimeConfig(
            Settings(
                project_dir=self.root,
                group_id="group",
                channel_secret="secret",
                channel_access_token="token",
                settle_delay=0,
                public_tunnel_url="https://bms.ngrok.app",
                internal_api_token="private",
            )
        )
        self.worker = Dispatcher(workers=2, capacity=8)
        self.addCleanup(self.worker.close)

    def make_bot(self, detector=None, api=None):
        api = api or Mock(text=LineAPI.text, image=LineAPI.image)
        player = Mock()
        detector = detector or Mock(state=lambda: SessionState.LOGGED_IN)
        guard = SessionGuard(detector, player, settle_delay=0)
        wf = Workflow(
            self.runtime,
            player,
            guard,
            CaptureEngine(
                self.root / "screenshots",
                grab=lambda: Image.new("RGB", (300, 200), "green"),
            ),
            {"07C": {"macro": "DH07C.json"}},
        )
        scheduler = Scheduler(self.worker.submit)
        self.addCleanup(scheduler.close)
        return Bot(self.runtime, wf, api, scheduler)

    def signed(self, client, body):
        raw = json.dumps(body).encode()
        signature = base64.b64encode(
            hmac.new(b"secret", raw, hashlib.sha256).digest()
        ).decode()
        return client.post(
            "/callback", data=raw, headers={"X-Line-Signature": signature}
        )

    def event(self, text="capture", id="event"):
        return {
            "type": "message",
            "webhookEventId": id,
            "replyToken": "reply",
            "message": {"type": "text", "text": text},
            "source": {"type": "group", "groupId": "group", "userId": "user"},
        }

    def test_capture_reply_urls_serve_real_jpeg_and_archives_stay_private(self):
        bot = self.make_bot()
        bot.handle(self.event("capture 07C status"), time.monotonic())
        bot.line.push.assert_not_called()
        messages = bot.line.reply.call_args.args[1]
        self.assertEqual(messages[0]["text"], "status")
        image = messages[1]
        self.assertTrue(image["originalContentUrl"].endswith("_line.jpg"))
        app = create_app(self.runtime, bot=bot, dispatcher=self.worker)
        with app.test_client() as client:
            route = image["originalContentUrl"].removeprefix("https://bms.ngrok.app")
            result = client.get(route)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.mimetype, "image/jpeg")
            with Image.open(io.BytesIO(result.data)) as saved:
                self.assertEqual(saved.size, (300, 200))
            archive_name = route.rsplit("/", 1)[1].replace("_line.jpg", ".jpg")
            self.assertEqual(client.get("/images/07C/" + archive_name).status_code, 404)
            result.close()

    def test_interactive_failure_and_expiry_never_push(self):
        bot = self.make_bot()
        bot.workflow.capture_engine = Mock(
            capture=Mock(side_effect=RuntimeError("bad screen"))
        )
        with self.assertLogs("commands", level="ERROR"):
            bot.handle(self.event(), time.monotonic())
        bot.line.reply.assert_called_once()
        bot.line.push.assert_not_called()
        bot.line.reply.reset_mock()
        bot.handle(self.event(), time.monotonic() - 46)
        bot.line.reply.assert_not_called()
        bot.line.push.assert_not_called()

    def test_interactive_login_and_macro_use_replies(self):
        bot = self.make_bot()
        for command in (
            "login",
            "macro custom.json",
            "check-id",
            "check-quota",
        ):
            with self.subTest(command=command):
                bot.line.reply.reset_mock()
                bot.handle(self.event(command), time.monotonic())
                bot.line.reply.assert_called_once()
        bot.line.push.assert_not_called()

    def test_close_menu_command_is_removed_and_does_not_run_a_macro(self):
        bot = self.make_bot()
        bot.handle(self.event("close-menu"), time.monotonic())
        bot.workflow.player.play.assert_not_called()
        bot.line.reply.assert_not_called()
        bot.line.push.assert_not_called()
        bot.handle(self.event("help"), time.monotonic())
        self.assertNotIn("close-menu", bot.line.reply.call_args.args[1][0]["text"])

    def test_archival_files_remain_and_public_image_expiry_can_be_disabled(self):
        bot = self.make_bot()
        bot.handle(self.event("capture 07C"), time.monotonic())
        route = bot.line.reply.call_args.args[1][0]["originalContentUrl"].removeprefix(
            "https://bms.ngrok.app"
        )
        path = self.root / "screenshots" / route.removeprefix("/images/")
        old = time.time() - 365 * 86400
        os.utime(path, (old, old))
        for ttl, status in ((0, 200), (604800, 404)):
            with self.subTest(ttl=ttl):
                runtime = RuntimeConfig(
                    replace(self.runtime.snapshot(), image_ttl_seconds=ttl)
                )
                app = create_app(runtime, bot=bot, dispatcher=self.worker)
                with app.test_client() as client:
                    with client.get(route) as response:
                        self.assertEqual(response.status_code, status)
                self.assertTrue(path.is_file())
                self.assertTrue(
                    path.with_name(path.name.replace("_line.jpg", ".jpg")).is_file()
                )

    def test_scheduled_capture_only_pushes_and_cancelled_job_does_nothing(self):
        bot = self.make_bot()
        cancel = threading.Event()
        bot._scheduled("07C", "scheduled", cancel)
        bot.line.push.assert_called_once()
        bot.line.reply.assert_not_called()
        cancel.set()
        with self.assertRaises(RuntimeError):
            bot._scheduled(None, "", cancel)
        self.assertEqual(bot.line.push.call_count, 1)

    def test_one_time_schedule_acknowledges_and_sends_only_when_due(self):
        bot = self.make_bot()
        bot.handle(self.event("capture note --starttime 15:30:00"), time.monotonic())
        bot.line.reply.assert_called_once()
        bot.line.push.assert_not_called()
        job = next(iter(bot.scheduler.jobs.values()))
        self.assertIsNone(job.interval)
        self.assertEqual(job.due.hour, 15)
        self.assertEqual(job.due.minute, 30)

    def test_malformed_signed_payloads_and_unauthorized_group_are_rejected(self):
        bot = Mock()
        app = create_app(self.runtime, bot=bot, dispatcher=self.worker)
        with app.test_client() as client:
            for payload in (
                [],
                {},
                {"events": {}},
                {"events": [self.event() | {"source": None}]},
            ):
                self.assertEqual(self.signed(client, payload).status_code, 400)
            other = self.event()
            other["source"]["groupId"] = "other"
            self.assertEqual(self.signed(client, {"events": [other]}).status_code, 200)
            self.assertEqual(
                client.post(
                    "/callback", data=b"x", headers={"X-Line-Signature": "é"}
                ).status_code,
                401,
            )
        self.assertEqual(bot.handle.call_count, 0)

    def test_private_commands_are_authorized_by_user_id_when_group_is_blank(self):
        self.runtime = RuntimeConfig(
            replace(self.runtime.snapshot(), group_id="", user_id="user")
        )
        bot = self.make_bot()
        replied = threading.Event()
        bot.line.reply.side_effect = lambda *args: replied.set()
        app = create_app(self.runtime, bot=bot, dispatcher=self.worker)
        with app.test_client() as client:
            for index, source in enumerate(
                (
                    {"type": "user", "userId": "other"},
                    {"type": "group", "groupId": "group", "userId": "user"},
                    {"type": "user", "userId": "user"},
                )
            ):
                event = self.event(id=str(index)) | {"source": source}
                self.assertEqual(
                    self.signed(client, {"events": [event]}).status_code, 200
                )
        self.assertTrue(replied.wait(2), "Authorized private reply must finish.")
        self.worker.close()
        bot.line.reply.assert_called_once()
        self.assertEqual(bot.line.reply.call_args.args[1][0]["type"], "image")
        bot.line.push.assert_not_called()

    def test_group_takes_priority_over_user_for_ingress_and_scheduled_delivery(self):
        self.runtime = RuntimeConfig(replace(self.runtime.snapshot(), user_id="user"))
        bot = self.make_bot()
        replied = threading.Event()
        bot.line.reply.side_effect = lambda *args: replied.set()
        app = create_app(self.runtime, bot=bot, dispatcher=self.worker)
        with app.test_client() as client:
            private = self.event(id="private") | {
                "source": {"type": "user", "userId": "user"}
            }
            self.signed(client, {"events": [private, self.event(id="group")]})
        self.assertTrue(replied.wait(2), "Authorized group reply must finish.")
        self.worker.close()
        bot.line.reply.assert_called_once()
        bot._scheduled(None, "", threading.Event())
        self.assertEqual(bot.line.push.call_args.args[0], "group")

    def test_private_schedule_pushes_to_user_and_never_uses_reply(self):
        self.runtime = RuntimeConfig(
            replace(self.runtime.snapshot(), group_id="", user_id="user")
        )
        bot = self.make_bot()
        event = self.event("start-capture 30m") | {
            "source": {"type": "user", "userId": "user"}
        }
        bot.handle(event, time.monotonic())
        self.assertEqual(len(bot.scheduler.jobs), 1)
        bot.line.reply.assert_called_once()
        bot.line.push.assert_not_called()
        bot._scheduled(None, "private", threading.Event())
        self.assertEqual(bot.line.push.call_args.args[0], "user")

    def test_check_id_discovery_works_in_private_and_group_chats_without_configured_ids(
        self,
    ):
        replied = threading.Event()
        self.runtime = RuntimeConfig(
            replace(self.runtime.snapshot(), group_id="", user_id="")
        )
        bot = self.make_bot()
        bot.line.reply.side_effect = lambda *args: (
            replied.set() if bot.line.reply.call_count == 2 else None
        )
        app = create_app(self.runtime, bot=bot, dispatcher=self.worker)
        with app.test_client() as client:
            private = self.event("check-id", "private") | {
                "source": {"type": "user", "userId": "user"}
            }
            self.signed(
                client,
                {
                    "events": [
                        private,
                        self.event("check-id", "group"),
                        self.event("capture", "blocked"),
                    ]
                },
            )
        self.assertTrue(
            replied.wait(2),
            "Both discovery replies should complete before shutting down the queue.",
        )
        self.worker.close()
        self.assertEqual(bot.line.reply.call_count, 2)
        bot.workflow.player.play.assert_not_called()
        bot.line.push.assert_not_called()

    def test_invalid_private_user_id_is_rejected_as_malformed_payload(self):
        app = create_app(self.runtime, bot=Mock(), dispatcher=self.worker)
        event = self.event() | {"source": {"type": "user", "userId": ["user"]}}
        with app.test_client() as client:
            self.assertEqual(self.signed(client, {"events": [event]}).status_code, 400)

    def test_private_dashboard_does_not_request_group_member_metrics(self):
        cfg = replace(self.runtime.snapshot(), group_id="", user_id="user")
        line = Mock(quota=lambda: {"limit": 500, "consumed": 10, "remaining": 490})
        with redirect_stdout(io.StringIO()) as output:
            startup_banner(cfg, line)
        line.group_reach.assert_not_called()
        self.assertIn("Private", output.getvalue())
        self.assertIn("user", output.getvalue())

    def test_webhook_redelivery_is_suppressed_after_app_restart(self):
        bot = Mock()
        first = create_app(self.runtime, bot=bot, dispatcher=self.worker)
        with first.test_client() as client:
            self.assertEqual(
                self.signed(client, {"events": [self.event()]}).status_code, 200
            )
        self.worker.close()
        second_worker = Dispatcher()
        self.addCleanup(second_worker.close)
        second = create_app(self.runtime, bot=bot, dispatcher=second_worker)
        with second.test_client() as client:
            self.assertEqual(
                self.signed(client, {"events": [self.event()]}).status_code, 200
            )
        second_worker.close()
        self.assertEqual(bot.handle.call_count, 1)

    def test_queue_overflow_allows_redelivery(self):
        worker = Mock(submit=Mock(side_effect=RuntimeError("full")))
        bot = Mock()
        app = create_app(self.runtime, bot=bot, dispatcher=worker)
        with app.test_client() as client:
            self.assertEqual(
                self.signed(client, {"events": [self.event()]}).status_code, 503
            )
            worker.submit.side_effect = lambda fn: (fn(), Future())[1]
            self.assertEqual(
                self.signed(client, {"events": [self.event()]}).status_code, 200
            )
        bot.handle.assert_called_once()

    def test_visual_detection_uses_only_the_login_page_anchor(self):
        assets = self.root / "assets"
        assets.mkdir()
        rng = np.random.default_rng(7)
        out_image = Image.fromarray(rng.integers(0, 256, (8, 12, 3), dtype=np.uint8))
        out_image.save(assets / "login_anchor.png")
        screen = Image.new("RGB", (100, 80), "black")
        detector = VisualDetector(
            assets / "login_anchor.png", confidence=0.99, grab=lambda: screen
        )
        self.assertEqual(detector.state(), SessionState.LOGGED_IN)
        screen.paste(out_image, (5, 5))
        self.assertEqual(detector.state(), SessionState.LOGGED_OUT)
        screen = Image.new("RGB", (100, 80), "black")
        self.assertEqual(detector.state(), SessionState.LOGGED_IN)
        screen = Image.new("RGB", (4, 4), "black")
        self.assertEqual(detector.state(), SessionState.UNKNOWN)

    def test_missing_login_anchor_blocks_detection_instead_of_assuming_logged_in(self):
        detector = VisualDetector(
            self.root / "assets" / "login_anchor.png",
            grab=lambda: Image.new("RGB", (100, 80)),
        )
        with self.assertRaisesRegex(RuntimeError, "login_anchor.png"):
            detector.state()

    def test_capture_recovers_and_confirms_logout_with_only_a_login_anchor(self):
        assets = self.root / "assets"
        assets.mkdir()
        anchor = Image.fromarray(
            np.random.default_rng(13).integers(0, 256, (8, 12, 3), dtype=np.uint8)
        )
        anchor.save(assets / "login_anchor.png")
        login_screen = Image.new("RGB", (100, 80), "black")
        login_screen.paste(anchor, (5, 5))
        screen = login_screen.copy()
        self.runtime = RuntimeConfig(replace(self.runtime.snapshot(), auto_logout=True))
        detector = VisualDetector(
            assets / "login_anchor.png", confidence=0.99, grab=lambda: screen
        )
        bot = self.make_bot(detector=detector)

        def play(name, **options):
            nonlocal screen
            screen = (
                login_screen.copy()
                if name == "logout.json"
                else Image.new("RGB", (100, 80), "black")
            )

        bot.workflow.player.play.side_effect = play
        for command, login_macro in (
            ("capture", "login_bms.json"),
            ("capture 07C", "DH07C.json"),
        ):
            with self.subTest(command=command):
                bot.workflow.player.play.reset_mock()
                bot.line.reply.reset_mock()
                bot.handle(self.event(command), time.monotonic())
                self.assertEqual(
                    [call.args[0] for call in bot.workflow.player.play.call_args_list],
                    [login_macro, "logout.json"],
                )
                self.assertEqual(bot.line.reply.call_args.args[1][0]["type"], "image")
                self.assertEqual(detector.state(), SessionState.LOGGED_OUT)
        bot.line.push.assert_not_called()

    def test_api_quota_unlimited_and_reply_timeout_single_attempt(self):
        transport = Mock()
        transport.request.side_effect = [
            Mock(status_code=200, json=lambda: {"type": "none"}),
            Mock(status_code=200, json=lambda: {"totalUsage": 17}),
        ]
        api = LineAPI("token", session=transport, sleep=lambda _: None)
        self.assertEqual(
            api.quota(), {"limit": None, "consumed": 17, "remaining": None}
        )
        transport.request.reset_mock()
        transport.request.side_effect = requests.Timeout()
        with self.assertRaises(LineAPIError):
            api.reply("reply", [api.text("ok")])
        self.assertEqual(transport.request.call_count, 1)

    def test_push_retries_keep_same_idempotency_key(self):
        transport = Mock()
        transport.request.side_effect = [
            requests.Timeout(),
            Mock(status_code=200, json=lambda: {}),
        ]
        api = LineAPI("token", session=transport, sleep=lambda _: None)
        api.push("group", [api.text("scheduled")])
        keys = [
            call.kwargs["headers"]["X-Line-Retry-Key"]
            for call in transport.request.call_args_list
        ]
        self.assertEqual(keys[0], keys[1])

    def test_banner_reports_values_without_credentials(self):
        api = Mock(
            quota=lambda: {"limit": 100, "consumed": 20, "remaining": 80},
            group_reach=lambda _: 7,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            startup_banner(self.runtime.snapshot(), api)
        text = output.getvalue()
        for value in (
            "5000",
            "100 messages",
            "20 messages",
            "80 messages",
            "7 members",
            "unblocked reach unavailable",
        ):
            self.assertIn(value, text)
        self.assertNotIn("secret", text)

    def test_single_instance_rejects_second_process_lock(self):
        with single_instance(self.root):
            with self.assertRaises(RuntimeError):
                with single_instance(self.root):
                    pass

    def test_unknown_session_does_not_trigger_logout_input(self):
        self.runtime.set_autologout(True)
        bot = self.make_bot(detector=Mock(state=lambda: SessionState.UNKNOWN))
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            bot.workflow.capture()
        bot.workflow.player.play.assert_not_called()

    def test_capture_waits_for_its_precheck_to_finish(self):
        now = datetime.fromisoformat("2026-10-09T15:00:00+07:00")
        pending = []

        def dispatch(fn):
            future = Future()
            pending.append((fn, future))
            return future

        scheduler = Scheduler(dispatch, clock=lambda: now)
        scheduler.add(
            30, now + timedelta(seconds=30), lambda cancel: None, lambda cancel: None
        )
        scheduler.tick(now + timedelta(seconds=20))
        self.assertEqual(len(pending), 1)
        scheduler.tick(now + timedelta(seconds=30))
        self.assertEqual(
            len(pending), 1, "Capture must not race an unfinished precheck"
        )
        fn, future = pending[0]
        future.set_result(fn())
        scheduler.tick(now + timedelta(seconds=31))
        self.assertEqual(len(pending), 2)

    def test_stop_capture_cancels_even_when_background_queue_is_occupied(self):
        pending = []
        dispatcher = Mock(submit=lambda fn: pending.append(fn))
        bot = self.make_bot()
        bot.scheduler.add(
            30,
            datetime.now().astimezone() + timedelta(seconds=30),
            lambda c: None,
            lambda c: None,
        )
        app = create_app(self.runtime, bot=bot, dispatcher=dispatcher)
        with app.test_client() as client:
            self.assertEqual(
                self.signed(
                    client, {"events": [self.event("stop-capture")]}
                ).status_code,
                200,
            )
        self.assertEqual(
            len(bot.scheduler.jobs),
            0,
            "Authorized cancellation must not wait for desktop workers",
        )

    def test_macro_command_obeys_unknown_session_guard(self):
        bot = self.make_bot(detector=Mock(state=lambda: SessionState.UNKNOWN))
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            bot.workflow.macro("custom.json")
        bot.workflow.player.play.assert_not_called()

    def test_navigation_session_timeout_relogs_once_with_target_macro(self):
        states = iter(
            [
                SessionState.LOGGED_IN,
                SessionState.LOGGED_OUT,
                SessionState.LOGGED_OUT,
                SessionState.LOGGED_IN,
                SessionState.LOGGED_IN,
            ]
        )
        bot = self.make_bot(detector=Mock(state=lambda: next(states)))
        shot = bot.workflow.capture("07C")
        self.assertTrue(shot.archive.is_file())
        self.assertEqual(bot.workflow.player.play.call_count, 2)
        for call in bot.workflow.player.play.call_args_list:
            self.assertEqual(call.args, ("DH07C.json",))

    def test_stop_invalidates_prior_queued_schedule_registration(self):
        pending = []
        dispatcher = Mock(submit=lambda fn: pending.append(fn))
        bot = self.make_bot()
        app = create_app(self.runtime, bot=bot, dispatcher=dispatcher)
        with app.test_client() as client:
            events = [
                self.event("start-capture 30m", "start"),
                self.event("stop-capture", "stop"),
            ]
            self.assertEqual(self.signed(client, {"events": events}).status_code, 200)
            for fn in pending:
                fn()
            self.assertEqual(len(bot.scheduler.jobs), 0)
            pending.clear()
            self.assertEqual(
                self.signed(
                    client, {"events": [self.event("start-capture 30m", "new-start")]}
                ).status_code,
                200,
            )
            pending.pop(0)()
            self.assertEqual(len(bot.scheduler.jobs), 1)

    def test_full_queue_stop_claim_is_not_replayed_against_new_schedules(self):
        dispatcher = Mock(submit=Mock(side_effect=RuntimeError("full")))
        bot = self.make_bot()
        app = create_app(self.runtime, bot=bot, dispatcher=dispatcher)
        with app.test_client() as client:
            body = {"events": [self.event("stop-capture", "stop")]}
            with self.assertLogs("server", level="WARNING"):
                self.assertEqual(self.signed(client, body).status_code, 200)
            bot.scheduler.add(
                30,
                datetime.now().astimezone() + timedelta(seconds=30),
                lambda c: None,
                lambda c: None,
            )
            self.assertEqual(self.signed(client, body).status_code, 200)
            self.assertEqual(len(bot.scheduler.jobs), 1)


if __name__ == "__main__":
    unittest.main()

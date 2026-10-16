import unittest
from contextlib import nullcontext
from os import environ
from unittest.mock import Mock, patch

from config import Settings, RuntimeConfig
from line_api import LineAPI, LineAPIError
from server import main, send_online_notification


class StartupTests(unittest.TestCase):
    def test_online_message_uses_group_priority_or_private_user(self):
        for group, user, recipient in (
            ("group", "user", "group"),
            ("", "user", "user"),
        ):
            with self.subTest(recipient=recipient):
                line = Mock(text=LineAPI.text)
                send_online_notification(Settings(group_id=group, user_id=user), line)
                line.push.assert_called_once()
                destination, messages = line.push.call_args.args
                self.assertEqual(destination, recipient)
                self.assertEqual(messages[0]["type"], "text")
                self.assertIn("online", messages[0]["text"])
                self.assertIn("Default macro: login_bms.json", messages[0]["text"])
                line.reply.assert_not_called()

    def test_no_destination_does_not_send_a_notification(self):
        line = Mock(text=LineAPI.text)
        send_online_notification(Settings(), line)
        line.push.assert_not_called()

    def test_notification_failure_is_logged_without_stopping_the_bot(self):
        line = Mock(
            text=LineAPI.text, push=Mock(side_effect=LineAPIError("unavailable"))
        )
        with self.assertLogs("server", level="WARNING") as logs:
            send_online_notification(Settings(user_id="user"), line)
        self.assertIn("online notification", "\n".join(logs.output))
        line.push.assert_called_once()

    def test_startup_publishes_online_after_notification_attempt(self):
        cfg = Settings(
            group_id="group", channel_secret="secret", channel_access_token="token"
        )
        events = []
        line = Mock(
            text=LineAPI.text,
            push=Mock(side_effect=lambda *args: events.append("notification")),
        )
        bot = Mock(runtime=RuntimeConfig(cfg), line=line)
        queued = []
        worker = Mock(submit=lambda fn: queued.append(fn))
        http = Mock(run=Mock(side_effect=KeyboardInterrupt))
        control = Mock()
        control.stop_requested.return_value = False
        control.watch.return_value = nullcontext()
        control.publish.side_effect = lambda stage, **details: events.append(stage)

        def bind(*args, **kwargs):
            self.assertEqual(queued, [])
            return http

        with patch("server.Settings.load", return_value=cfg), patch(
            "server.single_instance", return_value=nullcontext()
        ), patch("server.Dispatcher", return_value=worker), patch(
            "server.build_bot", return_value=bot
        ), patch(
            "server.create_app"
        ), patch(
            "server.startup_banner"
        ), patch(
            "server.signal.signal"
        ), patch(
            "server.logging.basicConfig"
        ), patch(
            "server.log.info"
        ), patch(
            "waitress.create_server", side_effect=bind
        ), patch(
            "runtime_control.environment_control", return_value=control
        ):
            main()
        http.run.assert_called_once()
        self.assertEqual(len(queued), 1)
        bot.line.push.assert_not_called()
        control.publish.assert_not_called()
        queued[0]()
        bot.line.push.assert_called_once()
        control.publish.assert_called_once_with(
            "online",
            port=cfg.port,
            webhook=cfg.public_tunnel_url + "/callback",
        )
        self.assertEqual(events, ["notification", "online"])
        http.close.assert_called_once()

    def test_launcher_managed_startup_defers_notification_to_launcher(self):
        cfg = Settings(
            group_id="group", channel_secret="secret", channel_access_token="token"
        )
        bot = Mock(runtime=RuntimeConfig(cfg), line=Mock(text=LineAPI.text))
        queued = []
        worker = Mock(submit=lambda fn: queued.append(fn))
        http = Mock(run=Mock(side_effect=KeyboardInterrupt))
        control = Mock()
        control.stop_requested.return_value = False
        control.watch.return_value = nullcontext()

        with patch.dict(environ, {"BMS_LAUNCHER_MANAGED_STARTUP": "1"}), patch(
            "server.Settings.load", return_value=cfg
        ), patch("server.single_instance", return_value=nullcontext()), patch(
            "server.Dispatcher", return_value=worker
        ), patch("server.build_bot", return_value=bot), patch(
            "server.create_app"
        ), patch("server.startup_banner"), patch("server.signal.signal"), patch(
            "server.logging.basicConfig"
        ), patch("server.log.info"), patch(
            "waitress.create_server", return_value=http
        ), patch(
            "runtime_control.environment_control", return_value=control
        ):
            main()

        self.assertEqual(queued, [])
        bot.line.push.assert_not_called()
        control.publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()

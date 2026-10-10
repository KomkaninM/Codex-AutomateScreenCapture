import unittest
from contextlib import nullcontext
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

    def test_startup_dispatches_notification_after_binding_without_waiting_for_line(
        self,
    ):
        cfg = Settings(
            group_id="group", channel_secret="secret", channel_access_token="token"
        )
        bot = Mock(runtime=RuntimeConfig(cfg), line=Mock(text=LineAPI.text))
        queued = []
        worker = Mock(submit=lambda fn: queued.append(fn))
        http = Mock(run=Mock(side_effect=KeyboardInterrupt))

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
        ):
            main()
        http.run.assert_called_once()
        self.assertEqual(len(queued), 1)
        bot.line.push.assert_not_called()
        queued[0]()
        bot.line.push.assert_called_once()
        http.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()

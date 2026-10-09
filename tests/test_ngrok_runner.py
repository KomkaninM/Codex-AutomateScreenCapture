import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from config import Settings
from ngrok_runner import NgrokDiagnostics, NgrokStartupError, prepare_auth_config
from launcher import connect_tunnel, start_tunnel


class NgrokRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binary = self.root / "ngrok.exe"
        self.binary.touch()
        self.cfg = Settings(
            project_dir=self.root,
            ngrok_exe_path=str(self.binary),
            ngrok_authtoken="sample-private-token",
            ngrok_domain="bms.ngrok.app",
        )

    def test_auth_config_is_created_locally_without_modifying_global_ngrok_settings(
        self,
    ):
        path = prepare_auth_config(self.cfg, self.root)
        config = json.loads(path.read_text())
        self.assertEqual(path, self.root / ".runtime" / "ngrok.yml")
        self.assertEqual(config["version"], "2")
        self.assertEqual(config["authtoken"], "sample-private-token")
        self.assertEqual(config["web_addr"], "127.0.0.1:4040")

    def test_no_token_preserves_use_of_existing_ngrok_authentication(self):
        self.assertIsNone(
            prepare_auth_config(Settings(project_dir=self.root), self.root)
        )
        self.assertFalse((self.root / ".runtime" / "ngrok.yml").exists())

    def test_ngrok_output_survives_process_exit_and_redacts_tokens(self):
        script = "print('ERROR: Your authtoken: sample-private-token ERR_NGROK_107')"
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            diagnostic = NgrokDiagnostics(self.root, secrets=("sample-private-token",))
            diagnostic.start(process.stdout)
            process.wait(timeout=5)
            diagnostic.join()
        self.assertEqual(diagnostic.error_code, "ERR_NGROK_107")
        self.assertNotIn("sample-private-token", diagnostic.log_path.read_text())
        self.assertNotIn("sample-private-token", output.getvalue())
        self.assertIn("ngrok", output.getvalue())

    def test_startup_failure_exposes_auth_error_and_keeps_diagnostic_log(self):
        process = Mock(
            poll=lambda: 1,
            stdout=io.StringIO("ERROR: authentication failed ERR_NGROK_4018\n"),
        )
        with patch("launcher.read_tunnels", return_value=None), patch(
            "launcher.subprocess.Popen", return_value=process
        ), redirect_stdout(io.StringIO()):
            with self.assertRaises(NgrokStartupError) as raised:
                start_tunnel(self.cfg, self.root)
        self.assertTrue(raised.exception.auth_problem)
        self.assertIn("ERR_NGROK_4018", str(raised.exception))
        self.assertIn(
            "ERR_NGROK_4018", (self.root / ".runtime" / "ngrok.log").read_text()
        )

    def test_ngrok_launch_uses_private_config_and_captures_console_output(self):
        process = Mock(poll=lambda: None, stdout=io.StringIO(""))
        data = {
            "tunnels": [
                {
                    "public_url": "https://bms.ngrok.app",
                    "config": {"addr": "http://localhost:5000"},
                }
            ]
        }
        with patch("launcher.read_tunnels", side_effect=[None, data]), patch(
            "launcher.subprocess.Popen", return_value=process
        ) as popen, redirect_stdout(io.StringIO()):
            url, owned = start_tunnel(self.cfg, self.root)
        args = popen.call_args.args[0]
        self.assertNotIn("sample-private-token", " ".join(args))
        self.assertIn("--config=" + str(self.root / ".runtime" / "ngrok.yml"), args)
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.STDOUT)
        self.assertEqual(url, "https://bms.ngrok.app")
        self.assertIs(owned, process)

    def test_authentication_wizard_reloads_env_and_retries_once(self):
        (self.root / ".env").write_text("NGROK_DOMAIN=bms.ngrok.app\n")

        def save_token(*args):
            (self.root / ".env").write_text(
                "NGROK_DOMAIN=bms.ngrok.app\nNGROK_AUTHTOKEN=corrected-token\n"
            )
            return ""

        process = Mock()
        with patch("launcher.os.environ", {}), patch(
            "launcher.start_tunnel",
            side_effect=[
                NgrokStartupError("ERR_NGROK_4018", 1),
                ("https://bms.ngrok.app", process),
            ],
        ) as start, patch("launcher.subprocess.Popen"), patch(
            "launcher.webbrowser.open"
        ) as browser, patch(
            "builtins.input", side_effect=save_token
        ), redirect_stdout(
            io.StringIO()
        ):
            cfg, url, owned = connect_tunnel(
                Settings.load(self.root, environ={}), self.root
            )
        self.assertEqual(start.call_count, 2)
        self.assertEqual(start.call_args.args[0].ngrok_authtoken, "corrected-token")
        self.assertEqual(cfg.ngrok_authtoken, "corrected-token")
        self.assertIs(owned, process)
        browser.assert_called_once()

    def test_non_authentication_error_does_not_open_browser_or_retry(self):
        with patch(
            "launcher.start_tunnel", side_effect=NgrokStartupError("ERR_NGROK_108", 1)
        ) as start, patch("launcher.webbrowser.open") as browser:
            with self.assertRaises(NgrokStartupError):
                connect_tunnel(self.cfg, self.root)
        self.assertEqual(start.call_count, 1)
        browser.assert_not_called()

    def test_authentication_retry_stops_after_second_failure(self):
        (self.root / ".env").write_text("NGROK_AUTHTOKEN=rejected-token\n")
        with patch("launcher.os.environ", {}), patch(
            "launcher.start_tunnel", side_effect=NgrokStartupError("ERR_NGROK_107", 1)
        ) as start, patch("launcher.webbrowser.open") as browser, patch(
            "launcher.subprocess.Popen"
        ), patch(
            "builtins.input"
        ), redirect_stdout(
            io.StringIO()
        ):
            with self.assertRaises(NgrokStartupError):
                connect_tunnel(self.cfg, self.root)
        self.assertEqual(start.call_count, 2)
        browser.assert_called_once()


if __name__ == "__main__":
    unittest.main()

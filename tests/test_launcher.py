import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from launcher import (
    ensure_env_file,
    install_dependencies,
    find_tunnel,
    stop_owned_process,
    validate_interpreter,
    run_application,
    start_tunnel,
)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        quiet = patch("launcher.say")
        quiet.start()
        self.addCleanup(quiet.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".env.example").write_text("GROUP_ID=\n", encoding="utf-8")
        (self.root / "requirements.txt").write_text("flask==3.1.2\n", encoding="utf-8")
        self.python = self.root / ".venv-launcher" / "Scripts" / "python.exe"
        self.python.parent.mkdir(parents=True)
        self.python.touch()

    def test_first_setup_creates_env_without_replacing_operator_settings(self):
        self.assertTrue(ensure_env_file(self.root))
        self.assertEqual((self.root / ".env").read_text(), "GROUP_ID=\n")
        (self.root / ".env").write_text("GROUP_ID=operator-group\n")
        self.assertFalse(ensure_env_file(self.root))
        self.assertEqual((self.root / ".env").read_text(), "GROUP_ID=operator-group\n")

    def test_install_is_cached_but_requirement_changes_trigger_refresh(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0))
        install_dependencies(self.root, self.python, runner=runner)
        self.assertTrue(any("install" in c.args[0] for c in runner.call_args_list))
        runner.reset_mock()
        install_dependencies(self.root, self.python, runner=runner)
        self.assertFalse(any("install" in c.args[0] for c in runner.call_args_list))
        (self.root / "requirements.txt").write_text("flask==3.1.3\n")
        runner.reset_mock()
        install_dependencies(self.root, self.python, runner=runner)
        self.assertTrue(any("install" in c.args[0] for c in runner.call_args_list))

    def test_failed_install_is_not_cached_as_successful(self):
        failed = Mock(side_effect=subprocess.CalledProcessError(1, ["pip"]))
        with self.assertRaises(subprocess.CalledProcessError):
            install_dependencies(self.root, self.python, runner=failed)
        runner = Mock(return_value=subprocess.CompletedProcess([], 0))
        install_dependencies(self.root, self.python, runner=runner)
        self.assertTrue(any("install" in c.args[0] for c in runner.call_args_list))

    def test_existing_tunnel_must_forward_to_this_port_and_expected_domain(self):
        def tunnel(url, addr):
            return {"public_url": url, "config": {"addr": addr}}

        data = {
            "tunnels": [
                tunnel("http://wrong.ngrok.app", "http://localhost:5000"),
                tunnel("https://other.ngrok.app", "http://localhost:8000"),
                tunnel("https://bms.ngrok.app", "http://localhost:5000"),
            ]
        }
        self.assertEqual(
            find_tunnel(data, 5000, "bms.ngrok.app"), "https://bms.ngrok.app"
        )
        self.assertIsNone(find_tunnel(data, 5000, "another.ngrok.app"))
        self.assertIsNone(
            find_tunnel(
                {"tunnels": [tunnel("https://bms.ngrok.app", "http://remote:5000")]},
                5000,
            )
        )

    def test_shutdown_only_stops_owned_process(self):
        process = Mock(poll=lambda: None)
        stop_owned_process(None)
        process.terminate.assert_not_called()
        stop_owned_process(process)
        process.terminate.assert_called_once()
        process.wait.assert_called_once()

    def test_https_upstream_cannot_forward_to_plain_http_bot(self):
        data = {
            "tunnels": [
                {
                    "public_url": "https://bms.ngrok.app",
                    "config": {"addr": "https://localhost:5000"},
                }
            ]
        }
        self.assertIsNone(find_tunnel(data, 5000))

    def test_explicit_public_origin_is_respected_when_domain_alias_is_blank(self):
        cfg = SimpleNamespace(
            ngrok_domain="", public_tunnel_url="https://configured.ngrok.app", port=5000
        )
        data = {
            "tunnels": [
                {
                    "public_url": "https://other.ngrok.app",
                    "config": {"addr": "http://localhost:5000"},
                }
            ]
        }
        with patch("launcher.read_tunnels", return_value=data), self.assertRaises(
            RuntimeError
        ):
            start_tunnel(cfg, self.root)

    def test_detected_url_overrides_stale_windows_environment_for_server(self):
        (self.root / ".env").write_text(
            "CHANNEL_ACCESS_TOKEN=private\nLINE_CHANNEL_SECRET=private\nGROUP_ID=group\n"
        )
        server = Mock(poll=lambda: 0, wait=lambda: 0)
        with patch(
            "launcher.os.environ", {"PUBLIC_TUNNEL_URL": "https://stale.ngrok.app"}
        ), patch(
            "launcher.subprocess.run", return_value=subprocess.CompletedProcess([], 0)
        ), patch(
            "launcher.start_tunnel", return_value=("https://actual.ngrok.app", None)
        ), patch(
            "launcher.subprocess.Popen", return_value=server
        ) as popen:
            run_application(self.root)
        self.assertEqual(
            popen.call_args.kwargs["env"]["PUBLIC_TUNNEL_URL"],
            "https://actual.ngrok.app",
        )

    def test_ctrl_c_allows_time_for_interactive_capture_and_logout(self):
        (self.root / ".env").write_text(
            "CHANNEL_ACCESS_TOKEN=private\nLINE_CHANNEL_SECRET=private\nGROUP_ID=group\nENABLE_AUTO_LOGOUT=True\n"
        )
        server = Mock(poll=lambda: 0)
        server.wait.side_effect = [KeyboardInterrupt(), 0]
        with patch(
            "launcher.subprocess.run", return_value=subprocess.CompletedProcess([], 0)
        ), patch(
            "launcher.start_tunnel", return_value=("https://bms.ngrok.app", None)
        ), patch(
            "launcher.subprocess.Popen", return_value=server
        ):
            self.assertEqual(run_application(self.root), 0)
        self.assertGreaterEqual(server.wait.call_args.kwargs["timeout"], 82)

    def test_supported_interpreter_rejects_incompatible_windows_builds(self):
        info = {
            "version": [3, 14],
            "platform": "win-amd64",
            "bits": 64,
            "free_threaded": False,
        }
        validate_interpreter(info)
        validate_interpreter(info | {"version": [3, 12]})
        for change in (
            {"platform": "win-arm64"},
            {"bits": 32},
            {"free_threaded": True},
            {"version": [3, 10]},
        ):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                validate_interpreter(info | change)

    def test_launcher_runs_checks_persists_tunnel_and_cleans_up_after_server_exit(self):
        (self.root / ".env").write_text(
            "CHANNEL_ACCESS_TOKEN=private\nLINE_CHANNEL_SECRET=private\nGROUP_ID=group\n"
        )
        tunnel = Mock(poll=lambda: None)
        server = Mock(poll=lambda: 7, wait=lambda: 7)
        runner = Mock(return_value=subprocess.CompletedProcess([], 0))
        with patch("launcher.subprocess.run", runner), patch(
            "launcher.start_tunnel", return_value=("https://bms.ngrok.app", tunnel)
        ), patch("launcher.subprocess.Popen", return_value=server):
            self.assertEqual(run_application(self.root), 7)
        config = (self.root / ".env").read_text()
        self.assertIn("CHANNEL_ACCESS_TOKEN=private", config)
        self.assertIn("https://bms.ngrok.app", config)
        self.assertTrue(any("unittest" in c.args[0] for c in runner.call_args_list))
        tunnel.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()

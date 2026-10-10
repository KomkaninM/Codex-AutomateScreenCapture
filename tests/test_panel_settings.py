import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import Settings
from panel_settings import ProjectStore, StaleConfiguration


class PanelSettingsTests(unittest.TestCase):
    def test_gui_manages_new_bms_and_timing_settings(self):
        from panel_pages import FORM_SECTIONS
        from panel_settings import ENV_FIELDS, SECRET_FIELDS

        form_fields = {
            field[0]
            for groups in FORM_SECTIONS.values()
            for _, fields in groups
            for field in fields
        }
        for key in (
            "BMS_USERNAME",
            "BMS_PASSWORD",
            "DETECTOR_INTERVAL_SEC",
            "LOGIN_WAIT_SECONDS",
            "RECORDED_STEP_DELAY_SECONDS",
        ):
            self.assertIn(key, ENV_FIELDS)
            self.assertIn(key, form_fields)
        self.assertIn("BMS_PASSWORD", SECRET_FIELDS)

        values, version = self.store.load_settings()
        self.store.save_settings(
            values
            | {
                "BMS_USERNAME": "operator",
                "BMS_PASSWORD": "private-password",
                "DETECTOR_INTERVAL_SEC": "0.1",
                "RECORDED_STEP_DELAY_SECONDS": "0.2",
            },
            version,
        )
        cfg = Settings.load(self.root, environ={})
        self.assertEqual(cfg.bms_username, "operator")
        self.assertEqual(cfg.bms_password, "private-password")
        self.assertEqual(cfg.detector_interval, 0.1)
        self.assertEqual(cfg.recorded_step_delay_seconds, 0.2)

    def test_replace_failure_preserves_original_settings(self):
        values, version = self.store.load_settings()
        original = (self.root / ".env").read_bytes()
        with patch(
            "panel_settings.os.replace",
            side_effect=PermissionError("file held by sync"),
        ), self.assertRaises(PermissionError):
            self.store.save_settings(values | {"PORT": "8000"}, version)
        self.assertEqual((self.root / ".env").read_bytes(), original)

    def test_other_launcher_prevents_writing_project_settings(self):
        from launcher import launcher_lock

        values, version = self.store.load_settings()
        original = (self.root / ".env").read_bytes()
        with launcher_lock(self.root), self.assertRaises(RuntimeError):
            self.store.save_settings(values | {"PORT": "8000"}, version)
        self.assertEqual((self.root / ".env").read_bytes(), original)

    def test_clearing_canonical_values_also_clears_legacy_fallbacks(self):
        (self.root / ".env").write_text(
            "LINE_CHANNEL_ACCESS_TOKEN=legacy\nPUBLIC_TUNNEL_URL=https://old.ngrok.app\nDEFAULT_LOGIN_MACRO=old.json\n"
        )
        values, version = self.store.load_settings()
        self.store.save_settings(
            values
            | {
                "CHANNEL_ACCESS_TOKEN": "",
                "NGROK_DOMAIN": "",
                "LOGIN_MACRO_SCRIPT": "",
            },
            version,
        )
        cfg = Settings.load(self.root, environ={})
        self.assertEqual(cfg.channel_access_token, "")
        self.assertEqual(cfg.public_tunnel_url, "")
        self.assertEqual(cfg.default_login_macro, "login_bms.json")

    def test_settings_values_and_version_come_from_the_same_snapshot(self):
        from dotenv import dotenv_values

        path = self.root / ".env"
        path.write_text("USER_ID=original\n")

        def parse_and_change(*args, **kwargs):
            result = dotenv_values(*args, **kwargs)
            path.write_text("USER_ID=changed\n")
            return result

        with patch("panel_settings.dotenv_values", side_effect=parse_and_change):
            values, version = self.store.load_settings()
        self.assertEqual(values["USER_ID"], "original")
        with self.assertRaises(StaleConfiguration):
            self.store.save_settings(values, version)
        self.assertIn("changed", path.read_text())

    def test_target_values_and_version_come_from_the_same_snapshot(self):
        path = self.root / "targets.json"
        original = [{"id": "old", "name": "Old target", "macro": "old.json"}]
        updated = [{"id": "new", "name": "New target", "macro": "new.json"}]
        path.write_text(json.dumps(original))
        parse = json.loads

        def parse_and_change(payload, *args, **kwargs):
            result = parse(payload, *args, **kwargs)
            path.write_text(json.dumps(updated))
            return result

        with patch("json.loads", side_effect=parse_and_change):
            rows, version = self.store.load_targets()
        self.assertEqual(rows, original)
        with self.assertRaises(StaleConfiguration):
            self.store.save_targets(rows, version)
        self.assertEqual(json.loads(path.read_text()), updated)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        template = Path(__file__).resolve().parents[1] / ".env.example"
        (self.root / ".env.example").write_bytes(template.read_bytes())
        self.store = ProjectStore(self.root)

    def test_settings_preserve_comments_unknown_values_and_windows_paths(self):
        original = "# operator note\nCUSTOM_FLAG=keep\nPORT=5000\nNGROK_EXE_PATH='C:\\My tools\\ngrok.exe'\n"
        (self.root / ".env").write_text(original)
        values, version = self.store.load_settings()
        self.assertEqual(values["NGROK_EXE_PATH"], r"C:\My tools\ngrok.exe")
        values["PORT"] = "8000"
        self.store.save_settings(values, version)
        self.assertIn("# operator note", (self.root / ".env").read_text())
        self.assertIn("CUSTOM_FLAG=keep", (self.root / ".env").read_text())
        cfg = Settings.load(self.root, environ={})
        self.assertEqual(cfg.port, 8000)
        self.assertEqual(cfg.ngrok_exe_path, r"C:\My tools\ngrok.exe")
        backups = list((self.root / ".runtime" / "backups").glob("*.env"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), original)

    def test_invalid_settings_do_not_modify_original_file(self):
        values, version = self.store.load_settings()
        original = (self.root / ".env").read_bytes()
        for field, value in (
            ("PORT", "70000"),
            ("LINE_WEBP_QUALITY", "101"),
            ("LOGIN_MACRO_SCRIPT", "../bad.json"),
            ("TIMEZONE", "Unknown/Zone"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.store.save_settings(values | {field: value}, version)
            self.assertEqual((self.root / ".env").read_bytes(), original)

    def test_stale_form_cannot_overwrite_runtime_login_change(self):
        values, version = self.store.load_settings()
        with (self.root / ".env").open("a") as file:
            file.write("\nLOGIN_MACRO_SCRIPT=changed.json\n")
        with self.assertRaises(StaleConfiguration):
            self.store.save_settings(values | {"PORT": "8000"}, version)
        self.assertEqual(
            Settings.load(self.root, environ={}).default_login_macro, "changed.json"
        )

    def test_legacy_aliases_are_presented_in_canonical_fields(self):
        (self.root / ".env").write_text(
            "LINE_CHANNEL_ACCESS_TOKEN=legacy\nDEFAULT_LOGIN_MACRO=DH09D.json\nPUBLIC_TUNNEL_URL=https://bms.ngrok.app\n"
        )
        values, version = self.store.load_settings()
        self.assertEqual(values["CHANNEL_ACCESS_TOKEN"], "legacy")
        self.assertEqual(values["LOGIN_MACRO_SCRIPT"], "DH09D.json")
        self.assertEqual(values["NGROK_DOMAIN"], "bms.ngrok.app")
        self.store.save_settings(
            values | {"GROUP_ID": "", "USER_ID": "private"}, version
        )
        self.assertEqual(Settings.load(self.root, environ={}).delivery_id, "private")

    def test_targets_preserve_extra_fields_and_reject_bad_rows(self):
        rows = [{"id": "09D", "name": "Generator", "macro": "DH09D.json", "custom": 3}]
        loaded, version = self.store.load_targets()
        self.assertEqual(loaded, [])
        self.store.save_targets(rows, version)
        loaded, version = self.store.load_targets()
        self.assertEqual(loaded, rows)
        original = (self.root / "targets.json").read_bytes()
        for bad in (
            rows + [rows[0] | {"id": "09d"}],
            [rows[0] | {"macro": "../bad.json"}],
            [rows[0] | {"name": ""}],
        ):
            with self.subTest(rows=bad), self.assertRaises(ValueError):
                self.store.save_targets(bad, version)
            self.assertEqual((self.root / "targets.json").read_bytes(), original)
        self.store.save_targets([], version)
        self.assertEqual(json.loads((self.root / "targets.json").read_text()), [])

    def test_macro_import_converts_legacy_and_preserves_existing(self):
        self.store.load_settings()
        source = self.root / "old.json"
        source.write_text(
            json.dumps(
                {
                    "default_post_delay": 0.1,
                    "steps": [{"action": "click_coord", "x": 1783, "y": 1255}],
                }
            )
        )
        path = self.store.import_macro(source, "DH09D.json")
        data = json.loads(path.read_text())
        self.assertEqual(
            data["steps"][0], {"action": "click", "x": 1783, "y": 1255, "delay": 0.1}
        )
        with self.assertRaises(FileExistsError):
            self.store.import_macro(source, "DH09D.json")
        macros = self.store.list_macros()
        self.assertEqual(
            (macros[0]["name"], macros[0]["steps"], macros[0]["valid"]),
            ("DH09D.json", 1, True),
        )

    def test_existing_empty_env_is_preserved_and_readiness_supports_discovery(self):
        (self.root / ".env").write_text("")
        values, version = self.store.load_settings()
        self.assertEqual((self.root / ".env").read_text(), "")
        self.assertEqual(values["GROUP_ID"], "")
        checks = self.store.readiness()
        self.assertTrue(any("check-id" in row["detail"] for row in checks))


if __name__ == "__main__":
    unittest.main()

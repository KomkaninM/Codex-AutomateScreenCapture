import io
import random
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from capture import CaptureEngine
from automation_errors import AutomationError
from commands import parse_command
from config import Settings
from line_api import LineAPI


class CaptureOptionsTests(unittest.TestCase):
    def test_completed_report_survives_webp_encoding_and_delivery_size_failures(self):
        original_save = Image.Image.save
        for failure in ("encoding", "size"):
            with self.subTest(
                failure=failure
            ), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                engine = CaptureEngine(
                    root, grab=lambda: Image.new("RGB", (300, 200), "green")
                )

                def save(image, path, format=None, **options):
                    if format == "WEBP":
                        if failure == "encoding":
                            Path(path).write_bytes(b"partial-webp")
                            raise OSError("WebP encoding failed")
                        original_save(image, path, format, **options)
                        with Path(path).open("r+b") as file:
                            file.truncate(10_000_001)
                        return
                    return original_save(image, path, format, **options)

                with patch.object(Image.Image, "save", save), self.assertRaises(
                    (OSError, AutomationError)
                ):
                    engine.capture("report")
                archives = list((root / "report").glob("*.jpg"))
                self.assertEqual(len(archives), 1)
                self.assertEqual(list((root / "report").glob("*.webp")), [])
                with Image.open(archives[0]) as report:
                    self.assertEqual((report.format, report.size), ("JPEG", (300, 200)))

    def test_incomplete_report_encoding_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = CaptureEngine(root, grab=lambda: Image.new("RGB", (300, 200)))

            def fail_save(image, path, *args, **kwargs):
                Path(path).write_bytes(b"incomplete-report")
                raise OSError("Report encoding failed")

            with patch.object(Image.Image, "save", fail_save), self.assertRaises(
                OSError
            ):
                engine.capture("report")
            self.assertEqual(list((root / "report").iterdir()), [])

    def test_smart_starttime_dash_preserves_note_and_validates_options(self):
        for command in ("capture 07C", "start-capture 30m 07C"):
            for flag in ("--starttime", "—starttime", "–starttime"):
                with self.subTest(command=command, flag=flag):
                    parsed = parse_command(
                        f"{command} pump — check {flag} 15:30:00", {"07C": {}}
                    )
                    self.assertEqual(parsed.note, "pump — check")
                    self.assertEqual(parsed.target, "07C")
                    self.assertEqual(parsed.starttime, "15:30:00")
        for text in (
            "capture —starttime",
            "capture —starttime 25:90",
            "capture --starttime 15:30 —starttime 16:30",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_command(text, {})

    def test_bandwidth_defaults_preserve_report_resolution_and_capture_instant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Fixed textured frame gives a meaningful size comparison to the old encoder.
            tile = Image.frombytes(
                "RGB", (256, 256), random.Random(42).randbytes(256 * 256 * 3)
            )
            frame = tile.resize((3000, 2000), Image.Resampling.BILINEAR)
            baseline = io.BytesIO()
            frame.save(baseline, "JPEG", quality=100, subsampling=0, optimize=True)
            instant = datetime(2026, 10, 10, 18, 34, 56, tzinfo=timezone.utc)
            engine = CaptureEngine(root, grab=lambda: frame, clock=lambda: instant)
            shot = engine.capture("DH09D")
            self.assertEqual(shot.captured_at, instant)
            with Image.open(shot.archive) as image:
                self.assertEqual((image.format, image.size), ("JPEG", (3000, 2000)))
            with Image.open(shot.webp) as image:
                self.assertEqual((image.format, image.size), ("WEBP", (3000, 2000)))
            with Image.open(shot.original) as image:
                self.assertEqual((image.format, image.size), ("WEBP", (3000, 2000)))
            self.assertEqual(shot.original, shot.webp)
            self.assertEqual(len(list(shot.archive.parent.iterdir())), 3)
            self.assertLess(
                shot.original.stat().st_size, len(baseline.getvalue()) * 0.8
            )
            urls = shot.urls("https://bms.ngrok.app")
            self.assertTrue(all(url.endswith(".webp") for url in urls))
            self.assertEqual(LineAPI.image(*urls)["originalContentUrl"], urls[0])

    def test_webp_quality_is_configurable_without_resizing_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = Settings.load(root, environ={"LINE_WEBP_QUALITY": "95"})
            self.assertEqual(cfg.line_webp_quality, 95)
            engine = CaptureEngine(
                root,
                grab=lambda: Image.new("RGB", (3000, 2000)),
                webp_quality=cfg.line_webp_quality,
            )
            with Image.open(engine.capture("report").original) as image:
                self.assertEqual(image.size, (3000, 2000))
            for env in ({"LINE_WEBP_QUALITY": "0"}, {"LINE_WEBP_QUALITY": "101"}):
                with self.subTest(env=env), self.assertRaises(ValueError):
                    Settings.load(root, environ=env)

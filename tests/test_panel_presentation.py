import importlib
import importlib.util
import unittest


def load_module(testcase, name):
    testcase.assertIsNotNone(
        importlib.util.find_spec(name), f"{name} must be available"
    )
    return importlib.import_module(name)


class FakeStyle:
    def __init__(self):
        self.configured = {}
        self.mapped = {}

    def configure(self, name, **options):
        self.configured[name] = options

    def map(self, name, **options):
        self.mapped[name] = options


class PanelThemeTests(unittest.TestCase):
    def test_light_and_dark_palettes_define_complete_semantic_roles(self):
        module = load_module(self, "panel_theme")
        required = {
            "name",
            "background",
            "surface",
            "surface_alt",
            "text",
            "muted",
            "border",
            "primary",
            "primary_hover",
            "on_primary",
            "success",
            "success_surface",
            "warning",
            "warning_surface",
            "danger",
            "danger_surface",
            "sidebar",
            "sidebar_active",
            "sidebar_text",
            "sidebar_muted",
            "log_background",
            "log_text",
            "focus",
        }
        for name in ("light", "dark"):
            theme = module.palette(name)
            self.assertEqual(theme.name, name)
            self.assertEqual(set(theme.__dataclass_fields__), required)
            for field in required - {"name"}:
                self.assertRegex(getattr(theme, field), r"^#[0-9A-F]{6}$")

    def test_unknown_theme_is_rejected_and_preference_is_supported(self):
        module = load_module(self, "panel_theme")
        with self.assertRaisesRegex(ValueError, "Unknown theme"):
            module.palette("sepia")
        self.assertIn(module.preferred_theme(), {"light", "dark"})

    def test_ttk_styles_include_visible_focus_and_semantic_states(self):
        module = load_module(self, "panel_theme")
        style = FakeStyle()
        theme = module.palette("light")
        module.configure_ttk_styles(style, theme, "Segoe UI", "Consolas")
        self.assertEqual(style.configured["TButton"]["focuscolor"], theme.focus)
        self.assertEqual(
            style.configured["Primary.TButton"]["background"], theme.primary
        )
        self.assertEqual(
            style.configured["Danger.TButton"]["foreground"], theme.danger
        )
        for required in (
            "Page.TFrame",
            "Card.TFrame",
            "Status.Success.TLabel",
            "Status.Warning.TLabel",
            "Status.Danger.TLabel",
            "Treeview",
        ):
            self.assertIn(required, style.configured)


class PanelWidgetUtilityTests(unittest.TestCase):
    def test_process_states_map_to_text_supported_semantic_tones(self):
        module = load_module(self, "panel_widgets")
        self.assertEqual(module.status_tone("online"), "success")
        self.assertEqual(module.status_tone("error"), "danger")
        self.assertEqual(module.status_tone("stopping"), "warning")
        self.assertEqual(module.status_tone("starting"), "info")
        self.assertEqual(module.status_tone("unexpected"), "neutral")
        self.assertEqual(module.status_style("online"), "Status.Success.TLabel")
        self.assertEqual(module.status_style("error"), "Status.Danger.TLabel")

    def test_log_classification_recognizes_errors_warnings_and_normal_output(self):
        module = load_module(self, "panel_widgets")
        self.assertEqual(module.classify_log_line("2026 ERROR request failed"), "error")
        self.assertEqual(module.classify_log_line("WARNING tunnel is slow"), "warning")
        self.assertEqual(module.classify_log_line("SETUP REQUIRED: add a token"), "warning")
        self.assertEqual(module.classify_log_line("Bot online"), "info")

    def test_log_filter_is_case_insensitive_level_aware_and_stable(self):
        module = load_module(self, "panel_widgets")
        lines = [
            "Bot online",
            "WARNING Tunnel retry",
            "ERROR tunnel failed",
            "Capture accepted",
        ]
        self.assertEqual(
            module.filter_log_lines(lines, query="TUNNEL", levels={"warning", "error"}),
            ["WARNING Tunnel retry", "ERROR tunnel failed"],
        )
        self.assertEqual(
            module.filter_log_lines(lines, query="capture", levels=None),
            ["Capture accepted"],
        )
        self.assertEqual(module.filter_log_lines(lines), lines)

    def test_log_view_buffer_is_bounded_filterable_and_clearable(self):
        module = load_module(self, "panel_widgets")
        buffer = module.LogViewBuffer(limit=3)
        buffer.append("Bot starting")
        buffer.append("WARNING tunnel retry")
        buffer.append("Bot online")
        buffer.append("ERROR send failed")
        self.assertEqual(
            buffer.lines,
            ["WARNING tunnel retry", "Bot online", "ERROR send failed"],
        )
        self.assertEqual(
            buffer.visible(query="bot", levels={"info"}), ["Bot online"]
        )
        buffer.clear()
        self.assertEqual(buffer.lines, [])

    def test_responsive_mode_and_item_counts_are_consistent(self):
        module = load_module(self, "panel_widgets")
        self.assertEqual(module.responsive_mode(819), "compact")
        self.assertEqual(module.responsive_mode(820), "wide")
        self.assertEqual(module.item_count("target", 0), "0 targets")
        self.assertEqual(module.item_count("target", 1), "1 target")
        self.assertEqual(module.item_count("target", 2), "2 targets")


if __name__ == "__main__":
    unittest.main()

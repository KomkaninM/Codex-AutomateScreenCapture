"""Semantic colors and ttk styles for the native control panel."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ThemePalette:
    name: str
    background: str
    surface: str
    surface_alt: str
    text: str
    muted: str
    border: str
    primary: str
    primary_hover: str
    on_primary: str
    success: str
    success_surface: str
    warning: str
    warning_surface: str
    danger: str
    danger_surface: str
    sidebar: str
    sidebar_active: str
    sidebar_text: str
    sidebar_muted: str
    log_background: str
    log_text: str
    focus: str


_PALETTES = {
    "light": ThemePalette(
        name="light",
        background="#F6F8FB",
        surface="#FFFFFF",
        surface_alt="#EEF3F7",
        text="#172033",
        muted="#526174",
        border="#D8E1EA",
        primary="#2563EB",
        primary_hover="#1D4ED8",
        on_primary="#FFFFFF",
        success="#15803D",
        success_surface="#DCFCE7",
        warning="#B45309",
        warning_surface="#FEF3C7",
        danger="#B91C1C",
        danger_surface="#FEE2E2",
        sidebar="#132238",
        sidebar_active="#1E3A5F",
        sidebar_text="#E6EEF7",
        sidebar_muted="#AFC1D4",
        log_background="#111C2E",
        log_text="#E2E8F0",
        focus="#2563EB",
    ),
    "dark": ThemePalette(
        name="dark",
        background="#0F172A",
        surface="#172033",
        surface_alt="#1E293B",
        text="#F8FAFC",
        muted="#CBD5E1",
        border="#334155",
        primary="#60A5FA",
        primary_hover="#93C5FD",
        on_primary="#0F172A",
        success="#4ADE80",
        success_surface="#14532D",
        warning="#FBBF24",
        warning_surface="#713F12",
        danger="#F87171",
        danger_surface="#450A0A",
        sidebar="#090F1C",
        sidebar_active="#1E3A5F",
        sidebar_text="#F8FAFC",
        sidebar_muted="#B8C7D9",
        log_background="#070D18",
        log_text="#E2E8F0",
        focus="#93C5FD",
    ),
}


def palette(name: str) -> ThemePalette:
    try:
        return _PALETTES[name.strip().lower()]
    except (AttributeError, KeyError):
        raise ValueError(f"Unknown theme: {name}") from None


def preferred_theme() -> str:
    """Return the Windows application theme, falling back to light elsewhere."""
    if os.name != "nt":
        return "light"
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if int(value) else "dark"
    except (ImportError, OSError, TypeError, ValueError):
        return "light"


def configure_ttk_styles(style, theme, font_family, mono_family):
    """Register the complete semantic ttk style set for one palette."""
    style.configure(
        ".", font=(font_family, 10), foreground=theme.text, background=theme.background
    )
    style.configure("Page.TFrame", background=theme.background)
    style.configure("Header.TFrame", background=theme.background)
    style.configure("Card.TFrame", background=theme.surface)
    style.configure(
        "Card.TLabelframe",
        background=theme.surface,
        bordercolor=theme.border,
        relief="solid",
    )
    style.configure(
        "Card.TLabelframe.Label",
        background=theme.surface,
        foreground=theme.text,
        font=(font_family, 11, "bold"),
    )
    style.configure("Field.TLabel", background=theme.surface, foreground=theme.text)
    style.configure(
        "Hint.TLabel",
        background=theme.surface,
        foreground=theme.muted,
        font=(font_family, 9),
    )
    style.configure(
        "Help.TLabel",
        background=theme.surface,
        foreground=theme.text,
        font=(font_family, 10),
    )
    style.configure("Muted.TLabel", background=theme.background, foreground=theme.muted)
    style.configure(
        "PageTitle.TLabel",
        background=theme.background,
        foreground=theme.text,
        font=(font_family, 23, "bold"),
    )
    style.configure(
        "SectionTitle.TLabel",
        background=theme.background,
        foreground=theme.text,
        font=(font_family, 12, "bold"),
    )
    for tone, foreground, background in (
        ("Success", theme.success, theme.success_surface),
        ("Warning", theme.warning, theme.warning_surface),
        ("Danger", theme.danger, theme.danger_surface),
        ("Info", theme.primary, theme.surface_alt),
        ("Neutral", theme.muted, theme.surface_alt),
    ):
        style.configure(
            f"Status.{tone}.TLabel",
            background=background,
            foreground=foreground,
            font=(font_family, 9, "bold"),
            padding=(9, 4),
        )
    style.configure("Card.TCheckbutton", background=theme.surface, foreground=theme.text)
    style.configure("TCheckbutton", background=theme.background, foreground=theme.text)
    style.configure(
        "TButton",
        padding=(12, 8),
        background=theme.surface,
        foreground=theme.text,
        bordercolor=theme.border,
        focuscolor=theme.focus,
        focusthickness=2,
    )
    style.map(
        "TButton",
        background=[("active", theme.surface_alt), ("disabled", theme.surface_alt)],
        foreground=[("disabled", theme.muted)],
    )
    style.configure(
        "Primary.TButton",
        background=theme.primary,
        foreground=theme.on_primary,
        bordercolor=theme.primary,
        focuscolor=theme.focus,
        font=(font_family, 10, "bold"),
    )
    style.map(
        "Primary.TButton",
        background=[("active", theme.primary_hover), ("disabled", theme.surface_alt)],
        foreground=[("disabled", theme.muted)],
    )
    style.configure(
        "Danger.TButton",
        foreground=theme.danger,
        background=theme.surface,
        bordercolor=theme.border,
        focuscolor=theme.focus,
    )
    style.map("Danger.TButton", background=[("active", theme.danger_surface)])
    style.configure(
        "TEntry",
        fieldbackground=theme.surface,
        foreground=theme.text,
        insertcolor=theme.text,
        bordercolor=theme.border,
        padding=7,
    )
    style.configure(
        "TCombobox",
        fieldbackground=theme.surface,
        foreground=theme.text,
        arrowcolor=theme.text,
        bordercolor=theme.border,
        padding=6,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", theme.surface), ("disabled", theme.surface_alt)],
        foreground=[("readonly", theme.text), ("disabled", theme.muted)],
    )
    style.configure("TNotebook", background=theme.background, borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        padding=(18, 10),
        background=theme.surface_alt,
        foreground=theme.muted,
        focuscolor=theme.focus,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", theme.surface)],
        foreground=[("selected", theme.text)],
    )
    style.configure(
        "Treeview",
        rowheight=34,
        fieldbackground=theme.surface,
        background=theme.surface,
        foreground=theme.text,
        bordercolor=theme.border,
    )
    style.map(
        "Treeview",
        background=[("selected", theme.primary)],
        foreground=[("selected", theme.on_primary)],
    )
    style.configure(
        "Treeview.Heading",
        padding=8,
        background=theme.surface_alt,
        foreground=theme.text,
        bordercolor=theme.border,
        font=(font_family, 10, "bold"),
    )

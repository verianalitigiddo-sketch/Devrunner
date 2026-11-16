"""
Temalar: tek QSS şablonu (themes/base.qss) + her tema için bir palet.

    theme.apply(app, "dracula")
    theme.color("success")      # durum renkleri koda gömülmez, buradan alınır

Yeni tema eklemek: PALETTES'e bir sözlük ekleyin (tüm anahtarlar dolu olmalı; testler kontrol eder).
Yeni widget stili eklemek: yalnızca base.qss'e yazın, beş temada birden geçerli olur.
"""
from __future__ import annotations

from string import Template

from PySide6.QtGui import QColor, QFont, QPalette

from scripts.config import THEME_DIR

_FLAT = {"radius": "6px", "border_w": "1px", "button_border": "", "input_border": "", "panel_border": ""}

PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "title": "Aydınlık",
        "bg": "#f5f6f8", "surface": "#ffffff", "surface_alt": "#eef0f3", "input_bg": "#ffffff",
        "text": "#1f2328", "muted": "#656d76", "border": "#d0d7de",
        "accent": "#0969da", "accent_hover": "#0860c4", "accent_text": "#ffffff",
        "success": "#1a7f37", "warning": "#9a6700", "danger": "#cf222e", "info": "#0969da", "missing": "#8c959f",
        "header_bg": "#ffffff", "header_text": "#1f2328",
        "selection": "#ddf4ff", "selection_text": "#1f2328",
        "font": "'Segoe UI', 'Noto Sans', 'Helvetica Neue', Arial", "mono": "Consolas, 'DejaVu Sans Mono', monospace",
        "heading_font": "'Segoe UI', 'Noto Sans', Arial", "font_size": "10pt",
        **_FLAT,
    },
    "dark": {
        "title": "Karanlık",
        "bg": "#0d1117", "surface": "#161b22", "surface_alt": "#21262d", "input_bg": "#0d1117",
        "text": "#e6edf3", "muted": "#8d96a0", "border": "#30363d",
        "accent": "#2f81f7", "accent_hover": "#4493f8", "accent_text": "#ffffff",
        "success": "#3fb950", "warning": "#d29922", "danger": "#f85149", "info": "#58a6ff", "missing": "#6e7681",
        "header_bg": "#161b22", "header_text": "#e6edf3",
        "selection": "#1f3a5f", "selection_text": "#e6edf3",
        "font": "'Segoe UI', 'Noto Sans', 'Helvetica Neue', Arial", "mono": "Consolas, 'DejaVu Sans Mono', monospace",
        "heading_font": "'Segoe UI', 'Noto Sans', Arial", "font_size": "10pt",
        **_FLAT,
    },
    "cyberpunk": {
        "title": "Cyberpunk",
        "bg": "#0a0a12", "surface": "#12121f", "surface_alt": "#1c1c33", "input_bg": "#07070d",
        "text": "#e0f7ff", "muted": "#8a9bbd", "border": "#3b2b5c",
        "accent": "#fcee0a", "accent_hover": "#fff35c", "accent_text": "#0a0a12",
        "success": "#05ffa1", "warning": "#fcee0a", "danger": "#ff2a6d", "info": "#05d9e8", "missing": "#5c5c80",
        "header_bg": "#000000", "header_text": "#05d9e8",
        "selection": "#4a1035", "selection_text": "#ffffff",
        "font": "'Segoe UI', 'Noto Sans', Arial", "mono": "Consolas, 'DejaVu Sans Mono', monospace",
        "heading_font": "Consolas, 'DejaVu Sans Mono', monospace", "font_size": "10pt",
        **_FLAT, "radius": "0px",
    },
    "dracula": {
        "title": "Dracula",
        "bg": "#282a36", "surface": "#21222c", "surface_alt": "#44475a", "input_bg": "#191a21",
        "text": "#f8f8f2", "muted": "#a3acd1", "border": "#44475a",
        "accent": "#bd93f9", "accent_hover": "#ff79c6", "accent_text": "#282a36",
        "success": "#50fa7b", "warning": "#ffb86c", "danger": "#ff5555", "info": "#8be9fd", "missing": "#6272a4",
        "header_bg": "#191a21", "header_text": "#f8f8f2",
        "selection": "#44475a", "selection_text": "#f8f8f2",
        "font": "'Segoe UI', 'Noto Sans', Arial", "mono": "Consolas, 'DejaVu Sans Mono', monospace",
        "heading_font": "'Segoe UI', 'Noto Sans', Arial", "font_size": "10pt",
        **_FLAT,
    },
    "nineties": {
        "title": "90'lar",
        "bg": "#c0c0c0", "surface": "#c0c0c0", "surface_alt": "#d4d0c8", "input_bg": "#ffffff",
        "text": "#000000", "muted": "#404040", "border": "#808080",
        "accent": "#000080", "accent_hover": "#1084d0", "accent_text": "#ffffff",
        "success": "#006400", "warning": "#7a5c00", "danger": "#b00000", "info": "#000080", "missing": "#5a5a5a",
        "header_bg": "#000080", "header_text": "#ffffff",
        "selection": "#000080", "selection_text": "#ffffff",
        "font": "Tahoma, 'MS Sans Serif', 'DejaVu Sans', Arial", "mono": "'Courier New', 'DejaVu Sans Mono', monospace",
        "heading_font": "Tahoma, 'MS Sans Serif', Arial", "font_size": "9pt",
        "radius": "0px", "border_w": "2px",
        # Windows 95 kabartma kenarlıklar
        "button_border": "border: 2px outset #ffffff; border-right-color: #404040; border-bottom-color: #404040;",
        "input_border": "border: 2px inset #808080; border-right-color: #ffffff; border-bottom-color: #ffffff;",
        "panel_border": "border: 2px groove #ffffff;",
    },
}

DEFAULT_THEME = "dark"
_current = DEFAULT_THEME

STATE_ROLE = {   # sağlık / pipeline / pod durumu -> palet rengi
    "up": "success", "succeeded": "success", "ok": "success", "online": "success", "Running": "success",
    "slow": "warning", "partial": "warning", "Pending": "warning",
    "running": "info", "queued": "muted",
    "down": "danger", "failed": "danger", "Failed": "danger", "CrashLoopBackOff": "danger",
    "canceled": "missing", "missing": "missing", "unknown": "missing", "none": "missing", "Succeeded": "missing",
}


def names() -> list[tuple[str, str]]:
    return [(k, v["title"]) for k, v in PALETTES.items()]


def current() -> str:
    return _current


def palette(name: str | None = None) -> dict[str, str]:
    return PALETTES.get(name or _current, PALETTES[DEFAULT_THEME])


def color(role: str, name: str | None = None) -> str:
    p = palette(name)
    return p.get(role) or p[STATE_ROLE.get(role, "muted")]


def state_color(state: str) -> str:
    return color(STATE_ROLE.get(state, "muted"))


def build_qss(name: str) -> str:
    p = dict(palette(name))
    for key in ("button_border", "input_border", "panel_border"):
        if not p[key]:
            p[key] = f"border: {p['border_w']} solid {p['border']};"
    template = Template((THEME_DIR / "base.qss").read_text(encoding="utf-8"))
    return template.substitute(p)


def _qpalette(p: dict[str, str]) -> QPalette:
    pal = QPalette()
    c = QColor
    for group in (QPalette.Active, QPalette.Inactive):
        pal.setColor(group, QPalette.Window, c(p["bg"]))
        pal.setColor(group, QPalette.WindowText, c(p["text"]))
        pal.setColor(group, QPalette.Base, c(p["input_bg"]))
        pal.setColor(group, QPalette.AlternateBase, c(p["surface_alt"]))
        pal.setColor(group, QPalette.Text, c(p["text"]))
        pal.setColor(group, QPalette.Button, c(p["surface_alt"]))
        pal.setColor(group, QPalette.ButtonText, c(p["text"]))
        pal.setColor(group, QPalette.Highlight, c(p["selection"]))
        pal.setColor(group, QPalette.HighlightedText, c(p["selection_text"]))
        pal.setColor(group, QPalette.ToolTipBase, c(p["surface"]))
        pal.setColor(group, QPalette.ToolTipText, c(p["text"]))
        pal.setColor(group, QPalette.Link, c(p["info"]))
        pal.setColor(group, QPalette.PlaceholderText, c(p["muted"]))
    pal.setColor(QPalette.Disabled, QPalette.Text, c(p["muted"]))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, c(p["muted"]))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, c(p["muted"]))
    return pal


def apply(app, name: str) -> None:
    global _current
    _current = name if name in PALETTES else DEFAULT_THEME
    p = palette()
    app.setPalette(_qpalette(p))
    family = p["font"].split(",")[0].strip("' ")
    font = QFont(family)
    font.setPointSizeF(float(p["font_size"].rstrip("pt")))
    app.setFont(font)
    app.setStyleSheet(build_qss(_current))

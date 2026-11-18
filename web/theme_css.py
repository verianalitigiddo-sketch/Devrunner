"""
scripts/theme.PALETTES -> CSS (PLAN_WEB.md Bölüm 5, "Görünüm").

Her tema için [data-theme="<ad>"] altında CSS değişkenleri üretilir; button_border / input_border /
panel_border anahtarları geçerli CSS bildirimleridir ve ilgili sınıflara aynen uygulanır.
Renk CSS dosyalarına elle yazılmaz: static/css/app.css yalnızca bu değişkenleri kullanır.
"""
from __future__ import annotations

from functools import lru_cache

from scripts import theme

VARS = ["bg", "surface", "surface_alt", "input_bg", "text", "muted", "border", "accent", "accent_hover", "accent2",
        "accent_text", "success", "warning", "danger", "info", "missing", "header_bg", "header_text", "rail_bg",
        "heading", "selection", "selection_text", "font", "mono", "heading_font", "font_size", "radius",
        "rail_radius", "border_w"]

# Yalnızca CSS'e özgü ek ayarlar (palete dokunmadan): koyu mu, cam yüzey mi
DARK = {"modern", "dark", "cyberpunk", "dracula", "bios"}
GLASS = {"modern", "dark", "dracula", "cyberpunk"}


def _border(p: dict, key: str) -> str:
    return p.get(key) or f"border: {p['border_w']} solid {p['border']};"


@lru_cache(maxsize=1)
def css() -> str:
    out = ["/* Üretildi: web/theme_css.py (scripts/theme.PALETTES). Elle değiştirmeyin. */"]
    for name, p in theme.PALETTES.items():
        sel = f'[data-theme="{name}"]'
        decls = [f"--{k.replace('_', '-')}: {p[k]};" for k in VARS]
        decls.append(f"--scheme: {'dark' if name in DARK else 'light'}; color-scheme: {'dark' if name in DARK else 'light'};")
        decls.append(f"--glass: {1 if name in GLASS else 0};")
        out.append(f"{sel} {{ {' '.join(decls)} }}")
        out.append(f"{sel} .btn, {sel} .chip-btn {{ {_border(p, 'button_border')} }}")
        out.append(f"{sel} .input, {sel} input.input, {sel} select.input, {sel} textarea.input {{ {_border(p, 'input_border')} }}")
        out.append(f"{sel} .panel, {sel} .card, {sel} .table-wrap {{ {_border(p, 'panel_border')} }}")
    return "\n".join(out) + "\n"


def names() -> list[tuple[str, str]]:
    return theme.names()


def state_role(state: str) -> str:
    """Durum -> CSS sınıfı (success | warning | danger | info | missing)."""
    role = theme.STATE_ROLE.get(state, "missing")
    return "missing" if role == "muted" else role

"""
Ayarlar -> scripts/config.py (form config.SETTINGS'ten üretilir; yeni ayar için yalnızca config.py).

Secret kuralları (PLAN_WEB.md Bölüm 6): secret değer ASLA geri gönderilmez, yalnızca "kayıtlı / boş".
Boş gönderilen secret alanı "değiştirme" demektir; silmek için ayrı "temizle" kutusu vardır.
Kaydet -> qt.call(relations.save_settings): Qt nesnelerine dokunduğu için ana iş parçacığında çalışır.
"""
from __future__ import annotations

from flask import Blueprint, render_template, request

from scripts import config, relations
from scripts.config import ENV_FILE, SETTINGS, TRUE_WORDS
from web import ui_state
from web.common import error_text, qt, toast

bp = Blueprint("settings", __name__)


def _group(group_id: str):
    return next((g for g in SETTINGS if g.id == group_id), SETTINGS[0])


def _fields(group) -> list[dict]:
    out = []
    for s in group.settings:
        value = config.get(s.key)
        out.append({"s": s, "value": "" if s.secret else value, "has_value": bool(value),
                    "checked": value.lower() in TRUE_WORDS, "from_os": config.is_from_os(s.key)})
    return out


@bp.get("/")
def page():
    ui_state.update(last_page="settings")
    g = _group(request.args.get("group", "general"))
    return render_template("settings/page.html", groups=SETTINGS, group=g, fields=_fields(g), env_file=ENV_FILE)


@bp.get("/group/<group_id>")
def group_form(group_id):
    g = _group(group_id)
    return render_template("settings/_form.html", group=g, fields=_fields(g))


def _values(group) -> dict[str, str]:
    values = {}
    for s in group.settings:
        if s.readonly:
            continue
        if s.kind == "bool":
            values[s.key] = "1" if request.form.get(s.key) else "0"
        elif s.secret:
            if request.form.get(f"clear_{s.key}"):
                values[s.key] = ""
            elif request.form.get(s.key, "").strip():
                values[s.key] = request.form[s.key]
            # boş: değiştirme
        elif s.key in request.form:
            values[s.key] = request.form[s.key]
    return values


@bp.post("/save/<group_id>")
def save(group_id):
    g = _group(group_id)
    try:
        changed = qt.call(relations.save_settings, g.id, _values(g))
    except ValueError as e:
        return render_template("settings/_form.html", group=g, fields=_fields(g), result=(False, str(e)))
    labels = ", ".join(config.setting(k).label for k in changed)
    result = (True, f"Kaydedildi ({ENV_FILE.name}): {labels}" if changed else "Değişiklik yok.")
    return render_template("settings/_form.html", group=g, fields=_fields(g), result=result), \
        toast("Ayarlar kaydedildi." if changed else "Değişiklik yok.")


@bp.post("/test/<group_id>")
def test(group_id):
    g = _group(group_id)
    try:
        qt.call(relations.save_settings, g.id, _values(g))     # önce kaydet: kaydedilmemiş değerle sınama yanıltır
        ok, message = relations.test_connection(g.id)
    except Exception as e:   # noqa: BLE001
        ok, message = False, error_text(e)
    return render_template("settings/_form.html", group=g, fields=_fields(g), result=(ok, error_text(message)))

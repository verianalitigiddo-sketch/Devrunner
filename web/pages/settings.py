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
from web import ui_state, web_config
from web.common import error_text, qt, toast, trigger

bp = Blueprint("settings", __name__)


ALL_GROUPS = tuple(SETTINGS) + tuple(web_config.groups())


def _group(group_id: str):
    return next((g for g in ALL_GROUPS if g.id == group_id), SETTINGS[0])


def _store(group):
    """Backend grubu -> scripts.config; web grubu -> web.web_config (aynı .env dosyası)."""
    return web_config if web_config.is_web_group(group.id) else config


def _fields(group) -> list[dict]:
    store = _store(group)
    out = []
    for s in group.settings:
        value = store.get(s.key)
        out.append({"s": s, "value": "" if s.secret else value, "has_value": bool(value),
                    "checked": value.lower() in TRUE_WORDS, "from_os": store.is_from_os(s.key)})
    return out


@bp.get("/")
def page():
    ui_state.update(last_page="settings")
    g = _group(request.args.get("group", "general"))
    return render_template("settings/page.html", groups=ALL_GROUPS, group=g, fields=_fields(g), env_file=ENV_FILE,
                           web_group=web_config.is_web_group(g.id))


@bp.get("/group/<group_id>")
def group_form(group_id):
    g = _group(group_id)
    return render_template("settings/_form.html", group=g, fields=_fields(g), web_group=web_config.is_web_group(g.id))


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
    if web_config.is_web_group(g.id):
        return _save_web(g)
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


def _save_web(g):
    try:
        changed = web_config.save(_values(g))
    except ValueError as e:
        return render_template("settings/_form.html", group=g, fields=_fields(g), web_group=True, result=(False, str(e)))
    notes = []
    if "DEVRUNNER_PORT" in changed:
        notes.append("Port değişikliği uygulama yeniden başlatılınca geçerli olur.")
    if "AURA_SYNC_MIN" in changed or "AURA_ENABLED" in changed:
        notes.append("Aura eşitleme aralığı bir sonraki turda uygulanır.")
    labels = ", ".join(web_config.setting(k).label for k in changed)
    result = (True, (f"Kaydedildi: {labels}. " + " ".join(notes)) if changed else "Değişiklik yok.")
    events = ["settings_saved"]
    headers = trigger(*events, toast_message="Ayarlar kaydedildi." if changed else "Değişiklik yok.")
    if "UI_EFFECTS" in changed or "AURA_ENABLED" in changed:
        headers["HX-Refresh"] = "true"      # görünüm düzeyi / menü tüm sayfayı etkiler
    return render_template("settings/_form.html", group=g, fields=_fields(g), web_group=True, result=result), 200, headers

"""
Aura (PLAN_V2.md Bölüm 6) -> scripts/AuraAPI.py, web/aura_service.py.

Kartım, sıralama (tüm zamanlar / son 30 gün / uygulamaya göre), etiketler (tak / çıkar), eşitleme durumu.
Kişi eşleme ilk açılışta girilen adla yapılır (Ayarlar → Genel); eşleşmezse en yakın adlar önerilir.
"""
from __future__ import annotations

from flask import Blueprint, abort, render_template, request

from scripts import AuraAPI
from web import aura_service as svc
from web import ui_state
from web.common import error_fragment, toast, trigger

bp = Blueprint("aura", __name__)


def _ctx(**kw) -> dict:
    return {"BADGES": AuraAPI.BADGES, "BADGE_BY_ID": AuraAPI.BADGE_BY_ID, "STATS": AuraAPI.STATS,
            "RULES": AuraAPI.RULES, "KIND_TITLES": AuraAPI.KIND_TITLES, "TITLES": AuraAPI.TITLES,
            "NEGATIVE_TITLE": AuraAPI.NEGATIVE_TITLE, "PERIODS": svc.PERIODS, **kw}


@bp.get("/")
def page():
    ui_state.update(last_page="aura")
    return render_template("aura/page.html", **_ctx(mode=svc.mode(), mode_text=svc.MODE_TEXT[svc.mode()]))


@bp.get("/me")
def me_part():
    try:
        c = svc.me()
        sugg = [] if c else svc.suggestions()
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Aura")
    return render_template("aura/_me.html", **_ctx(c=c, suggestions=sugg, mine=True))


@bp.get("/board")
def board_part():
    period, app = request.args.get("period", "all"), request.args.get("app", "")
    try:
        cards = [c for c in svc.board(period, app) if not c.hidden or c.key == svc.my_key()]
        apps = svc.apps()
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Aura")
    return render_template("aura/_board.html", **_ctx(cards=cards, period=period, app=app, apps=apps,
                                                      my_key=svc.my_key()))


@bp.get("/person/<path:key>")
def person(key):
    c = svc.card(key)
    if c is None or (c.hidden and key != svc.my_key()):
        abort(404)
    return render_template("aura/_person.html", **_ctx(c=c, mine=key == svc.my_key()))


@bp.get("/badges")
def badges_part():
    c = svc.me()
    rows = []
    for b in AuraAPI.BADGES:
        earned, frac, text = b.progress(c.totals) if c else (False, 0.0, "0")
        rows.append({"b": b, "earned": earned, "frac": frac, "text": text,
                     "equipped": bool(c and c.equipped == b.id and earned)})
    return render_template("aura/_badges.html", **_ctx(rows=rows, c=c))


@bp.get("/status")
def status_part():
    return render_template("aura/_status.html", **_ctx(state=svc.sync_state, last=svc.last_sync(),
                                                       ignored=svc.ignored_count(), mode=svc.mode()))


@bp.post("/sync")
def sync():
    started = svc.sync()
    resp = status_part()
    msg = "Eşitleme başladı." if started else "Eşitleme zaten sürüyor."
    return resp, 200, toast(msg, "info")


@bp.post("/equip")
def equip():
    bid = request.form.get("badge", "")
    if bid and bid not in AuraAPI.BADGE_BY_ID:
        abort(400)
    try:
        svc.equip(bid)
    except ValueError as e:
        return "", 400, toast(str(e), "error")
    name = AuraAPI.BADGE_BY_ID[bid].name if bid else ""
    return "", 204, trigger("aura_changed", toast_message=f"Takıldı: {name}" if bid else "Etiket çıkarıldı; seviye unvanı görünür.")


@bp.post("/hide")
def hide():
    hidden = request.form.get("hidden") == "1"
    svc.set_hidden(hidden)
    return "", 204, trigger("aura_changed", toast_message="Sıralamada gizlendiniz." if hidden else "Sıralamada görünüyorsunuz.")

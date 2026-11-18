"""
Erişilebilirlik -> scripts/HealthAPI.py (yoklama relations.Services'te, sayfa kapalıyken de çalışır).
Uygulama × ortam tablosu, son 1 saatin süre grafiği (sunucuda üretilen satır içi SVG), sessize alma,
"şimdi yokla", ekip geneli olay geçmişi.
"""
from __future__ import annotations

import time

from flask import Blueprint, render_template, request
from markupsafe import Markup

from scripts import HealthAPI, database, relations
from scripts.apps import ENVS
from scripts.config import ENV
from web import ui_state
from web.common import db_problem, error_fragment, qt, toast

bp = Blueprint("health", __name__)
W, H = 160, 26


def sparkline(history, state: str) -> Markup:
    """Son 1 saatin yanıt süreleri; yanıt olmayan noktalar kırmızı dikey çizgi."""
    pts = list(history)
    if not pts:
        return Markup("")
    t1 = time.time()
    t0 = t1 - HealthAPI.HISTORY_SEC
    values = [v for _, v in pts if v is not None]
    top = max(values) if values else 1.0
    line, downs = [], []
    for t, v in pts:
        x = 2 + (t - t0) / (t1 - t0) * (W - 4)
        if v is None:
            downs.append(f'<line class="down" x1="{x:.1f}" y1="2" x2="{x:.1f}" y2="{H - 2}"/>')
        else:
            line.append(f"{x:.1f},{H - 3 - (v / top) * (H - 6):.1f}")
    parts = []
    if len(line) > 1:
        first_x, last_x = line[0].split(",")[0], line[-1].split(",")[0]
        parts.append(f'<polygon class="area" points="{first_x},{H} {" ".join(line)} {last_x},{H}"/>')
        parts.append(f'<polyline class="line" points="{" ".join(line)}"/>')
    elif line:
        x, y = line[0].split(",")
        parts.append(f'<circle class="line" cx="{x}" cy="{y}" r="1.6"/>')
    cls = "spark slow" if state == "slow" else "spark"
    return Markup(f'<svg class="{cls}" viewBox="0 0 {W} {H}" preserveAspectRatio="none">{"".join(downs + parts)}</svg>')


@bp.get("/")
def page():
    ui_state.update(last_page="health")
    return render_template("health/page.html", demo=relations.catalog_source() == "demo", enabled=ENV.HEALTH_ENABLED,
                           interval=ENV.HEALTH_INTERVAL_SEC, timeout=ENV.HEALTH_TIMEOUT_SEC, slow=ENV.HEALTH_SLOW_MS)


@bp.get("/table")
def table():
    env_f = request.args.get("env") or ""
    problems = bool(request.args.get("problems"))
    hide_missing = "1" in request.args.getlist("hide_missing") or "hide_missing" not in request.args
    states = {(s.app, s.env): s for s in relations.health_monitor().states()}
    rows = []
    for a in relations.catalog():
        muted = a.muted_envs()
        for env in ENVS:
            if env_f and env != env_f:
                continue
            s = states.get((a.name, env))
            state = s.state if s else "unknown"
            if problems and state not in ("down", "slow"):
                continue
            if hide_missing and state == "missing":
                continue
            rows.append({"app": a.name, "env": env, "url": a.url(env), "host": a.host(env), "s": s, "state": state,
                         "muted": env in muted, "spark": sparkline(s.history, state) if s else Markup("")})
    summary = relations.health_summary()
    return render_template("health/_table.html", rows=rows, summary=summary, T=HealthAPI.STATE_TITLES)


@bp.post("/probe")
def probe():
    qt.call(relations.refresh_health)
    return "", 204, toast("Yoklama başlatıldı.", "info")


@bp.post("/mute")
def mute():
    problem = db_problem()
    if problem:
        return "", 204, toast("Sessize alma ekip geneli tutulur: " + problem, "error")
    app_name, env = request.form.get("app", ""), request.form.get("env", "")
    a = relations.app(app_name)
    if not a or env not in ENVS:
        return "", 204, toast("Geçersiz uygulama / ortam.", "error")
    on = env not in a.muted_envs()
    try:
        qt.call(relations.set_muted, app_name, env, on)
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Sessize alma")
    headers = toast(f"{app_name} · {env}: {'sessize alındı' if on else 'bildirimler açık'}.")
    headers["HX-Trigger-After-Swap"] = "health_updated"
    return "", 204, headers


@bp.get("/events")
def events():
    if not database.is_ready():
        return render_template("_error.html", title="Olay geçmişi", message=db_problem())
    try:
        rows = HealthAPI.events(None, 100)
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Olay geçmişi")
    return render_template("health/_events.html", rows=rows, T=HealthAPI.STATE_TITLES)

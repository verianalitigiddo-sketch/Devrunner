"""
Flask uygulaması: create_app(), sayfa listesi (PAGES), ortak rotalar.

Yeni sayfa eklemek (README "Yeni sayfa"):
  1) scripts/XAPI.py (backend) — mevcut backend dondurulmuştur, yalnızca YENİ dosya eklenebilir
  2) web/pages/x.py: bp = Blueprint("x", __name__) ve bp.get("/") sayfası
  3) web/templates/x/... şablonları
  4) Aşağıdaki PAGES listesine PageDef ekleyin
Bir sayfa içe aktarılamazsa veya kurulumda hata verirse yerine hata kartı gösterilir; diğerleri çalışır.
"""
from __future__ import annotations

import importlib
import logging
import traceback
from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import urlparse

from markupsafe import Markup, escape
from flask import (Flask, Response, abort, jsonify, redirect, render_template, request, send_from_directory,
                   url_for)

from scripts import __version__, database, fmt, relations, theme
from scripts import user as user_mod
from scripts.apps import ENV_TITLES, ENVS
from scripts.config import APP_NAME, ASSET_DIR, PROFILE, redact
from web import security, theme_css, ui_state
from web.web_config import WENV
from web.qt_bridge import BridgeTimeout

log = logging.getLogger("web")


@dataclass(frozen=True)
class PageDef:
    id: str
    title: str
    module: str
    icon: str


PAGES = [
    PageDef("home", "Uygulamalar", "web.pages.home", "apps"),
    PageDef("devops", "DevOps", "web.pages.devops", "git"),
    PageDef("k8s", "Kubernetes", "web.pages.k8s", "cube"),
    PageDef("health", "Erişilebilirlik", "web.pages.health", "pulse"),
    PageDef("terminal", "Terminal", "web.pages.terminal", "terminal"),
    PageDef("notes", "Notlar", "web.pages.notes", "note"),
    PageDef("bilge", "Bilge", "web.pages.bilge", "bookmark"),
    PageDef("freeze", "Freeze", "web.pages.freeze", "snow"),
    PageDef("chat", "Sohbet", "web.pages.chat", "chat"),
    PageDef("settings", "Ayarlar", "web.pages.settings", "gear"),
]
PAGE_IDS = {p.id for p in PAGES}
WELCOME_EXEMPT = ("/welcome", "/static/", "/fonts/", "/theme.css", "/auth", "/api/events", "/api/shutdown",
                  "/terminal/ws/", "/k8s/ws/")


def current_theme() -> str:
    t = ui_state.get("theme")
    return t if t in dict(theme_css.names()) else "modern"


def create_app(bridge, hub, token: str, runner=None, on_shutdown=None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.update(DR_PORT=None, DR_EXTRA_HOSTS=(), TEMPLATES_AUTO_RELOAD=False,
                      MAX_CONTENT_LENGTH=8 * 1024 * 1024, JSON_AS_ASCII=False)
    app.extensions["dr"] = SimpleNamespace(bridge=bridge, hub=hub, runner=runner, on_shutdown=on_shutdown,
                                           broken={}, token=token)
    security.install(app, token)
    from web.ws import SERVER_OPTIONS, sock
    app.config["SOCK_SERVER_OPTIONS"] = SERVER_OPTIONS
    sock.init_app(app)
    _filters(app)
    _core_routes(app)

    for pd in PAGES:
        try:
            module = importlib.import_module(pd.module)
            app.register_blueprint(module.bp, url_prefix=f"/{pd.id}")
        except Exception as e:   # noqa: BLE001 - bozuk sayfa uygulamayı düşürmez
            log.exception("Sayfa yüklenemedi: %s", pd.module)
            app.extensions["dr"].broken[pd.id] = e
            _broken_route(app, pd, e)
    return app


def _broken_route(app: Flask, pd: PageDef, e: BaseException) -> None:
    """İçe aktarılamayan sayfanın yerine hata kartı (uygulamanın geri kalanı çalışır)."""
    def broken_page():
        tb = redact("".join(traceback.format_exception(e)))
        return render_template("_broken.html", page=pd, error=redact(f"{type(e).__name__}: {e}"), tb=tb)
    app.add_url_rule(f"/{pd.id}/", f"broken_{pd.id}", broken_page)


# ---------------------------------------------------------------------------
# Şablon süzgeçleri ve ortak bağlam
# ---------------------------------------------------------------------------
def icon(name: str, cls: str = "") -> Markup:
    """Satır içi SVG simge (templates/_icons.html'deki <symbol>'e başvurur)."""
    return Markup(f'<svg class="i {escape(cls)}" aria-hidden="true"><use href="#i-{escape(name)}"/></svg>')


def _filters(app: Flask) -> None:
    app.jinja_env.globals["icon"] = icon
    app.add_template_filter(lambda v, s=False: fmt.dt(v, s), "dt")
    app.add_template_filter(fmt.day, "day")
    app.add_template_filter(fmt.ago, "ago")
    app.add_template_filter(fmt.ms, "ms")
    app.add_template_filter(fmt.duration, "duration")
    app.add_template_filter(theme_css.state_role, "role")
    app.add_template_filter(lambda u: urlparse(u or "").hostname or "", "host")
    app.add_template_filter(lambda t: redact(str(t or "")), "redact")
    app.add_template_filter(lambda n: "".join(w[0] for w in str(n or "?").split()[:2]).upper() or "?", "initials")
    app.add_template_filter(lambda e: ENV_TITLES.get(e, e), "envtitle")

    @app.context_processor
    def _ctx():
        bp = request.blueprint or ""
        return {
            "PAGES": PAGES, "page_id": bp, "APP_NAME": APP_NAME, "VERSION": __version__, "PROFILE": PROFILE,
            "theme": current_theme(), "THEMES": theme_css.names(), "ENVS": ENVS, "ENV_TITLES": ENV_TITLES,
            "me": user_mod.current_user(), "broken": app.extensions["dr"].broken,
            "theme_bg": theme.palette(current_theme())["bg"], "effects": WENV.UI_EFFECTS,
            "aura_badge": _aura_badge, "aura_on": WENV.AURA_ENABLED,
        }


# ---------------------------------------------------------------------------
# Ortak rotalar
# ---------------------------------------------------------------------------
def _core_routes(app: Flask) -> None:
    ext = app.extensions["dr"]

    @app.before_request
    def _welcome():
        if user_mod.needs_name() and not request.path.startswith(WELCOME_EXEMPT) and request.method == "GET":
            if request.headers.get("HX-Request"):
                return Response(status=204, headers={"HX-Redirect": "/welcome"})
            return redirect("/welcome")
        return None

    @app.get("/")
    def index():
        last = ui_state.get("last_page", "home")
        return redirect(f"/{last if last in PAGE_IDS else 'home'}/")

    @app.route("/welcome", methods=["GET", "POST"])
    def welcome():
        error = ""
        if request.method == "POST":
            name = " ".join(request.form.get("name", "").split())[:100]
            if name:
                ext.bridge.call(relations.save_settings, "general", {"USER_NAME": name})
                return Response(status=204, headers={"HX-Redirect": "/"})
            from web.common import toast
            return Response(status=204, headers=toast("Adınızı yazın.", "error"))
        return render_template("welcome.html", error=error, suggestion=user_mod.os_user())

    @app.get("/theme.css")
    def theme_file():
        return Response(theme_css.css(), mimetype="text/css", headers={"Cache-Control": "max-age=3600"})

    @app.get("/fonts/<path:name>")
    def fonts(name):
        return send_from_directory(ASSET_DIR / "fonts", name, max_age=86400)

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(ASSET_DIR, "devrunner.png", max_age=86400)

    @app.get("/api/events")
    def events():
        q = ext.hub.subscribe()
        return Response(ext.hub.stream(q), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/api/shutdown")
    def shutdown():
        if ext.on_shutdown:
            ext.on_shutdown()
        return Response(status=204)

    @app.post("/api/open-browser")
    def open_browser():
        """Sistem tarayıcısında bu oturumu aç (yalnızca yerel makine; belirteç tek açılışlıktır)."""
        import webbrowser
        port = app.config.get("DR_PORT")
        if not port:
            abort(409)
        webbrowser.open(f"http://127.0.0.1:{port}/auth?t={ext.token}")
        from web.common import toast
        return Response(status=204, headers=toast("Varsayılan tarayıcıda açılıyor…", "info"))

    @app.post("/api/sessions/reset")
    def reset_sessions():
        """Tüm tarayıcı oturumlarını kapat: imzalama sırrı yenilenir; bu pencere yeni çerezle devam eder."""
        security.rotate_secret(app)
        from web.common import toast
        resp = Response(status=204, headers=toast("Tüm tarayıcı oturumları kapatıldı.", "info"))
        security.set_session_cookie(app, resp)
        return resp

    @app.post("/api/ui")
    def ui():
        theme = request.values.get("theme")
        if theme:
            if theme not in dict(theme_css.names()):
                abort(400)
            ui_state.update(theme=theme)
        return Response(status=204)

    @app.get("/api/palette")
    def palette():
        """Komut paleti (Ctrl+K) verisi."""
        items = [{"kind": "Sayfa", "title": p.title, "url": f"/{p.id}/", "icon": p.icon} for p in PAGES]
        for a in relations.catalog():
            items.append({"kind": "Uygulama", "title": a.name, "url": f"/home/?app={a.name}", "icon": "apps"})
            items.append({"kind": "DevOps", "title": f"{a.name} pipeline'ları", "url": f"/devops/?app={a.name}",
                          "icon": "git"})
        for key, title in theme_css.names():
            items.append({"kind": "Tema", "title": f"Tema: {title}", "theme": key, "icon": "palette"})
        return jsonify(items)

    @app.get("/api/header")
    def header():
        return render_template("_header_status.html", **_status())

    @app.get("/api/statusbar")
    def statusbar():
        return render_template("_statusbar.html", **_status())

    @app.get("/api/notify-check")
    def notify_check():
        return jsonify(ok=True)

    @app.errorhandler(BridgeTimeout)
    def _timeout(e):
        return render_template("_error.html", title="Zaman aşımı", message=str(e)), 504

    @app.errorhandler(database.DbUnavailable)
    def _nodb(e):
        return render_template("_error.html", title="Veritabanı", message=redact(str(e))), 503

    @app.errorhandler(Exception)
    def _any(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e
        log.exception("İstek hatası: %s", request.path)
        return render_template("_error.html", title="Beklenmeyen hata",
                               message=redact(f"{type(e).__name__}: {e}")), 500


def _aura_badge(name: str) -> dict | None:
    """Şablonlar için: kişinin aura rozeti (yoksa None). Aura modülü yüklenemezse sessizce None."""
    if not WENV.AURA_ENABLED:
        return None
    try:
        from web import aura_service
        return aura_service.badge_for(name)
    except Exception:   # noqa: BLE001 - rozet görünmez, sayfa çalışır
        return None


def _status() -> dict:
    chat = relations.chat_service()
    online = None
    peers = []
    if chat is not None and chat.running:
        online = relations.online_count()
        peers = [p.name for p in chat.node.peers() if chat.node.is_online(p.user_id)]
    st = relations.freeze_status()
    upcoming_days = (st.upcoming[0].start - fmt.today()).days if st.upcoming else None
    from scripts import apps as catalog
    return {
        "freeze": st, "upcoming_days": upcoming_days, "online": online, "peers": peers,
        "chat_error": chat.error if chat else "",
        "db_configured": database.db_configured(), "db_ready": database.is_ready(), "db_text": database.status_text(),
        "catalog_source": relations.catalog_source(), "overview_error": relations.overview_error(),
        "catalog_updated": catalog.updated_at(), "app_count": len(relations.catalog()),
        "health": relations.health_summary(),
    }

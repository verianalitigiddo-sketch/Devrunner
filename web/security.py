"""
Yerel sunucunun güvenliği (PLAN_WEB.md Bölüm 6, PLAN_V2.md Bölüm 2 ve 5.3).

* Yalnızca 127.0.0.1 (web/server.py). Port DEVRUNNER_PORT (varsayılan 28657).
* /auth?t=<belirteç>: belirteç her açılışta yenidir. Doğruysa HttpOnly, SameSite=Strict, 30 gün ömürlü çerez
  kurar. Çerez değeri, profil başına .local/web_secret dosyasındaki kalıcı sırla imzalanır: bir kez
  yetkilendirilen tarayıcıda 127.0.0.1:<port> yeniden başlatmadan sonra da açılır. "Tüm tarayıcı oturumlarını
  kapat" sırrı yeniler (rotate_secret). Güven düzeyi .env ile aynıdır (PAT de aynı klasördedir).
* /auth, /static, /fonts, /theme.css dışındaki HER istek (WebSocket el sıkışması dahil) geçerli çerez ister.
* Host başlığı izin verilen değerlerden biri değilse istek reddedilir (DNS rebinding).
* Durum değiştiren istekler: Origin (varsa) kendi kökenimiz, X-Devrunner: 1 başlığı zorunlu.
* WebSocket el sıkışması bir GET'tir: Origin AÇIKÇA zorunlu ve kendi kökenimiz olmalı (CSWSH).
* CSP: satır içi betik, eval ve dış kaynak yok.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sys

from flask import Flask, abort, make_response, redirect, request

from scripts import config

COOKIE = "devrunner_session"
COOKIE_DAYS = 30
PUBLIC_PREFIXES = ("/static/", "/fonts/")
PUBLIC_PATHS = {"/auth", "/theme.css", "/favicon.ico"}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")


# ---------------------------------------------------------------------------
# Kalıcı imzalama sırrı (.local/web_secret, yalnızca sahibi okuyabilir)
# ---------------------------------------------------------------------------
def _secret_path():
    return config.LOCAL_DIR / "web_secret"


def load_secret() -> str:
    path = _secret_path()
    try:
        value = path.read_text(encoding="ascii").strip()
        if len(value) >= 32:
            return value
    except OSError:
        pass
    return rotate_secret_file()


def rotate_secret_file() -> str:
    path = _secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(value, encoding="ascii")
    if sys.platform != "win32":
        tmp.chmod(0o600)
    tmp.replace(path)
    return value


def cookie_value(secret: str) -> str:
    """Çerezde sırrın kendisi değil, ondan türetilen değer tutulur."""
    return hmac.new(secret.encode(), b"devrunner-cookie", hashlib.sha256).hexdigest()


def cookie_name(app: Flask) -> str:
    # Çerezler porta özgü değildir: iki profil (farklı port) birbirinin çerezini ezmesin
    port = app.config.get("DR_PORT")
    return f"{COOKIE}_{port}" if port else COOKIE


def rotate_secret(app: Flask) -> None:
    """Tüm tarayıcı oturumlarını geçersiz kılar (bu oturum dahil; çağıran yeni çerez kurmalı)."""
    app.config["DR_COOKIE_VALUE"] = cookie_value(rotate_secret_file())


def set_session_cookie(app: Flask, resp) -> None:
    resp.set_cookie(cookie_name(app), app.config["DR_COOKIE_VALUE"], httponly=True, samesite="Strict", path="/",
                    max_age=COOKIE_DAYS * 86400)


def allowed_hosts(app: Flask) -> set[str]:
    port = app.config.get("DR_PORT")
    hosts = set(app.config.get("DR_EXTRA_HOSTS", ()))
    if port:
        hosts |= {f"127.0.0.1:{port}", f"localhost:{port}"}
    return hosts


def own_origins(app: Flask) -> set[str]:
    return {f"http://{h}" for h in allowed_hosts(app)}


def is_websocket() -> bool:
    return request.headers.get("Upgrade", "").lower() == "websocket"


def install(app: Flask, token: str) -> None:
    app.config["DR_COOKIE_VALUE"] = cookie_value(load_secret())

    @app.before_request
    def _guard():
        if request.host not in allowed_hosts(app):
            abort(400, "Geçersiz Host")
        path = request.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return None
        if not hmac.compare_digest(request.cookies.get(cookie_name(app), ""), app.config["DR_COOKIE_VALUE"]):
            if (request.method == "GET" and not request.headers.get("HX-Request") and not path.startswith("/api/")
                    and not is_websocket()):
                return make_response(_UNAUTHORIZED, 401, {"Content-Type": "text/html; charset=utf-8"})
            abort(401)
        if is_websocket() and request.headers.get("Origin") not in own_origins(app):
            abort(403, "WebSocket: yabancı veya eksik köken")
        if request.method in UNSAFE:
            origin = request.headers.get("Origin")
            if origin and origin not in own_origins(app):
                abort(403, "Yabancı köken")
            if request.headers.get("X-Devrunner") != "1":
                abort(403, "Eksik X-Devrunner başlığı")
        return None

    @app.get("/auth")
    def _auth():
        if not hmac.compare_digest(request.args.get("t", ""), token):
            abort(401)
        resp = redirect("/")
        set_session_cookie(app, resp)
        return resp

    @app.after_request
    def _headers(resp):
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if not request.path.startswith(PUBLIC_PREFIXES) and request.path != "/theme.css":
            resp.headers["Cache-Control"] = "no-store"
        return resp


_UNAUTHORIZED = """<!doctype html><meta charset="utf-8"><title>Devrunner</title>
<body style="font-family:system-ui;background:#07080d;color:#eceef5;display:grid;place-items:center;height:100vh">
<div><h2>Oturum yok</h2><p>Devrunner'ı masaüstü kısayolundan açın veya pencerede
<b>Tarayıcıda aç</b>'a basın.</p></div></body>"""

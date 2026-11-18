"""
Yerel sunucunun güvenliği (PLAN_WEB.md Bölüm 6).

* Yalnızca 127.0.0.1; port her açılışta rastgele (web/server.py).
* Her açılışta yeni oturum belirteci. /auth?t=<belirteç> doğruysa HttpOnly, SameSite=Strict çerez kurar.
  /auth, /static, /fonts ve /theme.css dışındaki HER istek geçerli çerez ister (sabit zamanlı karşılaştırma).
* Host başlığı izin verilen değerlerden biri değilse istek reddedilir (DNS rebinding).
* Durum değiştiren istekler: Origin (varsa) kendi kökenimiz olmalı ve X-Devrunner: 1 başlığı bulunmalı
  (htmx her isteğe ekler). CORS başlığı gönderilmez.
* CSP: satır içi betik, eval ve dış kaynak yok.
"""
from __future__ import annotations

import hashlib
import hmac

from flask import Flask, abort, make_response, redirect, request

COOKIE = "devrunner_session"
PUBLIC_PREFIXES = ("/static/", "/fonts/")
PUBLIC_PATHS = {"/auth", "/theme.css", "/favicon.ico"}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")


def cookie_value(token: str) -> str:
    """Çerezde belirtecin kendisi değil, ondan türetilen değer tutulur."""
    return hmac.new(token.encode(), b"devrunner-cookie", hashlib.sha256).hexdigest()


def allowed_hosts(app: Flask) -> set[str]:
    port = app.config.get("DR_PORT")
    hosts = set(app.config.get("DR_EXTRA_HOSTS", ()))
    if port:
        hosts |= {f"127.0.0.1:{port}", f"localhost:{port}"}
    return hosts


def install(app: Flask, token: str) -> None:
    expected = cookie_value(token)

    @app.before_request
    def _guard():
        host = request.host
        if host not in allowed_hosts(app):
            abort(400, "Geçersiz Host")
        path = request.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return None
        if not hmac.compare_digest(request.cookies.get(COOKIE, ""), expected):
            if request.method == "GET" and not request.headers.get("HX-Request") and not path.startswith("/api/"):
                return make_response(_UNAUTHORIZED, 401, {"Content-Type": "text/html; charset=utf-8"})
            abort(401)
        if request.method in UNSAFE:
            origin = request.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in allowed_hosts(app)}:
                abort(403, "Yabancı köken")
            if request.headers.get("X-Devrunner") != "1":
                abort(403, "Eksik X-Devrunner başlığı")
        return None

    @app.get("/auth")
    def _auth():
        if not hmac.compare_digest(request.args.get("t", ""), token):
            abort(401)
        resp = redirect("/")
        resp.set_cookie(COOKIE, expected, httponly=True, samesite="Strict", path="/")
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
<div><h2>Oturum yok</h2><p>Devrunner'ı masaüstü kısayolundan açın.</p></div></body>"""

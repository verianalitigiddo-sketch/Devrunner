"""Web katmanı: güvenlik, sayfalar (örnek veriyle), secret kuralları, Prod onayı, hata kartı, tema CSS'i."""
from __future__ import annotations

import re

import pytest

from scripts import config
from web import app as web_app
from web.events import EventHub
from web.qt_bridge import DirectBridge
from web.security import COOKIE

TOKEN = "t" * 40
HOST = "127.0.0.1:5999"


def make_app(runner=None):
    app = web_app.create_app(DirectBridge(), EventHub(), TOKEN, runner=runner)
    app.config.update(DR_PORT=5999, TESTING=True, SERVER_NAME=HOST)   # test istemcisi bu Host ile ister
    return app


@pytest.fixture(scope="module")
def demo_catalog():
    """Örnek DevOps kataloğu (servisler çalışmadan)."""
    from scripts import DevopsAPI
    from scripts import apps as catalog
    ov = DevopsAPI.fetch_overview()
    catalog.set_apps(ov.apps, ov.source, ov.fetched_at)
    yield ov
    catalog.set_apps([], "", "")


@pytest.fixture
def client(demo_catalog):
    app = make_app()
    c = app.test_client()
    r = c.get(f"/auth?t={TOKEN}")
    assert r.status_code == 302
    return c


def post(c, url, **kw):
    headers = {"X-Devrunner": "1", "HX-Request": "true", **kw.pop("headers", {})}
    return c.post(url, headers=headers, **kw)


# ---------------------------------------------------------------------------
# Güvenlik
# ---------------------------------------------------------------------------
def test_requires_session_cookie(demo_catalog):
    c = make_app().test_client()
    assert c.get("/home/").status_code == 401
    assert c.get("/api/palette").status_code == 401
    assert c.get("/api/events").status_code == 401
    assert c.get("/auth?t=yanlis").status_code == 401
    assert c.get("/static/css/app.css").status_code == 200        # statik dosya serbest
    r = c.get(f"/auth?t={TOKEN}")
    cookie = r.headers["Set-Cookie"]
    assert COOKIE in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert TOKEN not in cookie                                     # çerezde belirtecin kendisi yok
    assert c.get("/home/").status_code == 200


def test_rejects_foreign_host_origin_and_missing_header(client):
    assert client.get("/home/", base_url="http://evil.example:5999").status_code == 400
    assert client.post("/api/ui", data={"theme": "bios"}).status_code == 403                    # başlık yok
    assert client.post("/api/ui", data={"theme": "bios"},
                       headers={"X-Devrunner": "1", "Origin": "http://evil.example"}).status_code == 403
    assert post(client, "/api/ui", data={"theme": "bios"}, headers={"Origin": f"http://{HOST}"}).status_code == 204


def test_security_headers(client):
    r = client.get("/home/")
    csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-eval" not in csp and "frame-ancestors 'none'" in csp
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in r.headers
    assert not re.search(r"<script>(?!</script>)", r.get_data(as_text=True))                   # satır içi betik yok


# ---------------------------------------------------------------------------
# Sayfalar
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/home/", "/devops/", "/k8s/", "/health/", "/notes/", "/bilge/", "/freeze/",
                                  "/chat/", "/settings/", "/settings/?group=k8s"])
def test_pages_render(client, path):
    r = client.get(path)
    assert r.status_code == 200, path
    body = r.get_data(as_text=True)
    assert 'class="rail"' in body and "Beklenmeyen hata" not in body


@pytest.mark.parametrize("path", ["/home/table", "/home/app/Musteri360", "/home/app/KrediSkor/k8s",
                                  "/devops/table", "/devops/app/KrediSkor", "/devops/app/KrediSkor/history/ci",
                                  "/devops/app/KampanyaTakip/history/cd", "/devops/app/Musteri360/commits",
                                  "/k8s/list?env=dev", "/k8s/list?env=prod&app=Musteri360", "/health/table",
                                  "/api/header", "/api/header?part=freeze", "/api/statusbar", "/api/palette"])
def test_fragments(client, path):
    r = client.get(path, headers={"HX-Request": "true"})
    assert r.status_code == 200, path
    assert "Beklenmeyen hata" not in r.get_data(as_text=True)


def test_devops_steps_and_log(client, demo_catalog):
    from scripts import DevopsAPI
    build = DevopsAPI.history(demo_catalog.pipelines["KrediSkor"].ci)[0]
    body = client.get(f"/devops/build/{build.id}/steps").get_data(as_text=True)
    assert "düşen adım" in body
    log = client.get(f"/devops/build/{build.id}/log/7?download=1")
    assert log.headers["Content-Disposition"].startswith("attachment") and b"pyarrow" in log.data


def test_secret_never_echoed(client):
    config.save({"DEVOPS_PAT": "gizli-pat-degeri-123"})
    for path in ("/settings/?group=devops", "/settings/group/devops", "/api/palette", "/home/"):
        assert "gizli-pat-degeri-123" not in client.get(path).get_data(as_text=True), path
    # Boş gönderilen secret değişmez; "temizle" ile silinir
    r = post(client, "/settings/save/devops", data={"DEVOPS_PAT": "", "DEVOPS_URL": config.get("DEVOPS_URL")})
    assert r.status_code == 200 and config.get("DEVOPS_PAT") == "gizli-pat-degeri-123"
    assert "gizli-pat-degeri-123" not in r.get_data(as_text=True)
    post(client, "/settings/save/devops", data={"clear_DEVOPS_PAT": "1"})
    assert config.get("DEVOPS_PAT") == ""


def test_prod_exec_requires_server_side_confirmation(client):
    form = {"env": "prod", "pod": "musteri360-x", "container": "musteri360", "command": "id"}
    body = post(client, "/k8s/exec", data=form).get_data(as_text=True)
    assert "PROD ortamında" in body
    token = re.search(r'name="confirm" value="([^"]+)"', body).group(1)
    assert "Onay geçersiz" in post(client, "/k8s/exec", data={**form, "confirm": "uydurma"}).get_data(as_text=True)
    other = post(client, "/k8s/exec", data={**form, "command": "rm -rf /", "confirm": token}).get_data(as_text=True)
    assert "Onay geçersiz" in other                                  # belirteç başka komuta kullanılamaz
    body = post(client, "/k8s/exec", data=form).get_data(as_text=True)
    token = re.search(r'name="confirm" value="([^"]+)"', body).group(1)
    ok = post(client, "/k8s/exec", data={**form, "confirm": token}).get_data(as_text=True)
    assert "[örnek]" in ok                                           # demo cluster yanıtı
    again = post(client, "/k8s/exec", data={**form, "confirm": token}).get_data(as_text=True)
    assert "Onay geçersiz" in again                                  # tek kullanımlık
    dev = post(client, "/k8s/exec", data={**form, "env": "dev"}).get_data(as_text=True)
    assert "[örnek]" in dev and "PROD" not in dev                    # Dev'de onay yok


def test_terminal_prefill_does_not_run(demo_catalog):
    class FakeRunner:
        shells, running, calls = [], False, []

        def shell(self, _):
            return None

        def output(self):
            return ""

        def start(self, *a, **k):
            self.calls.append(a)

    runner = FakeRunner()
    c = make_app(runner).test_client()
    c.get(f"/auth?t={TOKEN}")
    body = c.get("/terminal/?cmd=rm%20-rf%20%2F").get_data(as_text=True)
    assert 'value="rm -rf /"' in body and runner.calls == []


def test_welcome_redirect_when_no_name(client):
    config.save({"USER_NAME": ""})
    config.load()
    r = client.get("/home/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/welcome")
    assert post(client, "/welcome", data={"name": "Yeni Kişi"}).headers.get("HX-Redirect") == "/"
    assert config.get("USER_NAME") == "Yeni Kişi"


def test_broken_page_shows_error_card(monkeypatch, demo_catalog):
    monkeypatch.setattr(web_app, "PAGES", web_app.PAGES + [web_app.PageDef("bozuk", "Bozuk", "web.pages.yok", "x")])
    c = make_app().test_client()
    c.get(f"/auth?t={TOKEN}")
    body = c.get("/bozuk/").get_data(as_text=True)
    assert "Bu modül yüklenemedi" in body and "ModuleNotFoundError" in body
    assert c.get("/home/").status_code == 200


def test_theme_css_covers_all_themes(client):
    from scripts import theme
    css = client.get("/theme.css").get_data(as_text=True)
    for name in theme.PALETTES:
        assert f'[data-theme="{name}"]' in css
    assert "$" not in css and "--accent:" in css
    assert post(client, "/api/ui", data={"theme": "yok"}).status_code == 400
    post(client, "/api/ui", data={"theme": "dracula"})
    assert 'data-theme="dracula"' in client.get("/home/").get_data(as_text=True)


def test_error_text_is_redacted(client, monkeypatch):
    from scripts import DevopsAPI
    config.save({"DEVOPS_PAT": "sizinti-olmasin-987"})

    def boom(*a, **k):
        raise DevopsAPI.DevopsError("hata: sizinti-olmasin-987")

    monkeypatch.setattr(DevopsAPI, "timeline", boom)
    body = client.get("/devops/build/5025/steps").get_data(as_text=True)
    assert "sizinti-olmasin-987" not in body and "***" in body

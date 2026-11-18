"""PLAN_V2 Faz A: web ayarları, sabit port, kalıcı oturum çerezi, WebSocket köken denetimi, deployment'sız K8s."""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import K8sAPI, config
from tests.test_web import HOST, TOKEN, make_app, post
from web import k8s_access, security, web_config

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Web ayarları ve port
# ---------------------------------------------------------------------------
def test_web_config_defaults_and_save():
    assert web_config.WENV.DEVRUNNER_PORT == 28657 and not web_config.is_explicit("DEVRUNNER_PORT")
    assert web_config.WENV.UI_EFFECTS == "normal" and web_config.WENV.AURA_ENABLED is True
    with pytest.raises(ValueError):
        web_config.save({"DEVRUNNER_PORT": "80"})
    with pytest.raises(ValueError):
        web_config.save({"UI_EFFECTS": "parlak"})
    assert web_config.save({"DEVRUNNER_PORT": "31415", "UI_EFFECTS": "kapali"}) == ["DEVRUNNER_PORT", "UI_EFFECTS"]
    web_config.load()   # aynı .env'den geri okunur
    assert web_config.WENV.DEVRUNNER_PORT == 31415 and web_config.is_explicit("DEVRUNNER_PORT")
    assert "DEVRUNNER_PORT='31415'" in config.ENV_FILE.read_text()


def test_gpu_off_defaults_to_reduced_effects(monkeypatch):
    monkeypatch.setenv("DEVRUNNER_DISABLE_GPU", "1")
    web_config.load()
    assert web_config.WENV.UI_EFFECTS == "azaltilmis"


def test_settings_page_shows_and_saves_web_group(demo_client):
    body = demo_client.get("/settings/?group=web").get_data(as_text=True)
    assert "DEVRUNNER_PORT" in body and "Tüm tarayıcı oturumlarını kapat" in body
    r = post(demo_client, "/settings/save/web", data={"DEVRUNNER_PORT": "28660", "UI_EFFECTS": "azaltilmis",
                                                      "TERMINAL_DETACHED_MIN": "30", "TERMINAL_MAX_SESSIONS": "8"})
    assert r.status_code == 200 and r.headers.get("HX-Refresh") == "true"
    assert "yeniden başlatılınca" in r.get_data(as_text=True)
    assert 'data-effects="azaltilmis"' in demo_client.get("/home/").get_data(as_text=True)


def test_port_in_use_is_reported(tmp_path):
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    port = busy.getsockname()[1]
    try:
        env = {**__import__("os").environ, "DEVRUNNER_PROFILE": "porttest", "USER_NAME": "Port Test",
               "QT_QPA_PLATFORM": "offscreen"}
        p = subprocess.run([sys.executable, "-m", "web.server", "--port", str(port)], cwd=ROOT, env=env,
                           input="x" * 40 + "\n", capture_output=True, text=True, timeout=60)
    finally:
        busy.close()
    assert p.returncode == 3 and f"ERROR port_in_use {port}" in p.stdout


# ---------------------------------------------------------------------------
# Oturum çerezi
# ---------------------------------------------------------------------------
@pytest.fixture
def demo_client():
    from scripts import DevopsAPI
    from scripts import apps as catalog
    ov = DevopsAPI.fetch_overview()
    catalog.set_apps(ov.apps, ov.source, ov.fetched_at)
    c = make_app().test_client()
    c.get(f"/auth?t={TOKEN}")
    yield c
    catalog.set_apps([], "", "")


def test_cookie_survives_restart_until_rotated():
    first = make_app().test_client()
    r = first.get(f"/auth?t={TOKEN}")
    cookie = r.headers["Set-Cookie"]
    assert "Max-Age=2592000" in cookie and security.cookie_name(make_app()) in cookie
    value = cookie.split("=", 1)[1].split(";", 1)[0]
    # "yeniden başlatma": yeni süreç, yeni /auth belirteci, aynı kalıcı sır
    app2 = web_app_with_token("y" * 40)
    c2 = app2.test_client()
    c2.set_cookie(security.cookie_name(app2), value, domain=HOST.split(":")[0])
    assert c2.get("/home/").status_code in (200, 302)          # 302: ad sorulmuşsa hoş geldin
    assert c2.get(f"/auth?t={TOKEN}").status_code == 401        # eski belirteç yeni süreçte geçersiz
    r = post(c2, "/api/sessions/reset")
    assert r.status_code == 204 and security.COOKIE in r.headers["Set-Cookie"]   # bu pencere yeni çerezle sürer
    c3 = web_app_with_token("z" * 40).test_client()
    c3.set_cookie(security.cookie_name(app2), value, domain=HOST.split(":")[0])
    assert c3.get("/home/").status_code == 401                  # eski çerez artık geçersiz


def web_app_with_token(token):
    from web import app as web_app
    from web.events import EventHub
    from web.qt_bridge import DirectBridge
    app = web_app.create_app(DirectBridge(), EventHub(), token)
    app.config.update(DR_PORT=5999, TESTING=True, SERVER_NAME=HOST)
    return app


def test_websocket_handshake_requires_own_origin(demo_client):
    ws = {"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Version": "13",
          "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}
    assert demo_client.get("/k8s/ws/logs?env=dev&pod=x&container=y", headers=ws).status_code == 403
    assert demo_client.get("/k8s/ws/logs", headers={**ws, "Origin": "http://evil.example"}).status_code == 403
    anon = make_app().test_client()
    assert anon.get("/k8s/ws/logs", headers={**ws, "Origin": f"http://{HOST}"}).status_code == 401


def test_open_browser_uses_session_token(demo_client, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    assert post(demo_client, "/api/open-browser").status_code == 204
    assert opened == [f"http://127.0.0.1:5999/auth?t={TOKEN}"]


# ---------------------------------------------------------------------------
# Kubernetes: deployment hiç istenmez
# ---------------------------------------------------------------------------
class StrictCluster:
    """deployments() çağrılırsa test düşer; ingress / service 403."""
    calls: list[str] = []

    def __init__(self, env):
        self.env, self.ns = env, "verianalitigi"
        self.data = K8sAPI.DemoCluster(env)

    def deployments(self):
        raise AssertionError("deployments() çağrılmamalı")

    def ingresses(self):
        StrictCluster.calls.append(f"{self.env}:ingresses")
        raise K8sAPI.K8sForbidden("Ingress: servis hesabının yetkisi yok (403).")

    def services(self):
        StrictCluster.calls.append(f"{self.env}:services")
        raise K8sAPI.K8sForbidden("Service: yetki yok (403).")

    def pods(self, selector=""):
        if selector:        # etiketle eşleme bu kümede çalışmasın: ad önekine düşülmeli
            return []
        return self.data.pods("")

    def events(self, pod):
        raise K8sAPI.K8sForbidden("Event: yetki yok (403).")

    def logs(self, pod, container, lines, previous=False):
        return "log"


@pytest.fixture
def strict(monkeypatch, demo_client):
    StrictCluster.calls = []
    k8s_access.reset()
    monkeypatch.setattr(K8sAPI, "cluster", StrictCluster)
    yield demo_client
    k8s_access.reset()


def test_k8s_pages_never_list_deployments(strict):
    for path in ("/k8s/list?env=dev", "/k8s/list?env=prod&app=Musteri360", "/home/app/KrediSkor/k8s",
                 "/k8s/pod?env=dev&pod=musteri360-82af085c-a21b0", "/k8s/events?env=dev&pod=x"):
        body = strict.get(path).get_data(as_text=True)
        assert "Beklenmeyen hata" not in body and "403" not in body, path
    body = strict.get("/k8s/list?env=dev&app=KrediSkor").get_data(as_text=True)
    assert "krediskor-" in body and "pod adı öneki" in body
    for env in ("dev", "test", "prod"):                          # yetkisiz kaynak ortam başına bir kez sorulur
        assert StrictCluster.calls.count(f"{env}:ingresses") <= 1, StrictCluster.calls
    assert "yetki dışı: Event, Ingress" in strict.get("/k8s/list?env=dev").get_data(as_text=True)


def test_pod_summary_without_deployments(strict):
    summary = k8s_access.pod_summary()
    assert summary["KrediSkor"]["dev"] == "0/1" and summary["Musteri360"]["prod"] == "2/2"


# ---------------------------------------------------------------------------
# Titreme: seçim güncel adresten okunur
# ---------------------------------------------------------------------------
def test_table_selection_follows_current_url(demo_client):
    body = demo_client.get("/home/table?app=ChurnTahmin",
                           headers={"HX-Request": "true", "HX-Current-URL": "http://127.0.0.1:5999/home/?app=KrediSkor"}
                           ).get_data(as_text=True)
    selected = body.split('selected"', 1)[0].rsplit("<tr", 1)[-1] + body.split('selected"', 1)[1][:200]
    assert "KrediSkor" in selected and "ChurnTahmin" not in selected

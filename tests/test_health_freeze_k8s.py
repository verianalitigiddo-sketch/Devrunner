"""Erişilebilirlik durum makinesi, freeze tarih mantığı, kubeconfig, terminal yardımcıları."""
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer

import yaml

from scripts import FreezeAPI, HealthAPI, K8sAPI, TerminalAPI, config
from scripts.HealthAPI import HealthMonitor, ProbeResult


def r(code=200, ms=100.0, dns=True, url="https://a/"):
    return ProbeResult(url, dns, code, ms if code else None)


def test_classify():
    assert HealthAPI.classify(r(200, 100), False, 3000) == "up"
    assert HealthAPI.classify(r(404, 100), False, 3000) == "up"          # sunucu yanıt veriyor
    assert HealthAPI.classify(r(200, 5000), False, 3000) == "slow"
    assert HealthAPI.classify(r(503), False, 3000) == "down"
    assert HealthAPI.classify(r(None), False, 3000) == "down"            # zaman aşımı / ret
    assert HealthAPI.classify(r(dns=False), False, 3000) == "missing"    # hiç aktarılmamış
    assert HealthAPI.classify(r(dns=False), True, 3000) == "down"        # vardı, artık çözülmüyor


def test_change_needs_two_consecutive_results():
    m = HealthMonitor()
    assert m.update("A", "dev", r(200), 3000) is None                    # ilk gözlem: bildirim yok
    assert m.get("A", "dev").state == "up"
    assert m.update("A", "dev", r(503), 3000) is None                    # tek takılma
    assert m.update("A", "dev", r(200), 3000) is None
    assert m.get("A", "dev").state == "up"
    assert m.update("A", "dev", r(503), 3000) is None
    change = m.update("A", "dev", r(503), 3000)
    assert change and (change.old, change.new) == ("up", "down")
    assert m.update("A", "dev", r(503), 3000) is None                    # aynı durum tekrar bildirilmez


def test_probe_against_local_server():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(503 if self.path == "/kapali" else 200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        ok = HealthAPI.probe(base + "/", 5)
        assert ok.status_code == 200 and ok.ms is not None
        assert HealthAPI.probe(base + "/kapali", 5).status_code == 503
    finally:
        srv.shutdown()
        srv.server_close()
    refused = HealthAPI.probe(base + "/", 2)
    assert refused.status_code is None and refused.error
    assert not HealthAPI.probe("https://yok.invalid/", 2).dns_ok


def test_freeze_status_boundaries():
    f = FreezeAPI.Freeze(1, date(2026, 12, 25), date(2027, 1, 2), "Yıl sonu")
    assert FreezeAPI.status(date(2026, 12, 25), [f], 3).is_active          # başlangıç dahil
    assert FreezeAPI.status(date(2027, 1, 2), [f], 3).is_active            # bitiş dahil
    assert not FreezeAPI.status(date(2027, 1, 3), [f], 3).is_active
    up = FreezeAPI.status(date(2026, 12, 22), [f], 3)
    assert not up.is_active and up.upcoming == [f]
    assert FreezeAPI.status(date(2026, 12, 21), [f], 3).upcoming == []


USER_TEMPLATE = """apiVersion: v1
kind: Config
clusters:
- cluster:
    insecure-skip-tls-verify: true
    server: https://10.1.2.3:5443
  name: cce-cluster
contexts:
- context:
    cluster: cce-cluster
    namespace: verianalitigi
    user: verianalitigi-sa
  name: verianalitigi-context
current-context: verianalitigi-context
users:
- name: verianalitigi-sa
  user:
    token: tok123
"""


def test_kubeconfig_matches_given_template():
    config.save({"K8S_DEV_SERVER": "10.1.2.3:5443", "K8S_DEV_TOKEN": "tok123"})
    assert K8sAPI.normalize_server("10.1.2.3:5443") == "https://10.1.2.3:5443"
    assert K8sAPI.kubeconfig_text("dev") == USER_TEMPLATE
    assert yaml.safe_load(K8sAPI.kubeconfig_text("dev")) == K8sAPI.kubeconfig_dict("dev")
    assert K8sAPI.configured_envs() == ["dev"] and not K8sAPI.is_demo()
    assert K8sAPI.write_kubeconfigs() == ["dev"]
    assert K8sAPI.kubeconfig_path("dev").read_text() == USER_TEMPLATE


def test_k8s_demo_workload_resolution():
    from scripts.apps import AppRef
    w = K8sAPI.resolve_workload(AppRef("KrediSkor", "DDDB.Iz.Anm.KrediSkor"), "dev")
    assert w.found and "Ingress" in w.how and w.pods and w.pods[0].status == "CrashLoopBackOff"
    w = K8sAPI.resolve_workload(AppRef("Musteri360", "DDDB.Iz.Anm.Musteri360"), "prod")   # ingress 403 (örnek)
    assert w.found and w.how == "deployment adı" and w.warnings
    assert not K8sAPI.resolve_workload(AppRef("SubeAnaliz", "DDDB.Iz.Anm.SubeAnaliz"), "dev").pods


def test_terminal_helpers(tmp_path):
    sub = tmp_path / "alt"
    sub.mkdir()
    assert TerminalAPI.parse_cd("cd alt", str(tmp_path)) == str(sub)
    assert TerminalAPI.parse_cd("nslookup x", str(tmp_path)) is None
    import pytest
    with pytest.raises(ValueError):
        TerminalAPI.parse_cd("cd yok", str(tmp_path))
    ps = TerminalAPI.Shell("powershell", "Windows PowerShell", "powershell.exe", "5.1")
    assert "curl.exe" in TerminalAPI.hint(ps, "curl https://a")
    assert TerminalAPI.hint(ps, "nslookup")
    prog, args, native = TerminalAPI.build_command(ps, "nslookup a")
    assert "UTF8" in args[-1] and args[-1].endswith("nslookup a") and native is None
    cmd = TerminalAPI.Shell("cmd", "CMD", "cmd.exe")
    assert "chcp 65001" in TerminalAPI.build_command(cmd, "ver")[2]
    bash = TerminalAPI.Shell("bash", "Bash", "/bin/bash")
    prog, args, _ = TerminalAPI.build_command(bash, "echo hi")
    assert args[-2:] == ["-c", "echo hi"]
    env = TerminalAPI.process_env("dev")
    assert env["KUBECONFIG"].endswith("dev.yaml")
    config.save({"DEVOPS_PAT": "gizli-deger"})
    assert "gizli-deger" not in TerminalAPI.process_env("").values()


def test_detect_shells_runs():
    shells = TerminalAPI.detect_shells()
    assert shells and all(s.path for s in shells)

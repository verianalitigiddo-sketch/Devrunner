"""Web altyapısı: olay akışı, Qt köprüsü, terminal çalıştırıcısı, mimari kurallar (dondurulmuş backend, import yalıtımı)."""
from __future__ import annotations

import ast
import subprocess
import threading
import time
from pathlib import Path

import pytest

from web.events import EventHub

ROOT = Path(__file__).resolve().parent.parent
BASE_COMMIT = "4d5e134"     # çatalın temel aldığı Devrunner main commit'i


def test_event_stream_format_and_unsubscribe():
    hub = EventHub()
    q = hub.subscribe()
    gen = hub.stream(q)
    assert next(gen).startswith("retry:")
    hub.publish("health_updated", {"n": 1, "metin": "çalışıyor"})
    chunk = next(gen)
    assert chunk == 'event: health_updated\ndata: {"n": 1, "metin": "çalışıyor"}\n\n'
    gen.close()
    assert hub.subscriber_count() == 0


def test_slow_client_does_not_block():
    hub = EventHub()
    q = hub.subscribe()
    for i in range(2000):
        hub.publish("x", {"i": i})
    assert q.qsize() == 500 and q.queue[-1][1]["i"] == 1999


def test_qt_bridge_runs_on_main_thread(qapp):
    from web.qt_bridge import BridgeTimeout, QtBridge
    bridge = QtBridge()
    main = threading.current_thread()
    seen = {}

    def work():
        seen["result"] = bridge.call(lambda: threading.current_thread() is main)
        try:
            bridge.call(lambda: (_ for _ in ()).throw(ValueError("x")))
        except ValueError:
            seen["error"] = True

    t = threading.Thread(target=work)
    t.start()
    deadline = time.time() + 5
    while t.is_alive() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert seen == {"result": True, "error": True}
    with pytest.raises(BridgeTimeout):
        threading_result = {}

        def slow():
            try:
                bridge.call(lambda: None, timeout=0.2)     # olay döngüsü işlenmediği için zaman aşımı
            except BridgeTimeout as e:
                threading_result["e"] = e

        th = threading.Thread(target=slow)
        th.start()
        th.join()
        raise threading_result["e"]


def test_terminal_runner_streams_and_stops(tmp_path):
    from scripts import TerminalAPI
    from web.terminal_runner import TerminalRunner

    events = []

    class Hub:
        def publish(self, name, data):
            events.append((name, data))

    r = TerminalRunner(Hub())
    assert r.shells_ready.wait(10) and r.shells
    shell = r.shell("bash") or r.shells[0]
    r.start("echo Türkçe-ğüşiöç", shell, str(tmp_path), "", "── başlık\n")
    deadline = time.time() + 10
    while not any(n == "terminal_done" for n, _ in events) and time.time() < deadline:
        time.sleep(0.05)
    out = r.output()
    assert "── başlık" in out and "Türkçe-ğüşiöç" in out and "çıkış kodu 0" in out
    events.clear()
    r.start("sleep 30 & sleep 31; echo bitmemeli", shell, str(tmp_path))
    time.sleep(0.4)
    assert r.stop()
    deadline = time.time() + 5
    while not any(n == "terminal_done" for n, _ in events) and time.time() < deadline:
        time.sleep(0.05)
    done = [d for n, d in events if n == "terminal_done"][0]
    assert done["stopped"] and "bitmemeli" not in r.output().split("sleep 31")[-1]
    assert TerminalAPI.IS_WINDOWS or "sleep 31" not in subprocess.run(
        ["ps", "-eo", "stat,args"], capture_output=True, text=True).stdout.replace("Z", "")


# ---------------------------------------------------------------------------
# Mimari kurallar (PLAN_WEB.md Bölüm 2 ve 3)
# ---------------------------------------------------------------------------
def test_backend_is_frozen():
    """scripts/ altında taban commit'e göre yalnızca YENİ dosya olabilir."""
    try:
        ok = subprocess.run(["git", "cat-file", "-e", BASE_COMMIT], cwd=ROOT, capture_output=True).returncode == 0
    except OSError:
        ok = False
    if not ok:
        pytest.skip("git veya taban commit yok (sığ klon)")
    out = subprocess.run(["git", "diff", "--name-status", BASE_COMMIT, "--", "scripts/"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    untracked = subprocess.run(["git", "status", "--porcelain", "--", "scripts/"], cwd=ROOT,
                               capture_output=True, text=True).stdout
    changed = [line for line in (out + untracked).splitlines() if line and not line.startswith(("A", "??"))]
    assert changed == [], f"Dondurulmuş backend değişmiş: {changed}"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_qt_bindings_are_isolated():
    """shell/ yalnızca PyQt6; web/ ve scripts/ yalnızca PySide6 (iki Qt bağlamı aynı süreçte çakışır)."""
    for f in (ROOT / "shell").glob("*.py"):
        bad = _imports(f) & {"PySide6", "scripts", "web", "shiboken6"}
        assert not bad, f"{f.name} backend'i import ediyor: {bad}"
    for folder in ("web", "scripts"):
        for f in (ROOT / folder).rglob("*.py"):
            assert "PyQt6" not in _imports(f), f"{f} PyQt6 import ediyor"


def test_pages_do_not_import_other_modules_apis():
    """Bir sayfa yalnızca kendi API'sini ve relations'ı kullanır (çekirdek modüller hariç)."""
    core = {"relations", "config", "database", "fmt", "apps", "theme", "user", "BookmarkAPI"}
    own = {"home": set(), "devops": {"DevopsAPI"}, "k8s": {"K8sAPI"}, "health": {"HealthAPI"},
           "terminal": {"TerminalAPI", "K8sExecAPI"}, "notes": {"NotesAPI"}, "bilge": {"BilgeAPI"}, "freeze": {"FreezeAPI"},
           "chat": {"ChatAPI"}, "aura": {"AuraAPI"}, "settings": set()}
    apis = {"AuraAPI", "DevopsAPI", "K8sAPI", "K8sExecAPI", "HealthAPI", "TerminalAPI", "NotesAPI", "BilgeAPI", "FreezeAPI", "ChatAPI"}
    for page, allowed in own.items():
        tree = ast.parse((ROOT / "web" / "pages" / f"{page}.py").read_text(encoding="utf-8"))
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("scripts"):
                used |= {a.name for a in node.names} | {node.module.split(".")[-1]}
        # home: yalnızca durum başlıkları (sabitler) için DevopsAPI / HealthAPI izinli
        extra = {"DevopsAPI", "HealthAPI"} if page == "home" else set()
        bad = (used & apis) - allowed - extra - core
        assert not bad, f"{page} başka modülün API'sini import ediyor: {bad}"

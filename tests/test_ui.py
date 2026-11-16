"""Arayüz: pencere açılır, her sayfa demo veriyle yüklenir, beş tema uygulanır, bozuk modül uygulamayı düşürmez."""
import pytest
from PySide6.QtWidgets import QApplication

import app as main_app
from _pages._common import ErrorPage
from scripts import relations, theme


@pytest.fixture
def window(qtbot):
    w = main_app.MainWindow(QApplication.instance())
    qtbot.addWidget(w)
    relations.start_services(w)
    qtbot.waitUntil(lambda: bool(relations.catalog()), timeout=10000)
    yield w
    relations.stop_services()
    w._quitting = True


def test_all_pages_load_in_all_themes(window, qtbot):
    for key, _ in theme.names():
        window.theme_combo.setCurrentIndex(window.theme_combo.findData(key))
        assert theme.current() == key
        for pd in main_app.PAGES:
            window.open_page(pd.id)
            qtbot.wait(50)
            assert not isinstance(window.pages[pd.id], ErrorPage), pd.id


def test_broken_module_shows_error_page(window, monkeypatch):
    monkeypatch.setattr(main_app, "PAGES", main_app.PAGES + [main_app.PageDef("bozuk", "Bozuk", "_pages.yok", "x")])
    window.nav.addItem("Bozuk")
    window._show_row(len(main_app.PAGES) - 1)
    assert isinstance(window.pages["bozuk"], ErrorPage)


def test_settings_page_saves_to_env(window, qtbot):
    window.open_page("settings")
    page = window.pages["settings"]
    form = page.forms["health"]
    form.fields["HEALTH_SLOW_MS"].setText("1234")
    form.save()
    from scripts.config import ENV, ENV_FILE  # noqa: F401
    assert ENV.HEALTH_SLOW_MS == 1234


def test_terminal_runs_command(window, qtbot):
    window.open_page("terminal")
    page = window.pages["terminal"]
    qtbot.waitUntil(lambda: bool(page.shells), timeout=10000)
    page.input.setText("echo Türkçe-çıktı-ğüşiöç")
    page.run()
    qtbot.waitUntil(lambda: page.proc is None, timeout=10000)
    text = page.console.toPlainText()
    assert "Türkçe-çıktı-ğüşiöç" in text and "çıkış kodu 0" in text
    assert page.shell().name in text          # hangi kabuk olduğu yazıyor


def test_terminal_stop_kills_long_command(window, qtbot):
    window.open_page("terminal")
    page = window.pages["terminal"]
    qtbot.waitUntil(lambda: bool(page.shells), timeout=10000)
    page.input.setText("sleep 30 & sleep 31; echo bitmemeli")
    page.run()
    qtbot.wait(500)
    page.stop()
    qtbot.waitUntil(lambda: page.proc is None, timeout=5000)
    text = page.console.toPlainText()
    assert "bitmemeli" not in text.split("\n", 3)[-1] and ("durduruldu" in text or "çıkış kodu" in text)
    import subprocess
    def leftovers():
        out = subprocess.run(["ps", "-eo", "stat,args"], capture_output=True, text=True).stdout.splitlines()
        rows = [line.split(None, 1) for line in out[1:]]
        return [r for r in rows if len(r) == 2 and r[1].strip() == "sleep 31" and not r[0].startswith("Z")]

    qtbot.waitUntil(lambda: not leftovers(), timeout=3000)


def test_freeze_badge(window, monkeypatch):
    from datetime import date

    from scripts import FreezeAPI
    monkeypatch.setattr(FreezeAPI, "_cache", [FreezeAPI.Freeze(1, date(2000, 1, 1), date(2999, 1, 1), "Uzun")])
    window._update_freeze()
    assert window.freeze_badge.isVisibleTo(window) and "FREEZE" in window.freeze_badge.text()
    monkeypatch.setattr(FreezeAPI, "_cache", [])
    window._update_freeze()
    assert not window.freeze_badge.isVisibleTo(window)

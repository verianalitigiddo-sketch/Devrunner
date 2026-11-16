"""
Erişilebilirlik -> scripts/HealthAPI.py (yoklama döngüsü relations.Services içinde, sayfa kapalıyken de çalışır)

Uygulama × ortam tablosu: durum, HTTP kodu, son süre, son 1 saatin süre grafiği, son değişim.
Uygulama / ortam bazında sessize alma; "Terminalde dene" ile curl / nslookup.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QCheckBox, QComboBox, QSplitter, QVBoxLayout, QWidget

from _pages import _common as ui
from scripts import HealthAPI, database, relations, theme
from scripts.apps import ENV_TITLES, ENVS
from scripts.bus import bus
from scripts.config import ENV
from scripts.fmt import ago, dt, ms
from scripts.worker import run_async


class Sparkline(QWidget):
    """Son 1 saatin yanıt süreleri. Yanıt olmayan noktalar kırmızı çizgiyle işaretlenir."""

    def __init__(self, history, state: str):
        super().__init__()
        self.points = list(history)
        self.state = state
        self.setMinimumSize(160, 22)

    def paintEvent(self, _event) -> None:
        if not self.points:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width() - 4, self.height() - 6
        t0, t1 = time.time() - HealthAPI.HISTORY_SEC, time.time()
        values = [v for _, v in self.points if v is not None]
        top = max(values) if values else 1
        line = QPolygonF()
        for t, v in self.points:
            x = 2 + (t - t0) / (t1 - t0) * w
            if v is None:
                p.setPen(QPen(QColor(theme.color("danger")), 2))
                p.drawLine(QPointF(x, 3), QPointF(x, 3 + h))
                continue
            line.append(QPointF(x, 3 + h - (v / top) * h))
        p.setPen(QPen(QColor(theme.state_color(self.state if self.state != "down" else "up")), 1.5))
        if line.size() == 1:
            p.drawEllipse(line[0], 1.5, 1.5)
        else:
            p.drawPolyline(line)
        p.end()


class Page(ui.Page):
    COLS = ["Uygulama", "Ortam", "Durum", "HTTP", "Süre", "Son 1 saat", "Son değişim", "Adres"]

    def __init__(self):
        super().__init__("Erişilebilirlik", "Dev/Test/Prod adreslerine düzenli HTTP isteği. "
                                            "Durum iki ardışık yoklamada aynı çıkınca değişmiş sayılır.")
        self.env_filter = QComboBox()
        self.env_filter.addItem("Tüm ortamlar", None)
        for e in ENVS:
            self.env_filter.addItem(ENV_TITLES[e], e)
        self.env_filter.currentIndexChanged.connect(self.render)
        self.problems = QCheckBox("Yalnızca sorunlular")
        self.problems.toggled.connect(self.render)
        self.hide_missing = QCheckBox("Aktarılmamışları gizle")
        self.hide_missing.setChecked(True)
        self.hide_missing.toggled.connect(self.render)
        self.add_tool(self.env_filter)
        self.add_tool(self.problems)
        self.add_tool(self.hide_missing)
        self.add_tool(ui.button("Şimdi yokla", "primary", relations.refresh_health))

        self.summary = ui.label("", "muted")
        self.body.addWidget(self.summary)
        split = QSplitter(Qt.Vertical)
        self.table = ui.table(self.COLS, stretch=7)
        self.table.cellDoubleClicked.connect(lambda r, _c: ui.open_url(self._row(r)[2]) if self._row(r) else None)
        split.addWidget(self.table)

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 6, 0, 0)
        self.mute_btn = ui.button("Sessize al / aç", slot=self._toggle_mute,
                                  tooltip="Sessizdeki uygulama/ortam bildirim üretmez (ekip geneli).")
        bl.addLayout(ui.hbox(ui.button("Tarayıcıda aç", slot=lambda: self._with_row(lambda a, e, u: ui.open_url(u))),
                             ui.button("Terminalde curl", slot=lambda: self._with_row(
                                 lambda a, e, u: relations.open_in_terminal(f"curl -k -I -s -m 15 {u}", ""))),
                             ui.button("Terminalde nslookup", slot=lambda: self._with_row(
                                 lambda a, e, u: relations.open_in_terminal(f"nslookup {relations.app(a).host(e)}", ""))),
                             self.mute_btn, None))
        bl.addWidget(ui.label("Son durum değişimleri (ekip geneli, dr_health_events)", "h2"))
        self.events = ui.table(["Zaman", "Uygulama", "Ortam", "Durum", "HTTP", "Süre", "Ayrıntı", "Gözlemleyen"], stretch=6)
        bl.addWidget(self.events, 1)
        split.addWidget(bottom)
        split.setSizes([480, 260])
        self.body.addWidget(split, 1)

        self._rows: list[tuple[str, str, str]] = []
        ui.on(bus.health_updated, self, self.render)
        ui.on(bus.health_changed, self, lambda *_: self.load_events())
        ui.on(bus.catalog_changed, self, self.render)
        ui.on(bus.data_changed, self, lambda k: k == "overrides" and self.render())
        ui.on(bus.theme_changed, self, lambda _t: self.render())
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.isVisible() and self.load_events())
        self._timer.start(60_000)
        self.render()

    def on_shown(self) -> None:
        self.render()
        self.load_events()

    def _row(self, r: int):
        return self._rows[r] if 0 <= r < len(self._rows) else None

    def _with_row(self, fn) -> None:
        row = self._row(self.table.currentRow())
        if row:
            fn(*row)

    def render(self) -> None:
        if not ENV.HEALTH_ENABLED:
            self.banner.show_message("warning", "Düzenli yoklama kapalı (Ayarlar → Erişilebilirlik).")
        elif relations.catalog_source() == "demo":
            self.banner.show_message("demo", "Örnek katalog: sonuçlar uydurmadır, gerçek adreslere istek atılmaz.")
        else:
            self.banner.clear()
        env_f = self.env_filter.currentData()
        states = {(s.app, s.env): s for s in relations.health_monitor().states()}
        rows, self._rows = [], []
        widgets = []
        for a in relations.catalog():
            muted = a.muted_envs()
            for env in ENVS:
                if env_f and env != env_f:
                    continue
                s = states.get((a.name, env))
                state = s.state if s else "unknown"
                if self.problems.isChecked() and state not in ("down", "slow"):
                    continue
                if self.hide_missing.isChecked() and state == "missing":
                    continue
                url = a.url(env)
                title = HealthAPI.STATE_TITLES.get(state, state) + (" · sessiz" if env in muted else "")
                tip = ((s.error or "") + (f"\nSon yoklama: {dt(s.checked_at, True)}" if s.checked_at else "")) if s else ""
                rows.append([ui.item(a.name, data=a.name, bold=True), ui.item(ENV_TITLES[env]),
                             ui.item(f"● {title}", state, tooltip=tip.strip()),
                             ui.item(s.status_code if s and s.status_code else ""),
                             ui.item(ms(s.ms) if s else "", "slow" if s and state == "slow" else None),
                             ui.item(""), ui.item(ago(s.changed_at) if s else "", tooltip=dt(s.changed_at, True) if s else ""),
                             ui.item(url)])
                self._rows.append((a.name, env, url))
                widgets.append((s.history, state) if s else ([], state))
        ui.fill_table(self.table, rows)
        for r, (hist, state) in enumerate(widgets):
            self.table.setCellWidget(r, 5, Sparkline(hist, state))
        summ = relations.health_summary()
        self.summary.setText(" · ".join(f"{HealthAPI.STATE_TITLES[k]}: {v}" for k, v in summ.items()) +
                             f"   (her {ENV.HEALTH_INTERVAL_SEC} sn, zaman aşımı {ENV.HEALTH_TIMEOUT_SEC} sn, "
                             f"yavaş eşiği {ENV.HEALTH_SLOW_MS} ms)")

    def load_events(self) -> None:
        if not database.is_ready():
            self.events.setRowCount(0)
            return
        run_async(HealthAPI.events, None, 100, on_done=lambda rows: ui.fill_table(self.events, [[
            ui.item(dt(e["observed_at"], True)), ui.item(e["app_name"]), ui.item(ENV_TITLES.get(e["env"], e["env"])),
            ui.item(f"● {HealthAPI.STATE_TITLES.get(e['state'], e['state'])}", e["state"]),
            ui.item(e["status_code"] or ""), ui.item(ms(e["response_ms"]) if e["response_ms"] is not None else ""),
            ui.item(e["detail"] or ""), ui.item(e["observed_by"] or "")] for e in rows]),
            on_error=lambda e: None, owner=self)

    def _toggle_mute(self) -> None:
        row = self._row(self.table.currentRow())
        if not row:
            return
        if not database.is_ready():
            ui.show_error(self, "Sessize alma ekip geneli tutulur: " + database.status_text())
            return
        app_name, env, _url = row
        a = relations.app(app_name)
        muted = env in (a.muted_envs() if a else set())
        run_async(relations.set_muted, app_name, env, not muted, on_done=lambda _r: self.render(),
                  on_error=lambda e: ui.show_error(self, e), owner=self)

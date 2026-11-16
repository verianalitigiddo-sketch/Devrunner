"""
Freeze -> scripts/FreezeAPI.py

Geliştiriciler freeze dönemlerini girer (başlangıç ve bitiş dahil). Bugün bir dönemin içindeyse başlık
çubuğunun sağ üstünde FREEZE rozeti görünür; FREEZE_WARN_DAYS gün içinde başlayacaksa "yaklaşıyor".
"""
from __future__ import annotations

from datetime import timedelta

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QBrush, QColor, QTextCharFormat
from PySide6.QtWidgets import QCalendarWidget, QDateEdit, QLineEdit, QSplitter, QVBoxLayout, QWidget

from _pages import _common as ui
from scripts import FreezeAPI, database, relations, theme
from scripts.bus import bus
from scripts.fmt import day, today
from scripts.worker import run_async


def qdate(d) -> QDate:
    return QDate(d.year, d.month, d.day)


class Page(ui.Page):
    def __init__(self):
        super().__init__("Freeze", "Freeze dönemleri ekip geneli tutulur. Bugün bir dönemin içindeyse sağ üstte "
                                   "FREEZE rozeti görünür; CD ve Prod işlemleri uyarı işaretiyle gösterilir.")
        self.status = ui.label("", "h2")
        self.body.addWidget(self.status)
        split = QSplitter(Qt.Horizontal)
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.setFirstDayOfWeek(Qt.Monday)
        self.calendar.clicked.connect(self._day_clicked)
        split.addWidget(self.calendar)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(ui.label("Yeni freeze dönemi", "h2"))
        self.start = QDateEdit(QDate.currentDate())
        self.end = QDateEdit(QDate.currentDate())
        for w in (self.start, self.end):
            w.setCalendarPopup(True)
            w.setDisplayFormat("dd.MM.yyyy")
        self.start.dateChanged.connect(lambda d: self.end.setDate(d) if self.end.date() < d else None)
        self.title = QLineEdit()
        self.title.setPlaceholderText("Açıklama (ör. Yıl sonu kapanış freeze'i)")
        self.add_btn = ui.button("Ekle", "primary", self.add)
        rl.addLayout(ui.hbox(ui.label("Başlangıç"), self.start, ui.label("Bitiş"), self.end, None))
        rl.addLayout(ui.hbox(self.title, self.add_btn))
        rl.addWidget(ui.label("Dönemler", "h2"))
        self.table = ui.table(["Başlangıç", "Bitiş", "Gün", "Açıklama", "Ekleyen", "Durum"], stretch=3)
        self.table.itemSelectionChanged.connect(self._selected)
        rl.addWidget(self.table, 1)
        self.del_btn = ui.button("Seçili dönemi sil", "danger", self.delete)
        rl.addLayout(ui.hbox(self.del_btn, None))
        split.addWidget(right)
        split.setSizes([420, 760])
        self.body.addWidget(split, 1)

        self.items: list[FreezeAPI.Freeze] = []
        ui.on(bus.db_state_changed, self, lambda _ok: self.load())
        ui.on(bus.freeze_changed, self, self.load)
        ui.on(bus.theme_changed, self, lambda _t: self._paint())

    def on_shown(self) -> None:
        self.load()

    def load(self) -> None:
        self._status_text()
        if not database.is_ready():
            self.banner.show_message("error", ui.db_unavailable_text(), "Ayarlar", lambda: relations.open_page("settings"))
            self.add_btn.setEnabled(False)
            return
        self.banner.clear()
        self.add_btn.setEnabled(True)
        run_async(FreezeAPI.list_freezes, on_done=self._show, on_error=lambda e: self.banner.show_message("error", str(e)),
                  owner=self)

    def _status_text(self) -> None:
        st = relations.freeze_status()
        if st.is_active:
            self.status.setText(f"❄ Freeze dönemindeyiz: {', '.join(f.title for f in st.active)} "
                                f"(bitiş {day(st.active_until)})")
            self.status.setStyleSheet(f"color: {theme.color('danger')};")
        elif st.upcoming:
            f = st.upcoming[0]
            self.status.setText(f"Yaklaşan freeze: {f.label} ({(f.start - today()).days} gün sonra)")
            self.status.setStyleSheet(f"color: {theme.color('warning')};")
        else:
            self.status.setText("Şu an freeze yok.")
            self.status.setStyleSheet(f"color: {theme.color('success')};")

    def _show(self, items: list[FreezeAPI.Freeze]) -> None:
        self.items = items
        t = today()
        rows = []
        for f in items:
            state = "Devam ediyor" if f.contains(t) else "Geçti" if f.end < t else "Gelecek"
            role = "failed" if state == "Devam ediyor" else "missing" if state == "Geçti" else "slow"
            rows.append([ui.item(day(f.start), data=f.id), ui.item(day(f.end)), ui.item((f.end - f.start).days + 1),
                         ui.item(f.title), ui.item(f.created_by), ui.item(f"● {state}", role)])
        ui.fill_table(self.table, rows)
        self._paint()
        self._status_text()

    def _paint(self) -> None:
        self.calendar.setDateTextFormat(QDate(), QTextCharFormat())   # temizle
        fmt = QTextCharFormat()
        fmt.setBackground(QBrush(QColor(theme.color("danger"))))
        fmt.setForeground(QBrush(QColor("#ffffff")))
        for f in self.items:
            d = f.start
            while d <= f.end and (d - f.start).days < 400:
                self.calendar.setDateTextFormat(qdate(d), fmt)
                d += timedelta(days=1)

    def _day_clicked(self, qd: QDate) -> None:
        d = qd.toPython()
        hits = [f for f in self.items if f.contains(d)]
        if hits:
            for r in range(self.table.rowCount()):
                if self.table.item(r, 0).data(Qt.UserRole) == hits[0].id:
                    self.table.selectRow(r)
        else:
            self.start.setDate(qd)
            self.end.setDate(qd)

    def _selected(self) -> None:
        self.del_btn.setEnabled(ui.row_data(self.table) is not None)

    def add(self) -> None:
        start, end, title = self.start.date().toPython(), self.end.date().toPython(), self.title.text()

        def done(_r):
            self.title.clear()
            relations.refresh_shared()      # rozet hemen güncellensin
            bus.data_changed.emit("freeze")
            self.load()

        run_async(FreezeAPI.add_freeze, start, end, title, on_done=done, on_error=lambda e: ui.show_error(self, e),
                  owner=self)

    def delete(self) -> None:
        fid = ui.row_data(self.table)
        f = next((x for x in self.items if x.id == fid), None)
        if not f or not ui.confirm(self, "Freeze sil", f"{f.label}\n\nsilinsin mi?"):
            return
        run_async(FreezeAPI.delete_freeze, f.id,
                  on_done=lambda _r: (relations.refresh_shared(), bus.data_changed.emit("freeze"), self.load()),
                  on_error=lambda e: ui.show_error(self, e), owner=self)

"""
Bilge Talepleri -> scripts/BilgeAPI.py

Bilge'de açılan taleplerin başlık + link listesi. Çift tıklama tarayıcıda açar.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QPlainTextEdit

from _pages import _common as ui
from scripts import BilgeAPI, database, relations
from scripts.bus import bus
from scripts.config import ENV
from scripts.fmt import ago, dt
from scripts.worker import run_async

STATE = {"Açık": "info", "Devam ediyor": "warning", "Kapandı": "missing"}


class RequestDialog(QDialog):
    def __init__(self, parent=None, data: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Talep düzenle" if data else "Yeni Bilge talebi")
        form = QFormLayout(self)
        self.title = QLineEdit((data or {}).get("title", ""))
        self.url = QLineEdit((data or {}).get("url", ""))
        self.url.setPlaceholderText("https://…")
        self.app = ui.app_combo(include_none="(uygulama yok)")
        ui.select_combo_data(self.app, (data or {}).get("app_name") or (relations.selected_app() if not data else ""))
        self.status = QComboBox()
        self.status.addItems(BilgeAPI.STATUSES)
        self.status.setCurrentText((data or {}).get("status", "Açık"))
        self.desc = QPlainTextEdit((data or {}).get("description") or "")
        self.desc.setMaximumHeight(100)
        form.addRow("Başlık", self.title)
        form.addRow("Bilge adresi", self.url)
        form.addRow("Uygulama", self.app)
        form.addRow("Durum", self.status)
        form.addRow("Açıklama", self.desc)
        self.error = ui.label("", wrap=True)
        form.addRow(self.error)
        b = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        b.accepted.connect(self._check)
        b.rejected.connect(self.reject)
        form.addRow(b)
        self.resize(620, 0)

    def _check(self) -> None:
        if not self.title.text().strip():
            self.error.setText("Başlık boş olamaz.")
        elif not BilgeAPI.valid_url(self.url.text()):
            self.error.setText("Adres http:// veya https:// ile başlamalı.")
        else:
            self.accept()

    def values(self) -> tuple:
        return (self.title.text(), self.url.text(), self.app.currentData() or None, self.status.currentText(),
                self.desc.toPlainText())


class Page(ui.Page):
    def __init__(self):
        super().__init__("Bilge Talepleri", "Geliştirme sırasında Bilge'de açılan taleplerin linkleri. "
                                            "Çift tıklama tarayıcıda açar.")
        self.filter_app = ui.app_combo(include_all="Tüm uygulamalar")
        self.filter_app.currentIndexChanged.connect(self.load)
        self.filter_status = QComboBox()
        self.filter_status.addItem("Tüm durumlar", None)
        for s in BilgeAPI.STATUSES:
            self.filter_status.addItem(s, s)
        self.filter_status.currentIndexChanged.connect(self.load)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Ara…")
        self.search.returnPressed.connect(self.load)
        self.add_tool(self.filter_app)
        self.add_tool(self.filter_status)
        self.add_tool(self.search)
        self.add_btn = self.add_tool(ui.button("Yeni talep", "primary", self.add))

        self.table = ui.table(["Başlık", "Uygulama", "Durum", "Ekleyen", "Tarih", "Adres"], stretch=0)
        self.table.cellDoubleClicked.connect(lambda r, _c: ui.open_url(self.rows[r]["url"]))
        self.body.addWidget(self.table, 1)
        self.desc = ui.label("", "muted", wrap=True, selectable=True)
        self.body.addWidget(self.desc)
        self.table.itemSelectionChanged.connect(self._selected)
        self.body.addLayout(ui.hbox(ui.button("Tarayıcıda aç", slot=lambda: self._with(lambda r: ui.open_url(r["url"]))),
                                    ui.button("Düzenle", slot=lambda: self._with(self.edit)),
                                    ui.button("Kapandı olarak işaretle", slot=lambda: self._with(self.close_request)),
                                    ui.button("Sil", "danger", slot=lambda: self._with(self.delete)), None))
        self.rows: list[dict] = []
        ui.on(bus.db_state_changed, self, lambda _ok: self.load())
        ui.on(bus.catalog_changed, self, self.filter_app.refill)
        ui.on(bus.data_changed, self, lambda k: k == "bilge" and self.load())
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.isVisible() and self.load())
        self._timer.start(max(15, ENV.DB_REFRESH_SEC) * 1000)

    def on_shown(self) -> None:
        self.load()

    def load(self) -> None:
        if not database.is_ready():
            self.banner.show_message("error", ui.db_unavailable_text(), "Ayarlar", lambda: relations.open_page("settings"))
            self.add_btn.setEnabled(False)
            return
        self.banner.clear()
        self.add_btn.setEnabled(True)
        run_async(BilgeAPI.list_requests, self.filter_app.currentData(), self.filter_status.currentData(),
                  self.search.text(), on_done=self._show, on_error=lambda e: self.banner.show_message("error", str(e)),
                  owner=self)

    def _show(self, rows: list[dict]) -> None:
        self.rows = rows
        ui.fill_table(self.table, [[ui.item(r["title"], data=r["id"], bold=r["status"] != "Kapandı", tooltip=r["url"]),
                                    ui.item(r["app_name"] or ""), ui.item(f"● {r['status']}", STATE.get(r["status"])),
                                    ui.item(r["created_by"]), ui.item(ago(r["created_at"]), tooltip=dt(r["created_at"])),
                                    ui.item(r["url"])] for r in rows])

    def _current(self) -> dict | None:
        rid = ui.row_data(self.table)
        return next((r for r in self.rows if r["id"] == rid), None)

    def _with(self, fn) -> None:
        r = self._current()
        if r:
            fn(r)

    def _selected(self) -> None:
        r = self._current()
        self.desc.setText(r["description"] or "" if r else "")

    def _run(self, fn, *args) -> None:
        run_async(fn, *args, on_done=lambda _r: bus.data_changed.emit("bilge"),
                  on_error=lambda e: ui.show_error(self, e), owner=self)

    def add(self) -> None:
        dlg = RequestDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self._run(BilgeAPI.add_request, *dlg.values())

    def edit(self, r: dict) -> None:
        dlg = RequestDialog(self, r)
        if dlg.exec() == QDialog.Accepted:
            self._run(BilgeAPI.update_request, r["id"], *dlg.values())

    def close_request(self, r: dict) -> None:
        self._run(BilgeAPI.update_request, r["id"], r["title"], r["url"], r["app_name"], "Kapandı", r["description"] or "")

    def delete(self, r: dict) -> None:
        if ui.confirm(self, "Talebi sil", f"“{r['title']}” listeden kaldırılsın mı? (Bilge'deki talep etkilenmez)"):
            self._run(BilgeAPI.delete_request, r["id"])

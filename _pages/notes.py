"""
Notlar -> scripts/NotesAPI.py

Ortak geliştirici notları. Genel veya bir uygulamaya (ve ortama) bağlı; Markdown önizleme;
herkes ekler ve düzenler, her değişiklik geçmişe düşer.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from _pages import _common as ui
from scripts import NotesAPI, database, relations
from scripts.apps import ENV_TITLES, ENVS
from scripts.bus import bus
from scripts.config import ENV
from scripts.fmt import ago, dt
from scripts.worker import run_async


class Page(ui.Page):
    def __init__(self):
        super().__init__("Notlar", "Ekibin ortak notları. Genel veya bir uygulamaya bağlı; Markdown desteklenir.")
        self.filter_app = ui.app_combo(include_all="Tüm notlar", include_none="Yalnızca genel")
        self.filter_app.currentIndexChanged.connect(self.load)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Notlarda ara…")
        self.search.returnPressed.connect(self.load)
        self.add_tool(self.filter_app)
        self.add_tool(self.search)
        self.add_tool(ui.button("Yeni not", "primary", self.new_note))

        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda *_: self._open(self.list.currentItem()))
        split.addWidget(self.list)

        editor = QWidget()
        el = QVBoxLayout(editor)
        el.setContentsMargins(0, 0, 0, 0)
        self.title = QLineEdit()
        self.title.setPlaceholderText("Başlık")
        self.app = ui.app_combo(include_none="(genel)")
        self.env = QComboBox()
        self.env.addItem("(tüm ortamlar)", "")
        for e in ENVS:
            self.env.addItem(ENV_TITLES[e], e)
        self.pinned = QCheckBox("Sabitle")
        el.addWidget(self.title)
        el.addLayout(ui.hbox(ui.label("Uygulama"), self.app, ui.label("Ortam"), self.env, self.pinned, None))
        self.tabs = QTabWidget()
        self.body_edit = QPlainTextEdit()
        self.body_edit.setPlaceholderText("Not (Markdown: **kalın**, `kod`, - liste, [bağlantı](https://…))")
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)
        self.tabs.addTab(self.body_edit, "Düzenle")
        self.tabs.addTab(self.preview, "Önizleme")
        self.tabs.currentChanged.connect(lambda i: i == 1 and self.preview.setMarkdown(self.body_edit.toPlainText()))
        el.addWidget(self.tabs, 1)
        self.meta = ui.label("", "muted", wrap=True)
        el.addWidget(self.meta)
        self.save_btn = ui.button("Kaydet", "primary", self.save)
        self.delete_btn = ui.button("Sil", "danger", self.delete)
        el.addLayout(ui.hbox(self.save_btn, self.delete_btn, None))
        el.addWidget(ui.label("Bu notun geçmişi", "h2"))
        self.history = ui.table(["Zaman", "Kişi", "İşlem", "Alanlar"], stretch=3)
        self.history.setMaximumHeight(150)
        el.addWidget(self.history)
        split.addWidget(editor)
        split.setSizes([380, 820])
        self.body.addWidget(split, 1)

        self.notes: list[dict] = []
        self.current_id: int | None = None
        ui.on(bus.db_state_changed, self, lambda _ok: self.load())
        ui.on(bus.catalog_changed, self, lambda: (self.filter_app.refill(), self.app.refill()))
        ui.on(bus.data_changed, self, lambda k: k == "notes" and self.load())
        ui.on(bus.app_selected, self, self._app_selected)
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.isVisible() and self.load())
        self._timer.start(max(15, ENV.DB_REFRESH_SEC) * 1000)
        self.new_note()

    def on_shown(self) -> None:
        self.load()

    def _app_selected(self, name: str) -> None:
        if not self.isVisible():
            ui.select_combo_data(self.filter_app, name)

    def _set_enabled(self, ok: bool) -> None:
        for w in (self.title, self.app, self.env, self.pinned, self.body_edit, self.save_btn, self.list):
            w.setEnabled(ok)
        self.delete_btn.setEnabled(ok and self.current_id is not None)

    def load(self) -> None:
        if not database.is_ready():
            self.banner.show_message("error", ui.db_unavailable_text(), "Ayarlar", lambda: relations.open_page("settings"))
            self._set_enabled(False)
            return
        self.banner.clear()
        self._set_enabled(True)
        run_async(NotesAPI.list_notes, self.filter_app.currentData(), self.search.text(),
                  on_done=self._show_list, on_error=lambda e: self.banner.show_message("error", str(e)), owner=self)

    def _show_list(self, notes: list[dict]) -> None:
        self.notes = notes
        self.list.blockSignals(True)
        self.list.clear()
        select = None
        for n in notes:
            pin = "📌 " if n["pinned"] else ""
            scope = n["app_name"] or "genel"
            if n["env"]:
                scope += f" · {ENV_TITLES.get(n['env'], n['env'])}"
            it = QListWidgetItem(f"{pin}{n['title']}\n{scope} · {n['updated_by'] or n['created_by']} · "
                                 f"{ago(n['updated_at'] or n['created_at'])}")
            it.setData(Qt.UserRole, n["id"])
            self.list.addItem(it)
            if n["id"] == self.current_id:
                select = it
        if select:
            self.list.setCurrentItem(select)
        self.list.blockSignals(False)

    def _open(self, it) -> None:
        if not it:
            return
        note = next((n for n in self.notes if n["id"] == it.data(Qt.UserRole)), None)
        if not note:
            return
        self.current_id = note["id"]
        self.title.setText(note["title"])
        ui.select_combo_data(self.app, note["app_name"] or "")
        ui.select_combo_data(self.env, note["env"] or "")
        self.pinned.setChecked(bool(note["pinned"]))
        self.body_edit.setPlainText(note["body"])
        self.preview.setMarkdown(note["body"])
        self.tabs.setCurrentIndex(1)
        meta = f"Yazan: {note['created_by']} · {dt(note['created_at'])}"
        if note["updated_at"]:
            meta += f"   Son düzenleyen: {note['updated_by']} · {dt(note['updated_at'])}"
        self.meta.setText(meta)
        self.delete_btn.setEnabled(True)
        run_async(database.history, entity="note", entity_id=note["id"], limit=30,
                  on_done=lambda rows: ui.fill_table(self.history, [[
                      ui.item(dt(r["at"])), ui.item(r["user_name"]),
                      ui.item({"create": "ekledi", "update": "düzenledi", "delete": "sildi"}.get(r["action"], r["action"])),
                      ui.item(", ".join((r.get("detail") or {}).get("fields") or []))] for r in rows]),
                  on_error=lambda e: None, owner=self)

    def new_note(self) -> None:
        self.current_id = None
        self.list.clearSelection()
        self.title.clear()
        self.body_edit.clear()
        self.preview.clear()
        self.pinned.setChecked(False)
        ui.select_combo_data(self.app, relations.selected_app() or "")
        ui.select_combo_data(self.env, "")
        self.meta.setText("Yeni not")
        self.history.setRowCount(0)
        self.tabs.setCurrentIndex(0)
        self.delete_btn.setEnabled(False)
        self.title.setFocus()

    def save(self) -> None:
        args = (self.title.text(), self.body_edit.toPlainText(), self.app.currentData() or None,
                self.env.currentData() or None, self.pinned.isChecked())
        note_id = self.current_id

        def work():
            if note_id is None:
                return NotesAPI.add_note(*args)
            NotesAPI.update_note(note_id, *args)
            return note_id

        def done(nid):
            self.current_id = nid
            bus.data_changed.emit("notes")
            self.preview.setMarkdown(self.body_edit.toPlainText())
            self.tabs.setCurrentIndex(1)

        run_async(work, on_done=done, on_error=lambda e: ui.show_error(self, e), owner=self)

    def delete(self) -> None:
        if self.current_id is None or not ui.confirm(self, "Notu sil", f"“{self.title.text()}” silinsin mi?"):
            return
        nid = self.current_id
        run_async(NotesAPI.delete_note, nid, on_done=lambda _r: (self.new_note(), bus.data_changed.emit("notes")),
                  on_error=lambda e: ui.show_error(self, e), owner=self)

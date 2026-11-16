"""
Sohbet -> scripts/ChatAPI.py (servis relations.Services içinde; sayfa kapalıyken de mesaj alınır)

Solda "Genel" oda ve çevrimiçi kişiler, sağda mesajlar. Mesajlar doğrudan ve uçtan uca şifreli gider;
çevrimdışı kişiye mesaj gitmez. Geçmiş yalnızca bu oturum boyunca tutulur.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem, QSplitter, QTextBrowser, QVBoxLayout, QWidget

from _pages import _common as ui
from scripts import relations, theme
from scripts.bus import bus
from scripts.config import ENV
from scripts.fmt import esc

GENERAL = "general"


class Page(ui.Page):
    def __init__(self):
        super().__init__("Sohbet", "Çevrimiçi geliştiricilerle doğrudan (P2P) ve uçtan uca şifreli sohbet. "
                                   "Mesajlar sunucuda saklanmaz; geçmiş yalnızca bu oturum boyunca tutulur.")
        self.me = ui.label("", "muted", wrap=True, selectable=True)
        self.body.addWidget(self.me)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.people = QListWidget()
        self.people.currentItemChanged.connect(lambda *_: self._room_changed())
        ll.addWidget(self.people, 1)
        self.peer_info = ui.label("", "muted", wrap=True, selectable=True)
        ll.addWidget(self.peer_info)
        self.accept_btn = ui.button("Yeni anahtarı kabul et", "danger", self._accept_key)
        self.accept_btn.hide()
        ll.addWidget(self.accept_btn)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.room_title = ui.label("Genel", "h2")
        rl.addWidget(self.room_title)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        rl.addWidget(self.view, 1)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Mesaj yazın, Enter ile gönderin")
        self.input.returnPressed.connect(self.send)
        self.send_btn = ui.button("Gönder", "primary", self.send)
        rl.addLayout(ui.hbox(self.input, self.send_btn))
        self.send_info = ui.label("", "muted")
        rl.addWidget(self.send_info)
        split.addWidget(right)
        split.setSizes([300, 900])
        self.body.addWidget(split, 1)

        self.room = GENERAL
        self._connected_chat = None
        ui.on(bus.presence_changed, self, self.refresh)
        ui.on(bus.theme_changed, self, lambda _t: self._render_messages())
        self.refresh()

    def chat(self):
        return relations.chat_service()

    def _hook(self) -> None:
        chat = self.chat()
        if chat is not None and chat is not self._connected_chat:
            chat.message.connect(self._on_message)
            chat.key_warning.connect(lambda uid, name: ui.show_error(
                self, f"{name} kişisinin şifreleme anahtarı değişti. Kişi yeni bir bilgisayara geçmiş olabilir; "
                      "ya da biri onun adına bağlanmaya çalışıyor olabilir. Parmak izini kişiyle başka bir kanaldan "
                      "teyit etmeden kabul etmeyin.", "Anahtar değişti"))
            self._connected_chat = chat

    def on_shown(self) -> None:
        self.refresh()
        chat = self.chat()
        if chat:
            chat.mark_read(self.room)
            chat.refresh()
        self.input.setFocus()

    # ---- liste
    def refresh(self) -> None:
        self._hook()
        chat = self.chat()
        if not ENV.CHAT_ENABLED:
            self.banner.show_message("warning", "Sohbet kapalı (Ayarlar → Sohbet).")
        elif chat is None or not chat.running:
            msg = (chat.error if chat and chat.error else "Sohbet için ortak PostgreSQL bağlantısı gerekir "
                   "(çevrimiçi kişiler oradan bulunur).")
            self.banner.show_message("error", msg, "Ayarlar", lambda: relations.open_page("settings"))
        elif chat.error:
            self.banner.show_message("warning", chat.error)
        else:
            self.banner.clear()
        if chat and chat.running:
            from scripts.ChatAPI import fingerprint
            self.me.setText(f"Siz: {chat.name} · dinleniyor {chat.ip}:{chat.node.port} · "
                            f"anahtar parmak izi {fingerprint(chat.node.pubkey)}")
        else:
            self.me.setText("")

        current = self.room
        self.people.blockSignals(True)
        self.people.clear()
        unread = chat.unread if chat else {}
        general = QListWidgetItem(self._label("# Genel", unread.get(GENERAL, 0)))
        general.setData(Qt.UserRole, GENERAL)
        self.people.addItem(general)
        select = general
        if chat and chat.running:
            for p in chat.node.peers():
                online = chat.node.is_online(p.user_id)
                mark = "⚠" if p.key_state == "changed" else "●" if online else "○"
                status = "" if online else "  (bağlantı kurulamadı)" if p.key_state != "changed" else "  (anahtar değişti)"
                it = QListWidgetItem(self._label(f"{mark} {p.name}{status}", unread.get(p.user_id, 0)))
                it.setData(Qt.UserRole, p.user_id)
                it.setForeground(Qt.gray if not online else it.foreground())
                self.people.addItem(it)
                if p.user_id == current:
                    select = it
        self.people.setCurrentItem(select)
        self.people.blockSignals(False)
        self._room_changed()

    @staticmethod
    def _label(text: str, unread: int) -> str:
        return f"{text}   ({unread})" if unread else text

    def _room_changed(self) -> None:
        it = self.people.currentItem()
        self.room = it.data(Qt.UserRole) if it else GENERAL
        chat = self.chat()
        self.accept_btn.hide()
        self.peer_info.setText("")
        if self.room == GENERAL:
            n = chat.node.online_count() if chat and chat.running else 0
            self.room_title.setText(f"# Genel  ·  {n} kişi çevrimiçi")
        elif chat:
            p = chat.node.peer(self.room)
            if p:
                online = chat.node.is_online(p.user_id)
                self.room_title.setText(f"{p.name}  ·  {'çevrimiçi' if online else 'bağlantı yok'}")
                self.peer_info.setText(f"{p.ip}:{p.port} · sürüm {p.app_version or '?'}\nParmak izi: {p.fingerprint}")
                if p.key_state == "changed":
                    self.peer_info.setText(self.peer_info.text() + "\n⚠ Anahtar, ilk görülenden farklı.")
                    self.accept_btn.show()
        if chat:
            chat.mark_read(self.room)
        self._render_messages()

    # ---- mesajlar
    def _render_messages(self) -> None:
        chat = self.chat()
        msgs = chat.history.get(self.room, []) if chat else []
        c = theme.palette()
        html = []
        for m in msgs[-500:]:
            when = datetime.fromtimestamp(m["ts"]).strftime("%H:%M")
            color = c["accent"] if m["mine"] else c["info"]
            html.append(f"<p style='margin:4px 0'><span style='color:{c['muted']}'>{when}</span> "
                        f"<b style='color:{color}'>{esc(m['name'])}</b>: {esc(m['text']).replace(chr(10), '<br>')}</p>")
        if not msgs:
            html.append(f"<p style='color:{c['muted']}'>Henüz mesaj yok.</p>")
        self.view.setHtml("".join(html))
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_message(self, msg: dict) -> None:
        if msg["room"] == self.room and self.isVisible():
            self.chat().mark_read(self.room)
            self._render_messages()
        else:
            self.refresh()
        if not msg["mine"] and not (self.isVisible() and self.window().isActiveWindow()):
            where = "Genel" if msg["room"] == GENERAL else "özel mesaj"
            bus.notify.emit(f"{msg['name']} ({where})", msg["text"][:120], "info")

    def send(self) -> None:
        chat = self.chat()
        text = self.input.text().strip()
        if not text or not chat or not chat.running:
            return
        if self.room == GENERAL and chat.node.online_count() == 0:
            self.send_info.setText("Şu an çevrimiçi kimse yok; mesaj gönderilmedi.")
            return
        failed = chat.send(self.room, text)
        if self.room != GENERAL and failed:
            self.send_info.setText("Kişi çevrimdışı veya bağlantı kurulamadı; mesaj gönderilmedi.")
            return
        self.send_info.setText(f"Ulaşmadı: {', '.join(failed)}" if failed else "")
        self.input.clear()

    def _accept_key(self) -> None:
        chat = self.chat()
        if chat and self.room != GENERAL and ui.confirm(
                self, "Yeni anahtar", "Parmak izini kişiyle başka bir kanaldan (telefon, yüz yüze) teyit ettiniz mi?",
                danger=True):
            chat.node.accept_new_key(self.room)
            self.refresh()

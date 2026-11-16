"""
Devrunner ana penceresi.

Çalıştırma:  Devrunner.pyw (konsolsuz, çift tıklama)  veya  python app.py (konsollu, hata ayıklama)

Yeni modül eklemek (README "Yeni modül"):
  1) scripts/XAPI.py ve _pages/x.py (içinde Page sınıfı) yazın
  2) Aşağıdaki PAGES listesine ekleyin
  3) Ayarı varsa scripts/config.py -> SETTINGS'e grup ekleyin (Ayarlar ekranı kendiliğinden oluşur)
  4) Diğer modüllerle ilişkisini scripts/relations.py'ye yazın
"""
from __future__ import annotations

import importlib
import logging
import sys
from dataclasses import dataclass

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QStackedWidget, QStatusBar, QStyleFactory, QSystemTrayIcon, QVBoxLayout,
                               QWidget, QDialog, QDialogButtonBox)

from scripts import __version__, database, logsetup, relations, theme
from scripts import apps as catalog
from scripts import user as user_mod
from _pages._common import on
from scripts.bus import bus
from scripts.config import APP_NAME, PROFILE, redact
from scripts.fmt import ago, today

log = logging.getLogger("devrunner")


@dataclass(frozen=True)
class PageDef:
    id: str
    title: str
    module: str
    glyph: str


PAGES = [
    PageDef("home", "Uygulamalar", "_pages.home", "▦"),
    PageDef("devops", "DevOps", "_pages.devops", "⟳"),
    PageDef("k8s", "Kubernetes", "_pages.k8s", "☸"),
    PageDef("health", "Erişilebilirlik", "_pages.health", "♥"),
    PageDef("terminal", "Terminal", "_pages.terminal", "❯"),
    PageDef("notes", "Notlar", "_pages.notes", "✎"),
    PageDef("bilge", "Bilge Talepleri", "_pages.bilge", "⚑"),
    PageDef("freeze", "Freeze", "_pages.freeze", "❄"),
    PageDef("chat", "Sohbet", "_pages.chat", "✉"),
    PageDef("settings", "Ayarlar", "_pages.settings", "⚙"),
]


def app_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#7c3aed"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QColor("#ffffff"))
    f = QFont("Arial", 34)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "D")
    p.end()
    return QIcon(pm)


class _ErrorRelay(QObject):
    """Herhangi bir iş parçacığındaki yakalanmamış hatayı arayüz iş parçacığında gösterir."""
    show = Signal(str)


class ErrorDialog(QDialog):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME}: beklenmeyen hata")
        self.resize(720, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Beklenmeyen bir hata oluştu. Uygulama çalışmaya devam ediyor.\n"
                             f"Ayrıntı log dosyasına yazıldı: {logsetup.LOG_FILE}"))
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        box.setProperty("role", "mono")
        lay.addWidget(box)
        btn = QDialogButtonBox(QDialogButtonBox.Close)
        btn.rejected.connect(self.reject)
        lay.addWidget(btn)


class MainWindow(QMainWindow):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.settings = QSettings()
        self.pages: dict[str, QWidget] = {}
        self._quitting = False
        self._tray_hint_shown = False
        self.setWindowTitle(APP_NAME + (f" [{PROFILE}]" if PROFILE else ""))
        self.setWindowIcon(app_icon())
        self.resize(1360, 860)

        # Yerleşim (Discord gibi): solda tam boy simge şeridi | sağda başlık + sayfa kartı
        central = QWidget()
        central.setObjectName("Central")
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_rail())
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._build_header())
        self.stack = QStackedWidget()
        self.stack.setObjectName("Stack")
        self.stack.setContentsMargins(10, 4, 12, 10)
        right.addWidget(self.stack, 1)
        outer.addLayout(right, 1)
        self.setCentralWidget(central)
        self._build_statusbar()
        self._build_tray()

        self.nav.currentRowChanged.connect(self._show_row)
        on(bus.open_page, self, self.open_page)
        on(bus.freeze_changed, self, self._update_freeze)
        on(bus.presence_changed, self, self._update_online)
        on(bus.db_state_changed, self, lambda _ok: self._update_status())
        on(bus.catalog_changed, self, self._update_status)
        on(bus.health_updated, self, self._update_status)
        on(bus.notify, self, self._notify)
        on(bus.settings_changed, self, self._on_settings)

        geometry = self.settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        self.open_page(self.settings.value("window/page", "home"))
        self._update_freeze()
        self._update_online()
        self._update_status()
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(30_000)

    # ------------------------------------------------------------------ simge şeridi
    def _build_rail(self) -> QWidget:
        rail = QFrame()
        rail.setObjectName("Rail")
        rail.setFixedWidth(76)
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(0, 10, 0, 10)
        lay.setSpacing(8)
        logo = QLabel("D")
        logo.setObjectName("RailLogo")
        logo.setToolTip(f"{APP_NAME} v{__version__}")
        lay.addWidget(logo, 0, Qt.AlignHCenter)
        line = QFrame()
        line.setObjectName("Separator")
        line.setFixedWidth(32)
        lay.addWidget(line, 0, Qt.AlignHCenter)
        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.setCursor(Qt.PointingHandCursor)
        for pd in PAGES:
            it = QListWidgetItem(pd.glyph)
            it.setData(Qt.UserRole, pd.id)
            it.setToolTip(pd.title)
            it.setTextAlignment(Qt.AlignCenter)
            it.setSizeHint(QSize(76, 54))
            self.nav.addItem(it)
        lay.addWidget(self.nav, 1)
        return rail

    # ------------------------------------------------------------------ başlık
    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        lay = QHBoxLayout(header)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(12)
        title = QLabel(APP_NAME)
        title.setObjectName("HeaderTitle")
        version = QLabel(f"v{__version__}" + (f" · profil: {PROFILE}" if PROFILE else ""))
        version.setProperty("role", "muted")
        lay.addWidget(title)
        lay.addWidget(version)
        lay.addStretch(1)

        lay.addWidget(QLabel("Tema"))
        self.theme_combo = QComboBox()
        for key, name in theme.names():
            self.theme_combo.addItem(name, key)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(theme.current())))
        self.theme_combo.currentIndexChanged.connect(self._change_theme)
        lay.addWidget(self.theme_combo)

        self.online_label = QLabel()
        self.online_label.setObjectName("HeaderOnline")
        self.online_label.setCursor(Qt.PointingHandCursor)
        self.online_label.mousePressEvent = lambda _e: self.open_page("chat")
        lay.addWidget(self.online_label)

        self.avatar = QLabel()
        self.avatar.setObjectName("Avatar")
        self.avatar.setCursor(Qt.PointingHandCursor)
        self.avatar.mousePressEvent = lambda _e: self.open_page("settings")
        lay.addWidget(self.avatar)
        self.user_label = QLabel()
        self.user_label.setObjectName("HeaderUser")
        self.user_label.setCursor(Qt.PointingHandCursor)
        self.user_label.mousePressEvent = lambda _e: self.open_page("settings")
        lay.addWidget(self.user_label)

        self.freeze_badge = QLabel()
        self.freeze_badge.setObjectName("FreezeBadge")
        self.freeze_badge.setCursor(Qt.PointingHandCursor)
        self.freeze_badge.mousePressEvent = lambda _e: self.open_page("freeze")
        self.freeze_badge.hide()
        lay.addWidget(self.freeze_badge)
        self._update_user()
        return header

    def _update_user(self) -> None:
        u = user_mod.current_user()
        self.user_label.setText(u.name)
        self.avatar.setText("".join(w[0] for w in u.name.split()[:2]).upper() or "?")
        self.avatar.setToolTip(u.name)
        self.user_label.setToolTip(f"Kullanıcı kimliği: {u.id}\nAdı değiştirmek için Ayarlar → Genel")

    def _change_theme(self) -> None:
        key = self.theme_combo.currentData()
        theme.apply(self.app, key)
        self.settings.setValue("ui/theme", key)
        self._update_freeze()
        bus.theme_changed.emit(key)

    def _update_freeze(self) -> None:
        st = relations.freeze_status()
        if st.is_active:
            self.freeze_badge.setText("❄ FREEZE")
            state = "active"
        elif st.upcoming:
            days = (st.upcoming[0].start - today()).days
            self.freeze_badge.setText(f"❄ Freeze {days} gün sonra")
            state = "upcoming"
        else:
            self.freeze_badge.hide()
            return
        self.freeze_badge.setProperty("state", state)
        self.freeze_badge.style().unpolish(self.freeze_badge)
        self.freeze_badge.style().polish(self.freeze_badge)
        self.freeze_badge.setToolTip(st.tooltip())
        self.freeze_badge.show()

    def _update_online(self) -> None:
        chat = relations.chat_service()
        if chat is None or not chat.running:
            self.online_label.setText("○ sohbet kapalı")
            self.online_label.setToolTip(chat.error if chat and chat.error else
                                         "Sohbet için PostgreSQL bağlantısı gerekir.")
            return
        n = relations.online_count()
        self.online_label.setText(f"● {n} çevrimiçi")
        self.online_label.setToolTip("\n".join(p.name for p in chat.node.peers() if chat.node.is_online(p.user_id))
                                     or "Şu an başka çevrimiçi geliştirici yok.")

    # ------------------------------------------------------------------ durum çubuğu
    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self.db_status = QLabel()
        self.devops_status = QLabel()
        self.health_status = QLabel()
        for w in (self.db_status, self.devops_status, self.health_status):
            sb.addPermanentWidget(w)
        self.setStatusBar(sb)

    def _update_status(self) -> None:
        if not database.db_configured():
            self.db_status.setText("PostgreSQL: ayarlanmamış")
        elif database.is_ready():
            self.db_status.setText("PostgreSQL: bağlı")
        else:
            self.db_status.setText("PostgreSQL: bağlantı yok")
        self.db_status.setToolTip(database.status_text())

        src = relations.catalog_source()
        err = relations.overview_error()
        if err:
            text = "DevOps: hata"
        elif src == "demo":
            text = "DevOps: örnek veri"
        elif catalog.updated_at():
            text = f"DevOps: {ago(catalog.updated_at())} eşitlendi"
        else:
            text = "DevOps: bekleniyor"
        self.devops_status.setText(text)
        self.devops_status.setToolTip(err or f"{len(relations.catalog())} uygulama")

        s = relations.health_summary()
        if s:
            parts = [f"{s.get('up', 0)} çalışıyor"]
            if s.get("slow"):
                parts.append(f"{s['slow']} yavaş")
            if s.get("down"):
                parts.append(f"{s['down']} kapalı")
            self.health_status.setText("Erişim: " + " · ".join(parts))
        else:
            self.health_status.setText("Erişim: bekleniyor")

    # ------------------------------------------------------------------ tepsi
    def _build_tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        show = QAction("Göster", self)
        show.triggered.connect(self.bring_to_front)
        quit_ = QAction("Çıkış", self)
        quit_.triggered.connect(self.quit)
        menu.addAction(show)
        menu.addSeparator()
        menu.addAction(quit_)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.bring_to_front()
                                    if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.messageClicked.connect(self.bring_to_front)
        self.tray.show()

    def _notify(self, title: str, message: str, level: str) -> None:
        self.statusBar().showMessage(f"{title}: {message}", 10_000)
        if self.tray and (not self.isActiveWindow() or self.isHidden()):
            icon = {"error": QSystemTrayIcon.Critical, "warning": QSystemTrayIcon.Warning}.get(
                level, QSystemTrayIcon.Information)
            self.tray.showMessage(title, message, icon, 8000)

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------ sayfalar
    def open_page(self, page_id: str) -> None:
        for row, pd in enumerate(PAGES):
            if pd.id == page_id:
                self.nav.setCurrentRow(row)
                return
        self.nav.setCurrentRow(0)

    def _show_row(self, row: int) -> None:
        if row < 0:
            return
        pd = PAGES[row]
        page = self.pages.get(pd.id)
        if page is None:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                module = importlib.import_module(pd.module)
                page = module.Page()
            except Exception as e:   # noqa: BLE001 - bozuk modül uygulamayı düşürmez
                log.exception("Sayfa yüklenemedi: %s", pd.module)
                from _pages._common import ErrorPage
                page = ErrorPage(pd.title, e)
            finally:
                QApplication.restoreOverrideCursor()
            self.pages[pd.id] = page
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self.settings.setValue("window/page", pd.id)
        if hasattr(page, "on_shown"):
            try:
                page.on_shown()
            except Exception:   # noqa: BLE001
                log.exception("on_shown hatası: %s", pd.id)

    def _on_settings(self, group_id: str) -> None:
        if group_id == "general":
            self._update_user()
        if group_id == "freeze":
            self._update_freeze()
        self._update_status()

    # ------------------------------------------------------------------ kapanış
    def closeEvent(self, event) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        if self.tray and not self._quitting:
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.showMessage(APP_NAME, "Arka planda çalışmaya devam ediyor (yoklama ve sohbet). "
                                                "Çıkmak için tepsi simgesine sağ tıklayın.",
                                      QSystemTrayIcon.Information, 5000)
            return
        self._quitting = True
        relations.stop_services()
        event.accept()
        self.app.quit()

    def quit(self) -> None:
        self._quitting = True
        self.close()


def ask_name(parent=None) -> bool:
    """İlk açılışta ad sorar. Vazgeçilirse False."""
    while user_mod.needs_name():
        name, ok = QInputDialog.getText(parent, f"{APP_NAME}'a hoş geldiniz",
                                        "Adınız ve soyadınız (notlarda, sohbette ve değişiklik geçmişinde görünür):",
                                        QLineEdit.Normal, user_mod.os_user())
        if not ok:
            return False
        name = name.strip()
        if name:
            relations.save_settings("general", {"USER_NAME": name[:100]})
    return True


def _single_instance(app: QApplication) -> QLocalServer | None:
    """Uygulama zaten açıksa onu öne getirir ve None döner."""
    key = f"devrunner-{user_mod.os_user()}-{PROFILE or 'default'}"
    sock = QLocalSocket()
    sock.connectToServer(key)
    if sock.waitForConnected(400):
        sock.write(b"show")
        sock.waitForBytesWritten(400)
        sock.disconnectFromServer()
        return None
    QLocalServer.removeServer(key)
    server = QLocalServer(app)
    server.listen(key)
    return server


def main(argv=None) -> int:
    logsetup.setup_logging()
    logsetup.install_excepthook()
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setApplicationName(APP_NAME + (f"-{PROFILE}" if PROFILE else ""))
    app = QApplication(argv or sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)

    server = _single_instance(app)
    if server is None:
        log.info("Devrunner zaten açık; mevcut pencere öne getirildi.")
        return 0

    theme.load_fonts()
    theme.apply(app, QSettings().value("ui/theme", theme.DEFAULT_THEME))
    user_mod.ensure_identity()
    if not ask_name():
        return 0

    relay = _ErrorRelay()
    relay.show.connect(lambda text: ErrorDialog(text, window).exec())
    logsetup.set_error_hook(relay.show.emit)

    window = MainWindow(app)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), window.bring_to_front()))
    relations.start_services(window)
    window.show()
    log.info("Devrunner %s açıldı (profil: %s)", __version__, PROFILE or "varsayılan")
    code = app.exec()
    relations.stop_services()
    return code


if __name__ == "__main__":
    sys.exit(main())

"""
Kabuk penceresi (PyQt6): QWebEngineView, gezinme kuralları, bildirim, indirme, tepsi.

Gezinme kuralı: görünüm yalnızca kendi kökenini (http://127.0.0.1:<port>) yükler. Başka her adres
(Bilge talebi, DevOps bağlantısı, uygulama adresi) sistemin varsayılan tarayıcısında açılır.
Gerekçe: uzak içerik, yerel API'ye çerezle erişebilen görünümde çalışmamalı.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QSettings, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QIcon, QKeySequence, QShortcut
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QFileDialog, QLabel, QMainWindow, QMenu, QPlainTextEdit, QPushButton, QStackedWidget,
                             QSystemTrayIcon, QVBoxLayout, QWidget)

log = logging.getLogger("shell")
APP_NAME = "Devrunner"


def same_origin(url: QUrl, origin: QUrl) -> bool:
    return url.scheme() == origin.scheme() and url.host() == origin.host() and url.port() == origin.port()


class ExternalPage(QWebEnginePage):
    """target=_blank / window.open: ilk adresi sistem tarayıcısında açar, kendisi yüklenmez."""

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
        self.deleteLater()
        return False


class AppPage(QWebEnginePage):
    def __init__(self, profile, window: "MainWindow"):
        super().__init__(profile, window)
        self.window_ = window

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        origin = self.window_.origin
        if origin is not None and same_origin(url, origin):
            return True
        if url.scheme() in ("http", "https"):
            if is_main_frame:
                QDesktopServices.openUrl(url)
            return False
        return url.scheme() in ("about", "data", "blob")

    def createWindow(self, _type):
        return ExternalPage(self.profile(), self)

    def javaScriptConsoleMessage(self, level, message, line, source):
        if self.window_.debug:
            log.info("JS %s:%s %s", source, line, message)


class StatusScreen(QWidget):
    """Açılış ve hata ekranı (web arayüzü yokken)."""
    retry = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setStyleSheet("QWidget { background: #07080d; color: #eceef5; font-size: 14px; }"
                           "QPushButton { background: #8b5cf6; color: white; border: 0; border-radius: 10px; padding: 10px 18px; font-weight: 700; }"
                           "QPlainTextEdit { background: #10121b; border: 1px solid #20233a; border-radius: 10px; font-family: Consolas, monospace; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(60, 60, 60, 60)
        self.logo = QLabel("D")
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo.setFixedSize(72, 72)
        self.logo.setStyleSheet("background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #8b5cf6, stop:1 #22d3ee);"
                                "border-radius: 18px; font-size: 34px; font-weight: 900;")
        self.title = QLabel("Devrunner başlatılıyor…")
        self.title.setStyleSheet("font-size: 22px; font-weight: 800;")
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.hide()
        self.button = QPushButton("Yeniden başlat")
        self.button.hide()
        self.button.clicked.connect(self.retry)
        lay.addStretch(1)
        lay.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.title, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.detail, 2)
        lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)

    def starting(self) -> None:
        self.title.setText("Devrunner başlatılıyor…")
        self.detail.hide()
        self.button.hide()

    def error(self, message: str, details: str) -> None:
        self.title.setText(message)
        self.detail.setPlainText(details)
        self.detail.show()
        self.button.show()


class MainWindow(QMainWindow):
    def __init__(self, profile_name: str, storage: str, icon: QIcon, debug: bool = False):
        super().__init__()
        self.debug = debug
        self.origin: QUrl | None = None
        self.quitting = False
        self._tray_hint = False
        self.settings = QSettings(APP_NAME, f"shell-{profile_name or 'default'}")
        self.setWindowTitle(APP_NAME + (f" [{profile_name}]" if profile_name else ""))
        self.setWindowIcon(icon)
        self.resize(1440, 900)

        # Profil uygulamaya bağlı: sayfalardan SONRA silinmeli (önce silinirse Qt uyarır, veri kaybı olabilir)
        from PyQt6.QtWidgets import QApplication
        self.profile = QWebEngineProfile(f"devrunner-{profile_name or 'default'}", QApplication.instance())
        self.profile.setPersistentStoragePath(storage)
        self.profile.setCachePath(storage + "/cache")
        self.profile.setHttpAcceptLanguage("tr-TR,tr;q=0.9,en;q=0.5")
        self.profile.downloadRequested.connect(self._download)
        self.profile.setNotificationPresenter(self._notification)

        self.view = QWebEngineView(self)
        self.page = AppPage(self.profile, self)
        # Titreme önleme: sayfa yüklenirken varsayılan beyaz zemin görünmesin; zemin temayı izler
        self.page.setBackgroundColor(QColor("#07080d"))
        self.page.loadFinished.connect(self._sync_background)
        self.page.titleChanged.connect(lambda _t: self._sync_background(True))
        # Pano: terminalde kopyala / yapıştır, "Bağlantıyı kopyala" (yalnızca kendi kökenimiz yüklenebilir)
        s = self.page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanPaste, True)
        self.view.setPage(self.page)
        self._connect_permissions()
        self.page.titleChanged.connect(lambda t: self.setWindowTitle(t or APP_NAME))
        self.status = StatusScreen()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.status)
        self.stack.addWidget(self.view)
        self.setCentralWidget(self.stack)
        self._build_tray(icon)
        self._shortcuts()
        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        zoom = self.settings.value("zoom")
        if zoom:
            self.view.setZoomFactor(float(zoom))

    # ---- izinler: yalnızca kendi kökenimiz için bildirim
    def _connect_permissions(self) -> None:
        if hasattr(self.page, "permissionRequested"):           # Qt 6.8+
            self.page.permissionRequested.connect(self._permission)
        else:                                                     # eski API
            self.page.featurePermissionRequested.connect(self._feature_permission)

    def _permission(self, permission) -> None:
        from PyQt6.QtWebEngineCore import QWebEnginePermission
        allowed = {QWebEnginePermission.PermissionType.Notifications}
        clip = getattr(QWebEnginePermission.PermissionType, "ClipboardReadWrite", None)
        if clip is not None:
            allowed.add(clip)
        ok = (self.origin is not None and same_origin(permission.origin(), self.origin)
              and permission.permissionType() in allowed)
        permission.grant() if ok else permission.deny()

    def _sync_background(self, _ok=True) -> None:
        """Sayfa zeminini temanın --bg rengine eşitle (bir sonraki sayfa geçişinde beyaz parlama olmasın)."""
        def apply(value):
            color = QColor(str(value or "").strip())
            if color.isValid():
                self.page.setBackgroundColor(color)
        self.page.runJavaScript("getComputedStyle(document.documentElement).getPropertyValue('--bg')", 0, apply)

    def _feature_permission(self, origin, feature) -> None:
        ok = (self.origin is not None and same_origin(origin, self.origin)
              and feature == QWebEnginePage.Feature.Notifications)
        policy = (QWebEnginePage.PermissionPolicy.PermissionGrantedByUser if ok
                  else QWebEnginePage.PermissionPolicy.PermissionDeniedByUser)
        self.page.setFeaturePermission(origin, feature, policy)

    def _notification(self, notification) -> None:
        if self.tray:
            self.tray.showMessage(notification.title() or APP_NAME, notification.message(),
                                  QSystemTrayIcon.MessageIcon.Information, 8000)
        notification.show()

    # ---- indirme (log, yer imi dışa aktarma)
    def _download(self, download) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Kaydet", download.downloadFileName())
        if not path:
            download.cancel()
            return
        import os
        download.setDownloadDirectory(os.path.dirname(path))
        download.setDownloadFileName(os.path.basename(path))
        download.accept()

    # ---- tepsi
    def _build_tray(self, icon: QIcon) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        show = QAction("Göster", self)
        show.triggered.connect(self.bring_to_front)
        browser = QAction("Tarayıcıda aç", self)
        browser.triggered.connect(self.open_in_browser)
        quit_ = QAction("Çıkış", self)
        quit_.triggered.connect(self.quit)
        menu.addAction(show)
        menu.addAction(browser)
        menu.addSeparator()
        menu.addAction(quit_)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.bring_to_front() if reason in (
            QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.messageClicked.connect(self.bring_to_front)
        self.tray.show()

    def _shortcuts(self) -> None:
        def zoom(delta):
            f = 1.0 if delta == 0 else max(0.5, min(2.5, self.view.zoomFactor() + delta))
            self.view.setZoomFactor(f)
            self.settings.setValue("zoom", f)

        for seq, fn in (("Ctrl++", lambda: zoom(0.1)), ("Ctrl+=", lambda: zoom(0.1)), ("Ctrl+-", lambda: zoom(-0.1)),
                        ("Ctrl+0", lambda: zoom(0)), ("F5", self.view.reload)):
            QShortcut(QKeySequence(seq), self, activated=fn)
        if self.debug:
            QShortcut(QKeySequence("F12"), self, activated=self._devtools)

    def _devtools(self) -> None:
        dev = QWebEngineView()
        dev.setWindowTitle("Devrunner — geliştirici araçları")
        dev.resize(QSize(1100, 700))
        self.page.setDevToolsPage(dev.page())
        dev.show()
        self._dev = dev

    # ---- içerik
    def open_in_browser(self) -> None:
        """Aynı oturumu sistem tarayıcısında aç (yalnızca 127.0.0.1; belirteç tek açılışlıktır)."""
        if self.origin is not None and getattr(self, "_token", ""):
            QDesktopServices.openUrl(QUrl(f"{self.origin.toString()}/auth?t={self._token}"))

    def load(self, base_url: str, token: str) -> None:
        self._token = token
        self.origin = QUrl(base_url)
        self.stack.setCurrentWidget(self.view)
        self.view.load(QUrl(f"{base_url}/auth?t={token}"))

    def show_starting(self) -> None:
        self.status.starting()
        self.stack.setCurrentWidget(self.status)

    def show_error(self, message: str, details: str) -> None:
        self.status.error(message, details)
        self.stack.setCurrentWidget(self.status)
        self.bring_to_front()

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        if self.tray and not self.quitting:
            event.ignore()
            self.hide()
            if not self._tray_hint:
                self._tray_hint = True
                self.tray.showMessage(APP_NAME, "Arka planda çalışmaya devam ediyor (yoklama ve sohbet). "
                                                "Çıkmak için tepsi simgesine sağ tıklayın.",
                                      QSystemTrayIcon.MessageIcon.Information, 5000)
            return
        event.accept()

    def quit(self) -> None:
        self.quitting = True
        self.close()
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()

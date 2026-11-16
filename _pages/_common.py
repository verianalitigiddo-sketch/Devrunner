"""Sayfaların ortak arayüz parçaları. Sayfa yazarken buradan alın; renkleri koda gömmeyin (theme.color)."""
from __future__ import annotations

import traceback

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from scripts import theme
from scripts.config import redact


def on(signal, owner: QWidget, fn) -> None:
    """
    Global olaya (scripts.bus) bağlanır; owner silinince çağrılmaz ve bağlantı kopar.
    Sayfalarda bus.X.connect(lambda ...) yerine bunu kullanın: ui.on(bus.X, self, fn)
    """
    import shiboken6

    def slot(*args):
        if shiboken6.isValid(owner):
            fn(*args)

    signal.connect(slot)

    def drop(*_):
        try:
            signal.disconnect(slot)
        except (RuntimeError, TypeError):
            pass

    owner.destroyed.connect(drop)


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def set_prop(w: QWidget, name: str, value) -> None:
    w.setProperty(name, value)
    repolish(w)


def label(text: str = "", role: str = "", wrap: bool = False, selectable: bool = False) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    lbl.setWordWrap(wrap)
    if selectable:
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lbl


def pill(text: str, state: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("pill", theme.STATE_ROLE.get(state, "missing") if state not in
                    ("success", "warning", "danger", "info", "missing") else state)
    return lbl


def button(text: str, kind: str = "", slot=None, tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if slot:
        b.clicked.connect(slot)
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0), stretch_at: int | None = None) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for i, w in enumerate(widgets):
        if stretch_at is not None and i == stretch_at:
            lay.addStretch(1)
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return lay


def separator() -> QFrame:
    f = QFrame()
    f.setObjectName("Separator")
    return f


def open_url(url: str) -> None:
    if url and url.lower().startswith(("http://", "https://")):
        QDesktopServices.openUrl(QUrl(url))


class Banner(QFrame):
    """Sayfa üstünde bilgi şeridi. level: info | warning | error | demo"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.action = QPushButton()
        self.action.setVisible(False)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.action)
        self._slot = None
        self.hide()

    def show_message(self, level: str, text: str, action: str = "", slot=None) -> None:
        set_prop(self, "level", level)
        self.text.setText(text)
        if self._slot:
            self.action.clicked.disconnect(self._slot)
            self._slot = None
        if action and slot:
            self.action.setText(action)
            self.action.clicked.connect(slot)
            self._slot = slot
        self.action.setVisible(bool(action and slot))
        self.show()

    def clear(self) -> None:
        self.hide()


class Page(QWidget):
    """
    Sayfa iskeleti: başlık + açıklama + sağda araç düğmeleri + bilgi şeridi + gövde (self.body).

        class Page(_common.Page):
            def __init__(self):
                super().__init__("Notlar", "Ortak geliştirici notları")
                self.body.addWidget(...)

    on_shown(): sayfa her öne geldiğinde çağrılır.
    """

    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        self.setObjectName("PageRoot")
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label(title, "h1"))
        self.subtitle = label(subtitle, "muted", wrap=True)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.tools = QHBoxLayout()
        self.tools.setSpacing(6)
        head.addLayout(self.tools)
        root.addLayout(head)
        self.banner = Banner()
        root.addWidget(self.banner)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        root.addLayout(self.body, 1)

    def add_tool(self, w: QWidget) -> QWidget:
        self.tools.addWidget(w)
        return w

    def on_shown(self) -> None:
        pass


class ErrorPage(Page):
    """İçe aktarma / kurulum hatası veren sayfa yerine gösterilir; uygulamanın kalanı çalışır."""

    def __init__(self, title: str, error: BaseException):
        super().__init__(title, "Bu modül yüklenemedi. Diğer modüller çalışmaya devam ediyor.")
        self.banner.show_message("error", f"{type(error).__name__}: {redact(str(error))}")
        box = QPlainTextEdit(redact("".join(traceback.format_exception(error))))
        box.setReadOnly(True)
        box.setProperty("role", "mono")
        self.body.addWidget(box)


def table(headers: list[str], stretch: int | None = None) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.setShowGrid(False)
    t.setWordWrap(False)
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    h.setHighlightSections(False)
    if stretch is not None:
        h.setSectionResizeMode(stretch, QHeaderView.Stretch)
    else:
        h.setStretchLastSection(True)
    t.verticalHeader().setDefaultSectionSize(28)
    return t


def item(text, state: str | None = None, data=None, tooltip: str = "", bold: bool = False,
         align=None) -> QTableWidgetItem:
    it = QTableWidgetItem("" if text is None else str(text))
    if state:
        it.setForeground(QColor(theme.state_color(state)))
    if data is not None:
        it.setData(Qt.UserRole, data)
    if tooltip:
        it.setToolTip(tooltip)
    if bold:
        f = QFont()
        f.setBold(True)
        it.setFont(f)
    if align is not None:
        it.setTextAlignment(align)
    return it


def fill_table(t: QTableWidget, rows: list[list[QTableWidgetItem]], keep_selection_key: int = 0) -> None:
    """Satırları yeniden doldurur; seçili satırın UserRole verisini korur."""
    selected = None
    cur = t.currentRow()
    if cur >= 0 and t.item(cur, keep_selection_key):
        selected = t.item(cur, keep_selection_key).data(Qt.UserRole)
    t.setSortingEnabled(False)
    t.setRowCount(len(rows))
    reselect = -1
    for r, cells in enumerate(rows):
        for c, it in enumerate(cells):
            t.setItem(r, c, it)
        key_item = cells[keep_selection_key] if len(cells) > keep_selection_key else None
        if selected is not None and key_item is not None and key_item.data(Qt.UserRole) == selected:
            reselect = r
    if reselect >= 0:
        t.blockSignals(True)
        t.selectRow(reselect)
        t.blockSignals(False)


def row_data(t: QTableWidget, column: int = 0):
    r = t.currentRow()
    if r < 0 or not t.item(r, column):
        return None
    return t.item(r, column).data(Qt.UserRole)


def app_combo(include_all: str = "", include_none: str = "") -> QComboBox:
    """Uygulama seçici. Öğe verisi: uygulama adı; '' = tümü / genel."""
    from scripts import relations
    cb = QComboBox()
    cb.setMinimumWidth(180)

    def refill():
        current = cb.currentData()
        cb.blockSignals(True)
        cb.clear()
        if include_all:
            cb.addItem(include_all, None)
        if include_none:
            cb.addItem(include_none, "")
        for a in relations.catalog():
            cb.addItem(a.name, a.name)
        idx = cb.findData(current)
        cb.setCurrentIndex(idx if idx >= 0 else 0)
        cb.blockSignals(False)

    cb.refill = refill
    refill()
    return cb


def select_combo_data(cb: QComboBox, value) -> bool:
    idx = cb.findData(value)
    if idx >= 0:
        cb.setCurrentIndex(idx)
        return True
    return False


def show_error(parent: QWidget, error: BaseException | str, title: str = "Hata") -> None:
    text = error if isinstance(error, str) else (str(error) or type(error).__name__)
    QMessageBox.warning(parent, title, redact(text))


def confirm(parent: QWidget, title: str, text: str, danger: bool = False) -> bool:
    box = QMessageBox(QMessageBox.Warning if danger else QMessageBox.Question, title, text,
                      QMessageBox.Yes | QMessageBox.No, parent)
    box.setDefaultButton(QMessageBox.No)
    box.button(QMessageBox.Yes).setText("Evet")
    box.button(QMessageBox.No).setText("Vazgeç")
    return box.exec() == QMessageBox.Yes


def db_unavailable_text() -> str:
    from scripts import database
    return database.status_text() + "\nBu sayfa ortak PostgreSQL gerektirir; diğer modüller çalışmaya devam eder."


def expanding(w: QWidget) -> QWidget:
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return w

"""
Ayarlar -> scripts/config.py

Ekran config.SETTINGS listesinden kendiliğinden oluşur: yeni ayar eklemek için yalnızca config.py'ye
Setting ekleyin. Kaydet -> .env'e yazılır, relations.save_settings ilgili modülü yeniden kurar;
yeniden başlatma gerekmez.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from _pages import _common as ui
from scripts import config, relations
from scripts.config import ENV_FILE, SETTINGS, Group
from scripts.worker import run_async


class GroupForm(QWidget):
    def __init__(self, group: Group):
        super().__init__()
        self.group = group
        self.fields: dict[str, QWidget] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(ui.label(group.title, "h2"))
        if group.description:
            lay.addWidget(ui.label(group.description, "muted", wrap=True))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        for s in group.settings:
            field = self._make_field(s)
            self.fields[s.key] = field
            row = QVBoxLayout()
            row.setSpacing(2)
            row.addWidget(field)
            hints = []
            if s.help:
                hints.append(s.help)
            if config.is_from_os(s.key):
                hints.append("Bu değer işletim sistemi ortam değişkeninden geliyor; .env'e yazılan değeri ezer.")
            if hints:
                row.addWidget(ui.label(" ".join(hints), "muted", wrap=True))
            form.addRow(s.label, row)
        lay.addLayout(form)

        self.result = ui.label("", wrap=True, selectable=True)
        self.result.hide()
        lay.addWidget(self.result)
        buttons = [ui.button("Kaydet", "primary", self.save)]
        if group.testable:
            self.test_btn = ui.button("Bağlantıyı sına", slot=self.test)
            buttons.append(self.test_btn)
        buttons.append(ui.button("Geri al", slot=self.load))
        lay.addLayout(ui.hbox(*buttons, None))
        lay.addStretch(1)
        self.load()

    def _make_field(self, s) -> QWidget:
        if s.kind == "bool":
            w = QCheckBox()
        elif s.kind == "choice":
            w = QComboBox()
            w.addItems(list(s.choices))
        elif s.secret:
            w = QWidget()
            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.Password)
            eye = QToolButton()
            eye.setText("göster")
            eye.setCheckable(True)
            eye.toggled.connect(lambda on, e=edit, b=eye: (e.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password),
                                                           b.setText("gizle" if on else "göster")))
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(edit, 1)
            h.addWidget(eye)
            w.edit = edit
        else:
            w = QLineEdit()
            if s.default:
                w.setPlaceholderText(s.default)
            if s.readonly:
                w.setReadOnly(True)
        w.setMinimumWidth(420)
        return w

    def _get(self, key: str) -> str:
        w = self.fields[key]
        if isinstance(w, QCheckBox):
            return "1" if w.isChecked() else "0"
        if isinstance(w, QComboBox):
            return w.currentText()
        if hasattr(w, "edit"):
            return w.edit.text()
        return w.text()

    def _set(self, key: str, value: str) -> None:
        w = self.fields[key]
        if isinstance(w, QCheckBox):
            w.setChecked(value.lower() in config.TRUE_WORDS)
        elif isinstance(w, QComboBox):
            w.setCurrentText(value)
        elif hasattr(w, "edit"):
            w.edit.setText(value)
        else:
            w.setText(value)

    def load(self) -> None:
        for s in self.group.settings:
            self._set(s.key, config.get(s.key))
        self.result.hide()

    def values(self) -> dict[str, str]:
        return {s.key: self._get(s.key) for s in self.group.settings if not s.readonly}

    def _show(self, ok: bool, text: str) -> None:
        self.result.setText(("✔ " if ok else "✖ ") + text)
        self.result.setProperty("role", "" if ok else "muted")
        self.result.setStyleSheet(f"color: {ui.theme.color('success' if ok else 'danger')};")
        self.result.show()

    def save(self) -> None:
        try:
            changed = relations.save_settings(self.group.id, self.values())
        except ValueError as e:
            self._show(False, str(e))
            return
        if changed:
            self._show(True, f"Kaydedildi ({ENV_FILE.name}): " + ", ".join(config.setting(k).label for k in changed))
        else:
            self._show(True, "Değişiklik yok.")

    def test(self) -> None:
        # Kaydedilmemiş değerlerle sınamak kafa karıştırır: önce kaydet
        try:
            relations.save_settings(self.group.id, self.values())
        except ValueError as e:
            self._show(False, str(e))
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Sınanıyor…")

        def done(result):
            self.test_btn.setEnabled(True)
            self.test_btn.setText("Bağlantıyı sına")
            self._show(*result)

        def failed(e):
            done((False, str(e)))

        run_async(relations.test_connection, self.group.id, on_done=done, on_error=failed, owner=self)


class Page(ui.Page):
    def __init__(self):
        super().__init__("Ayarlar", f"Değerler {ENV_FILE} dosyasına yazılır (depoya girmez). "
                                    "Kaydedilen ayar uygulama yeniden başlatılmadan geçerli olur.")
        self.groups = QListWidget()
        self.groups.setFixedWidth(200)
        self.stack = QStackedWidget()
        self.forms: dict[str, GroupForm] = {}
        for g in SETTINGS:
            it = QListWidgetItem(g.title)
            it.setData(Qt.UserRole, g.id)
            self.groups.addItem(it)
            form = GroupForm(g)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(form)
            self.forms[g.id] = form
            self.stack.addWidget(scroll)
        self.groups.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.groups.setCurrentRow(0)
        row = QHBoxLayout()
        row.addWidget(self.groups)
        row.addWidget(self.stack, 1)
        self.body.addLayout(row)

    def show_group(self, group_id: str) -> None:
        for i in range(self.groups.count()):
            if self.groups.item(i).data(Qt.UserRole) == group_id:
                self.groups.setCurrentRow(i)

    def on_shown(self) -> None:
        for f in self.forms.values():
            f.load()

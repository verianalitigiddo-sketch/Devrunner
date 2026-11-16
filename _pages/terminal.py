"""
Terminal -> scripts/TerminalAPI.py

nslookup, curl, ping, kubectl gibi teşhis komutları. Hangi kabuğun (ad, sürüm, dosya yolu) çalıştığı
her zaman görünür ve her çıktı bloğunun başlığında yazar. Her komut ayrı süreçte çalışır; Durdur
süreci alt süreçleriyle birlikte sonlandırır. Komut geçmişi yalnızca yereldir.
"""
from __future__ import annotations

import codecs
import os
import time

from PySide6.QtCore import QEvent, QProcess, QProcessEnvironment, QSettings, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QPlainTextEdit, QWidget

from _pages import _common as ui
from scripts import TerminalAPI, relations
from scripts.apps import ENV_TITLES, ENVS
from scripts.bus import bus
from scripts.config import APP_ROOT
from scripts.worker import run_async

MAX_HISTORY = 200


class Page(ui.Page):
    def __init__(self):
        super().__init__("Terminal", "Teşhis komutları (nslookup, curl, ping, kubectl). Her komut ayrı süreçte çalışır; "
                                     "etkileşimli programlar (vim, parametresiz nslookup, ssh) desteklenmez.")
        self.shells: list[TerminalAPI.Shell] = []
        self.shell_combo = QComboBox()
        self.shell_combo.currentIndexChanged.connect(self._shell_changed)
        self.badge = ui.label("kabuklar aranıyor…")
        self.badge.setObjectName("ShellBadge")
        self.badge.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.kube = QComboBox()
        self.kube.setToolTip("kubectl için KUBECONFIG; .local/kube/<ortam>.yaml (Ayarlar → Kubernetes'ten üretilir)")
        self.kube.currentIndexChanged.connect(self._fill_quick)
        self.cwd = str(QSettings().value("terminal/cwd", str(APP_ROOT)))
        if not os.path.isdir(self.cwd):
            self.cwd = str(APP_ROOT)
        self.cwd_label = ui.label("", "muted")
        self.body.addLayout(ui.hbox(ui.label("Kabuk"), self.shell_combo, self.badge, None,
                                    ui.label("kubectl ortamı"), self.kube))
        self.body.addLayout(ui.hbox(ui.label("Dizin"), self.cwd_label, ui.button("Değiştir", "link", self._pick_dir), None))

        self.app = ui.app_combo(include_none="(uygulama seçin)")
        self.app.currentIndexChanged.connect(self._fill_quick)
        self.env = QComboBox()
        for e in ENVS:
            self.env.addItem(ENV_TITLES[e], e)
        self.env.currentIndexChanged.connect(self._fill_quick)
        self.quick_box = QHBoxLayout()
        self.quick_box.setSpacing(6)
        quick = QWidget()
        quick.setLayout(self.quick_box)
        self.body.addLayout(ui.hbox(ui.label("Hazır komutlar"), self.app, self.env, quick, None))

        self.console = QPlainTextEdit()
        self.console.setObjectName("Console")
        self.console.setReadOnly(True)
        self.console.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.console.setMaximumBlockCount(20000)
        self.body.addWidget(self.console, 1)

        self.hint = ui.label("", wrap=True)
        self.hint.hide()
        self.body.addWidget(self.hint)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Komut yazın ve Enter'a basın (↑/↓ geçmiş)")
        self.input.returnPressed.connect(self.run)
        self.input.textChanged.connect(self._update_hint)
        self.input.installEventFilter(self)
        self.input.setProperty("role", "mono")
        self.run_btn = ui.button("Çalıştır", "primary", self.run)
        self.stop_btn = ui.button("Durdur", "danger", self.stop)
        self.stop_btn.setEnabled(False)
        self.body.addLayout(ui.hbox(self.prompt_label(), self.input, self.run_btn, self.stop_btn,
                                    ui.button("Temizle", slot=self.console.clear)))

        self.proc: QProcess | None = None
        self._decoder = None
        self._started = 0.0
        self._pending: tuple[str, str] | None = None
        hist = QSettings().value("terminal/history", [])
        self.history: list[str] = list(hist) if isinstance(hist, list) else []
        self._hist_pos = len(self.history)

        ui.on(bus.run_command, self, self.run_external)
        ui.on(bus.app_selected, self, lambda n: ui.select_combo_data(self.app, n))
        ui.on(bus.catalog_changed, self, self.app.refill)
        ui.on(bus.settings_changed, self, lambda g: g == "k8s" and self._fill_kube())
        self._fill_kube()
        self._update_cwd()
        if relations.selected_app():
            ui.select_combo_data(self.app, relations.selected_app())
        run_async(TerminalAPI.detect_shells, on_done=self._shells_found, owner=self)

    def prompt_label(self):
        self.prompt = ui.label("❯", "mono")
        return self.prompt

    # ---- kabuklar
    def _shells_found(self, shells: list[TerminalAPI.Shell]) -> None:
        self.shells = shells
        self.shell_combo.blockSignals(True)
        for s in shells:
            self.shell_combo.addItem(s.title, s.id)
        default = TerminalAPI.default_shell(shells)
        saved = QSettings().value("terminal/shell", default.id if default else "")
        if not ui.select_combo_data(self.shell_combo, saved) and default:
            ui.select_combo_data(self.shell_combo, default.id)
        self.shell_combo.blockSignals(False)
        self._shell_changed()
        if not shells:
            self.badge.setText("Kabuk bulunamadı")
        if self._pending:
            cmd, env = self._pending
            self._pending = None
            self.run_external(cmd, env)

    def shell(self) -> TerminalAPI.Shell | None:
        sid = self.shell_combo.currentData()
        return next((s for s in self.shells if s.id == sid), None)

    def _shell_changed(self) -> None:
        s = self.shell()
        if not s:
            return
        QSettings().setValue("terminal/shell", s.id)
        self.badge.setText(f"Etkin kabuk: {s.title}  —  {s.path}")
        self.prompt.setText("PS ❯" if s.is_powershell else ">" if s.id == "cmd" else "$")
        self._fill_quick()
        self._update_hint()

    def _fill_kube(self) -> None:
        cur = self.kube.currentData()
        self.kube.blockSignals(True)
        self.kube.clear()
        self.kube.addItem("(yok)", "")
        for e in relations.kubectl_envs():
            self.kube.addItem(ENV_TITLES[e], e)
        ui.select_combo_data(self.kube, cur)
        self.kube.blockSignals(False)

    def _fill_quick(self) -> None:
        while self.quick_box.count():
            w = self.quick_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        name, env = self.app.currentData(), self.env.currentData()
        if not name:
            return
        for label, cmd in relations.quick_commands(name, env, self.shell()):
            b = ui.button(label, slot=lambda _=False, c=cmd: self._prefill(c), tooltip=cmd)
            self.quick_box.addWidget(b)

    def _prefill(self, cmd: str) -> None:
        if "kubectl" in cmd and not self.kube.currentData():
            ui.select_combo_data(self.kube, self.env.currentData())
        self.input.setText(cmd)
        self.input.setFocus()

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Çalışma dizini", self.cwd)
        if d:
            self.cwd = d
            self._update_cwd()

    def _update_cwd(self) -> None:
        self.cwd_label.setText(self.cwd)
        QSettings().setValue("terminal/cwd", self.cwd)

    def _update_hint(self) -> None:
        s = self.shell()
        text = TerminalAPI.hint(s, self.input.text()) if s else ""
        self.hint.setText("⚠ " + text if text else "")
        self.hint.setStyleSheet(f"color: {ui.theme.color('warning')};")
        self.hint.setVisible(bool(text))

    # ---- geçmiş
    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.KeyPress and self.history:
            if event.key() == Qt.Key_Up:
                self._hist_pos = max(0, self._hist_pos - 1)
                self.input.setText(self.history[self._hist_pos])
                return True
            if event.key() == Qt.Key_Down:
                self._hist_pos = min(len(self.history), self._hist_pos + 1)
                self.input.setText(self.history[self._hist_pos] if self._hist_pos < len(self.history) else "")
                return True
        return super().eventFilter(obj, event)

    def _remember(self, cmd: str) -> None:
        if not self.history or self.history[-1] != cmd:
            self.history.append(cmd)
            self.history = self.history[-MAX_HISTORY:]
            QSettings().setValue("terminal/history", self.history)
        self._hist_pos = len(self.history)

    # ---- çalıştırma
    def _write(self, text: str) -> None:
        self.console.moveCursor(QTextCursor.End)
        self.console.insertPlainText(text)
        self.console.moveCursor(QTextCursor.End)

    def run_external(self, cmd: str, kube_env: str) -> None:
        if not self.shells:
            self._pending = (cmd, kube_env)
            return
        if kube_env:
            ui.select_combo_data(self.kube, kube_env)
        self.input.setText(cmd)
        if self.proc is None:
            self.run()

    def run(self) -> None:
        cmd = self.input.text().strip()
        s = self.shell()
        if not cmd or not s or self.proc is not None:
            return
        self._remember(cmd)
        self.input.clear()
        if cmd.lower() in ("cls", "clear"):
            self.console.clear()
            return
        kube = self.kube.currentData()
        header = f"\n── {s.title} · {self.cwd}" + (f" · KUBECONFIG={ENV_TITLES[kube]}" if kube else "") + \
                 f"\n{self.prompt.text()} {cmd}\n"
        try:
            new_dir = TerminalAPI.parse_cd(cmd, self.cwd)
        except ValueError as e:
            self._write(header + f"{e}\n")
            return
        if new_dir is not None:
            self.cwd = new_dir
            self._update_cwd()
            self._write(header + f"{new_dir}\n")
            return

        program, args, native = TerminalAPI.build_command(s, cmd)
        proc = QProcess(self)
        proc.setProgram(program)
        proc.setArguments(args)
        if native is not None and hasattr(proc, "setNativeArguments"):
            proc.setNativeArguments(native)
        proc.setWorkingDirectory(self.cwd)
        penv = QProcessEnvironment()
        for k, v in TerminalAPI.process_env(kube).items():
            penv.insert(k, v)
        proc.setProcessEnvironment(penv)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._read)
        proc.finished.connect(self._finished)
        proc.errorOccurred.connect(self._error)
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._write(header)
        self.proc = proc
        self._started = time.monotonic()
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        proc.start()

    def _read(self) -> None:
        if self.proc:
            data = bytes(self.proc.readAllStandardOutput())
            self._write(self._decoder.decode(data).replace("\r\n", "\n"))

    def _finished(self, code: int, status) -> None:
        if not self.proc:
            return
        self._read()
        took = time.monotonic() - self._started
        how = "durduruldu" if status == QProcess.CrashExit else f"çıkış kodu {code}"
        self._write(f"\n[{how} · {took:.1f} sn]\n")
        self._done()

    def _error(self, err) -> None:
        if err == QProcess.FailedToStart and self.proc:
            self._write(f"[başlatılamadı: {self.proc.errorString()}]\n")
            self._done()

    def _done(self) -> None:
        if self.proc:
            self.proc.deleteLater()
        self.proc = None
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.input.setFocus()

    def stop(self) -> None:
        if self.proc:
            TerminalAPI.kill_tree(int(self.proc.processId()))
            self.proc.kill()

    def on_shown(self) -> None:
        self._fill_kube()
        self.input.setFocus()

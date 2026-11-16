"""
Log ve yakalanmamış hata yönetimi.

pythonw altında konsol yoktur (sys.stdout / sys.stderr None olur). Bu yüzden log
.local/logs/devrunner.log dosyasına döner ve yakalanmamış hatalar kullanıcıya pencereyle gösterilir.
Log'a yazılan her şey önce config.redact()'ten geçer: secret değerler dosyaya düşmez.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
import traceback

from scripts.config import LOCAL_DIR, redact

LOG_FILE = LOCAL_DIR / "logs" / "devrunner.log"
_error_hook = None   # app.py ayarlar: hata penceresi gösteren fonksiyon


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True


def setup_logging(level=logging.INFO) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.addFilter(RedactFilter())
    root.addHandler(file_handler)
    if sys.stderr is not None:   # python app.py ile açıldıysa konsola da yaz
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        stream.addFilter(RedactFilter())
        root.addHandler(stream)
    for noisy in ("urllib3", "kubernetes"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def set_error_hook(fn) -> None:
    global _error_hook
    _error_hook = fn


def _excepthook(exc_type, exc, tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    text = redact("".join(traceback.format_exception(exc_type, exc, tb)))
    logging.getLogger("devrunner").error("Yakalanmamış hata:\n%s", text)
    if _error_hook:
        try:
            _error_hook(text)
        except Exception:   # noqa: BLE001 - hata penceresi açılamazsa log yeterli
            pass


def install_excepthook() -> None:
    sys.excepthook = _excepthook
    threading.excepthook = lambda a: _excepthook(a.exc_type, a.exc_value, a.exc_traceback)

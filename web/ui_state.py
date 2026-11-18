"""
Web katmanına özgü kişisel arayüz durumu: .local/ui.json (masaüstündeki QSettings'in karşılığı).

    ui_state.get("theme", "modern")
    ui_state.update(theme="dracula")

.env'e yazılmaz (backend dondurulmuştur; config.SETTINGS'e ayar eklenemez). Secret içermez.
"""
from __future__ import annotations

import json
import threading

from scripts import config

_lock = threading.Lock()


def _path():
    return config.LOCAL_DIR / "ui.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get(key: str, default=None):
    with _lock:
        return load().get(key, default)


def update(**values) -> dict:
    with _lock:
        data = load()
        data.update(values)
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return data

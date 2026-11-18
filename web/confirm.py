"""
Tek kullanımlık sunucu onayı (Prod işlemleri). Arayüzdeki onay penceresine güvenilmez: sunucu önce onay
belirteci üretir; belirteç işleme (ör. ortam, pod, container, komut) bağlıdır, 2 dk geçerlidir ve bir kez
kullanılır. Belirteçsiz veya uyuşmayan istek reddedilir.
"""
from __future__ import annotations

import secrets
import threading
import time

TTL = 120
_tokens: dict[str, tuple] = {}
_lock = threading.Lock()


def issue(key: tuple) -> str:
    token = secrets.token_urlsafe(16)
    now = time.time()
    with _lock:
        for k in [k for k, v in _tokens.items() if v[1] < now]:
            del _tokens[k]
        _tokens[token] = (key, now + TTL)
    return token


def use(token: str, key: tuple) -> bool:
    with _lock:
        entry = _tokens.pop(token, None)
    return bool(entry) and entry[0] == key and entry[1] >= time.time()

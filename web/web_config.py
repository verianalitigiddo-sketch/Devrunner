"""
Web katmanına özgü ayarlar (PLAN_V2.md Bölüm 1).

Backend dondurulduğu için config.SETTINGS'e ayar eklenemez. Web ayarları burada aynı Setting / Group veri
sınıflarıyla tanımlanır ve AYNI .env dosyasına yazılır; öncelik aynıdır: ortam değişkeni > .env > varsayılan.
Ayarlar sayfası backend gruplarının ardından bu grupları da gösterir. Web katmanında os.getenv yalnızca burada.

    from web.web_config import WENV
    WENV.DEVRUNNER_PORT    # int
"""
from __future__ import annotations

import os
import sys
import threading

from dotenv import dotenv_values, set_key

from scripts import config
from scripts.config import TRUE_WORDS, Group, Setting

WEB_SETTINGS: tuple[Group, ...] = (
    Group("web", "Web arayüzü", (
        Setting("DEVRUNNER_PORT", "Port", "28657", kind="int",
                help="Yalnızca 127.0.0.1 dinlenir. 0 = her açılışta rastgele. Değişiklik yeniden başlatınca geçerli."),
        Setting("UI_EFFECTS", "Görsel efektler", "normal", kind="choice", choices=("normal", "azaltilmis", "kapali"),
                help="normal: cam yüzeyler · azaltilmis: saydamlık ve gölge yok · kapali: animasyon da yok "
                     "(ekran kartı zayıf / VDI makinelerde titreme olursa)."),
        Setting("TERMINAL_DETACHED_MIN", "Terminal: bağlantısız oturum ömrü (dk)", "30", kind="int",
                help="Hiçbir sekmede açık olmayan terminal oturumu bu süre sonunda kapatılır."),
        Setting("TERMINAL_MAX_SESSIONS", "Terminal: en çok oturum", "8", kind="int"),
    ), "Arayüz sunucusu, görünüm ve terminal oturumları."),
    Group("aura", "Aura", (
        Setting("AURA_ENABLED", "Aura (oyunlaştırma) açık", "1", kind="bool"),
        Setting("AURA_SINCE_DAYS", "İlk eşitlemede geriye dönük gün", "180", kind="int"),
        Setting("AURA_SYNC_MIN", "DevOps'tan eşitleme aralığı (dk)", "15", kind="int"),
    ), "DevOps etkinliğinden (commit, PR, CI/CD) aura puanı, seviye, unvan ve etiketler."),
)

_BY_KEY: dict[str, Setting] = {s.key: s for g in WEB_SETTINGS for s in g.settings}
_GROUPS: dict[str, Group] = {g.id: g for g in WEB_SETTINGS}
_values: dict[str, str] = {}
_from_os: set[str] = set()
_in_file: set[str] = set()
_lock = threading.Lock()


def groups() -> tuple[Group, ...]:
    return WEB_SETTINGS


def is_web_group(group_id: str) -> bool:
    return group_id in _GROUPS


def group(group_id: str) -> Group:
    return _GROUPS[group_id]


def setting(key: str) -> Setting:
    return _BY_KEY[key]


def _default(s: Setting) -> str:
    # Ekran kartsız / VDI makinede kabuk GPU'yu kapattıysa efektler varsayılan olarak azaltılır
    if s.key == "UI_EFFECTS" and os.environ.get("DEVRUNNER_DISABLE_GPU") == "1":
        return "azaltilmis"
    return s.default


def load() -> None:
    env_file = config.ENV_FILE
    file_values = dotenv_values(env_file) if env_file.exists() else {}
    with _lock:
        _values.clear()
        _from_os.clear()
        _in_file.clear()
        for key, s in _BY_KEY.items():
            os_value = os.environ.get(key)
            if os_value is not None and os_value.strip():
                _values[key] = os_value.strip()
                _from_os.add(key)
            elif file_values.get(key) is not None:
                _values[key] = str(file_values[key]).strip()
                _in_file.add(key)
            else:
                _values[key] = _default(s)


def get(key: str) -> str:
    with _lock:
        return _values.get(key, _default(_BY_KEY[key]))


def get_int(key: str) -> int:
    try:
        return int(get(key))
    except ValueError:
        return int(_BY_KEY[key].default or 0)


def get_bool(key: str) -> bool:
    return get(key).lower() in TRUE_WORDS


def is_from_os(key: str) -> bool:
    return key in _from_os


def is_explicit(key: str) -> bool:
    """Değer ortamda veya .env'de açıkça verilmiş mi (varsayılan değil)?"""
    return key in _from_os or key in _in_file


def validate(values: dict[str, str]) -> list[str]:
    errors = []
    for key, value in values.items():
        s = _BY_KEY[key]
        if s.kind == "int" and value.strip():
            try:
                n = int(value)
            except ValueError:
                errors.append(f"{s.label}: sayı olmalı")
                continue
            if key == "DEVRUNNER_PORT" and not (n == 0 or 1024 <= n <= 65535):
                errors.append(f"{s.label}: 0 veya 1024–65535 olmalı")
        if s.kind == "choice" and s.choices and value not in s.choices:
            errors.append(f"{s.label}: geçersiz seçim")
    return errors


def save(values: dict[str, str]) -> list[str]:
    values = {k: v.strip() for k, v in values.items() if k in _BY_KEY}
    errors = validate(values)
    if errors:
        raise ValueError("\n".join(errors))
    env_file = config.ENV_FILE
    if not env_file.exists():
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.touch()
        if sys.platform != "win32":
            env_file.chmod(0o600)
    changed = []
    for key, value in values.items():
        if value == get(key):
            continue
        set_key(str(env_file), key, value, quote_mode="always")
        with _lock:
            _values[key] = value
            _in_file.add(key)
        changed.append(key)
    return changed


class _WEnv:
    def __getattr__(self, key: str):
        s = _BY_KEY.get(key)
        if s is None:
            raise AttributeError(key)
        if s.kind == "int":
            return get_int(key)
        if s.kind == "bool":
            return get_bool(key)
        return get(key)


WENV = _WEnv()
load()

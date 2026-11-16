"""
Uygulamanın tüm ayarları TEK BURADA tanımlanır ve okunur. Başka dosyada os.getenv kullanmayın.

    from scripts.config import ENV
    ENV.DEVOPS_PAT          # str
    ENV.HEALTH_INTERVAL_SEC # int (Setting.kind'e göre tür dönüşür)

Değerler uygulama klasöründeki .env dosyasında durur (.gitignore'da). Ortamda zaten tanımlı bir
değişkeni .env ezmez. Ayarlar ekranı SETTINGS listesinden kendiliğinden oluşur; kaydedilen değer
.env'e yazılır ve uygulama yeniden başlatılmadan geçerli olur (relations.save_settings).

Yeni ayar eklemek: ilgili Group'a Setting ekleyin. .env.example'ı güncellemek için:
    python -m scripts.config
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, set_key

APP_NAME = "Devrunner"
APP_ROOT = Path(__file__).resolve().parent.parent

# Aynı makinede birden çok kopya (ör. sohbeti denemek) için: DEVRUNNER_PROFILE=ikinci
#   -> .env.ikinci ve .local/ikinci/ kullanılır. Devrunner.pyw --profile ikinci bunu ayarlar.
PROFILE = re.sub(r"[^A-Za-z0-9_-]", "", os.getenv("DEVRUNNER_PROFILE", ""))
ENV_FILE = APP_ROOT / (f".env.{PROFILE}" if PROFILE else ".env")
LOCAL_DIR = APP_ROOT / ".local" / PROFILE if PROFILE else APP_ROOT / ".local"
SAMPLE_DIR = APP_ROOT / "sample_data"
THEME_DIR = APP_ROOT / "themes"
ASSET_DIR = APP_ROOT / "assets"
EXAMPLE_FILE = APP_ROOT / ".env.example"


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    default: str = ""
    secret: bool = False
    help: str = ""
    kind: str = "text"                 # text | int | bool | choice
    choices: tuple[str, ...] = ()
    readonly: bool = False             # ekranda gösterilir, değiştirilemez


@dataclass(frozen=True)
class Group:
    id: str
    title: str
    settings: tuple[Setting, ...]
    description: str = ""
    testable: bool = False             # "Bağlantıyı sına" düğmesi (relations.test_connection)


SETTINGS: tuple[Group, ...] = (
    Group("general", "Genel", (
        Setting("USER_NAME", "Adınız", help="Notlarda, sohbette ve değişiklik geçmişinde görünür."),
        Setting("USER_ID", "Kullanıcı kimliği", readonly=True,
                help="İlk açılışta üretilir. Değiştirmeyin: geçmiş kayıtlar bu kimliğe bağlıdır."),
    ), "Bu bir kimlik doğrulama değildir; ekip içi güvene dayalı bir araçtır."),

    Group("db", "PostgreSQL", (
        Setting("DB_HOST", "Sunucu", help="Boşsa notlar, talepler, freeze ve sohbet kapalıdır."),
        Setting("DB_PORT", "Port", "5432", kind="int"),
        Setting("DB", "Veritabanı"),
        Setting("DB_USER", "Kullanıcı"),
        Setting("DB_PW", "Parola", secret=True),
        Setting("DB_REFRESH_SEC", "Ortak veriyi yenileme (sn)", "60", kind="int"),
    ), "Ekibin ortak verisi (notlar, Bilge talepleri, freeze, değişiklik geçmişi) burada tutulur.",
        testable=True),

    Group("devops", "Azure DevOps", (
        Setting("DEVOPS_URL", "Collection adresi", "https://devops.halkbank.com.tr/DefaultCollection"),
        Setting("DEVOPS_PROJECT", "Proje", "Halkbank"),
        Setting("DEVOPS_PAT", "Personal Access Token", secret=True,
                help="Gereken yetkiler: Code (Read), Build (Read), Release (Read). Boşsa örnek veri gösterilir."),
        Setting("DEVOPS_REPO_PREFIX", "Repo öneki", "DDDB.Iz.Anm."),
        Setting("PIPELINE_CI_SUFFIX", "CI pipeline soneki", "_Container-CI"),
        Setting("PIPELINE_CD_SUFFIX", "CD pipeline soneki", "_Container-CD"),
        Setting("DEVOPS_API_VERSION", "API sürümü", "6.0", help="Şirket içi sunucu sürümüne göre değişebilir."),
        Setting("DEVOPS_REFRESH_SEC", "Yenileme aralığı (sn)", "120", kind="int"),
        Setting("DEVOPS_USE_PROXY", "Sistem proxy'sini kullan", "0", kind="bool"),
        Setting("DEVOPS_VERIFY_TLS", "Sertifikayı doğrula", "1", kind="bool",
                help="Windows sertifika deposu kullanılır. Yalnızca zorunluysa kapatın."),
    ), "Repolar ve CI/CD pipeline'ları kişinin kendi PAT'i ile okunur.", testable=True),

    Group("urls", "Uygulama adresleri", (
        Setting("URL_DEV", "Dev", "https://{app}.dk8s.gentest.hqtest.local/"),
        Setting("URL_TEST", "Test", "https://{app}.tk8s.gentest.hqtest.local/"),
        Setting("URL_PROD", "Prod", "https://{app}.pk8s.gen.halkbank.local/"),
    ), "{app} yerine uygulama adı (repo adından önek atılmış, küçük harf) gelir. Kurala uymayan "
       "uygulamalar için adres, Uygulamalar sayfasında uygulama bazında ezilebilir."),

    Group("health", "Erişilebilirlik", (
        Setting("HEALTH_ENABLED", "Düzenli yokla", "1", kind="bool"),
        Setting("HEALTH_INTERVAL_SEC", "Yoklama aralığı (sn)", "60", kind="int"),
        Setting("HEALTH_TIMEOUT_SEC", "Zaman aşımı (sn)", "10", kind="int"),
        Setting("HEALTH_SLOW_MS", "Yavaş eşiği (ms)", "3000", kind="int"),
        Setting("HEALTH_USE_PROXY", "Sistem proxy'sini kullan", "0", kind="bool"),
        Setting("HEALTH_NOTIFY", "Durum değişince bildirim göster", "1", kind="bool"),
    ), "Uygulamaların Dev/Test/Prod adreslerine düzenli HTTP isteği atılır."),

    Group("k8s", "Kubernetes", (
        Setting("K8S_NAMESPACE", "Namespace", "verianalitigi"),
        Setting("K8S_DEV_SERVER", "Dev cluster adresi",
                help="ip, ip:port veya https://… kabul edilir; port yazılmazsa varsayılan port eklenir. Boşsa Dev kapalı."),
        Setting("K8S_DEV_TOKEN", "Dev token", secret=True),
        Setting("K8S_TEST_SERVER", "Test cluster adresi"),
        Setting("K8S_TEST_TOKEN", "Test token", secret=True),
        Setting("K8S_PROD_SERVER", "Prod cluster adresi"),
        Setting("K8S_PROD_TOKEN", "Prod token", secret=True),
        Setting("K8S_DEFAULT_PORT", "Varsayılan API portu", "5443",
                help="Adreste port yoksa kullanılır (CCE API sunucusu: 5443)."),
        Setting("K8S_PROXY", "Proxy (isteğe bağlı)",
                help="Boşsa cluster'a doğrudan gidilir; sistem proxy'si KULLANILMAZ. Gerekirse http://proxy:port"),
        Setting("K8S_LOG_LINES", "Log satır sayısı", "300", kind="int"),
    ), "Kubeconfig uygulamaya gömülüdür; yalnızca cluster adresi ve servis hesabı token'ı girilir. "
       "Hiçbir ortam girilmemişse örnek veri gösterilir.", testable=True),

    Group("terminal", "Terminal", (
        Setting("TERMINAL_SHELL", "Varsayılan kabuk", "auto",
                help="auto, powershell, pwsh, cmd, bash, zsh veya sh"),
    )),

    Group("chat", "Sohbet", (
        Setting("CHAT_ENABLED", "Sohbeti aç", "1", kind="bool"),
        Setting("CHAT_PORT", "Dinleme portu", "47800", kind="int", help="Doluysa boş bir port seçilir."),
        Setting("CHAT_ADVERTISE_IP", "Yayınlanacak IP",
                help="Boşsa veritabanı sunucusuna giden yoldaki yerel adres kullanılır."),
    ), "Eşler birbirini ortak PostgreSQL üzerinden bulur; mesajlar doğrudan ve uçtan uca şifreli gider."),

    Group("freeze", "Freeze", (
        Setting("FREEZE_WARN_DAYS", "Kaç gün önceden uyar", "3", kind="int"),
    )),
)

_SETTING_BY_KEY: dict[str, Setting] = {s.key: s for g in SETTINGS for s in g.settings}
_GROUP_BY_ID: dict[str, Group] = {g.id: g for g in SETTINGS}
_values: dict[str, str] = {}
_from_os: set[str] = set()

TRUE_WORDS = ("1", "true", "yes", "evet", "on")


def group(group_id: str) -> Group:
    return _GROUP_BY_ID[group_id]


def setting(key: str) -> Setting:
    return _SETTING_BY_KEY[key]


def load() -> None:
    """Değerleri (yeniden) okur: ortam değişkeni > .env > varsayılan."""
    file_values = dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}
    _values.clear()
    _from_os.clear()
    for key, s in _SETTING_BY_KEY.items():
        os_value = os.environ.get(key)
        if os_value is not None and os_value.strip():
            _values[key] = os_value.strip()
            _from_os.add(key)
        elif file_values.get(key) is not None:
            _values[key] = str(file_values[key]).strip()
        else:
            _values[key] = s.default


def get(key: str) -> str:
    return _values.get(key, _SETTING_BY_KEY[key].default)


def get_int(key: str) -> int:
    try:
        return int(get(key))
    except ValueError:
        return int(_SETTING_BY_KEY[key].default or 0)


def get_bool(key: str) -> bool:
    return get(key).lower() in TRUE_WORDS


def is_from_os(key: str) -> bool:
    """Değer işletim sistemi ortamından geliyorsa .env'e yazılan değer bir sonraki açılışta ezilir."""
    return key in _from_os


def validate(values: dict[str, str]) -> list[str]:
    errors = []
    for key, value in values.items():
        s = _SETTING_BY_KEY[key]
        if s.kind == "int" and value.strip():
            try:
                int(value)
            except ValueError:
                errors.append(f"{s.label}: sayı olmalı")
        if s.kind == "choice" and s.choices and value not in s.choices:
            errors.append(f"{s.label}: geçersiz seçim")
    return errors


def save(values: dict[str, str]) -> list[str]:
    """
    Değerleri .env'e yazar ve bellekte günceller. Değişen anahtarları döner.
    Doğrudan çağırmayın: relations.save_settings() olay yayınlar ve geçmişe yazar.
    """
    errors = validate(values)
    if errors:
        raise ValueError("\n".join(errors))
    if not ENV_FILE.exists():
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        ENV_FILE.touch()
        if sys.platform != "win32":
            ENV_FILE.chmod(0o600)
    changed = []
    for key, value in values.items():
        value = value.strip()
        if key not in _SETTING_BY_KEY or _SETTING_BY_KEY[key].readonly and _values.get(key):
            continue
        if value == get(key):
            continue
        set_key(str(ENV_FILE), key, value, quote_mode="always")
        _values[key] = value
        changed.append(key)
    return changed


def secret_values() -> list[str]:
    return [v for k, v in _values.items() if _SETTING_BY_KEY[k].secret and len(v) >= 4]


_TOKEN_IN_URL = re.compile(r"(?i)((?:token|pat|password|pw|api_key|apikey)=)[^&\s]+")
_BEARER = re.compile(r"(?i)(bearer\s+|basic\s+)[A-Za-z0-9._~+/=-]{8,}")


def redact(text: str) -> str:
    """Metindeki bilinen secret değerlerini ve URL'e gömülü token'ları *** ile değiştirir."""
    if not text:
        return text
    for value in sorted(secret_values(), key=len, reverse=True):
        text = text.replace(value, "***")
    text = _TOKEN_IN_URL.sub(r"\1***", text)
    return _BEARER.sub(r"\1***", text)


def example_text() -> str:
    lines = [
        "# Devrunner ayarları. Bu dosya scripts/config.py'den üretilir: python -m scripts.config",
        "# Kopyalayın: cp .env.example .env   (Windows: copy .env.example .env)",
        "# Değerleri uygulama içindeki Ayarlar sayfasından da girebilirsiniz.",
        "# .env dosyası depoya GİRMEZ (.gitignore).",
    ]
    for g in SETTINGS:
        lines += ["", "# " + "-" * 75, f"# {g.title}", "# " + "-" * 75]
        if g.description:
            lines.append(f"# {g.description}")
        for s in g.settings:
            if s.help:
                lines.append(f"# {s.label}: {s.help}")
            lines.append(f"{s.key}={'' if s.secret else s.default}")
    return "\n".join(lines) + "\n"


class _Env:
    """ENV.ANAHTAR -> Setting.kind'e göre str / int / bool."""

    def __getattr__(self, key: str):
        s = _SETTING_BY_KEY.get(key)
        if s is None:
            raise AttributeError(key)
        if s.kind == "int":
            return get_int(key)
        if s.kind == "bool":
            return get_bool(key)
        return get(key)


ENV = _Env()
load()


if __name__ == "__main__":
    EXAMPLE_FILE.write_text(example_text(), encoding="utf-8")
    print(f"{EXAMPLE_FILE} yazıldı.")

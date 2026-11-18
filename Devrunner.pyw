"""
Devrunner'ı konsolsuz başlatır (Windows'ta çift tıklama veya kur.bat'ın oluşturduğu kısayol).

    pythonw Devrunner.pyw                    normal (PyQt6 penceresi + arka planda web.server)
    pythonw Devrunner.pyw --profile ikinci   aynı makinede ikinci kopya (.env.ikinci, .local/ikinci/)
    python  Devrunner.pyw --debug            F12 geliştirici araçları, log konsola

Arayüzü normal tarayıcıda geliştirmek için: python -m web.server --dev
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if "--profile" in sys.argv:
    i = sys.argv.index("--profile")
    if i + 1 < len(sys.argv):
        os.environ["DEVRUNNER_PROFILE"] = sys.argv[i + 1]
        del sys.argv[i:i + 2]


def _fatal(text: str) -> None:
    """Qt açılamadan önceki hatalar (ör. PyQt6 kurulu değil): pythonw'da görünür kılmak için."""
    log = ROOT / ".local" / "logs" / "baslatma_hatasi.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(text, encoding="utf-8")
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, f"Devrunner başlatılamadı.\n\n{text[-1500:]}\n\nAyrıntı: {log}",
                                         "Devrunner", 0x10)


try:
    from shell.main import main
except Exception:   # noqa: BLE001
    import traceback
    _fatal(traceback.format_exc())
    raise SystemExit(1)

if __name__ == "__main__":
    sys.exit(main())

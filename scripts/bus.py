"""
Uygulama geneli olaylar. Modüller birbirini import etmez; yayınlar ve dinler.

    from scripts.bus import bus
    bus.app_selected.connect(self._on_app)
    bus.app_selected.emit("musteri360")

Yeni bir olay gerekiyorsa buraya ekleyin ve README'deki listeye yazın.
"""
from PySide6.QtCore import QObject, Signal


class Bus(QObject):
    # Kullanıcı bir uygulama seçti (her sayfa kendi ayrıntısını gösterebilir)
    app_selected = Signal(str)
    # Uygulama kataloğu / DevOps özeti yenilendi
    catalog_changed = Signal()
    # Ayarlar ekranında bir grup kaydedildi (grup kimliği: "devops", "k8s"…)
    settings_changed = Signal(str)
    # Freeze dönemleri değişti veya gün döndü
    freeze_changed = Signal()
    # Bir uygulamanın bir ortamdaki erişilebilirlik durumu değişti (app, env, state)
    health_changed = Signal(str, str, str)
    # Bir yoklama turu bitti (tablolar yenilensin)
    health_updated = Signal()
    # Çevrimiçi kişi listesi değişti
    presence_changed = Signal()
    # Veritabanı bağlantısı kuruldu (True) / koptu (False)
    db_state_changed = Signal(bool)
    # Ortak veri değişti: "notes" | "bilge" | "freeze" | "overrides" | "audit"
    data_changed = Signal(str)
    # Bir sayfayı öne getir (sayfa kimliği)
    open_page = Signal(str)
    # Terminalde komut çalıştır (komut, k8s ortamı veya "")
    run_command = Signal(str, str)
    # Kullanıcıya bildirim (başlık, mesaj, seviye: info | warning | error)
    notify = Signal(str, str, str)
    # Tema değişti (tema kimliği); HTML üreten sayfalar yeniden çizsin
    theme_changed = Signal(str)


bus = Bus()

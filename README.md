# Devrunner Web

Devrunner'ın web arayüzlü çatalı: **aynı backend** (`scripts/`, Devrunner `main` ile birebir), arayüz
ise yerel bir **Flask** sunucusunun ürettiği HTML ve bunu gösteren bir **PyQt6** tarayıcı penceresi.
Kullanıcı için hâlâ çift tıklanan bir masaüstü uygulamasıdır. Tasarım ve gerekçeler: [PLAN_WEB.md](PLAN_WEB.md).

| Modül | Ne yapar |
|---|---|
| **Uygulamalar** | Tüm `DDDB.Iz.Anm.*` uygulamaları: CI/CD, Dev/Test/Prod erişimi, pod, not ve talep sayısı; ayrıntıda pipeline, adresler, K8s, notlar, yer imleri, değişiklik geçmişi |
| **DevOps** | CI/CD geçmişi, düşen adım ve log'u (indirilebilir), commit'ler, açık PR'lar; freeze'de CD uyarısı |
| **Kubernetes** | Ortam / uygulama seçimi, pod ve deployment, log (canlı izleme), olaylar, komut; Prod komutu sunucuda onay ister |
| **Erişilebilirlik** | Uygulama × ortam tablosu, son 1 saatin süre grafiği, sessize alma, ekip geneli olay geçmişi |
| **Terminal** | nslookup / curl / ping / kubectl; etkin kabuğun adı, sürümü ve yolu her zaman görünür; çıktı canlı akar |
| **Notlar** | Ortak notlar, Markdown (güvenli), not geçmişi |
| **Bilge** | Talep linkleri **yer imi** gibi: klasörler, etiketler, kişisel ★ çubuğu, hızlı ekleme, toplu yapıştırma, sürükle-bırak, Edge/Chrome yer imi dosyası içe/dışa aktarma |
| **Freeze** | Ay takvimi, dönem ekle / sil; içindeysek sağ üstte **FREEZE** rozeti |
| **Sohbet** | Çevrimiçi geliştiricilerle P2P, uçtan uca şifreli sohbet (Discord tarzı) |
| **Ayarlar** | Tüm ayarlar `.env`'e; secret'lar asla geri gösterilmez |

Görünüm: Discord tarzı yerleşim (simge şeridi, başlık çubuğu, sayfa kartı), **Ctrl+K komut paleti**, canlı
bildirim balonları ve sistem bildirimleri, sayfa geçiş animasyonları, iskelet yükleyiciler. Yedi tema korunur ve
anında değişir: **Modern** (varsayılan), Aydınlık, Karanlık, Cyberpunk, Dracula, 90'lar, BIOS. Modern / Karanlık /
Dracula / Cyberpunk cam yüzey ve hareket kullanır; 90'lar ve BIOS bilinçli olarak düz kalır. Yazı tipleri (Inter,
JetBrains Mono) ve htmx depoda gelir: ağ / CDN gerekmez.

## Kurulum (Windows)

Python 3.10 veya üzeri gerekir.

1. Depoyu klonlayın, `DevrunnerWeb` dalına geçin.
2. `kur.bat`'a çift tıklayın: `.venv` oluşturur, bağımlılıkları kurar, masaüstüne **Devrunner** kısayolu koyar.
3. Kısayolla açın. İlk açılışta adınızı yazın; sonra **Ayarlar**'dan Azure DevOps PAT'i, PostgreSQL ve
   Kubernetes bilgilerini girip her grupta **Bağlantıyı sına**'ya basın (ayrıntılar masaüstü sürümle aynı).

Pencereyi kapatmak tepsiye küçültür (yoklama ve sohbet sürer); çıkmak için tepsi simgesi → **Çıkış**.
Aynı makinede ikinci kopya: `Devrunner.pyw --profile ikinci`. Hata ayıklama: `python Devrunner.pyw --debug`
(F12 geliştirici araçları). VDI / ekran kartı sürücüsü sorunlu makinelerde: `DEVRUNNER_DISABLE_GPU=1`.

### Geliştirme (normal tarayıcıda)

```bash
python -m web.server --dev
```

Konsola yazılan `http://127.0.0.1:<port>/auth?t=…` adresini tarayıcıda açın. Kabuk olmadan her şey çalışır
(tepsi hariç). Şablon değişikliği için sunucuyu yeniden başlatın.

## Mimari: iki süreç

```
Devrunner.pyw ─► KABUK (PyQt6: shell/)                    BACKEND (PySide6 QtCore + Flask: web/, scripts/)
                 QWebEngineView ── HTTP + SSE ──► 127.0.0.1:<rastgele port>
                 tepsi, bildirim, tek kopya          ana iş parçacığı: QCoreApplication + relations.start_services()
                 backend'i başlatır / kapatır        Flask iş parçacığı: web/pages/* → scripts/*API.py, relations.py
```

Backend PySide6 kullanır, tarayıcı PyQt6'dır; iki Qt bağlamı aynı süreçte çakıştığı için ayrı süreçlerde
çalışırlar. `shell/` yalnızca PyQt6, `web/` ve `scripts/` yalnızca PySide6 import eder (testle denetlenir).
Kabuk backend'i `python -m web.server` ile başlatır, oturum belirtecini **stdin**'den verir, `READY <port>`
satırını bekler. Kabuk kapanınca stdin kapanır ve backend kendini düzgün kapatır (sohbet çevrimiçi kaydı silinir).

```
DevrunnerWeb/
├── Devrunner.pyw        konsolsuz giriş -> shell.main
├── shell/               KABUK (PyQt6): main.py, backend_process.py, window.py
├── web/                 ARAYÜZ SUNUCUSU (Flask)
│   ├── server.py        backend süreci girişi
│   ├── app.py           create_app(), PAGES listesi, ortak rotalar, hata kartı
│   ├── qt_bridge.py     qt.call(): Flask iş parçacığından Qt ana iş parçacığına güvenli çağrı
│   ├── events.py        scripts.bus olayları -> Server-Sent Events
│   ├── security.py      belirteç çerezi, Host / Origin denetimi, CSP
│   ├── terminal_runner.py, ui_state.py (.local/ui.json), theme_css.py (PALETTES -> CSS), common.py
│   ├── pages/           her sayfa bir blueprint
│   ├── templates/       Jinja2 (tam sayfa + htmx parçaları)
│   └── static/          css/app.css, js/ (app, terminal, bilge, chat), vendor/htmx
├── scripts/             DONDURULMUŞ backend (Devrunner main) + BookmarkAPI.py (yeni)
└── tests/               backend testleri (değişmedi) + test_web*.py + test_bookmarks.py
```

### Kurallar

* **Backend dondurulmuştur.** `scripts/` altındaki mevcut dosyalar değiştirilmez; yalnızca yeni dosya eklenir
  (`tests/test_web_infra.py::test_backend_is_frozen`). Böylece Devrunner'daki backend düzeltmeleri çakışmasız alınır.
  Web'e özgü tercihler (tema, son sayfa, terminal geçmişi) `.local/ui.json`'da, `.env`'de değil.
* **İş parçacıkları.** Flask istekleri ayrı iş parçacıklarında çalışır. Engelleyen düz backend çağrıları
  (`DevopsAPI`, `K8sAPI`, `NotesAPI`…) doğrudan çağrılır. Qt nesnesine dokunan her şey (`relations.save_settings`,
  `refresh_*`, sohbet, `bus` olayları) `qt.call(fn, …)` ile ana iş parçacığına taşınır. `web/` içinde
  `worker.run_async` kullanılmaz.
* **Canlı güncelleme.** Tarayıcıda tek `EventSource`; her olay `body` üzerinde aynı adlı DOM olayı olur.
  Parçalar `hx-trigger="health_updated from:body"` ile kendini yeniler. Olay verisine secret yazılmaz.
* **Sayfalar** yalnızca kendi API'lerini ve `relations.py`'yi kullanır (testle denetlenir).
* **Arayüz** sunucuda üretilen HTML + htmx; derleme zinciri (npm) yok. Renk CSS'e yazılmaz: tema değişkenleri.

### Yeni sayfa eklemek

1. Gerekirse `scripts/XAPI.py` (yeni dosya; mevcut backend'e dokunmayın).
2. `web/pages/x.py`: `bp = Blueprint("x", __name__)`, `@bp.get("/")` sayfası, parçalar.
3. `web/templates/x/page.html` (`{% extends "base.html" %}`) ve parçalar; ortak makrolar `_macros.html`.
4. `web/app.py` → `PAGES` listesine `PageDef("x", "Başlık", "web.pages.x", "simge")`; simgeler `templates/_icons.html`.
5. Durum değiştiren istekler `hx-post` ile (htmx `X-Devrunner` başlığını ekler); yanıta `trigger(…)` ile olay /
   bildirim balonu eklenir.

İçe aktarılamayan sayfa uygulamayı düşürmez; yerinde hata kartı gösterilir.

### Yukarı akıştan (Devrunner) backend güncellemesi almak

Backend dosyaları değişmediği için Devrunner `main`'deki `scripts/` değişiklikleri doğrudan alınabilir
(ör. `git checkout <devrunner-main> -- scripts/` sonra testler). `tests/test_web_infra.py`'deki `BASE_COMMIT`
yeni taban commit'e güncellenir. `BookmarkAPI.py` yalnızca bu çatalda vardır.

## Güvenlik modeli

Yerel HTTP sunucusu komut çalıştırabilen ve secret yazabilen bir arayüzü açar; masaüstü sürümde olmayan iki
tehdit doğar: makinedeki başka bir süreç veya tarayıcıdaki bir web sayfası sunucuya istek atabilir; arayüz HTML
olduğu için ekip arkadaşından / uzak sistemden gelen metin betik olarak çalışabilir. Önlemler (hepsi testli):

* Yalnızca `127.0.0.1`, rastgele port. Her açılışta yeni belirteç; `/auth?t=…` doğru belirteçle `HttpOnly`,
  `SameSite=Strict` çerez kurar (çerezde belirtecin kendisi değil HMAC'i). `/static`, `/fonts`, `/theme.css` dışında
  **her** istek çerez ister. Erişim log'u kapalı (adres belirteç içerir).
* `Host` başlığı yalnızca `127.0.0.1:<port>` / `localhost:<port>` (DNS rebinding). Durum değiştiren istekler:
  `Origin` kendi kökenimiz olmalı ve `X-Devrunner: 1` başlığı bulunmalı. CORS başlığı yok.
* CSP: `script-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`; satır içi betik ve `eval` yok
  (htmx `allowEval=false`). Satır içi `style` izinlidir (yalnızca görünüm).
* Dışarıdan gelen her metin kaçışlanır (Jinja2 otomatik kaçışlama, JS'te yalnızca `textContent`). Notlarda Markdown
  ham HTML kapalı, bağlantılar yalnızca `http/https`. Yer imlerinde yalnızca `http/https` adres.
* Secret'lar hiçbir HTML / JSON / SSE / log'a girmez; Ayarlar "kayıtlı / boş" gösterir, boş gönderim değiştirmez.
* Prod'da K8s komutu sunucuda **tek kullanımlık, komuta bağlı** onay belirteci ister; doğrudan API çağrısıyla atlanamaz.
* "Terminalde dene" bağlantıları komutu kutuya yazar, **çalıştırmaz**.
* Kabuk yalnızca kendi kökenini yükler; diğer her adres (Bilge, DevOps…) sistem tarayıcısında açılır. Bildirim izni
  yalnızca kendi kökenimize verilir.

## Bilge yer imleri

Linkin kendisi `dr_bilge_requests`'te kalır ve `BilgeAPI` ile yazılır; masaüstü sürüm aynı talepleri görür
(web'de klasöre konmamış talepler "Klasörsüz"de). Düzen yan tablolardadır (`scripts/BookmarkAPI.py`):

| Tablo | İçerik | Kapsam |
|---|---|---|
| `dr_bilge_folders` | iç içe klasörler, sıra, yumuşak silme | ekip geneli |
| `dr_bilge_bookmarks` | talep → klasör, sıra, etiketler | ekip geneli |
| `dr_bilge_stars` | kişisel yıldızlar | kişisel |

Tablolar ilk kullanımda kendiliğinden oluşur; `dr_meta`'ya kendi anahtarını (`bookmarks_schema`) yazar,
`schema_version`'a dokunmaz (masaüstü sürüm uyarı vermez). Klasör silmek talep silmez; içindekiler üst klasöre taşınır.

Kullanım: adresi hızlı ekleme kutusuna yapıştırıp Enter (birden çok satır yapıştırınca toplu ekleme açılır);
kartları sürükleyip soldaki klasöre bırakın (klasör görünümünde sürükleyerek sıralayın); ★ ile kişisel yer imi
çubuğuna ekleyin; durum hapına tıklayıp Açık → Devam ediyor → Kapandı. **İçe aktar**: Edge / Chrome → Yer işaretleri
→ Dışa aktar dosyası (klasör yapısı korunur, yalnız http/https, kayıtlı adresler yeniden eklenmez).

## Testler

```bash
pip install -r requirements-dev.txt
```

```bash
pytest
```

Veritabanı testleri **ayrı bir test veritabanı** ister (her testte sıfırlanır), yoksa atlanır:

```bash
TEST_DB_HOST=localhost TEST_DB=app_test TEST_DB_USER=app TEST_DB_PW=app pytest
```

Kapsam: backend testlerinin tamamı (değişmedi); web güvenliği (çerez, Host, Origin, başlık, CSP), tüm sayfa ve
parçaların örnek veriyle açılması, secret'ın hiçbir yanıtta görünmemesi, hata metinlerinin arındırılması, Prod
onayının atlanamaması, adresle gelen komutun çalışmaması, hata kartı, tema CSS'i; olay akışı, Qt köprüsü, terminal
çalıştırıcısı (Türkçe çıktı, Durdur tüm alt süreçleri sonlandırır); yer imleri (klasör, sıra, etiket, kişisel yıldız,
Netscape içe/dışa aktarma, XSS); mimari kurallar (dondurulmuş backend, Qt bağlamı yalıtımı, sayfa → API kuralı).

Geliştirme sırasında ayrıca Playwright ile uçtan uca denendi: notta XSS denemesi, yer imi ekleme / yinelenen adres /
Türkçe adlı klasör / sürükle-bırak / yıldız / dışa aktarma, freeze → başlık rozeti, terminal, tema değişimi ve
hatırlanması, iki ayrı backend arasında sohbet (mesaj canlı ulaşır, HTML çalışmaz, okunmamış rozeti); kabuk
(PyQt6) ekransız açılıp sayfayı yükledi ve kapanınca backend süreci de kapandı.

## Windows'ta elle denenecekler

Geliştirme ortamında (Linux) doğrulanamayanlar:

* `kur.bat` kısayolu, `pythonw` ile konsolsuz açılış, backend alt sürecinin konsol penceresi açmaması.
* QtWebEngine'in banka masaüstlerinde / VDI'da açılması (gerekirse `DEVRUNNER_DISABLE_GPU=1`).
* Tepsi bildirimleri (Notification API → tepsi), indirme penceresi, tarih alanlarının Türkçe biçimi.
* Terminal: Windows PowerShell 5.1, PowerShell 7, CMD; Türkçe karakterler; Durdur'un `ping -t` gibi komutları sonlandırması.
* Sohbet portunun Windows Güvenlik Duvarı'ndan geçmesi; DevOps'a `truststore` ile TLS doğrulamalı bağlantı.
* İç PyPI aynasında `PyQt6`, `PyQt6-WebEngine`, `PySide6-Essentials`, `Flask`, `markdown-it-py` bulunması.

## Sorun giderme

| Belirti | Bakılacak yer |
|---|---|
| "Devrunner çekirdeği başlatılamadı" | Ekrandaki son log satırları; `.local/logs/devrunner.log`, `.local/logs/shell.log` |
| Pencere boş / beyaz | `DEVRUNNER_DISABLE_GPU=1`; `python Devrunner.pyw --debug` ile F12 konsolu |
| "Oturum yok" | Uygulamayı kısayoldan açın (her açılışta yeni belirteç); tarayıcıda geliştiriyorsanız `--dev` adresini kullanın |
| Diğerleri | Masaüstü sürümle aynı: Ayarlar → Bağlantıyı sına; durum çubuğundaki ipuçları |

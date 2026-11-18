# Devrunner Web

Devrunner'ın web arayüzlü çatalı: **aynı backend** (`scripts/`, Devrunner `main` ile birebir), arayüz
ise yerel bir **Flask** sunucusunun ürettiği HTML ve bunu gösteren bir **PyQt6** tarayıcı penceresi.
Kullanıcı için hâlâ çift tıklanan bir masaüstü uygulamasıdır. Tasarım ve gerekçeler: [PLAN_WEB.md](PLAN_WEB.md), ikinci tur: [PLAN_V2.md](PLAN_V2.md).

| Modül | Ne yapar |
|---|---|
| **Uygulamalar** | Tüm `DDDB.Iz.Anm.*` uygulamaları: CI/CD, Dev/Test/Prod erişimi, pod, not ve talep sayısı; ayrıntıda pipeline, adresler, K8s, notlar, yer imleri, değişiklik geçmişi |
| **DevOps** | CI/CD geçmişi, düşen adım ve log'u (indirilebilir), commit'ler, açık PR'lar; freeze'de CD uyarısı |
| **Kubernetes** | Ortam / uygulama seçimi, pod'lar (deployment listelemeden), log (WebSocket ile canlı akış, indirme), olaylar; pod'dan tek tıkla **Terminal** |
| **Erişilebilirlik** | Uygulama × ortam tablosu, son 1 saatin süre grafiği, sessize alma, ekip geneli olay geçmişi |
| **Terminal** | Gerçek terminal (xterm.js): sekmeli **yerel kabuk** (PowerShell / CMD / Bash) ve **pod içinde kabuk** (`kubectl exec -it` gibi); vim, top, Ctrl+C, renkler çalışır; oturumlar sayfa değişince açık kalır |
| **Notlar** | Ortak notlar, Markdown (güvenli), not geçmişi |
| **Bilge** | Talep linkleri **yer imi** gibi: klasörler, etiketler, kişisel ★ çubuğu, hızlı ekleme, toplu yapıştırma, sürükle-bırak, Edge/Chrome yer imi dosyası içe/dışa aktarma |
| **Freeze** | Ay takvimi, dönem ekle / sil; içindeysek sağ üstte **FREEZE** rozeti |
| **Sohbet** | Çevrimiçi geliştiricilerle P2P, uçtan uca şifreli sohbet (Discord tarzı); adların yanında Aura rozeti |
| **Aura** | DevOps etkinliğinden puan (commit, PR, CI/CD), seviye, unvan, RPG nitelikleri, 24 etiket ve ekip sıralaması; rozet sağ üstte ve sohbette |
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
(F12 geliştirici araçları). VDI / ekran kartı sürücüsü sorunlu makinelerde: `DEVRUNNER_DISABLE_GPU=1`
(bu durumda arayüz efektleri kendiliğinden "azaltılmış" olur; Ayarlar → Web arayüzü → `UI_EFFECTS`).

### Tarayıcıdan erişim

Uygulama sabit portta dinler: **`http://127.0.0.1:28657`** (`DEVRUNNER_PORT`, Ayarlar → Web arayüzü). Yalnızca
`127.0.0.1` dinlenir; başka makineden erişilemez. Başlıktaki ↗ düğmesi veya tepsi menüsü → **Tarayıcıda aç**
oturumu Edge / Chrome'da açar; tarayıcı çerezi 30 gün geçerlidir, uygulama yeniden başlasa da sürer. Ayarlar →
Web arayüzü → **Tüm tarayıcı oturumlarını kapat** bütün çerezleri geçersiz kılar. Uygulama kapalıyken adres açılmaz.
`--profile` ile açılan ikinci kopya kendi `.env.<profil>`'inde port verilmemişse rastgele port kullanır. Port
doluysa uygulama bunu söyler; başka bir port seçin.

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

* Yalnızca `127.0.0.1`, sabit port (`DEVRUNNER_PORT`). Her açılışta yeni belirteç; `/auth?t=…` doğru belirteçle
  `HttpOnly`, `SameSite=Strict`, porta özgü adlı çerez kurar. Çerez, `.local/web_secret`'taki kalıcı sırrın HMAC'idir
  (30 gün); "Tüm tarayıcı oturumlarını kapat" sırrı yeniler. `/static`, `/fonts`, `/theme.css` dışında
  **her** istek çerez ister. Erişim log'u kapalı (adres belirteç içerir).
* `Host` başlığı yalnızca `127.0.0.1:<port>` / `localhost:<port>` (DNS rebinding). Durum değiştiren istekler:
  `Origin` kendi kökenimiz olmalı ve `X-Devrunner: 1` başlığı bulunmalı. CORS başlığı yok.
* CSP: `script-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`; satır içi betik ve `eval` yok
  (htmx `allowEval=false`). Satır içi `style` izinlidir (yalnızca görünüm).
* Dışarıdan gelen her metin kaçışlanır (Jinja2 otomatik kaçışlama, JS'te yalnızca `textContent`). Notlarda Markdown
  ham HTML kapalı, bağlantılar yalnızca `http/https`. Yer imlerinde yalnızca `http/https` adres.
* Secret'lar hiçbir HTML / JSON / SSE / log'a girmez; Ayarlar "kayıtlı / boş" gösterir, boş gönderim değiştirmez.
* WebSocket (terminal, canlı log) el sıkışması çerez **ve** kendi `Origin`'imizi ister (başka sitedeki bir sayfa
  bağlanamaz). Terminal tuş vuruşları ve çıktısı hiçbir yere kaydedilmez.
* Prod'da pod kabuğu sunucuda **tek kullanımlık, pod / container'a bağlı** onay belirteci ister; doğrudan API çağrısıyla
  atlanamaz. Pod kabuğu açılış / kapanışı değişiklik geçmişine yazılır.
* "Terminalde dene" bağlantıları ve hazır komutlar komutu giriş satırına yazar, **Enter göndermez**.
* Kabuk yalnızca kendi kökenini yükler; diğer her adres (Bilge, DevOps…) sistem tarayıcısında açılır. Bildirim izni
  yalnızca kendi kökenimize verilir.

## Terminal

Terminal sayfası sekmelidir: **+ Yerel kabuk** (yanındaki listeden PowerShell / PowerShell 7 / CMD; Linux'ta Bash)
ve **Pod kabuğu** (ortam → uygulama → pod → container; kabuk otomatik bash → ash → sh). Kubernetes sayfasında pod
satırındaki **Terminal** düğmesi doğrudan o pod'a bağlanır. Üst şerit etkin kabuğun adını, sürümünü ve yolunu ya da
ortam / pod / container'ı gösterir. Yerel kabukta `kubectl` için üstteki listeden ortam seçilirse `KUBECONFIG`
o cluster'a ayarlanır (Ayarlar → Kubernetes'ten üretilir; uygulama secret'ları ortama aktarılmaz).

* Kopyala / yapıştır: **Ctrl+Shift+C / Ctrl+Shift+V**; seçim varken **Ctrl+C** kopyalar, yoksa kabuğa kesme sinyali
  gönderir. Sağ tık: seçim varsa kopyalar, yoksa yapıştırır. Terminal odaktayken komut paleti **Ctrl+Shift+K**.
* Oturumlar sunucudadır: sayfa değişse / yenilense açık kalır, dönünce son çıktı (200 bin karakter) geri gelir.
  Bağlı sekmesi kalmayan oturum `TERMINAL_DETACHED_MIN` dakika sonra kapanır; en çok `TERMINAL_MAX_SESSIONS` oturum.
  Uygulama kapanınca tüm kabuklar ve alt süreçleri sonlandırılır.
* Windows'ta gerçek terminal **pywinpty** (ConPTY) ile çalışır. Kurulamazsa sayfa eski kipe düşer (her komut ayrı
  süreç; etkileşimli programlar yok) ve bunu söyler.
* Pod kabuğu servis hesabının `pods/exec` yetkisini ister (`webterminal` ile aynı protokol); yetki yoksa 403 açıkça
  gösterilir. Kubernetes sayfası `deployments` listelemez (servis hesabının yetkisi yok); uygulama ↔ pod eşlemesi
  ingress / `app` etiketi / pod adı önekiyle yapılır, yetkisiz kaynaklar ortam başına bir kez sorulur.

## Aura

DevOps'taki etkinlik **aura** puanına çevrilir; seviye, unvan, nitelik ve etiketler bundan türetilir. Kural tablosu
`scripts/AuraAPI.py` → `RULES`'tadır; olgular saklanır, puanlar her okumada yeniden hesaplanır (kural değişince
geçmiş kendiliğinden yeniden puanlanır).

| Olay | Aura | | Olay | Aura |
|---|---|---|---|---|
| Commit (günde ilk 15) | +10 (merge commit +2) | | CD başarılı / başarısız | +15 / −10 |
| Revert commit'i (atan kişiye) | −15 | | CD Prod başarılı / başarısız | +25 / −20 |
| CI başarılı / kısmen / başarısız | +5 / +1 / −5 | | PR açma / birleşme / terk | +5 / +20 / −5 |
| Review (onay oyu) | +8 | | İptal edilen build | 0 |

* **Seviye** = ⌊√(aura / 25)⌋ + 1. **Unvanlar**: Git Noob (1–2), Noob Coder (3–5), Commit Çırağı (6–9), Merge
  Savaşçısı (10–14), Pipeline Şövalyesi (15–19), Programming Chad (20–29), DevOps Killer (30–39), Aura Lordu
  (40–49), 10x Efsane (50+); aura eksideyse **Build Kıran**.
* **Nitelikler** (1–99): STR commit hacmi, DEX CI başarı oranı, INT birleşen PR'lar, VIT etkin gün ve seri, LUK CD
  başarısı (Prod ağırlıklı), CHA review'lar ve farklı repolar. Az veride oranlar yumuşatılır.
* **24 etiket** (İlk Kan, Yeşil Seri, Gece Kuşu, Cuma Cesareti, Freeze Kaçağı, Yangın Söndürücü, Anka Kuşu…);
  kilitliler ilerleme gösterir. Kazanılan etiket **Tak** ile seçilir; seçilmezse seviye unvanı görünür.
* **Kişi eşleme**: DevOps'taki görünen ad (`Burak ALTINIŞIK (STRATEJİ DİJ. DÖNÜŞÜM VE Y.ZEKA DB)`) birim parantezi
  atılarak, Türkçe duyarlı büyük/küçük harf farkı gözetmeden eşlenir. Uygulamanın ilk açılışta sorduğu ad bununla
  eşleşir; eşleşmezse Aura sayfası en yakın adları önerir (Ayarlar → Genel'den düzeltin). **7 haneli kullanıcı
  kodları**, tek sözcüklü adlar ve sistem hesapları (Build Service…) puanlanmaz; sayısı sayfada görünür.
* **Eşitleme**: `AURA_SYNC_MIN` dakikada bir ve "Şimdi eşitle" ile; ilk tarama `AURA_SINCE_DAYS` gün geriye gider.
  Olgular ortak PostgreSQL'e yazılır (`dr_aura_facts`, `dr_aura_sync`, `dr_aura_profiles`; `dr_meta`'da
  `aura_schema`, `schema_version`'a dokunulmaz) ve herkes aynı sıralamayı görür; aynı anda tek istemci DevOps'u
  tarar. Veritabanı yoksa yalnızca bu oturum; PAT yoksa örnek veri gösterilir (veritabanına yazılmaz).
* YAML CD pipeline'ında hangi aşamanın Prod olduğu build listesinden okunamaz: pipeline adı "prod" içeriyorsa Prod
  sayılır. Klasik Release'te ortam adı "Prod" / "Canlı" ise Prod sayılır.
* Sıralamada kendinizi gizleyebilirsiniz (Aura → kartım → Sıralamada gizle); rozetiniz de görünmez.

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
onayının atlanamaması, adresle gelen komutun çalışmaması, hata kartı, tema CSS'i; sabit port / dolu port / kalıcı
çerez; deployment'sız K8s (sahte cluster `deployments()` çağrılırsa düşer); gerçek PTY oturumları (yankı, boyut,
geri oynatma, kapanışta alt süreç kalmaması), gerçek sunucu üzerinden WebSocket (çerez / Origin, kapanış baytları),
sahte `pods/exec` sunucusuyla kanal protokolü, pod kabuğu Prod onayı; Aura (kişi eşleme, kural tablosu, seviye /
unvan / nitelik / etiketler, sahte DevOps ile eşitlemenin yinelenebilirliği, veritabanı ve bellek depolarının aynı
sonucu vermesi, rozetlerin kaçışlanması); olay akışı, Qt köprüsü, terminal
çalıştırıcısı (Türkçe çıktı, Durdur tüm alt süreçleri sonlandırır); yer imleri (klasör, sıra, etiket, kişisel yıldız,
Netscape içe/dışa aktarma, XSS); mimari kurallar (dondurulmuş backend, Qt bağlamı yalıtımı, sayfa → API kuralı).

Geliştirme sırasında ayrıca Playwright ile uçtan uca denendi: notta XSS denemesi, yer imi ekleme / yinelenen adres /
Türkçe adlı klasör / sürükle-bırak / yıldız / dışa aktarma, freeze → başlık rozeti, terminal, tema değişimi ve
hatırlanması, titreme (canlı olaylarda tablo satırları aynı DOM düğümleri kalır), xterm terminali (komut çalışır,
renkler, sekmeler, pod kabuğu, Prod onayı, sayfa değişince geri oynatma, sekme kapatmada konsol hatası yok), Aura
(sıralama, kişi kartı, etiket takınca başlık rozeti, BIOS teması), iki ayrı backend arasında sohbet (mesaj canlı ulaşır, HTML çalışmaz, okunmamış rozeti); kabuk
(PyQt6) ekransız açılıp sayfayı yükledi ve kapanınca backend süreci de kapandı.

## Windows'ta elle denenecekler

Geliştirme ortamında (Linux) doğrulanamayanlar:

* `kur.bat` kısayolu, `pythonw` ile konsolsuz açılış, backend alt sürecinin konsol penceresi açmaması.
* QtWebEngine'in banka masaüstlerinde / VDI'da açılması (gerekirse `DEVRUNNER_DISABLE_GPU=1`).
* Tepsi bildirimleri (Notification API → tepsi), indirme penceresi, tarih alanlarının Türkçe biçimi.
* Terminal: `pywinpty` ile ConPTY; Windows PowerShell 5.1, PowerShell 7, CMD; Türkçe karakterler; vim / `ping -t` ve
  Ctrl+C; sekme kapatınca alt süreçlerin sonlanması; pano (Ctrl+Shift+C/V) hem pencerede hem Edge'de.
* Gerçek cluster'da pod kabuğu (`pods/exec` yetkisi, K8S_PROXY üzerinden WebSocket).
* Aura eşitlemesinin gerçek DevOps'ta süresi ve commit yazarlarının ne kadarının kullanıcı kodu olduğu.
* `28657` portunun banka makinelerinde boş olması; "Tarayıcıda aç" ile Edge'de oturumun açılması.
* Sohbet portunun Windows Güvenlik Duvarı'ndan geçmesi; DevOps'a `truststore` ile TLS doğrulamalı bağlantı.
* İç PyPI aynasında `PyQt6`, `PyQt6-WebEngine`, `PySide6-Essentials`, `Flask`, `flask-sock` (+ `simple-websocket`,
  `wsproto`, `h11`), `pywinpty`, `markdown-it-py` bulunması.

## Sorun giderme

| Belirti | Bakılacak yer |
|---|---|
| "Devrunner çekirdeği başlatılamadı" | Ekrandaki son log satırları; `.local/logs/devrunner.log`, `.local/logs/shell.log` |
| Pencere boş / beyaz | `DEVRUNNER_DISABLE_GPU=1`; `python Devrunner.pyw --debug` ile F12 konsolu |
| "Port 28657 kullanılıyor" | Başka bir kopya açık olabilir (tepsiye bakın) ya da başka program kullanıyor: Ayarlar → Web arayüzü → `DEVRUNNER_PORT` |
| Terminal "basit kipte" | Windows'ta `pip install pywinpty` (kur.bat kurar); kurulamıyorsa eski kip çalışır |
| Pod kabuğu 403 | Servis hesabına `pods/exec` (create) yetkisi gerekir |
| Aura'da "adınız eşleşmedi" | Ayarlar → Genel'de adınızı DevOps'taki gibi yazın (birim ve büyük/küçük harf önemsiz) |
| "Oturum yok" | Uygulamayı kısayoldan açın (her açılışta yeni belirteç); tarayıcıda geliştiriyorsanız `--dev` adresini kullanın |
| Diğerleri | Masaüstü sürümle aynı: Ayarlar → Bağlantıyı sına; durum çubuğundaki ipuçları |

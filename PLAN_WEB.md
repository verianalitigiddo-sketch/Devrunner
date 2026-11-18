# Devrunner Web — Geliştirme Planı

Bu belge, Devrunner'ın **web arayüzlü çatalını (fork)** yazacak modele verilen iş tanımıdır.
Temel: Devrunner `main` (commit `4d5e134`). Önce o deponun `README.md` ve `PLAN.md` dosyalarını
okuyun; bu belge yalnızca farkları anlatır. Kararlar gerekçeleriyle yazıldı; emin olunamayan noktalar
[Bölüm 12](#12-açık-sorular-ve-varsayımlar)'de varsayım olarak işaretli.

## 1. Amaç

Aynı uygulama, aynı backend; arayüz değişiyor:

- PySide6 widget arayüzü (`_pages/`, `app.py`) kaldırılır.
- Yerine **yerel bir Flask sunucusu** HTML arayüz sunar ve **PyQt6 tabanlı bir tarayıcı penceresi**
  (QWebEngineView) bu arayüzü gösterir. Kullanıcı için hâlâ çift tıklanan bir masaüstü uygulamasıdır.
- Yeni özellik: **Bilge Talepleri içinde yer imi (bookmark) yapısı** — klasörler, yıldızlar, etiketler,
  hızlı ekleme ([Bölüm 8](#8-bilge-yer-imleri)).

Masaüstü sürümdeki her özellik web sürümde de bulunur (eşlik tablosu: [Bölüm 7](#7-sayfaların-taşınması)).
İki sürüm aynı ortak PostgreSQL'i kullanabilir.

## 2. Değişmez kural: backend dondurulmuştur

`scripts/` altındaki **mevcut dosyalar değiştirilmez**. Bu, çatalın yukarı akıştan (Devrunner) backend
düzeltmelerini çakışmasız alabilmesi içindir.

- Doğrulama (her faz sonunda ve testlerde): çatalın temel aldığı commit'e göre
  `git diff --stat <taban> -- scripts/` çıktısında yalnızca **yeni eklenen** dosyalar görünür.
- `scripts/` altına yeni dosya eklenebilir (bu planda bir tane var: `scripts/BookmarkAPI.py`).
- Mevcut backend'de bir eksik görülürse düzeltilmez; `web/` katmanında çözülür veya
  "yukarı akışa öneri" olarak README'ye not düşülür.
- `sample_data/`, `themes/`, `assets/`, `tools/` de aynen kalır (`scripts/theme.py` bunlara bağlı).
- `tests/` altındaki backend testleri (`test_core`, `test_devops`, `test_health_freeze_k8s`,
  `test_chat`, `test_database`) değiştirilmeden geçmeye devam eder.

Sonucu: `config.SETTINGS`'e ayar eklenemez. Web katmanına özgü tercihler (tema, son sayfa, yer imi
görünümü) `.local/ui.json` dosyasında tutulur, `.env`'e yazılmaz.

## 3. Mimari: iki süreç

```
Devrunner.pyw
   │
   ▼
┌─ KABUK süreci (PyQt6) ───────────┐         ┌─ BACKEND süreci (PySide6 yalnız QtCore/QtNetwork) ─┐
│ shell/                           │  HTTP   │ web/server.py                                      │
│  QWebEngineView → 127.0.0.1:PORT │ ──────► │  ana iş parçacığı: QCoreApplication                │
│  tepsi, bildirim, tek kopya      │  + SSE  │     relations.start_services()  (yoklama, sohbet)  │
│  backend sürecini başlatır/kapatır│ ◄────── │  Flask iş parçacığı (werkzeug, threaded)           │
└──────────────────────────────────┘         │     web/pages/*  →  scripts/*API.py, relations.py  │
                                             └────────────────────────────────────────────────────┘
```

**Neden iki süreç.** Dondurulmuş backend PySide6 kullanıyor: `bus.py` ve `relations.py` (QObject, QTimer),
`worker.py` (QThreadPool), `ChatAPI.py` (QTcpServer/QTcpSocket), `theme.py` (QtGui). İstenen tarayıcı ise
PyQt6. İki Qt bağlamı (PySide6 + PyQt6) aynı süreçte yüklenmez: her biri kendi Qt kütüphanelerini getirir
ve çakışır. Ayrı süreçlerde ikisi de sorunsuz çalışır. Yan kazanç: arayüz, geliştirme sırasında normal bir
tarayıcıda da açılabilir (Playwright ile test edilebilir) ve kabuk çökerse backend etkilenmez.

Kural: `shell/` yalnızca PyQt6, `web/` ve `scripts/` yalnızca PySide6 import eder. Kabuk, backend kodunu
import etmez; yalnızca HTTP konuşur. Bir test bunu import taramasıyla doğrular.

**Doğrulanmış varsayım.** Backend'in pencere olmadan çalıştığı denendi: `QCoreApplication` altında
`relations.start_services()` örnek kataloğu yükledi, sağlık yoklaması döndü, `bus` olayları geldi;
Flask iş parçacığından yapılan çağrılar yanıt verdi. `QApplication` (GUI) gerekmez.

### 3.1 Başlatma ve kapanış

1. `Devrunner.pyw` → `shell.main`: tek kopya kontrolü (zaten açıksa pencereyi öne getir, çık).
2. Kabuk, 32 baytlık rastgele bir **oturum belirteci** üretir ve backend'i alt süreç olarak başlatır:
   `python -m web.server` (Windows'ta `CREATE_NO_WINDOW`). Belirteç ortam değişkeniyle değil **stdin**
   üzerinden verilir (süreç listesinde görünmesin).
3. Backend `127.0.0.1` üzerinde boş bir porta bağlanır, servisleri başlatır, stdout'a tek satır
   `READY <port>` yazar. Kabuk bu satırı en çok 30 sn bekler; bu sırada açılış ekranı gösterir.
   Süre dolarsa veya süreç ölürse `.local/logs/` içindeki son satırlarla hata ekranı gösterir.
4. Kabuk `http://127.0.0.1:<port>/auth?t=<belirteç>` adresini yükler; sunucu çerezi kurup `/`'a yönlendirir.
5. Kapanış: tepsiden **Çıkış** → kabuk `POST /api/shutdown` gönderir, 5 sn bekler, gerekirse süreci
   sonlandırır. Backend kapanırken `relations.stop_services()` çağrılır (sohbet çevrimiçi kaydı silinir).
6. Kabuk beklenmedik biçimde ölürse backend yetim kalmamalı: backend, stdin'in kapanmasını izleyen bir
   iş parçacığıyla kendini kapatır.

Pencereyi kapatmak eskisi gibi tepsiye küçültür; backend çalışmaya devam eder (yoklama, sohbet).
`--profile <ad>` desteği korunur (kabuk, `DEVRUNNER_PROFILE`'ı alt sürece aktarır).

Geliştirme kipi: `python -m web.server --dev` belirteci ve adresi konsola yazar; arayüz normal
tarayıcıda açılabilir. Kabuk olmadan da her şey çalışmalıdır (tepsi ve bildirim dışında).

### 3.2 İş parçacığı kuralları (`web/qt_bridge.py`)

Flask istekleri werkzeug iş parçacıklarında çalışır; Qt nesneleri ise ana iş parçacığında yaşar.

- **Engelleyen düz backend çağrıları** (`DevopsAPI.history`, `K8sAPI.cluster(...).pods()`,
  `NotesAPI.*`, `BilgeAPI.*`, `relations.app_workload`, `relations.test_connection`…) doğrudan istek iş
  parçacığında çağrılır. Bunlar zaten masaüstü sürümde arka plan iş parçacıklarından çağrılıyordu.
- **Qt nesnesine dokunan her şey** `qt.call(fn, *args)` ile ana iş parçacığına taşınır ve sonucu
  beklenir: `relations.save_settings`, `refresh_catalog`, `refresh_health`, `refresh_shared`,
  `chat.send`, `chat.node.accept_new_key`, `chat.history` okuma, `bus.*.emit`.
  Gerekçe: `worker.run_async` ve `Poller` çağrıldıkları iş parçacığında QObject oluşturur; olay döngüsü
  olmayan Flask iş parçacığında bu nesnelerin sinyalleri hiç teslim edilmez.
- Kilitle korunan düz okumalar (`relations.catalog()`, `health_monitor().states()`, `overview()`,
  `freeze_status()`, `database.is_ready()`) doğrudan okunabilir.
- `web/` içinde `worker.run_async` kullanılmaz: istek iş parçacığı zaten arka plandır.

`qt.call` gerçeklemesi: ana iş parçacığında yaşayan bir QObject, kuyruklu bir sinyal ve
`concurrent.futures.Future` (zaman aşımı 15 sn; aşılırsa 504).

### 3.3 Canlı güncelleme (`web/events.py`)

Ana iş parçacığındaki bir QObject `bus` sinyallerine bağlanır ve her olayı abone kuyruklarına koyar;
`GET /api/events` bunları **Server-Sent Events** olarak yayınlar (15 sn'de bir canlılık satırı).
Olay adları sinyal adlarıdır: `catalog_changed`, `health_updated`, `health_changed`, `freeze_changed`,
`presence_changed`, `db_state_changed`, `data_changed`, `settings_changed`, `notify`, ayrıca
`chat_message` (ChatService.message) ve `terminal_output`.

Sayfalar ilgili parçayı olay gelince yeniden ister (htmx: `hx-trigger="sse:health_updated"`).
Ortak veritabanı verisi (notlar, talepler, freeze) eskisi gibi görünür sayfada 60 sn'de bir de yenilenir.

## 4. Klasör yapısı

```
DevrunnerWeb/
├── Devrunner.pyw              Konsolsuz giriş → shell.main (başlatma hatasını pencereyle gösterir)
├── shell/                     KABUK — yalnızca PyQt6
│   ├── main.py                tek kopya, backend alt süreci, açılış / hata ekranı
│   ├── backend_process.py     başlat, READY bekle, kapat
│   └── window.py              QWebEngineView, gezinme kuralları, bildirim, indirme, tepsi
├── web/                       ARAYÜZ SUNUCUSU — Flask; PyQt6 import etmez
│   ├── server.py              giriş: QCoreApplication + relations.start_services + Flask iş parçacığı
│   ├── app.py                 create_app(), PAGES listesi, hata kartı, ortak bağlam (tema, kullanıcı, freeze)
│   ├── qt_bridge.py           qt.call
│   ├── events.py              bus → SSE
│   ├── security.py            belirteç çerezi, Host/Origin denetimi, CSRF, CSP
│   ├── ui_state.py            .local/ui.json
│   ├── theme_css.py           theme.PALETTES → CSS değişkenleri
│   ├── terminal_runner.py     komut alt süreci ve çıktı akışı
│   ├── pages/                 her sayfa bir blueprint: home, devops, k8s, health, terminal, notes,
│   │                          bilge, freeze, chat, settings
│   ├── templates/             base.html, _macros.html, <sayfa>/…  (tam sayfa + htmx parçaları)
│   └── static/                css/, js/ (htmx ve eklentisi depoda; CDN yok), ikonlar
├── scripts/                   DONDURULMUŞ + BookmarkAPI.py (yeni)
├── assets/ sample_data/ themes/ tools/      aynen
├── tests/                     backend testleri aynen; test_ui.py silinir, test_web_*.py eklenir
└── silinenler: _pages/, app.py
```

Değişen yardımcı dosyalar: `requirements*.txt`, `kur.bat`, `README.md`, `.gitignore`.

Bağımlılıklar: `PySide6-Essentials` (backend yalnız QtCore/QtNetwork/QtGui kullanır; tam PySide6 gerekmez),
`PyQt6`, `PyQt6-WebEngine`, `Flask`, `markdown-it-py` (notlar). Diğerleri aynı. Sürümler denenip sabitlenir.

## 5. Web katmanı sözleşmesi

Masaüstündeki kalıp korunur: `_pages/x.py → scripts/XAPI.py` yerine **`web/pages/x.py → scripts/XAPI.py`**.

- Her sayfa bir Flask blueprint'idir; `web/app.py` içindeki açık `PAGES` listesine eklenir
  (kimlik, başlık, modül, simge). Şeritteki sıra bu listedir.
- Blueprint'i içe aktarılamayan veya kurulumda hata veren sayfa yerine hata kartı gösterilir; diğer
  sayfalar çalışır (masaüstündeki `ErrorPage` davranışı).
- Bir sayfa yalnızca kendi API'sini ve `scripts/relations.py`'yi kullanır; başka modülün API'sini import
  etmez. Çatala özgü, relations'a eklenemeyen birleştirmeler `web/` içinde kalır.
- **Sunucuda üretilen HTML + htmx.** Derleme zinciri (npm) yoktur; her şey depoda gelir. Gerekçe: banka
  ağında CDN yok, Jinja2 otomatik kaçışlama XSS'e karşı varsayılan koruma sağlar, sayfa mantığı Python'da kalır.
  Küçük etkileşimler için sade JavaScript modülleri; satır içi `<script>` ve `eval` yoktur (CSP).
- Uzun süren istekler (DevOps, K8s) sayfayı bekletmez: sayfa iskeleti hemen gelir, parçalar htmx ile
  ayrı isteklerle dolar ve yüklenme / hata durumunu gösterir.
- Hata dili aynıdır: backend istisnaları (`DevopsError`, `K8sForbidden`, `DbUnavailable`…) kullanıcıya
  o bölümün içinde, `config.redact`'ten geçirilerek gösterilir.

**Görünüm.** Yerleşim masaüstündeki gibi Discord tarzı: solda simge şeridi, üstte başlık çubuğu (tema
seçici, çevrimiçi sayısı, avatar, **en sağda FREEZE rozeti**), ortada yuvarlak köşeli sayfa kartı, altta
durum çubuğu (PostgreSQL, DevOps eşitleme, erişim özeti). Yedi tema korunur: `web/theme_css.py`,
`scripts/theme.PALETTES` sözlüğünü CSS değişkenlerine çevirir (`[data-theme="dracula"] { --bg: … }`);
`button_border` / `input_border` / `panel_border` anahtarları geçerli CSS bildirimleridir, aynen
kullanılır. Renk CSS'e elle yazılmaz. Modern temada web'in olanakları (cam yüzey, bulanıklık, geçişler)
kullanılabilir; örnek: `webterminal` deposundaki `components/console/console.css`. Yazı tipleri
`assets/fonts/` altından `@font-face` ile sunulur.

## 6. Güvenlik

Yerel bir HTTP sunucusu, komut çalıştırabilen ve secret yazabilen bir arayüzü açığa çıkarır. Masaüstü
sürümde olmayan iki tehdit doğar: (1) makinedeki başka bir süreç / kullanıcı veya kullanıcının normal
tarayıcısındaki bir web sayfası sunucuya istek atabilir; (2) arayüz artık HTML olduğu için ekip
arkadaşından veya uzak sistemden gelen metin betik olarak çalışabilir. Aşağıdakilerin hepsi zorunludur
ve testle doğrulanır.

**Erişim**
- Yalnızca `127.0.0.1`'e bağlanılır; port her açılışta rastgeledir.
- Her açılışta yeni oturum belirteci. `/auth?t=…` doğru belirteçle `HttpOnly; SameSite=Strict` çerez
  kurar; `/auth` ve `/static` dışındaki **her** istek (sayfalar, `/api`, SSE) geçerli çerez ister.
  Karşılaştırma sabit zamanlıdır. Belirteç log'a yazılmaz (erişim log'u kapalı veya arındırılmış).
- `Host` başlığı `127.0.0.1:<port>` değilse istek reddedilir (DNS rebinding).
- Durum değiştiren istekler (POST/PUT/DELETE) ayrıca `Origin` denetiminden geçer ve özel bir başlık
  ister (`X-Devrunner: 1`; htmx bunu her isteğe ekler). CORS başlığı gönderilmez.

**İçerik**
- CSP: `default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`.
  Satır içi betik, `eval`, dış kaynak yok. htmx'te `allowEval` ve betik çalıştıran özellikler kapatılır.
- Dışarıdan gelen her metin kaçışlanır: not başlığı / gövdesi, talep başlığı, sohbet mesajı ve adı, pod /
  build log'u, commit mesajı, PR başlığı, K8s olayı, hata mesajı. Jinja2 otomatik kaçışlama açık kalır,
  `|safe` yalnızca sunucunun kendi ürettiği HTML için kullanılır; JavaScript'te `innerHTML` yerine
  `textContent`.
- Notlardaki Markdown sunucuda `markdown-it-py` ile ham HTML **kapalı** olarak çevrilir; bağlantılar yalnızca
  `http/https`.
- Arayüzden açılan dış bağlantılar `http/https` ile sınırlıdır ve uygulama görünümünde açılmaz
  ([Bölüm 9](#9-kabuk-pyqt6)).

**Secret'lar**
- Ayar API'si secret değeri **asla döndürmez**; yalnızca "kayıtlı / boş" bilgisini verir. Boş gönderilen
  secret alanı "değiştirme" demektir; silmek için ayrı bir işaret vardır.
- PAT, token, parola hiçbir HTML'e, JSON'a, SSE olayına veya log'a girmez (`config.redact`).
- Terminal alt süreçlerine uygulama secret'ları aktarılmaz (`TerminalAPI.process_env` zaten böyle).

**Onay isteyen işlemler** eskisi gibi: Prod'da K8s komutu sunucu tarafında da doğrulanan bir onay
adımından geçer (yalnızca arayüzdeki `confirm()` yetmez) ve audit'e yazılır.

## 7. Sayfaların taşınması

Her satır için davranış masaüstü sürümle aynıdır; "Dikkat" sütunu web'e özgü farkı söyler.
Kaynak: silinen `_pages/<sayfa>.py` (taban commit'te okunabilir).

| Sayfa | Backend | Dikkat |
|---|---|---|
| Uygulamalar | `relations` (yalnızca) | Tablo + ayrıntı paneli. Seçili uygulama adres durumudur (`/apps/<ad>`). Ayrıntıdaki K8s / not / talep / geçmiş bölümleri ayrı parçalar olarak yüklenir. Adres / eşleme düzenleme formu. İkinci sekme: değişiklik geçmişi |
| DevOps | `DevopsAPI`, `relations` | CI/CD geçmişi, düşen adım, log (uzun metin: kaçışlanmış `<pre>`, indirilebilir), commit'ler, PR'lar. Freeze'de CD uyarısı |
| Kubernetes | `K8sAPI`, `relations` | Ortam / uygulama seçimi, pod ve deployment tabloları, log (3 sn'de bir yenileme), olaylar, komut. Prod onayı sunucuda da denetlenir |
| Erişilebilirlik | `HealthAPI`, `relations` | Uygulama × ortam tablosu; son 1 saat grafiği sunucuda üretilen satır içi SVG. Sessize al, "şimdi yokla", olay geçmişi |
| Terminal | `TerminalAPI` | Aşağıya bakın |
| Notlar | `NotesAPI` | Liste + düzenleyici + Markdown önizleme (sunucuda, güvenli) + not geçmişi |
| Bilge | `BilgeAPI`, `BookmarkAPI` | [Bölüm 8](#8-bilge-yer-imleri) |
| Freeze | `FreezeAPI`, `relations` | Takvim (sunucuda üretilen ay ızgarası), dönem ekle / sil, başlık rozeti |
| Sohbet | `relations.chat_service()` | Tüm erişim `qt.call` ile. Yeni mesaj SSE ile gelir, `textContent` ile eklenir. Anahtar değişimi uyarısı ve kabul |
| Ayarlar | `config`, `relations` | Form `config.SETTINGS`'ten üretilir. Kaydet → `qt.call(relations.save_settings)`. "Bağlantıyı sına". Secret kuralları Bölüm 6 |

**İlk açılış.** `user.needs_name()` ise her sayfa `/welcome`'a yönlenir; ad girilince
`relations.save_settings("general", …)`.

**Terminal.** Masaüstünde süreç `QProcess` ile arayüz katmanında çalışıyordu; web'de
`web/terminal_runner.py`'ye taşınır: `subprocess.Popen` (komut satırı `TerminalAPI.build_command`,
ortam `TerminalAPI.process_env`, durdurma `TerminalAPI.kill_tree`, `cd` izleme `TerminalAPI.parse_cd`,
uyarılar `TerminalAPI.hint`). Çıktı bir okuyucu iş parçacığından SSE ile akar; aynı anda tek komut.
Etkin kabuğun adı, sürümü ve yolu her zaman görünür ve her çıktı bloğunun başlığında yazar. Windows'ta
`cmd.exe` için `build_command`'ın döndürdüğü `native_args` komut satırına aynen eklenir. Çıktı UTF-8
olarak çözülür. Komut geçmişi `.local/` altında yerel dosyada tutulur, veritabanına yazılmaz.
Diğer sayfalardaki "Terminalde dene" bağlantıları `/terminal?cmd=…&kube=…` adresine götürür; komut
kutuya **yazılır ama kendiliğinden çalıştırılmaz** (adresle komut çalıştırma olmasın).

**Arayüz durumu.** Masaüstünde `QSettings`'te duran tema, son sayfa, terminal kabuğu / dizini
`web/ui_state.py` ile `.local/ui.json`'da tutulur. Pencere konumu ve yakınlaştırma kabuğun kendi
`QSettings`'indedir.

## 8. Bilge yer imleri

Bugün Bilge sayfası düz bir listedir (`dr_bilge_requests`: başlık, adres, uygulama, durum, açıklama).
İstenen: talep linklerinin tarayıcı yer imleri gibi düzenlenebildiği bir yapı.

### 8.1 Veri modeli (`scripts/BookmarkAPI.py`, yeni dosya)

Linkin kendisi `dr_bilge_requests`'te kalır ve **`BilgeAPI` fonksiyonlarıyla** yazılır (doğrulama ve
audit orada). Böylece masaüstü sürüm aynı talepleri görmeye devam eder. Yer imi düzeni yan tablolarda durur:

| Tablo | Kolonlar | Kapsam |
|---|---|---|
| `dr_bilge_folders` | `id`, `name`, `parent_id` NULL, `position`, `created_by`, `created_at`, `deleted_at` | ekip geneli |
| `dr_bilge_bookmarks` | `request_id` PK → `dr_bilge_requests.id`, `folder_id` NULL, `position`, `tags` (metin dizisi), `updated_by`, `updated_at` | ekip geneli |
| `dr_bilge_stars` | `user_id`, `request_id`, `created_at`; PK (`user_id`, `request_id`) | kişisel |

- `dr_bilge_bookmarks` satırı olmayan talep "Klasörsüz" sayılır (masaüstünden eklenenler böyle görünür).
- Şema: `BookmarkAPI.ensure_schema()` tabloları `CREATE TABLE IF NOT EXISTS` ile, `database.init_db`'deki
  gibi danışma kilidi altında oluşturur ve `dr_meta`'ya **kendi anahtarını** (`bookmarks_schema`) yazar.
  `schema_version`'a dokunulmaz; yoksa masaüstü sürüm "veritabanı daha yeni" uyarısı verir.
  Kural aynı: yalnızca ekleyici değişiklik.
- Her yazma aynı transaction'da `database.audit(…, "bilge", …)` çağırır. Klasör silme yumuşaktır
  (`deleted_at`); içindeki yer imleri üst klasöre taşınır, talepler silinmez.
- Fonksiyonlar: `ensure_schema`, `tree(user_id)` (klasörler + sayılar), `list_bookmarks(folder_id |
  akıllı klasör, search, tag, status, app)`, `add_folder`, `rename_folder`, `move_folder`,
  `delete_folder`, `place(request_id, folder_id, position)`, `set_tags`, `star` / `unstar`,
  `import_netscape(html)`, `export_netscape()`.
- Backend'in geri kalanı gibi Qt'siz ve arayüzsüzdür; `tests/test_database.py` kalıbında test edilir.

### 8.2 Arayüz

- **Sol: klasör ağacı.** Akıllı klasörler (Tümü, ★ Yıldızlılar, Açık, Devam eden, Kapananlar, Son
  eklenenler, Klasörsüz, "Uygulamaya göre" altında her uygulama) + ekip klasörleri (iç içe, sürükleyerek
  taşıma, yeniden adlandırma, silme). Her düğümde sayı.
- **Üst: yer imi çubuğu.** Kullanıcının yıldızladığı talepler tek tıkla açılan düğmeler olarak.
- **Orta: yer imleri.** Başlık, adresin ana makinesi, uygulama, durum hapı, etiketler, ekleyen ve zaman.
  Eylemler: aç, bağlantıyı kopyala, yıldızla, düzenle, klasöre taşı (sürükle-bırak veya menü), durumu
  değiştir, sil. Arama (başlık, adres, açıklama, etiket) ve etiket / durum / uygulama süzgeçleri.
- **Hızlı ekleme.** Her zaman görünen tek satır: adres yapıştırılır, başlık yazılır, Enter. Seçili klasöre
  ve (varsa) seçili uygulamaya eklenir. Aynı adres zaten kayıtlıysa uyarır ve var olanı gösterir.
  Birden çok satır yapıştırılırsa (`başlık | adres` veya yalnız adres) toplu ekleme önizlemesi açılır.
- **İçe / dışa aktarma.** Tarayıcıların ortak yer imi biçimi (Netscape HTML): Edge / Chrome'dan dışa
  aktarılan dosya içe alınır (klasör yapısı korunur, yalnız `http/https`), seçili klasör dışa verilir.
- Uygulamalar sayfasındaki uygulama ayrıntısı, o uygulamanın yer imlerini klasör yoluyla gösterir.

**Kabul.** Masaüstü sürümden eklenen talep web'de "Klasörsüz"de görünür; web'de klasöre taşınan ve
etiketlenen talep masaüstünde aynen listelenir. Yıldız yalnızca yıldızlayan kullanıcıda görünür.
Klasör silmek talep silmez. `javascript:` gibi adresler eklenemez ve içe aktarmada atlanır.

## 9. Kabuk (PyQt6)

`shell/` küçük kalır (birkaç yüz satır); iş mantığı içermez.

- **Profil.** Kalıcı `QWebEngineProfile`, depolama `.local/webprofile/` altında (profil başına ayrı).
- **Gezinme.** Uygulama görünümü yalnızca kendi kökenini (`http://127.0.0.1:<port>`) yükler. Başka her
  adres (Bilge talebi, DevOps bağlantısı, uygulama adresi) sistemin varsayılan tarayıcısında açılır:
  `acceptNavigationRequest` ve `createWindow` bunu zorlar. Gerekçe: uzak içerik, yerel API'ye çerezle
  erişebilen görünümde çalışmamalı.
- **Bildirim.** Sayfa standart `Notification` API'sini kullanır; kabuk izin isteğini kendi kökeni için
  onaylar ve `setNotificationPresenter` ile sistem tepsisinde gösterir. Böylece arayüz normal tarayıcıda da
  aynı kodla çalışır. Bildirim kaynakları: erişilebilirlik durum değişimi, sohbet mesajı (pencere etkin değilken).
- **Tepsi ve pencere.** Göster / Çıkış; kapatınca tepsiye küçülme; pencere başlığı sayfa başlığını izler
  (freeze sırasında başlıkta ❄). Konum ve yakınlaştırma (`Ctrl +/-/0`) hatırlanır; `F5` yeniler.
- **İndirme.** `downloadRequested` → kaydetme penceresi (log indirme, yer imi dışa aktarma).
- **Hata ayıklama.** `--debug` ile `F12` geliştirici araçlarını açar ve backend log'u konsola akar.
- **Dayanıklılık.** Backend süreci ölürse kabuk hata ekranı gösterir ve "Yeniden başlat" sunar.
  VDI / sürücüsüz makinelerde GPU sorunları için `DEVRUNNER_DISABLE_GPU=1` → `--disable-gpu`.
- Sağ tık menüsü sadeleştirilir (kopyala, yapıştır, yenile).

## 10. Fazlar

Her faz sonunda uygulama açılır, o faza kadarki her şey çalışır, `scripts/` farkı boştur (yeni dosya
hariç) ve testler geçer. Fazlar ayrı commit'lerdir.

**Faz 0 — İskelet ve iki süreç.** `_pages/` ve `app.py` silinir. `web/server.py`, `qt_bridge`, `events`,
`security`, `ui_state`, `theme_css`, `create_app` + `PAGES` + hata kartı; temel yerleşim (şerit, başlık,
durum çubuğu, yedi tema, freeze rozeti, çevrimiçi sayısı); `/welcome`; Ayarlar sayfası; `shell/` (alt süreç,
açılış / hata ekranı, pencere, tepsi, tek kopya); `Devrunner.pyw`.
*Kabul:* `.pyw` ile açılır; tema anında değişir ve hatırlanır; ayar `.env`'e yazılır ve secret geri
okunamaz; çerezsiz, yanlış `Host`'lu ve `Origin`'i yabancı istekler reddedilir; kabuk öldürülünce backend
de kapanır; içe aktarılamayan sayfa hata kartı gösterir.

**Faz 1 — Ortak veri.** Notlar, Freeze, Bilge (mevcut düz liste işlevi), değişiklik geçmişi.
*Kabul:* masaüstü sürümle aynı veritabanında çift yönlü görünürlük; nota yazılan `<script>` /
`<img onerror>` çalışmaz, metin olarak görünür; veritabanı kapalıyken sayfalar "bağlantı yok" durumu gösterir.

**Faz 2 — Bilge yer imleri.** `scripts/BookmarkAPI.py` + arayüz ([Bölüm 8](#8-bilge-yer-imleri)).

**Faz 3 — Uygulamalar, DevOps, Erişilebilirlik.** Canlı güncelleme SSE ile.
*Kabul:* örnek veriyle masaüstündeki bilgilerin tamamı; yoklama turu bitince tablo kendiliğinden yenilenir.

**Faz 4 — Kubernetes ve Terminal.**
*Kabul:* örnek veriyle K8s sayfası dolar; Prod onayı atlanamaz (doğrudan API çağrısı da reddedilir);
terminalde Türkçe çıktı bozulmaz, uzun komut durdurulur, etkin kabuk görünür.

**Faz 5 — Sohbet ve bildirimler.**
*Kabul:* iki profil (`--profile`) birbirini çevrimiçi görür ve mesajlaşır; pencere etkin değilken
bildirim çıkar; mesajdaki HTML çalışmaz.

**Faz 6 — Teslim.** `kur.bat` (iki bağlam + kısayol), README (mimari, güvenlik modeli, yeni sayfa ekleme,
Windows'ta elle denenecekler, yukarı akıştan backend güncellemesi alma), bu planın güncellenmesi.

## 11. Test ve doğrulama

- **Backend testleri** değişmeden geçer. `scripts/` dondurma kuralı bir testle denetlenir.
- **Web birim testleri** (`app.test_client()`): `create_app` köprüyü parametre alır; testlerde eşzamanlı
  sahte köprü verilir. Güvenlik katmanı (çerez, Host, Origin, CSP başlığı, secret'ın hiçbir yanıtta
  görünmemesi), kaçışlama (XSS girdileriyle), her sayfanın örnek veriyle 200 dönmesi, Prod onayı.
- **Yer imleri**: yerel PostgreSQL'e karşı (`TEST_DB_HOST`), yoksa atlanır.
- **Uçtan uca**: gerçek `web.server` alt süreci örnek veriyle başlatılır, Playwright (Chromium) ile
  sayfalar gezilir; yedi temada ekran görüntüsü alınıp gözle kontrol edilir (okunurluk, FREEZE rozeti,
  yer imi sürükle-bırak). SSE ile canlı yenileme burada doğrulanır.
- **Kabuk**: `QT_QPA_PLATFORM=offscreen` ile açılış duman testi. Kapsayıcıda QtWebEngine ek sistem
  kütüphaneleri ve `--no-sandbox` isteyebilir; çalıştırılamazsa "Windows'ta elle denenecekler"e yazılır,
  arayüz Playwright ile doğrulanmış sayılır.
- Windows'a özel yollar (`pythonw`, `CREATE_NO_WINDOW`, PowerShell kodlaması, tepsi bildirimi,
  QtWebEngine'in VDI'da açılması) geliştirme ortamında doğrulanamaz; README'de liste olarak bırakılır.

## 12. Açık sorular ve varsayımlar

| # | Soru | Varsayım | Etkilediği yer |
|---|---|---|---|
| 1 | Çatal nerede yaşayacak? | Ayrı depo (ör. `DevrunnerWeb`), Devrunner `main`'den çatallanmış ve bu oturuma bağlanmış | Depo yoksa iş başlayamaz; alternatif: aynı depoda kalıcı `web` dalı |
| 2 | İç PyPI aynasında `PyQt6` ve `PyQt6-WebEngine` var mı? PyQt6'nın GPL lisansı kurum içi kullanım için uygun mu? | Var ve uygun | Değilse `shell/` PySide6 QtWebEngine'e (LGPL) çevrilir: API aynı, yalnız import'lar değişir; mimari aynı kalır |
| 3 | Yer imi klasörleri ekip geneli mi, kişisel mi? | Klasörler ve etiketler ekip geneli, yıldızlar kişisel | `BookmarkAPI` tabloları |
| 4 | Masaüstü (PySide6) sürüm kullanılmaya devam edecek mi? | Evet, aynı veritabanıyla | `schema_version`'a dokunmama, taleplerin `BilgeAPI` ile yazılması |
| 5 | Uygulama çok kullanıcılı makinelerde (VDI, terminal sunucusu) çalışacak mı? | Olabilir | Belirteç zorunluluğu zaten bunu karşılar; port rastgele |
| 6 | Bilge / DevOps bağlantıları uygulama içinde mi açılsın? | Hayır, sistem tarayıcısında (oturum / SSO orada hazır) | İstenirse Bölüm 13'teki uygulama içi tarayıcı |
| 7 | QtWebEngine banka masaüstlerinde / VDI'da sorunsuz açılıyor mu? | Evet; GPU kapatma seçeneği var | Faz 0'da ilk iş olarak Windows'ta denenmeli |

## 13. İlk sürümde olmayanlar

- **Uygulama içi tarayıcı sekmeleri**: Bilge taleplerini ayrı (yalıtılmış profilli) bir sekmede açmak ve
  araç çubuğundaki ★ ile açık sayfayı yer imlerine eklemek. Kurum SSO'sunun (Kerberos / NTLM) gömülü
  tarayıcıda çalıştığı doğrulanınca eklenir.
- Pod içinde etkileşimli terminal (`webterminal`'deki xterm.js + `exec -it`).
- Arayüze ağdan erişim (sunucu yalnızca yerel makineye hizmet eder; bu bir web uygulaması dağıtımı değildir).
- Backend'de değişiklik gerektiren her şey (yeni ayar, yeni `relations` fonksiyonu, yeni `bus` olayı).

# Devrunner — İlk Geliştirme Planı

Bu belge, Devrunner'ı sıfırdan yazacak modele verilen iş tanımıdır. Kararlar gerekçeleriyle
yazıldı; gerekçe geçerliliğini yitirirse karar da değiştirilebilir. Emin olunamayan noktalar
[Bölüm 10](#10-açık-sorular-ve-varsayımlar)'da varsayım olarak işaretli: kod bu varsayımlarla
yazılır, hepsi ayarlardan değiştirilebilir bırakılır.

## 1. Amaç

Devrunner, Veri Analitiği ekibindeki geliştiricilerin kendi masaüstünde çalıştırdığı bir PySide6
uygulamasıdır. Tek pencereden şunları yapar:

- Azure DevOps'taki `DDDB.Iz.Anm.*` repolarını ve bunların CI/CD pipeline'larını izler.
- Bu uygulamaların Kubernetes'teki container'larını gösterir (pod, log, komut).
- Uygulamaların Dev/Test/Prod adreslerini düzenli yoklar, yanıt süresini ve erişilebilirliği bildirir.
- Ağ teşhisi için gömülü bir terminal sunar (nslookup, curl, kubectl…).
- Ekibin ortak verisini PostgreSQL'de tutar: geliştirici notları, Bilge talep linkleri, freeze günleri, değişiklik geçmişi.
- Çevrimiçi geliştiriciler arasında uçtan uca şifreli P2P sohbet sağlar.

LDAP girişi yoktur. Kullanıcı ilk açılışta adını yazar; servis erişimleri kişinin kendi PAT'i ve
token'ı ile yapılır.

## 2. Bağlam ve kısıtlar

- **Hedef ortam Windows 10/11**, banka iç ağı. Uygulama `Devrunner.pyw` ile (konsolsuz, `pythonw`)
  açılır. Kod Linux/macOS'ta da çalışmalıdır, çünkü geliştirme ve testler orada yapılacak:
  Windows'a özel her şey `sys.platform` kontrolünün arkasında durur.
- **Geliştirme ortamından banka servislerine erişim yoktur** (DevOps, cluster, PostgreSQL, iç DNS).
  Bu yüzden her modül, servisi tanımlı değilken örnek veriyle çalışır (bkz. [4.8](#48-demo-modu)).
  Bu, `hb_streamlit_template`'teki "DSS_HOST boşsa sample_data kullan" kalıbının aynısıdır.
- **Kalıplar `hb_streamlit_template` reposundan alınır**: sayfalar `_pages/`, arka uç `scripts/`,
  tüm ayarlar tek `scripts/config.py` içinde, SQL'de yalnızca `:isim` parametreleri, ortak
  veritabanında tablo adlarına uygulama öneki. Streamlit, LDAP, DSS ve Kron kısımları alınmaz.
- Python 3.10+. Bağımlılıklar banka iç PyPI aynasından kurulacağı için sayısı az tutulur.
- Her geliştirici uygulamanın kendi kopyasını çalıştırır (repo klonu). Ortak olan tek şey PostgreSQL'dir.

## 3. Teknoloji ve klasör yapısı

| Konu | Seçim | Neden |
|---|---|---|
| Arayüz | PySide6 (Qt Widgets, Fusion stili + QSS) | İstenen; Fusion, QSS temalarının her platformda aynı görünmesini sağlar |
| HTTP | `requests` + `truststore` | `truststore` Windows sertifika deposunu kullanır; iç CA için `verify=False` gerekmez |
| Veritabanı | SQLAlchemy 2 + `psycopg2-binary` | Template ile aynı |
| Ayarlar | `python-dotenv` (`set_key` ile yazma) | Secret'lar `.env`'de, arayüzden değişir |
| Kubernetes | resmî `kubernetes` istemcisi | Pod/log/exec için `kubectl` kurulu olmasını gerektirmez |
| Şifreleme | `PyNaCl` (libsodium) | Kendi kripto yazılmaz |
| Eşzamanlılık | `QThreadPool` + `QTimer`; sohbet için `QTcpServer`/`QTcpSocket` | asyncio yok; tek model, Qt sinyalleriyle arayüze dönüş |
| Test | `pytest`, `pytest-qt`, `responses` | `QT_QPA_PLATFORM=offscreen` ile ekransız çalışır |

```
Devrunner/
├── Devrunner.pyw            Giriş noktası (konsolsuz). Log + hata yakalama + tek kopya kontrolü, sonra app.main()
├── app.py                   Ana pencere, PAGES listesi, başlık çubuğu. `python app.py` konsollu hata ayıklama içindir
├── _pages/                  Her modülün arayüzü (bir dosya = soldaki menüde bir öğe)
│   ├── home.py              Uygulamalar özeti (tüm modüllerin kesişimi)
│   ├── devops.py            → scripts/DevopsAPI.py
│   ├── k8s.py               → scripts/K8sAPI.py
│   ├── health.py            → scripts/HealthAPI.py
│   ├── terminal.py          → scripts/TerminalAPI.py
│   ├── notes.py             → scripts/NotesAPI.py
│   ├── bilge.py             → scripts/BilgeAPI.py
│   ├── chat.py              → scripts/ChatAPI.py
│   ├── freeze.py            → scripts/FreezeAPI.py
│   └── settings.py          → scripts/config.py
├── scripts/
│   ├── config.py            Tüm ayarlar: tanım, okuma, .env'e yazma
│   ├── apps.py              Uygulama kataloğu ve adlandırma kuralları (AppRef)
│   ├── relations.py         Modüller arası tek bağlantı noktası (iç API)
│   ├── bus.py               Uygulama geneli Qt sinyalleri
│   ├── worker.py            Arka plan işi çalıştırma yardımcıları
│   ├── database.py          PostgreSQL: engine, read/execute, tablolar, şema sürümü, audit()
│   ├── user.py              Yerel kimlik (ad, kullanıcı kimliği)
│   ├── theme.py             Tema paletleri ve QSS üretimi
│   └── <Modül>API.py        Yukarıdaki modül arka uçları
├── themes/base.qss          Tek QSS şablonu (renkler palet değişkeni)
├── assets/                  İkonlar
├── sample_data/             Demo modu verileri (JSON)
├── tests/
├── .local/                  Üretilen dosyalar: log, kubeconfig, sohbet anahtarı, önbellek (.gitignore)
├── .env.example             config.py'deki tanımlardan üretilir
├── requirements.txt
├── docker-compose.yml       Yerel PostgreSQL (template'teki ile aynı)
└── README.md
```

## 4. Çekirdek mimari

### 4.1 Modül sözleşmesi

Bir modül iki dosyadır: `_pages/x.py` içinde `Page(QWidget)` sınıfı ve `scripts/XAPI.py` içinde
Qt arayüzünden bağımsız arka uç. API dosyaları widget import etmez; böylece arayüz olmadan test edilir.

`app.py` içinde açık bir `PAGES` listesi vardır (template'teki gibi; otomatik keşif yok):

```python
PAGES = [
    PageDef("home",   "Uygulamalar", "_pages.home",   icon="apps"),
    PageDef("devops", "DevOps",      "_pages.devops", icon="pipeline"),
    ...
]
```

Sayfalar ilk açıldıklarında import edilir. Import veya kurulum hatası veren sayfa yerine hata kartı
gösterilir, uygulamanın geri kalanı çalışmaya devam eder. Bir modülün bozulması diğerlerini düşürmemelidir.

**Yeni modül eklemek** dört adımdır ve README'de böyle belgelenir:
1. `scripts/XAPI.py` ve `_pages/x.py` yazılır.
2. `app.py` → `PAGES` listesine eklenir.
3. Ayarları varsa `scripts/config.py` → `SETTINGS` içine bir grup eklenir (ayar ekranı kendiliğinden oluşur).
4. Diğer modüllerle ilişkisi `scripts/relations.py` içine yazılır.

### 4.2 Modüller arası ilişki: `relations.py` + `bus.py`

Kural: **bir modül başka bir modülün sayfasını veya API'sini import etmez.** Modüller arası her
şey `scripts/relations.py` üzerinden geçer; ilişkileri görmek veya değiştirmek isteyen tek dosyaya bakar.

İki tür ilişki vardır:

- **Olaylar** (`scripts/bus.py`, tek bir `QObject`): `app_selected(name)`, `catalog_changed()`,
  `settings_changed(group)`, `freeze_changed()`, `health_changed(app, env, state)`,
  `presence_changed()`, `db_state_changed(ok)`. Modüller yayınlar ve dinler, birbirini tanımaz.
- **Sorgular ve eylemler** (`relations.py` fonksiyonları, iç API): modülün başka modülden
  ihtiyaç duyduğu bilgi. Örnekler: `active_freeze()`, `app_urls(app)`, `app_pods(app, env)`,
  `app_health(app)`, `app_notes_count(app)`, `open_in_terminal(command)`, `open_page(page_id, app=None)`.
  Bu fonksiyonlar ilgili API'yi çağırır; çağıran taraf hangi modülün yanıtladığını bilmez.

İlk sürümde kurulacak ilişkiler:

| Kaynak | Hedef | İlişki |
|---|---|---|
| DevOps | Katalog | Repo listesi uygulama kataloğunu oluşturur (tek doğruluk kaynağı) |
| Katalog | Health | Yoklanacak Dev/Test/Prod adresleri |
| Katalog | K8s | Uygulamanın iş yükünü ve pod'larını bulma |
| Katalog | Notlar, Bilge | Kayıtlar isteğe bağlı olarak bir uygulamaya bağlanır (`app_name`) |
| Herhangi bir sayfa | Hepsi | `app_selected`: seçilen uygulamanın pipeline, pod, sağlık, not, talep bilgisi birlikte gösterilir |
| Freeze | Başlık çubuğu | Bugün freeze içindeyse sağ üstte rozet |
| Freeze | DevOps, K8s | Freeze sırasında CD pipeline'ları ve Prod işlemleri uyarı işaretiyle gösterilir |
| Health | Sistem tepsisi, Uygulamalar | Durum değişince bildirim; özet tabloda renkli nokta + ms |
| Health, K8s, DevOps | Terminal | "Terminalde dene" düğmeleri hazır komutla terminali açar (`nslookup <host>`, `curl.exe -I <url>`, `kubectl logs …`) |
| Ayarlar | Tüm API'ler | `settings_changed(group)`: ilgili istemci yeniden kurulur (ör. kubeconfig yeniden üretilir) |
| Sohbet | Başlık çubuğu | Çevrimiçi kişi sayısı |
| Tüm yazma işlemleri | Değişiklik geçmişi | `audit()` kaydı |

### 4.3 Ayarlar ve secret'lar (`scripts/config.py`)

Template'teki kural korunur: `os.getenv` yalnızca bu dosyada kullanılır. Fark, ayarların
bildirimsel tanımlanmasıdır; aynı tanımdan okuma, `.env`'e yazma, `.env.example` ve ayar ekranı üretilir:

```python
SETTINGS = [
    Group("devops", "Azure DevOps", [
        Setting("DEVOPS_URL", "Collection adresi", help="ör. https://<sunucu>/<collection>"),
        Setting("DEVOPS_PROJECT", "Proje"),
        Setting("DEVOPS_PAT", "Personal Access Token", secret=True),
        Setting("DEVOPS_REPO_PREFIX", "Repo öneki", default="DDDB.Iz.Anm."),
        ...
    ]),
    ...
]
```

- Değerler uygulama klasöründeki `.env` dosyasında durur (`.gitignore`'da). Ortamda zaten tanımlı
  değişkeni `.env` ezmez (template davranışı).
- Ayarlar sayfası her grup için form üretir. `secret=True` alanlar maskeli gösterilir, "göster"
  düğmesi vardır. Kaydet → `dotenv.set_key`, bellekteki değer güncellenir, `settings_changed(group)` yayınlanır.
  Uygulamayı yeniden başlatmak gerekmez.
- Her grupta **"Bağlantıyı sına"** düğmesi bulunur (DevOps: `_apis/connectionData`, PostgreSQL: `SELECT 1`,
  K8s: namespace'te pod listeleme). İlk kurulumda en çok zaman kazandıran özellik budur.
- Secret değerleri log'a, hata penceresine ve audit kaydına yazılmaz. Audit'e yalnızca "DEVOPS_PAT değişti" düşer.
- Pencere konumu, seçili tema, son açık sayfa gibi arayüz durumu `QSettings`'te tutulur; `.env`'e karışmaz.

### 4.4 Temalar (`scripts/theme.py`)

Beş tema: **Aydınlık, Karanlık, Cyberpunk, Dracula, 90'lar**. Tek `themes/base.qss` şablonu ve
her tema için bir palet (arka plan, yüzey, metin, soluk metin, vurgu, kenarlık, başarı, uyarı,
hata, yazı tipi, köşe yarıçapı, kenarlık stili). Beş ayrı QSS dosyası yazılmaz: yeni bir widget
stili eklendiğinde beş dosyayı eşitlemek gerekmesin.

- Durum renkleri (başarı/uyarı/hata) palet değişkenidir; sağlık noktaları ve pipeline rozetleri
  renkleri koda gömmez, temadan alır. Her temada okunur kalmaları kabul ölçütüdür.
- **Dracula**: resmî palet (`#282a36`, `#44475a`, `#f8f8f2`, `#6272a4`, `#bd93f9`, `#ff79c6`, `#50fa7b`, `#ff5555`, `#f1fa8c`, `#8be9fd`).
- **Cyberpunk**: siyaha yakın zemin, neon sarı/camgöbeği/macenta vurgular, başlıklarda eş aralıklı yazı tipi.
- **90'lar**: Windows 95 görünümü — `#c0c0c0` zemin, kabartma (outset/inset) kenarlıklar, lacivert
  başlık şeritleri, köşe yarıçapı 0, Tahoma/MS Sans Serif.
- Tema başlık çubuğundan anında değişir, yeniden başlatma istemez.

### 4.5 Arka plan işleri (`scripts/worker.py`)

Ağ ve veritabanı çağrıları arayüz iş parçacığında yapılmaz; pencere donmamalıdır.
`run_async(fn, *args, on_done=…, on_error=…)` işi `QThreadPool`'da çalıştırır, sonucu sinyalle
arayüz iş parçacığına döndürür. Periyodik işler `QTimer` → `run_async` ile yapılır; önceki tur
bitmeden yenisi başlatılmaz. Sayfalar yükleme ve hata durumlarını gösterir (boş tablo bırakmaz).

### 4.6 Veritabanı, kimlik, değişiklik geçmişi

- `scripts/database.py` template'tekinin Streamlit'siz hâlidir: tek engine, `read_sql` (satır listesi
  döner, pandas yok), `execute`, tablo tanımları, `init_db()`.
- `DB_HOST` boşsa veya bağlantı kurulamıyorsa veritabanı gerektiren modüller açıklayıcı bir
  "bağlantı yok" durumu gösterir; DevOps, K8s, Health, Terminal çalışmaya devam eder. Bağlantı
  durumu durum çubuğunda görünür ve periyodik yeniden denenir.
- **Şema sürümü**: farklı geliştiriciler uygulamanın farklı sürümlerini aynı veritabanına bağlayacak.
  Bu yüzden şema değişiklikleri yalnızca ekleyicidir (yeni tablo, yeni nullable kolon); kolon silinmez,
  yeniden adlandırılmaz. `dr_meta.schema_version` tutulur, uygulama açılışta eksik adımları sırayla
  uygular; veritabanı kendisinden yeniyse uyarı gösterir ama çalışır.
- **Kimlik** (`scripts/user.py`): ilk açılışta ad sorulur. Yerelde bir `USER_ID` (UUID) üretilir,
  ad ile birlikte `.env`'e yazılır ve `dr_users`'a kaydedilir. İşletim sistemi kullanıcı adı ve
  makine adı da kaydedilir. Ad sonradan ayarlardan değiştirilebilir. Bu bir kimlik doğrulama
  değildir: güvene dayalı ekip içi araçtır, README'de böyle yazılır.
- **Değişiklik geçmişi**: veritabanına yazan her API fonksiyonu aynı transaction içinde
  `audit(module, action, entity, entity_id, detail)` çağırır. Ortak kayıtlar silinmez, `deleted_at`
  ile işaretlenir. Geçmiş, Uygulamalar sayfasında ve ilgili kaydın yanında görüntülenir.
- Ortak veriler görünür sayfada 60 sn'de bir ve elle yenilenir. `LISTEN/NOTIFY` ilk sürümde yok.

### 4.7 Uygulama kataloğu ve adlandırma (`scripts/apps.py`)

Adlandırma kuralları tek yerde durur; hiçbir modül önek, sonek veya adres kalıbını kendi içine gömmez.

```
repo            DDDB.Iz.Anm.<ad>
CI pipeline     DDDB.Iz.Anm.<ad>_Container-CI
CD pipeline     DDDB.Iz.Anm.<ad>_Container-CD
Dev             https://<ad>.dk8s.gentest.hqtest.local/
Test            https://<ad>.tk8s.gentest.hqtest.local/
Prod            https://<ad>.pk8s.gen.halkbank.local/
```

`AppRef(name, repo, ci, cd, urls)` bu türetmeleri yapar. Önek, sonekler ve üç adres kalıbı
ayardır (`DEVOPS_REPO_PREFIX`, `PIPELINE_CI_SUFFIX`, `PIPELINE_CD_SUFFIX`, `URL_DEV`, `URL_TEST`,
`URL_PROD`; kalıpta `{app}`). Adres üretirken ad küçük harfe çevrilir. Kurala uymayan uygulamalar
için `dr_app_overrides` tablosunda uygulama bazında adres ve iş yükü adı ezilebilir.

Katalog DevOps repo listesinden oluşur, `.local/cache/apps.json`'a da yazılır: uygulama açılır
açılmaz son bilinen listeyle dolar, DevOps yanıtı gelince güncellenir.

### 4.8 Demo modu

Bir servisin ayarı boşsa ilgili API `sample_data/` altındaki JSON'dan yanıt verir ve sayfada
"örnek veri" şeridi görünür. DevOps, K8s ve Health için gereklidir; PostgreSQL için yerelde
`docker compose up -d` kullanılır. Demo verisi gerçek API yanıt biçimini taşır (aynı ayrıştırma
kodu çalışsın), gerçek ad, IP veya token içermez.

### 4.9 `Devrunner.pyw`

`pythonw` altında `sys.stdout/stderr` `None` olur ve konsol yoktur; buna göre:

- Log `.local/logs/devrunner.log` dosyasına döner (boyut sınırlı, döngülü).
- `sys.excepthook` yakalanmamış hatayı log'a yazar ve kullanıcıya pencereyle gösterir; uygulama sessizce kapanmaz.
- Tek kopya kontrolü (`QLockFile`): ikinci açılış var olan pencereyi öne getirir. Sohbet portu ve
  sistem tepsisi ikonu için gereklidir.
- Windows'ta alt süreçler (PowerShell, kubectl) konsol penceresi açmadan başlatılır (`CREATE_NO_WINDOW`).
- `kur.bat`: `.venv` oluşturur, bağımlılıkları kurar, masaüstüne `.venv\Scripts\pythonw.exe Devrunner.pyw` kısayolu koyar.

### 4.10 Ana pencere

Solda modül menüsü, ortada seçili sayfa. Üst başlık çubuğunda soldan sağa: uygulama adı, tema
seçici, çevrimiçi kişi sayısı, kullanıcı adı ve **en sağda freeze rozeti**. Altta durum çubuğu:
veritabanı durumu, DevOps son eşitleme zamanı, sağlık özeti ("12 çalışıyor · 1 kapalı").
Pencere kapatılınca sistem tepsisine küçülür (yoklama ve sohbet sürsün); tepsi menüsünden çıkılır.

## 5. Modüller

### 5.1 Uygulamalar (`_pages/home.py`)

Açılış sayfası ve modüllerin kesişimi. Her uygulama bir satır: ad · CI durumu · CD durumu ·
Dev/Test/Prod sağlık noktası ve ms · hazır pod sayısı · not ve talep sayısı. Arama kutusu ile
süzülür. Satır seçilince `app_selected` yayınlanır ve sağda ayrıntı paneli açılır: son build'ler,
pod'lar, adresler, notlar, Bilge talepleri, ilgili değişiklik geçmişi ve "Terminalde dene" kısayolları.
Bu sayfa hiçbir API'yi doğrudan çağırmaz; her şeyi `relations.py` üzerinden alır.

### 5.2 DevOps (`_pages/devops.py` → `scripts/DevopsAPI.py`)

Azure DevOps REST API, kişinin kendi PAT'i ile (`Authorization: Basic base64(":" + PAT)`).

Ayarlar: `DEVOPS_URL` (collection adresi), `DEVOPS_PROJECT`, `DEVOPS_PAT`, `DEVOPS_API_VERSION`
(varsayılan `6.0`; şirket içi sunucu sürümüne göre değişebilir), `DEVOPS_REFRESH_SEC` (varsayılan 120).
PAT için gereken yetkiler ayar ekranında yazılır: Code (Read), Build (Read), Release (Read).

| İş | Uç nokta |
|---|---|
| Bağlantı / PAT sınama | `GET {url}/_apis/connectionData` |
| Repolar | `GET {url}/{project}/_apis/git/repositories` → adı önekle başlayanlar |
| Pipeline'lar + son build | `GET …/_apis/build/definitions?name=<önek>*&includeLatestBuilds=true` |
| Build geçmişi | `GET …/_apis/build/builds?definitions=<id>&$top=20&queryOrder=queueTimeDescending` |
| Build adımları / log | `GET …/_apis/build/builds/{id}/timeline`, `…/logs/{logId}` |
| CD klasik release ise | `GET …/_apis/release/definitions?searchText=…`, `…/release/deployments?definitionId=…` |
| Son commit'ler, açık PR'lar | `GET …/git/repositories/{id}/commits`, `…/pullrequests?searchCriteria.status=active` |

- Önekle başlayan **tüm** repolar gelir. Her repo için `_Container-CI` ve `_Container-CD` adlı
  pipeline aranır; yoksa "pipeline yok" gösterilir (hata değildir).
- CD'nin YAML pipeline mı klasik release mi olduğu bilinmiyor: önce build tanımlarında, bulunamazsa
  release tanımlarında aranır.
- Gösterilen: durum (kuyrukta / çalışıyor / başarılı / kısmen / başarısız / iptal), dal, commit,
  tetikleyen kişi, süre, zaman, DevOps'ta aç bağlantısı. Başarısız build'de hangi adımın düştüğü ve log'u.
- Sayfalama `x-ms-continuationtoken` ile izlenir. 401/403 "PAT geçersiz veya yetkisi eksik" olarak,
  ağ hatası ayrı olarak gösterilir.
- İlk sürüm salt okunurdur. Pipeline tetikleme sonraki aşamadır ([Bölüm 11](#11-ilk-sürümde-olmayanlar)).

### 5.3 Kubernetes (`_pages/k8s.py` → `scripts/K8sAPI.py`)

Kubeconfig şablonu uygulamaya gömülüdür; kullanıcı yalnızca cluster adresini ve token'ı girer:

```yaml
apiVersion: v1
kind: Config
clusters:
- cluster:
    insecure-skip-tls-verify: true
    server: {server}
  name: cce-cluster
contexts:
- context:
    cluster: cce-cluster
    namespace: {namespace}
    user: verianalitigi-sa
  name: verianalitigi-context
current-context: verianalitigi-context
users:
- name: verianalitigi-sa
  user:
    token: {token}
```

- Adresler üç ayrı cluster'a işaret ettiği için (dk8s / tk8s / pk8s) ayarlar ortam başınadır:
  `K8S_DEV_SERVER`, `K8S_DEV_TOKEN`, `K8S_TEST_*`, `K8S_PROD_*`, ortak `K8S_NAMESPACE`
  (varsayılan `verianalitigi`). Boş bırakılan ortam kapalıdır. Tek cluster varsa yalnızca biri doldurulur.
- Sunucu alanı `ip`, `ip:port` veya tam adres kabul eder; şema yoksa `https://` eklenir.
- Python istemcisi yapılandırmayı bellekteki sözlükten yükler. Ayrıca her ortam için
  `.local/kube/<ortam>.yaml` üretilir ki Terminal modülünde `kubectl` `KUBECONFIG` ile çalışabilsin.
  Token değişince dosya yeniden yazılır.
- Gösterilen: deployment'lar (hazır/istenen kopya, imaj etiketi), pod'lar (durum, yeniden başlama
  sayısı, yaş, node), container'lar, olaylar, log (son N satır + canlı izleme).
- **Komut çalıştırma**: seçili container'da tek seferlik komut (`exec`), çıktısı gösterilir.
  Etkileşimli kabuk yoktur. Prod'da her komut öncesi onay istenir.
- **Uygulama ↔ iş yükü eşlemesi** sırasıyla denenir: `dr_app_overrides` kaydı → host'u `<ad>.` ile
  başlayan Ingress'in servisi ve seçicisi → adı `<ad>` olan deployment → `app=<ad>` etiketi.
- Servis hesabının yetkisi dar olabilir: 403 alınan kaynak türü "yetki yok" diye gösterilir, sayfanın kalanı çalışır.
- Yeniden başlatma, ölçekleme gibi değiştirici işlemler ilk sürümde yoktur.

### 5.4 Erişilebilirlik (`_pages/health.py` → `scripts/HealthAPI.py`)

Her uygulamanın Dev/Test/Prod adresine düzenli HTTP GET (ICMP ping değil: asıl soru uygulamanın
yanıt verip vermediği).

Ayarlar: `HEALTH_INTERVAL_SEC` (60), `HEALTH_TIMEOUT_SEC` (10), `HEALTH_SLOW_MS` (3000),
`HEALTH_USE_PROXY` (kapalı: iç adresler sistem proxy'sinden geçmesin).

- İstekler eşzamanlı atılır (iş parçacığı havuzu), yanıt başlığı gelince süre ölçülür, gövde indirilmez.
  Sertifika doğrulaması kapalıdır (yalnızca erişilebilirlik ölçülüyor, veri gönderilmiyor).
- Durumlar: **Çalışıyor** (2xx/3xx) · **Yavaş** (eşik üstü) · **Kapalı** (5xx, zaman aşımı, bağlantı reddi) ·
  **Yok** (DNS çözülmüyor ve daha önce hiç yanıt alınmamış: o ortama aktarılmamış sayılır, gri gösterilir, bildirim üretmez).
  K8s tanımlıysa "aktarılmış mı" bilgisi oradan gelir ve bu tahmini düzeltir.
- Durum iki ardışık yoklamada aynı çıkınca değişmiş sayılır (tek seferlik takılma bildirim üretmesin).
- Değişimde sistem tepsisi bildirimi, `health_changed` olayı ve `dr_health_events`'e kayıt (son
  kayıt aynı durumdaysa yazılmaz: birden çok istemci aynı değişimi görür).
- Sayfa: uygulama × ortam tablosu (durum, HTTP kodu, son süre, son 1 saatin süre grafiği, son
  değişim zamanı). Uygulama/ortam bazında "sessize al". Süre geçmişi bellekte tutulur, veritabanına yazılmaz.

### 5.5 Terminal (`_pages/terminal.py` → `scripts/TerminalAPI.py`)

nslookup, curl, ping, kubectl gibi teşhis komutları için.

- **Hangi kabuğun çalıştığı her zaman görünür**: kabuk seçicinin yanında ad, sürüm ve çalıştırılabilir
  dosya yolu ("Windows PowerShell 5.1 — C:\Windows\…\powershell.exe"). Windows'ta seçenekler:
  Windows PowerShell, varsa PowerShell 7 (`pwsh`), CMD. Linux/macOS'ta bash/zsh. Her çıktı bloğunun
  başlığında da hangi kabukta çalıştığı yazar.
- Her komut ayrı süreçte çalışır (`QProcess`); çıktı akarak gelir, bitince çıkış kodu ve süre
  gösterilir, **Durdur** düğmesi süreci ağacıyla sonlandırır. Çalışma dizini uygulama tarafından
  izlenir (`cd` yerleşik ele alınır). Kalıcı oturum ve etkileşimli programlar (parametresiz
  `nslookup`, `vim`) desteklenmez; sayfada belirtilir. Gerekçe: borulu kalıcı PowerShell oturumu
  kırılgandır, teşhis komutları için oturum durumu gerekmez.
- Çıktı kodlaması UTF-8'e sabitlenir (PowerShell'de `[Console]::OutputEncoding`, CMD'de `chcp 65001`);
  yoksa Türkçe Windows'ta karakterler bozulur.
- PowerShell 5.1'de `curl`, `Invoke-WebRequest` takma adıdır: kullanıcı `curl` yazarsa `curl.exe` önerilir.
- Hazır komut düğmeleri seçili uygulamaya göre dolar: `nslookup <host>`, `curl.exe -I <url>`,
  `Test-NetConnection <host> -Port 443`, `kubectl get pods`. Ortam seçilince `KUBECONFIG` ayarlanır.
- Komut geçmişi yereldir (ok tuşları). Komutlar veritabanına veya audit'e yazılmaz: içlerinde secret olabilir.
- Komutlar kullanıcının kendi yetkisiyle çalışır; yetki yükseltme yoktur.

### 5.6 Notlar (`_pages/notes.py` → `scripts/NotesAPI.py`)

Ortak geliştirici notları. Not genel olabilir veya bir uygulamaya (ve isteğe bağlı ortama) bağlanır.
Başlık, gövde (düz metin/Markdown), sabitleme, yazan, zaman. Liste + arama + uygulamaya göre süzme.
Herkes ekler ve düzenler; her değişiklik audit'e düşer, silme `deleted_at` ile yapılır.

### 5.7 Bilge talepleri (`_pages/bilge.py` → `scripts/BilgeAPI.py`)

Bilge'de açılan taleplerin başlık + link listesi. Alanlar: başlık, adres, isteğe bağlı uygulama,
durum (Açık / Devam ediyor / Kapandı), kısa açıklama, ekleyen. Çift tıklama tarayıcıda açar.
Yalnızca `http/https` adresleri kabul edilir. Arama ve duruma göre süzme.

### 5.8 Freeze (`_pages/freeze.py` → `scripts/FreezeAPI.py`)

Geliştiriciler freeze dönemlerini girer: başlangıç ve bitiş tarihi (ikisi de dahil), açıklama.
Takvimde dönemler boyanır, yanında liste durur.

- Bugün bir dönemin içindeyse başlık çubuğunun sağ üstünde belirgin **FREEZE** rozeti görünür;
  ipucunda açıklama ve bitiş tarihi yazar. Üç gün içinde başlayacak freeze daha soluk bir "yaklaşıyor" rozetiyle gösterilir.
- Tarih karşılaştırması yerel tarihle yapılır (Europe/Istanbul). Rozet açılışta, `freeze_changed`
  olayında, periyodik yenilemede ve gece yarısı yeniden hesaplanır.
- `relations.active_freeze()` diğer modüllere sunulur ([4.2](#42-modüller-arası-ilişki-relationspy--buspy)).

### 5.9 Sohbet ve çevrimiçi bilgisi (`_pages/chat.py` → `scripts/ChatAPI.py`)

Çevrimiçi geliştiriciler arasında doğrudan (P2P) ve uçtan uca şifreli sohbet. Çevrimiçi listesi bu katmandan gelir.

- **Buluşma**: eşlerin birbirini bulması için ortak bir nokta gerekir. UDP yayını yalnızca aynı alt
  ağda çalışır (VPN ve farklı VLAN'larda çalışmaz), bu yüzden buluşma noktası ortak PostgreSQL'dir:
  her istemci `dr_presence`'a IP, port ve açık anahtarını yazar, 20 sn'de bir `last_seen`'i
  sunucu saatiyle günceller, düzgün kapanışta satırını siler. Yayınlanacak IP, veritabanı sunucusuna
  giden rotadaki yerel adrestir (birden çok ağ kartı / VPN durumu için).
- **Çevrimiçi**: `last_seen` 60 sn'den yeniyse aday, P2P el sıkışma başarılıysa çevrimiçi.
  Başlık çubuğundaki sayı ve sohbet listesi bunu gösterir.
- **Bağlantı**: her istemci `QTcpServer` ile dinler (`CHAT_PORT`, doluysa boş bir port; gerçek port
  `dr_presence`'a yazılır). Mesajlar veritabanından geçmez, eşler arasında doğrudan TCP ile gider.
  Çerçeve: 4 bayt uzunluk + şifreli gövde, üst sınırlı.
- **Şifreleme** (PyNaCl): her istemci ilk açılışta kalıcı bir X25519 anahtar çifti üretir; gizli
  anahtar `.local/chat/` altında kalır, açık anahtar `dr_presence`'ta yayınlanır. Bağlantıda taraflar
  kalıcı anahtarlarla kimliği doğrulanmış biçimde geçici anahtar değiş tokuş eder; oturum geçici
  anahtarlarla `Box` (XSalsa20-Poly1305) üzerinden şifrelenir. Kendi şifreleme şeması tasarlanmaz.
- **Anahtar güveni**: açık anahtarlar ortak veritabanından geldiği için veritabanına yazabilen biri
  anahtar değiştirebilir. İlk görülen anahtar yerelde sabitlenir, değişirse kullanıcı uyarılır;
  kişi ayrıntısında anahtar parmak izi gösterilir.
- **Kapsam**: birebir sohbet ve "Genel" odası (mesaj her çevrimiçi eşe ayrı ayrı şifrelenip gönderilir;
  küçük ekip için yeterli). Çevrimdışı kişiye mesaj gitmez, arayüz bunu belirtir. Mesaj geçmişi yalnızca
  oturum boyunca bellekte tutulur.
- **Risk — gelen bağlantılar**: Windows Güvenlik Duvarı `pythonw.exe` için dinlemeyi engelleyebilir ve
  kullanıcıların izin verme yetkisi olmayabilir. İki taraftan biri bağlantı kabul edebiliyorsa bağlantı
  o yönde kurulur. İkisi de edemiyorsa yedek yol: aynı uçtan uca şifreli çerçeveler PostgreSQL
  `LISTEN/NOTIFY` üzerinden aktarılır (veritabanı yalnızca şifreli metni görür). Bu yedek yol,
  doğrudan bağlantının banka ağında çalışmadığı görülürse eklenir; taşıma katmanı buna izin verecek
  biçimde soyutlanır.

## 6. Veri modeli

Tüm tablolar `dr_` önekiyle ortak PostgreSQL'dedir.

| Tablo | Kolonlar |
|---|---|
| `dr_meta` | `key` PK, `value` — `schema_version` |
| `dr_users` | `id` UUID PK, `display_name`, `os_user`, `hostname`, `app_version`, `created_at`, `last_seen_at` |
| `dr_presence` | `user_id` PK, `ip`, `port`, `pubkey`, `app_version`, `last_seen` |
| `dr_notes` | `id`, `app_name` NULL, `env` NULL, `title`, `body`, `pinned`, `created_by`, `created_at`, `updated_by`, `updated_at`, `deleted_at` |
| `dr_bilge_requests` | `id`, `title`, `url`, `app_name` NULL, `status`, `description`, `created_by`, `created_at`, `updated_at`, `deleted_at` |
| `dr_freezes` | `id`, `start_date`, `end_date`, `title`, `created_by`, `created_at`, `deleted_at` |
| `dr_health_events` | `id`, `app_name`, `env`, `state`, `status_code`, `response_ms`, `detail`, `observed_by`, `observed_at` |
| `dr_app_overrides` | `app_name` PK, `url_dev`, `url_test`, `url_prod`, `k8s_workload`, `health_muted` (sessize alınan ortamlar, ör. `dev,prod`), `updated_by`, `updated_at` |
| `dr_audit_log` | `id`, `at`, `user_id`, `user_name`, `module`, `action`, `entity`, `entity_id`, `app_name` (uygulamaya göre süzme için), `detail` JSONB |

Zaman damgaları veritabanı saatiyle (`now()`) yazılır; istemci saatleri farklı olabilir.

## 7. Güvenlik kuralları

- PAT, token ve veritabanı parolası yalnızca `.env`'de durur; `.env` ve `.local/` `.gitignore`'dadır.
  Bu değerler log'a, hata penceresine, audit'e ve demo verisine girmez. Hata mesajları yazılmadan
  önce bilinen secret değerlerinden arındırılır (URL'e gömülü token dahil).
- SQL değerleri her zaman `:isim` parametresiyle verilir.
- DevOps çağrılarında sertifika doğrulaması açıktır (`truststore`). Doğrulamanın kapalı olduğu iki yer
  bilinçlidir: K8s (verilen kubeconfig `insecure-skip-tls-verify: true`) ve sağlık yoklaması.
- Prod'u etkileyen her işlem (ilk sürümde yalnızca K8s `exec`) onay ister ve audit'e düşer.
- Arayüzden açılan adresler `http/https` ile sınırlıdır.
- Ortak veritabanı kullanıcısının yetkisi `dr_*` tablolarıyla sınırlı olmalıdır (README'ye not).

## 8. Fazlar

Her faz sonunda uygulama açılır ve o faza kadarki her şey çalışır durumdadır. Fazlar ayrı commit'lerdir.

**Faz 0 — İskelet.** Klasör yapısı, `Devrunner.pyw`, log ve hata yakalama, tek kopya, `config.py`
(+ `.env` okuma/yazma, `.env.example` üretimi), ana pencere, `PAGES` ve tembel yükleme, `bus`,
`relations` iskeleti, `worker`, beş tema, Ayarlar sayfası, ilk açılışta ad sorma.
*Kabul:* `.pyw` çift tıklamayla açılır; beş tema anında değişir; ayar ekranından girilen değer
`.env`'e yazılır ve yeniden açılışta okunur; hata veren bir sayfa uygulamayı düşürmez.

**Faz 1 — Ortak veri.** `database.py`, şema sürümü, `dr_users`, audit, Notlar, Bilge talepleri,
Freeze ve başlık rozeti.
*Kabul:* iki ayrı kopya aynı veritabanına bağlıyken birinin eklediği not/talep/freeze diğerinde
bir dakika içinde görünür; bugünü kapsayan freeze girilince rozet çıkar; veritabanı kapalıyken
uygulama açılır ve durum anlaşılır biçimde gösterilir.

**Faz 2 — DevOps ve katalog.** `apps.py`, `DevopsAPI`, DevOps sayfası, demo verisi.
*Kabul:* önekle başlayan tüm repolar listelenir; CI/CD pipeline'ı olanların son durumu görünür,
olmayanlar "pipeline yok" olarak işaretlenir; geçersiz PAT anlaşılır hata verir.

**Faz 3 — Erişilebilirlik.** `HealthAPI`, Health sayfası, tepsi bildirimi, `dr_health_events`.
*Kabul:* yerel bir test sunucusu kapatılınca iki yoklama içinde "Kapalı"ya döner ve tek bildirim
üretir; hiç aktarılmamış ortam gri görünür, bildirim üretmez.

**Faz 4 — Kubernetes.** `K8sAPI`, kubeconfig üretimi, pod/log/exec, uygulama eşlemesi.
*Kabul:* demo veriyle sayfa dolar; üretilen kubeconfig verilen şablonla birebir aynı yapıdadır;
403 dönen kaynak sayfayı bozmaz.

**Faz 5 — Terminal.** *Kabul:* etkin kabuk adı ve sürümü görünür; uzun süren komut durdurulabilir;
Türkçe karakterler bozulmaz; "Terminalde dene" düğmeleri diğer sayfalardan komutla açar.

**Faz 6 — Sohbet.** `ChatAPI`, presence, şifreleme, birebir + Genel.
*Kabul:* aynı makinede iki kopya (farklı `USER_ID` ve port) birbirini çevrimiçi görür ve mesajlaşır;
ağ trafiğinde düz metin yoktur; anahtarı değişen eş için uyarı çıkar.

**Faz 7 — Birleştirme.** Uygulamalar sayfası ve ayrıntı paneli, kalan ilişkiler, `kur.bat`, README
(kurulum, PAT yetkileri, yeni modül ekleme, sorun giderme).

## 9. Test ve doğrulama

- API katmanı arayüzsüz test edilir: DevOps yanıt ayrıştırma (`responses` ile kayıtlı JSON),
  adlandırma türetmeleri, freeze tarih mantığı (sınır günleri dahil), `.env` yazma/okuma, sağlık
  durumu sınıflandırma ve çift yoklama kuralı, kubeconfig üretimi, şifreleme gidiş-dönüşü, secret arındırma.
- Arayüz `pytest-qt` ve `QT_QPA_PLATFORM=offscreen` ile: uygulama açılır, her sayfa demo veriyle
  yüklenir, beş temanın her biri uygulanır. Her faz sonunda `widget.grab()` ile ekran görüntüsü
  alınıp gözle kontrol edilir (özellikle beş temada durum renklerinin okunurluğu).
- Sohbet: tek süreçte iki eş, loopback üzerinden uçtan uca.
- Veritabanı testleri yerel PostgreSQL'e karşı (docker-compose); yoksa atlanır.
- Windows'a özel yollar (PowerShell kodlaması, `CREATE_NO_WINDOW`, `pythonw`) geliştirme ortamında
  doğrulanamaz: README'de "Windows'ta elle denenecekler" listesi olarak bırakılır.

## 10. Açık sorular ve varsayımlar

Kod aşağıdaki varsayımlarla yazılır. Yanıtlar farklı çıkarsa etkilenen yer belirtilmiştir; hepsi ayardır.

| # | Soru | Varsayım | Etkilediği yer |
|---|---|---|---|
| 1 | Ortak PostgreSQL'e geliştirici masaüstlerinden (5432) erişim var mı? | Var | Yoksa Faz 1 ve sohbet buluşması çalışmaz; altyapıdan erişim gerekir |
| 2 | DevOps collection adresi, proje adı ve sunucu sürümü nedir? Repolar tek projede mi? | **Yanıtlandı:** `https://devops.halkbank.com.tr/DefaultCollection`, proje `Halkbank` (repo adresi `…/Halkbank/_git/DDDB.Iz.Anm.<ad>`). Sunucu sürümü bilinmiyor: API 6.0 varsayıldı | `DEVOPS_*` ayarları |
| 3 | `_Container-CD` YAML pipeline mı, klasik Release mi? | Bilinmiyor: ikisi de aranır | `DevopsAPI` |
| 4 | Dev/Test/Prod ayrı cluster ve ayrı token mı? API portu nedir? | Üç ayrı cluster; port kullanıcı girer | `K8S_<ORTAM>_*` |
| 5 | Pod/deployment adı repo adından nasıl türüyor? | Ingress host'u, sonra küçük harfli ad | K8s eşlemesi, `dr_app_overrides` |
| 6 | Servis hesabı `pods/exec`, log ve ingress okuma yetkisine sahip mi? | Kısmen; 403 hoş görülür | K8s sayfası |
| 7 | Masaüstleri arasında doğrudan TCP mümkün mü (güvenlik duvarı, VLAN, VPN)? | Mümkün | Değilse sohbette `LISTEN/NOTIFY` yedeği |
| 8 | İç PyPI aynasında PySide6, PyNaCl, kubernetes, truststore var mı? | Var | `requirements.txt`, kurulum |
| 9 | Sohbet geçmişi kalıcı olsun mu? | Hayır, yalnızca oturum | `ChatAPI` |
| 10 | Adında büyük harf, nokta veya alt çizgi olan repolarda adres nasıl oluşuyor? | Küçük harfe çevrilir | `apps.py`, `dr_app_overrides` |

## 11. İlk sürümde olmayanlar

Bilinçli olarak dışarıda bırakılanlar; mimari bunlara engel olmayacak biçimde kurulur:

- DevOps'tan pipeline tetikleme (eklendiğinde freeze sırasında CD tetikleme uyarı/onay ister).
- K8s'te yeniden başlatma, ölçekleme ve etkileşimli kabuk.
- Sohbette dosya gönderme, kalıcı geçmiş, çevrimdışı mesaj.
- `LISTEN/NOTIFY` ile anlık eşitleme.
- Secret'ların işletim sistemi kasasında (Windows Credential Manager) saklanması.
- Tek dosyalık `.exe` paketleme ve otomatik güncelleme.

# Devrunner

Veri Analitiği geliştiricileri için modüler masaüstü uygulaması (PySide6). Tek pencereden:

| Modül | Ne yapar | Nereden |
|---|---|---|
| **Uygulamalar** | Tüm `DDDB.Iz.Anm.*` uygulamalarının özeti: CI/CD, Dev/Test/Prod erişimi, pod, not, talep, değişiklik geçmişi | tüm modüller (`relations.py`) |
| **DevOps** | Repolar, `…_Container-CI` / `…_Container-CD` pipeline'ları, build geçmişi, düşen adım ve log'u, commit'ler, açık PR'lar | Azure DevOps (kişisel PAT) |
| **Kubernetes** | Deployment, pod, container log'u (canlı izleme), olaylar, tek seferlik komut | gömülü kubeconfig + kullanıcının cluster adresi / token'ı |
| **Erişilebilirlik** | Uygulama adreslerine düzenli HTTP isteği, yanıt süresi, son 1 saatin grafiği, durum değişince bildirim | `https://<ad>.dk8s… / tk8s… / pk8s…` |
| **Terminal** | nslookup, curl, ping, kubectl; hangi kabuğun çalıştığı (ad, sürüm, yol) her zaman görünür | PowerShell / CMD / bash |
| **Notlar** | Ekibin ortak notları, genel veya uygulamaya bağlı, Markdown | ortak PostgreSQL |
| **Bilge Talepleri** | Bilge'de açılan taleplerin başlık + link listesi | ortak PostgreSQL |
| **Freeze** | Freeze dönemleri; içindeysek sağ üstte **FREEZE** rozeti | ortak PostgreSQL |
| **Sohbet** | Çevrimiçi geliştiricilerle P2P, uçtan uca şifreli sohbet | eşler PostgreSQL'den bulunur, mesajlar doğrudan gider |
| **Ayarlar** | Tüm ayarlar ve secret'lar; `.env`'e yazılır, yeniden başlatma gerekmez | `scripts/config.py` |

Temalar: **Aydınlık, Karanlık, Cyberpunk, Dracula, 90'lar** (başlık çubuğundan anında değişir).

LDAP girişi yoktur: ilk açılışta adınız sorulur. Servislere kendi PAT'iniz ve token'ınızla erişilir.

Servis ayarı girilmemiş modüller **örnek veriyle** açılır (sayfada "örnek veri" şeridi görünür). Böylece
banka ağına erişim olmadan da uygulama denenebilir ve geliştirilebilir.

## Kurulum (Windows)

Python 3.10 veya üzeri gerekir.

1. Depoyu klonlayın.
2. `kur.bat`'a çift tıklayın: `.venv` oluşturur, bağımlılıkları kurar, masaüstüne **Devrunner** kısayolu koyar.
3. Kısayolla açın (`pythonw Devrunner.pyw`, konsol penceresi açılmaz). İlk açılışta adınızı yazın.
4. **Ayarlar** sayfasında doldurun ve her grupta **Bağlantıyı sına**'ya basın:
   * **Azure DevOps**: Personal Access Token. Adres (`https://devops.halkbank.com.tr/DefaultCollection`) ve proje (`Halkbank`) hazır gelir.
     PAT yetkileri: *Code (Read)*, *Build (Read)*, *Release (Read)*.
   * **PostgreSQL**: ortak veritabanı (notlar, talepler, freeze, sohbet buluşması, değişiklik geçmişi).
   * **Kubernetes**: Dev / Test / Prod için cluster adresi (`ip`, `ip:port` veya `https://…`) ve servis hesabı token'ı.
     Boş bırakılan ortam kapalıdır.

Elle kurulum veya Linux / macOS:

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip ...
```

```bash
.venv/bin/python app.py                          # konsollu açılış (hata ayıklama)
```

Açılış hataları ve log: `.local/logs/devrunner.log`. Uygulama zaten açıksa ikinci açılış mevcut pencereyi öne getirir.
Pencereyi kapatmak uygulamayı sistem tepsisine küçültür (yoklama ve sohbet sürer); çıkmak için tepsi simgesi → **Çıkış**.

### Aynı makinede ikinci kopya (ör. sohbeti denemek)

```bash
.venv/bin/python Devrunner.pyw --profile ikinci
```

Bu kopya `.env.ikinci` ve `.local/ikinci/` kullanır (ayrı kimlik, ayrı sohbet anahtarı). Sohbet portunu
ikinci kopyada farklı verin (Ayarlar → Sohbet) ya da boş port otomatik seçilir.

## Ayarlar ve secret'lar

Tüm ayarlar `scripts/config.py` içindeki `SETTINGS` listesinde tanımlıdır; değerler uygulama klasöründeki
`.env` dosyasına yazılır (`.gitignore`'da, depoya girmez). Ayarlar ekranı bu listeden kendiliğinden oluşur.
Tam liste ve açıklamalar: `.env.example`.

* İşletim sistemi ortamında tanımlı bir değişken `.env`'deki değeri ezer (ekranda belirtilir).
* PAT, token ve parolalar log'a, hata pencerelerine ve değişiklik geçmişine yazılmaz (`config.redact`).
  Geçmişe yalnızca "DEVOPS_PAT değişti" bilgisi düşer.
* Pencere konumu, tema, terminal geçmişi gibi kişisel arayüz durumu `QSettings`'te tutulur.

## Adlandırma kuralları

```
repo            DDDB.Iz.Anm.<ad>
CI pipeline     DDDB.Iz.Anm.<ad>_Container-CI
CD pipeline     DDDB.Iz.Anm.<ad>_Container-CD       (YAML pipeline yoksa klasik Release tanımlarında aranır)
Dev             https://<ad>.dk8s.gentest.hqtest.local/
Test            https://<ad>.tk8s.gentest.hqtest.local/
Prod            https://<ad>.pk8s.gen.halkbank.local/
```

Önek, sonekler ve adres kalıpları ayardır. Adreste ad küçük harfe çevrilir. Kurala uymayan bir uygulamanın
adresi veya Kubernetes iş yükü adı **Uygulamalar → adres / eşleme düzenle** ile ezilebilir (ekip geneli,
`dr_app_overrides`). Öneki `DDDB.Iz.Anm.` olan **tüm** repolar listelenir; pipeline'ı olmayanlar
"pipeline yok" olarak görünür.

## Mimari

```
Devrunner/
├── Devrunner.pyw          Konsolsuz giriş: --profile, başlatma hatasını pencereyle gösterir
├── app.py                 Ana pencere, PAGES listesi, başlık çubuğu (tema, çevrimiçi, kullanıcı, FREEZE), tepsi
├── _pages/                Arayüz: her dosya soldaki menüde bir sayfa (Page sınıfı)
│   ├── _common.py         Ortak parçalar: Page iskeleti, Banner, tablo, ui.on(...)
│   └── devops.py, k8s.py, health.py, terminal.py, notes.py, bilge.py, freeze.py, chat.py, home.py, settings.py
├── scripts/               Arka uç (widget import etmez, arayüzsüz test edilir)
│   ├── config.py          Tüm ayarlar: tanım, okuma, .env'e yazma, secret arındırma
│   ├── relations.py       Modüller arası TEK bağlantı noktası (iç API) + arka plan servisleri
│   ├── bus.py             Uygulama geneli olaylar (Qt sinyalleri)
│   ├── worker.py          run_async / Poller: ağ ve veritabanı işleri arayüzü dondurmaz
│   ├── database.py        PostgreSQL: tablolar, şema sürümü, audit()
│   ├── apps.py            Uygulama kataloğu ve adlandırma kuralları
│   ├── theme.py           Beş tema paleti; themes/base.qss tek şablon
│   ├── user.py, fmt.py, logsetup.py
│   └── DevopsAPI.py, K8sAPI.py, HealthAPI.py, TerminalAPI.py, NotesAPI.py, BilgeAPI.py, FreezeAPI.py, ChatAPI.py
├── sample_data/           Demo verisi (gerçek API yanıt biçiminde); tools/make_sample_data.py üretir
├── tests/                 pytest + pytest-qt (ekransız)
└── .local/                Üretilen dosyalar: log, kubeconfig, sohbet anahtarı, katalog önbelleği (.gitignore)
```

**Kurallar**

* Bir modül başka bir modülün sayfasını veya API'sini **import etmez**. Başka modülden bilgi / eylem
  gerekiyorsa `scripts/relations.py`'ye fonksiyon eklenir; olaylar `scripts/bus.py` üzerinden yayınlanır.
  İlişkilerin tamamı `relations.py`'nin başındaki tabloda.
* Ağ ve veritabanı çağrıları arayüz iş parçacığında yapılmaz: `run_async(fn, on_done=..., owner=self)`.
* Sayfalar global olaylara `ui.on(bus.olay, self, fn)` ile bağlanır (sayfa silinirse bağlantı kopar).
* `os.getenv` yalnızca `config.py`'de. Renk koda yazılmaz: `theme.color("success")` / palet anahtarı.
* SQL'de değerler yalnızca `:isim` parametresiyle. Veritabanına yazan her fonksiyon aynı transaction'da
  `audit()` çağırır; ortak kayıtlar silinmez, `deleted_at` ile işaretlenir.
* Şema değişiklikleri yalnızca ekleyicidir (yeni tablo / nullable kolon): farklı sürümler aynı veritabanını
  kullanır. Yeni adım: `database.SCHEMA_VERSION`'ı artırıp `MIGRATIONS`'a fonksiyon ekleyin.

### Yeni modül eklemek

1. `scripts/XAPI.py` (arka uç, Qt widget'sız) ve `_pages/x.py` (`class Page(_common.Page)`) yazın.
2. `app.py` → `PAGES` listesine `PageDef("x", "Başlık", "_pages.x", "⚑")` ekleyin.
3. Ayarı varsa `scripts/config.py` → `SETTINGS`'e bir `Group` ekleyin; sonra `python -m scripts.config`
   ile `.env.example`'ı güncelleyin. Ayarlar ekranı kendiliğinden oluşur.
4. Diğer modüllerle ilişkisini `scripts/relations.py`'ye yazın (gerekirse `bus.py`'ye olay ekleyin).

Bir modülün içe aktarma / kurulum hatası uygulamayı düşürmez: o sayfanın yerine hata kartı gösterilir.

## Ortak veri (PostgreSQL, `dr_` önekli tablolar)

| Tablo | İçerik |
|---|---|
| `dr_meta` | `schema_version` |
| `dr_users` | kullanıcı kimliği, görünen ad, işletim sistemi kullanıcısı, makine, sürüm |
| `dr_presence` | sohbet için IP, port, açık anahtar, `last_seen` |
| `dr_notes` | notlar (uygulama / ortam bağlantılı, sabitleme, yumuşak silme) |
| `dr_bilge_requests` | Bilge talepleri (başlık, adres, uygulama, durum) |
| `dr_freezes` | freeze dönemleri (başlangıç ve bitiş dahil) |
| `dr_health_events` | erişilebilirlik durum değişimleri (birden çok istemci aynı değişimi tek kayıt yazar) |
| `dr_app_overrides` | uygulama bazında adres / iş yükü ezmesi, sessize alınan ortamlar |
| `dr_audit_log` | tüm değişikliklerin geçmişi (kim, ne zaman, hangi modül, hangi uygulama) |

Tablolar ilk bağlantıda kendiliğinden oluşur. Veritabanı kullanıcısının yetkisini `dr_*` tablolarıyla sınırlayın.

## Modül ayrıntıları

**Erişilebilirlik.** HTTP GET (ICMP değil), başlık gelince süre ölçülür, gövde indirilmez, sertifika doğrulanmaz.
Durumlar: *Çalışıyor* (yanıt var), *Yavaş* (eşik üstü), *Kapalı* (5xx, zaman aşımı, bağlantı reddi),
*Yok* (DNS çözülmüyor ve hiç yanıt alınmamış: o ortama aktarılmamış sayılır, bildirim üretmez). Durum iki
ardışık yoklamada aynı çıkınca değişmiş sayılır. Sistem proxy'si varsayılan olarak kullanılmaz.

**Kubernetes.** Kubeconfig şablonu (`K8sAPI.KUBECONFIG_TEMPLATE`) verilen yapıyla birebir aynıdır; kullanıcı
yalnızca adres ve token girer. Terminal'de `kubectl` için `.local/kube/<ortam>.yaml` üretilir ve seçilen
ortamda `KUBECONFIG` ayarlanır. Uygulama → iş yükü eşlemesi: elle tanımlı → `<ad>.` ile başlayan Ingress'in
servisi → `<ad>` adlı deployment → `app=<ad>` etiketi. Servis hesabının yetkisi olmayan kaynaklar "yetki yok"
gösterilir, sayfanın kalanı çalışır. Prod'da komut çalıştırmak onay ister ve geçmişe yazılır.

**Terminal.** Her komut ayrı süreçte çalışır (kalıcı oturum yok); `cd` uygulama tarafından izlenir. Çıktı
UTF-8'e sabitlenir. Windows PowerShell 5.1'de `curl` yazılırsa `curl.exe` önerilir. **Durdur** komutu alt
süreçleriyle birlikte sonlandırır. Komutlar veritabanına yazılmaz (içlerinde secret olabilir).

**Sohbet.** Ayrıntılı tasarım `scripts/ChatAPI.py`'nin başında. Özet: eşler PostgreSQL'deki `dr_presence`
üzerinden buluşur; mesajlar doğrudan TCP ile gider; kalıcı X25519 anahtarlarıyla kimliği doğrulanmış geçici
anahtar değişimi + `Box` (XSalsa20-Poly1305); ilk görülen anahtar yerelde sabitlenir, değişirse uyarı verilir
ve bağlantı kurulmaz. Geçmiş yalnızca oturum boyunca tutulur.

**Freeze.** Bugün bir dönemin içindeysek sağ üstte **❄ FREEZE**, `FREEZE_WARN_DAYS` içinde başlayacaksa
"Freeze N gün sonra" rozeti. DevOps'ta CD sekmesi ve Kubernetes'te Prod işlemleri uyarı gösterir.

## Testler

```bash
pip install -r requirements-dev.txt
```

```bash
pytest
```

Veritabanı testleri yerel PostgreSQL ister (`docker compose up -d`), yoksa atlanır:

```bash
TEST_DB_HOST=localhost TEST_DB=app TEST_DB_USER=app TEST_DB_PW=app pytest
```

Kapsam: DevOps ayrıştırma ve hata eşlemesi, adlandırma, `.env` okuma / yazma, secret arındırma, sağlık
durum makinesi ve yerel HTTP sunucusuna yoklama, freeze sınır günleri, kubeconfig'in verilen şablonla
aynılığı, K8s iş yükü eşlemesi, terminal (Türkçe çıktı, Durdur), sohbet kriptosu ve iki eşin loopback
üzerinden şifreli mesajlaşması, notlar / talepler / freeze / ezmeler / geçmiş, tüm sayfaların beş temada
açılması.

## Windows'ta elle denenecekler

Geliştirme ortamında (Linux) doğrulanamayan, ilk kurulumda bakılması gerekenler:

* `kur.bat` kısayolu ve `Devrunner.pyw` ile konsolsuz açılış; başlatma hatası penceresi.
* Terminal: Windows PowerShell 5.1, PowerShell 7 ve CMD'de Türkçe karakterler; `curl.exe`, `nslookup`,
  `Test-NetConnection`; **Durdur**'un `ping -t` gibi komutları sonlandırması; konsol penceresi açılmaması.
* Windows Güvenlik Duvarı'nın sohbet portunu (varsayılan 47800) engelleyip engellemediği. İki taraftan biri
  gelen bağlantıyı kabul edebiliyorsa sohbet çalışır; ikisi de edemiyorsa PLAN.md 5.9'daki yedek yol gerekir.
* DevOps'a `truststore` ile (Windows sertifika deposu) TLS doğrulamalı bağlantı.
* Tepsi bildirimleri.

## Sorun giderme

| Belirti | Bakılacak yer |
|---|---|
| DevOps "PAT geçersiz" | PAT süresi ve yetkileri (Code/Build/Release Read); Ayarlar → Azure DevOps → Bağlantıyı sına |
| DevOps "Sertifika doğrulanamadı" | Kurum CA'sı Windows deposunda mı? Son çare: "Sertifikayı doğrula" kapalı |
| Notlar / Freeze / Sohbet kapalı | Ayarlar → PostgreSQL → Bağlantıyı sına; durum çubuğundaki "PostgreSQL" yazısının ipucu |
| Sohbette kişi var ama "bağlantı kurulamadı" | Güvenlik duvarı / VLAN; Ayarlar → Sohbet → Yayınlanacak IP |
| K8s "yetki yok (403)" | Servis hesabının RBAC yetkisi (pods, pods/log, pods/exec, ingresses, services, events) |
| Uygulama açılmıyor | `.local/logs/devrunner.log`, `.local/logs/baslatma_hatasi.log`; `python app.py` ile konsollu açın |

Planlama belgesi ve açık sorular: [PLAN.md](PLAN.md).

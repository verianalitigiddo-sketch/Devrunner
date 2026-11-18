# Devrunner Web — v2 Geliştirme Planı

Bu belge, `DevrunnerWeb` dalındaki uygulamanın ikinci turunu yapacak modele verilen iş tanımıdır.
Önce `README.md` ve `PLAN_WEB.md`'yi okuyun; oradaki kurallar aynen geçerlidir. Beş iş var:

| # | İş | Bölüm |
|---|---|---|
| A | Sabit port (`DEVRUNNER_PORT`) ve tarayıcıdan erişim | [2](#2-sabit-port-ve-tarayıcıdan-erişim) |
| B | Kubernetes: deployment listelemeden çalışma (403 hatası) | [3](#3-kubernetes-deployment-listelemeden-çalışma) |
| C | Ekran titremesi | [4](#4-ekran-titremesi) |
| D | Native terminaller: yerel PTY kabuğu ve pod içinde etkileşimli kabuk (webterminal gibi) | [5](#5-native-terminaller) |
| E | **Aura**: oyunlaştırma, seviye, unvan ve etiketler | [6](#6-aura-oyunlaştırma) |

Emin olunamayan noktalar [Bölüm 9](#9-açık-sorular-ve-varsayımlar)'da varsayım olarak işaretli.

## 1. Kurallar ve ortak altyapı

**Backend dondurma kuralı sürer.** `scripts/` altındaki mevcut dosyalar değişmez; yeni dosya eklenebilir.
Bu planda iki yeni backend dosyası var: `scripts/K8sExecAPI.py` ve `scripts/AuraAPI.py`.
`tests/test_web_infra.py::test_backend_is_frozen` geçmeye devam etmelidir.

**Web'e özgü ayarlar: `web/web_config.py` (yeni).** `config.SETTINGS`'e ayar eklenemediği için web katmanının
ayarları burada tanımlanır: aynı `Setting` / `Group` veri sınıfları (yalnızca import edilir), aynı `.env`
dosyası (`dotenv.set_key`), aynı öncelik (ortam değişkeni > `.env` > varsayılan). Ayarlar sayfası backend
gruplarının ardına bu grupları da gösterir. Web katmanında `os.getenv` yalnızca bu dosyada kullanılır.
`.env.example` backend'den üretildiği için bu anahtarlar README'de belgelenir.

| Anahtar | Varsayılan | Anlamı |
|---|---|---|
| `DEVRUNNER_PORT` | `28657` | Arayüz sunucusunun portu (`0` = rastgele) |
| `UI_EFFECTS` | `normal` | `normal` / `azaltilmis` / `kapali` (Bölüm 4) |
| `TERMINAL_DETACHED_MIN` | `30` | Bağlı sekmesi kalmayan terminal oturumu kaç dakika sonra kapanır |
| `TERMINAL_MAX_SESSIONS` | `8` | Aynı anda açık terminal oturumu |
| `AURA_ENABLED` | `1` | Aura sayfası ve rozetleri |
| `AURA_SINCE_DAYS` | `180` | İlk eşitlemede geriye dönük kaç gün taranır |
| `AURA_SYNC_MIN` | `15` | DevOps'tan eşitleme aralığı (dakika) |

## 2. Sabit port ve tarayıcıdan erişim

- Backend varsayılan olarak **`127.0.0.1:28657`**'ye bağlanır. Gerekçe: Windows (49152+) ve Linux (32768+)
  geçici port aralıklarının altında, `/etc/services`'te kayıtlı değil, bir Fibonacci sayısı olduğu için akılda kalır.
  `DEVRUNNER_PORT` ile değişir. Yalnızca `127.0.0.1` dinlenir; bu değişmez.
- **Port doluysa** backend rastgele porta düşmez (yer imi sessizce bozulmasın): `ERROR port_in_use <port>`
  yazar ve kabuk hata ekranında "28657 portu kullanılıyor; başka bir Devrunner açık olabilir ya da
  Ayarlar → Web'den portu değiştirin" gösterir.
- `--profile <ad>` ile açılan kopyalar, `DEVRUNNER_PORT` kendi `.env.<ad>` dosyalarında verilmemişse rastgele port kullanır.
- **Tarayıcıda açma.** Tepsi menüsüne ve başlık çubuğuna "Tarayıcıda aç": sistem tarayıcısında
  `http://127.0.0.1:<port>/auth?t=<belirteç>` açılır.
- **Yer iminin yeniden başlatmadan sonra da çalışması.** Bugün çerez her açılışta geçersiz oluyor. Çerez
  imzalama sırrı profil başına `.local/web_secret` dosyasında (yalnız sahibi okuyabilir) kalıcı tutulur ve
  çerez 30 gün ömürlü olur; böylece bir kez "Tarayıcıda aç" denmiş tarayıcıda `127.0.0.1:28657` doğrudan açılır.
  `/auth` belirteci yine her açılışta yenidir. Ayarlar → Web'e "Tüm tarayıcı oturumlarını kapat" (sırrı yeniler) eklenir.
  Güven düzeyi `.env` ile aynıdır (PAT de aynı klasörde durur). `Host` / `Origin` / `X-Devrunner` denetimleri aynen kalır.

*Kabul:* uygulama 28657'de açılır; tarayıcıda bir kez yetkilendirilen adres yeniden başlatmadan sonra çalışır;
çerezsiz tarayıcı "Oturum yok" görür; port doluyken anlaşılır hata çıkar; başka makineden bağlanılamaz.

## 3. Kubernetes: deployment listelemeden çalışma

**Sorun.** Servis hesabının `deployments` yetkisi yok ve olması da gerekmiyor. Bugün iki dondurulmuş
fonksiyon yine de listelemeyi deniyor:

- `K8sAPI.resolve_workload` → 403'ü uyarı olarak ekliyor (ekrandaki hata mesajı buradan).
- `relations.pod_summary` → 403'te **o ortamı tümüyle atlıyor**; Uygulamalar tablosundaki Pod sütunu boş kalıyor.
- Ayrıca `web/pages/k8s.py` "Tüm namespace" görünümünde deployment tablosu için çağırıyor.

**Çözüm: `web/k8s_access.py` (yeni).** Web katmanının tüm Kubernetes erişimi buradan geçer; deployment
uç noktası **hiç çağrılmaz**.

- `NoDeployCluster`: `K8sAPI.cluster(env)`'i sarar, `deployments()` API'ye gitmeden `[]` döner.
  `K8sAPI.resolve_workload(app, env, c)` zaten cluster parametresi aldığı için dondurulmuş eşleme kodu
  bununla uyarısız çalışır.
- `workload(app, env)` ve `pod_summary()`: `relations.app_workload` / `relations.pod_summary` yerine
  kullanılır (Uygulamalar ve Kubernetes sayfaları). Bu, "sayfalar relations kullanır" kuralının bilinçli
  istisnasıdır; gerekçesi dosyanın başına yazılır.
- **Eşleme sırası (deployment'sız):** elle tanımlı iş yükü → host'u `<ad>.` ile başlayan Ingress'in servisinin
  seçicisi → `app=<ad>` etiketi → **adı `<ad>-` ile başlayan pod'lar** (yeni son çare).
- **Yetkisiz kaynak bir kez sorulur.** Bir ortamda 403 dönen kaynak türü (ör. ingress) oturum boyunca
  hatırlanır, tekrar istenmez ve her yenilemede uyarı basılmaz; Kubernetes sayfasında tek satırlık soluk bir
  not olarak görünür.
- Kubernetes sayfasından "Deployment'lar" bölümü kaldırılır; imaj etiketi pod'un container bilgisinden gösterilir.

*Kabul:* `deployments()` çağrılırsa hata fırlatan sahte cluster ile tüm K8s sayfa / parça testleri geçer;
ingress de 403 iken pod'lar etiket veya ad önekiyle bulunur; Pod sütunu dolar; ekranda 403 uyarısı çıkmaz.

## 4. Ekran titremesi

Kodda titremeye yol açabilecek somut kaynaklar bulundu. Önce hangisinin görüldüğü doğrulanır
(`Devrunner.pyw --debug`, F12 → Rendering → *Paint flashing*; bir de `DEVRUNNER_DISABLE_GPU=1` ile
karşılaştırma), ama düzeltmelerin hepsi ucuz olduğu için hepsi yapılır.

| # | Kaynak | Düzeltme |
|---|---|---|
| 1 | **Sayfa geçişinde beyaz parlama.** Kabukta `QWebEnginePage` varsayılan zemini beyaz; her menü tıklaması tam sayfa yüklemesi | `shell/window.py`: `page.setBackgroundColor(<tema zemini>)` (tema değişince güncellenir). `base.html`'in başına tema zeminini veren tek satırlık satır içi stil ve `color-scheme`. Belgeler arası `@view-transition` kaldırılır |
| 2 | **Her olayda tabloların baştan çizilmesi.** Yoklama turu (60 sn), katalog, 30/60 sn zamanlayıcılar `innerHTML`'i değiştiriyor: animasyonlar baştan başlıyor, hover / kaydırma / seçim kayboluyor | (a) İstemcide: yanıt bir öncekiyle aynıysa hiç dokunma (`htmx:beforeSwap`'te hedef başına son yanıt karşılaştırılır). (b) Değiştiyse **morph**: `idiomorph` htmx eklentisi depoya alınır, periyodik parçalar `hx-swap="morph:innerHTML"` kullanır; düğümler yerinde kalır |
| 3 | **Seçili satırın geri zıplaması.** Tablo parçası seçili uygulamayı sayfa ilk açıldığındaki adresten alıyor; başka satıra tıkladıktan sonraki ilk yenilemede vurgu eski satıra dönüyor | Parça uç noktaları seçili uygulamayı `HX-Current-URL` başlığından okur (satır tıklaması adresi zaten güncelliyor) |
| 4 | **Sürekli yeniden bulanıklaştırma.** Hareketli `aurora` katmanı (`filter: blur(50px)` + 28 sn animasyon), tüm sayfayı kaplayan `backdrop-filter` kartın altında: her karede tüm ekran yeniden bulanıklaştırılıyor; GPU'suz / VDI'da titreme ve takılma | Aurora **durağan** olur ve `filter` kullanmaz (radyal gradyanlar zaten yumuşak). Sayfa kartından `backdrop-filter` kalkar (yarı saydam düz renk). `UI_EFFECTS`: `normal` = durağan cam, `azaltilmis` = saydamlık ve gölge yok, `kapali` = animasyon da yok. Kabuk `DEVRUNNER_DISABLE_GPU=1` ile açıldıysa varsayılan `azaltilmis` |
| 5 | Freeze rozetindeki `box-shadow` nabzı her karede yeniden boyama yapıyor | Nabız, `transform` / `opacity` ile hareket eden sözde öğeye taşınır |
| 6 | K8s log'unun 3 sn'de bir tümden değişmesi | Bölüm 5'teki canlı log akışı (yalnızca ekleme) |

İskelet yükleyici yalnızca ilk yüklemede görünür; yenilemede asla.

*Kabul (Playwright):* bir yoklama turundan sonra tablo satırlarının DOM düğümleri aynı kalır ve seçili satır
değişmez; menüler arası geçişte hiçbir karede açık renkli zemin görünmez (koyu temalar); `UI_EFFECTS=kapali`
iken çalışan CSS animasyonu yoktur.

## 5. Native terminaller

Hedef: `webterminal`'deki deneyim. Gerçek terminal (renkler, `Ctrl+C`, sekme tamamlama, `vim` / `top`,
pencere boyutu), hem **yerel makinenin kabuğu** hem **pod içindeki kabuk** için. Başvuru kodu `webterminal`
deposunda: `kube/exec.py` (exec protokolü), `kube/sessions.py` (kalıcı oturum + geri oynatma),
`components/console/console.js` (xterm.js kullanımı), `static/vendor/xterm/` (kitaplık; MIT, depoya kopyalanır).

**Doğrulanmış varsayım.** `flask-sock` ile mevcut Flask sunucusunda (werkzeug, iş parçacıklı) WebSocket
üzerinden gerçek bir PTY kabuğu çalıştırıldı; çerezsiz el sıkışma mevcut güvenlik katmanınca reddedildi ve
`connect-src 'self'` CSP'si engel olmadı. İkinci port veya ayrı sunucu gerekmez.

### 5.1 Parçalar

| Dosya | İş |
|---|---|
| `scripts/K8sExecAPI.py` (yeni backend) | Pod'da etkileşimli kabuk: `pods/exec`'e `tty=true&stdin=true` ile WebSocket (`websockets` zaten bağımlılık). Kanallar: 0 girdi, 1/2 çıktı, 3 durum, 4 boyut, 255 kapat. Kabuk seçimi `auto` / `bash` / `sh` (`webterminal`'deki `SHELLS`: `TERM` ayarlanır, bash yoksa ash / sh). Adres ve token `K8sAPI.env_settings`'ten; proxy kuralı aynı (sistem proxy'si yok, `K8S_PROXY`). Demo kipinde yalnızca yankı yapan sahte kabuk |
| `web/pty_local.py` (yeni) | Yerel PTY. POSIX: `pty.openpty` + yeni oturum. Windows: **ConPTY**, `pywinpty` ile. Ortak arayüz: başlat (kabuk, dizin, boyut), oku (iş parçacığı), yaz, boyutlandır, sonlandır (`TerminalAPI.kill_tree`). Kabuk listesi ve sürümleri `TerminalAPI.detect_shells`; ortam `TerminalAPI.process_env` (uygulama secret'ları aktarılmaz, `KUBECONFIG` / `NO_PROXY` ayarlanır) |
| `web/term_sessions.py` (yeni) | Oturum yöneticisi (`webterminal/kube/sessions.py`'nin iş parçacıklı karşılığı): tahmin edilemez oturum kimliği, tür (yerel / pod), başlık, geri oynatma arabelleği (~200 bin karakter), bağlı istemciler, son etkinlik. Sayfa değişse / yenilense oturum açık kalır; yeniden bağlanınca son çıktı geri oynatılır. Bağlısı kalmayan oturum `TERMINAL_DETACHED_MIN` sonra kapanır; kapanışta hepsi sonlandırılır |
| `web/pages/terminal.py` (yeniden) | Sekmeli çalışma alanı. `GET /terminal/`, oturum listesi, `POST` oturum aç (yerel veya pod), kapat, **`WS /terminal/ws/<id>`** (bağlan: önce geri oynatma, sonra canlı; girdi ve boyut iletileri) |
| `web/static/js/terminal.js` (yeniden) | xterm.js + fit / search / unicode11 / web-links eklentileri; tema renkleri CSS değişkenlerinden; yeniden bağlanma |

Eski "her komut ayrı süreç" kipi (`web/terminal_runner.py`) **yedek** olarak kalır: Windows'ta `pywinpty`
kurulamıyorsa Terminal sayfası bu kipte açılır ve bunu belirtir.

### 5.2 Davranış

- **Sekmeler.** "+ Yerel kabuk" (kabuk seçimi: PowerShell / pwsh / CMD / bash) ve "+ Pod kabuğu" (ortam →
  uygulama → pod → container). Kubernetes sayfasındaki pod ayrıntısına "Terminal" düğmesi gelir; tek seferlik
  "Komut" bölümünün yerini alır.
- **Hangi kabuk olduğu her zaman görünür** (ilk plandaki şart): sekme başlığında ve üst şeritte yerel için
  kabuk adı / sürümü / yolu, pod için ortam / pod / container.
- **Hazır komutlar ve "Terminalde dene"**: komut etkin terminalin giriş satırına *yazılır*, Enter gönderilmez.
- **Kısayollar.** Terminal odaktayken `Ctrl+K` komut paletini açmaz (kabuğa gider); palet `Ctrl+Shift+K` ile de açılır.
  Seçim varken `Ctrl+C` kopyalar, yoksa kabuğa gider; `Ctrl+Shift+V` / sağ tık yapıştırır.
- **Pano.** Kabukta (`shell/window.py`) kendi kökenimiz için panoya yazma / okuma izni açılır
  (`JavascriptCanAccessClipboard`, `JavascriptCanPaste`). Bu, bugünkü "Bağlantıyı kopyala" düğmesi için de gereklidir.
- **Canlı log.** Pod log'u `follow=true` ile akar ve yalnızca eklenir (3 sn'lik yenileme kalkar); indirilebilir.

### 5.3 Güvenlik

Etkileşimli kabuk, mevcut "komut çalıştır" yeteneğinin aynısıdır ama kanal değişir; şunlar zorunludur:

- WebSocket el sıkışması da oturum çerezi ister (mevcut koruma kapsıyor; deneyde doğrulandı). Buna ek olarak
  **`Origin` açıkça denetlenir**: el sıkışma bir GET olduğu için "durum değiştiren istek" denetimine girmiyor.
- Oturum kimlikleri tahmin edilemez; oturum yalnızca onu açan çalışan sürece aittir.
- "Hoş geldiniz" yönlendirmesi WebSocket yollarını bozmamalı (deneyde 302 görüldü).
- **Prod pod kabuğu** açmak, mevcut tek kullanımlık sunucu onayını ister; freeze'de uyarı gösterilir.
- Pod kabuğu açılışı ve kapanışı (ortam, pod, container, kişi) değişiklik geçmişine yazılır. **Tuş vuruşları ve
  çıktı hiçbir yere kaydedilmez.**
- Adresle komut çalıştırma yok (yukarıdaki "yazılır, çalıştırılmaz" kuralı).

*Kabul:* yerel kabukta `vim` açılıp kapanır, sekme tamamlama ve `Ctrl+C` çalışır, pencere küçülünce satırlar
yeniden sarılır; sayfa değiştirip dönünce oturum ve son çıktı yerindedir; sahte exec sunucusuna karşı pod
kabuğunda girdi, çıktı ve boyut iletileri doğru kanaldan gider; yabancı `Origin`'li WebSocket reddedilir;
Prod onayı atlanamaz; `pywinpty` yokken sayfa yedek kipte açılır.

## 6. Aura (oyunlaştırma)

Menüde **Sohbet ile Ayarlar arasına** yeni sayfa: **Aura**. DevOps'taki etkinlikten puan (aura), seviye,
RPG nitelikleri, unvan ve etiketler üretir; sıralama gösterir. Kazanılan unvan / etiket başlık çubuğunda
(sağ üst, avatarın yanında) ve sohbette adın yanında rozet olarak görünür.

### 6.1 Kişi eşleme

- DevOps kimliği görünen addır: build / PR kayıtlarında `Burak ALTINIŞIK (STRATEJİ DİJ. DÖNÜŞÜM VE Y.ZEKA DB)` biçiminde.
- `person_key(ad)`: parantezli birim soneki atılır, boşluklar teklenir, Türkçe duyarlı küçük harfe çevrilir
  (`İ`, `I`, `ı` → `i`). Sıralamada **tam görünen ad** (birimle) gösterilir; eşleme anahtarla yapılır, böylece
  birim değişse de kişi aynı kalır.
- **Kullanıcı kodları yok sayılır.** Commit yazarı bazen 7 haneli kullanıcı kodudur. Kural: parantez atıldıktan
  sonra en az iki sözcüklü olmayan ad (kod, tek sözcük) kişi sayılmaz ve o kayıt puanlanmaz. Sistem kimlikleri
  ("Build Service", "Project Collection…") de yok sayılır. Yok sayılan kayıt sayısı Aura sayfasında görünür.
- **Uygulama kullanıcısı**: ilk açılışta girilen ad (`dr_users.display_name`) aynı anahtarla eşlenir. Eşleşmezse
  sayfa bunu söyler ve en yakın adları önerir ("Ayarlar → Genel'den adınızı DevOps'taki gibi yazın").
  Sohbetteki kişilerin rozetleri de aynı yolla bulunur; sohbet protokolü değişmez.

### 6.2 Veri: `scripts/AuraAPI.py` (yeni backend)

DevOps'tan olgular toplanır, ortak PostgreSQL'e yazılır; herkes aynı sıralamayı görür ve her istemci
DevOps'u baştan taramaz. `DevopsAPI.make_client()` yeniden kullanılır (yeni uç noktalar için genel `get` yeter).

| Kaynak | Uç nokta | Kişi | Olgular |
|---|---|---|---|
| Commit | `git/repositories/{id}/commits` (`searchCriteria.fromDate`, sayfalama) | `author.name` | `commit`, `merge_commit`, `revert` (mesaj "Revert" ile başlıyor veya "This reverts commit" içeriyor) |
| PR | `…/pullrequests?searchCriteria.status=all` | `createdBy`, `reviewers[]` | `pr_open`, `pr_merged`, `pr_abandoned`, `review` (onay oyu) |
| CI / CD (YAML) | `build/builds?definitions=…&minTime=…` (tamamlananlar) | `requestedFor` | `ci_ok`, `ci_partial`, `ci_fail`, `cd_ok`, `cd_fail` |
| CD (klasik) | `release/deployments?definitionId=…` | `requestedFor` | `cd_ok`, `cd_fail` (ortam adı Prod ise işaretli) |

Kapsam: katalogdaki tüm `DDDB.Iz.Anm.*` repoları ve onların `_Container-CI` / `_Container-CD` pipeline'ları.

Tablolar (`BookmarkAPI`'deki kalıp: kendi `ensure_schema`, `dr_meta`'da kendi anahtarı `aura_schema`,
`schema_version`'a dokunulmaz, yalnız ekleyici):

| Tablo | Kolonlar |
|---|---|
| `dr_aura_facts` | `id`, `fact_key` UNIQUE (ör. `commit:<repo>:<sha>`, `build:<id>`, `pr:<repo>:<no>:merged`), `kind`, `person_key`, `person_name`, `app_name`, `occurred_at`, `attrs` JSONB, `recorded_by`, `recorded_at` |
| `dr_aura_sync` | `scope` PK (repo / pipeline), `watermark`, `updated_by`, `updated_at` |
| `dr_aura_profiles` | `person_key` PK, `equipped` (seçili etiket), `hidden`, `updated_by`, `updated_at` |

- **Olgu saklanır, puan saklanmaz.** Puanlar okuma anında kod içindeki kural tablosundan hesaplanır; kural
  değişince geçmiş kendiliğinden yeniden puanlanır.
- **Eşitleme** `web/services.py` (yeni) içindeki bir `Poller` ile `AURA_SYNC_MIN`'de bir ve sayfadaki "Şimdi
  eşitle" ile çalışır. Aynı anda tek istemci tarar (`pg_try_advisory_lock`); kayıtlar `ON CONFLICT DO NOTHING`
  ile yazıldığı için çakışma zararsızdır. Kapsam başına filigran tutulur, bir günlük örtüşmeyle sürdürülür;
  ilk tarama `AURA_SINCE_DAYS` ile sınırlıdır ve ilerlemesi sayfada görünür. DevOps'a yük: istekler sıralı ve
  aralıklı atılır.
- **Veritabanı yokken / demo**: aynı kurallar bellekteki olgu listesi üzerinde çalışır (`MemoryStore`), sayfa
  örnek veriyle dolar ve "yalnızca bu oturum" şeridi gösterir.

### 6.3 Puanlama

| Olgu | Aura |
|---|---|
| Commit | **+10** (merge commit +2). Günde kişi başı ilk 15 commit puanlanır |
| Revert commit'i | **−15** |
| CI başarılı / kısmen / başarısız / iptal | **+5** / +1 / **−5** / 0 |
| CD başarılı / başarısız | **+15** / **−10**; Prod ortamıysa +25 / −20 |
| PR açma / birleştirme / terk | +5 / **+20** / −5 |
| Review (onay) | +8 |

Kural tablosu `AuraAPI.RULES` içinde tek yerdedir.

**Seviye**: `seviye = ⌊√(aura / 25)⌋ + 1` (aura < 0 ise 1). Seviye 10 ≈ 2.000, 20 ≈ 9.000, 30 ≈ 21.000 aura.

**Unvanlar** (seviyeye göre kendiliğinden):

| Seviye | Unvan | | Seviye | Unvan |
|---|---|---|---|---|
| aura < 0 | Build Kıran | | 15–19 | Pipeline Şövalyesi |
| 1–2 | Git Noob | | 20–29 | Programming Chad |
| 3–5 | Noob Coder | | 30–39 | DevOps Killer |
| 6–9 | Commit Çırağı | | 40–49 | Aura Lordu |
| 10–14 | Merge Savaşçısı | | 50+ | 10x Efsane |

**Nitelikler** (1–99; az veride uç değer vermemesi için oranlar yumuşatılır, sayılar logaritmik ölçeklenir):

| Nitelik | Neyi ölçer |
|---|---|
| **STR** Güç | Commit hacmi |
| **DEX** Çeviklik | CI başarı oranı |
| **INT** Zekâ | Birleştirilen PR'lar |
| **VIT** Dayanıklılık | Etkin gün sayısı ve en uzun gün serisi |
| **LUK** Şans | CD başarı oranı (Prod ağırlıklı) |
| **CHA** Karizma | Verilen review'lar ve katkı yapılan farklı repo sayısı |

**Etiketler** (başarımlar; her birinin ölçütü `AuraAPI.BADGES` içinde saf fonksiyondur, kilitli olanlar ilerleme gösterir):

| Etiket | Ölçüt | | Etiket | Ölçüt |
|---|---|---|---|---|
| İlk Kan | İlk commit | | Hakem | 50 review |
| Yeşil Seri | Art arda 10 başarılı CI | | PR Makinesi | 25 birleştirilmiş PR |
| Kusursuz | Art arda 25 başarılı CI | | Tek Atış | 10 PR, hiç terk yok |
| Kırmızı Alarm | Art arda 5 başarısız CI | | Çok Yönlü | 5+ farklı repoya commit |
| Revert Ustası | 5 revert | | Göçebe | 10+ farklı repoya commit |
| Gece Kuşu | 00:00–05:00 arası 10 commit | | Maratoncu | 30 gün aralıksız etkinlik |
| Erkenci | 07:00'den önce 10 commit | | Hafta Sonu Savaşçısı | Hafta sonu 10 commit |
| Cuma Cesareti | Cuma 16:00'dan sonra başarılı Prod CD | | Hız Tutkunu | Bir günde 20 commit |
| Freeze Kaçağı | Freeze döneminde CD | | Prod Fatihi | 25 başarılı Prod CD |
| Yangın Söndürücü | Başarısız CD'den sonraki 1 saatte başarılı CD | | Deploy Kralı | 100 başarılı CD |
| Yüzler Kulübü | 100 commit | | Binler Kulübü | 1.000 commit |
| Anka Kuşu | Negatif auradan pozitife dönmek | | Sessiz Güç | Hiç başarısız CD'siz 50 CD |

"Freeze Kaçağı" freeze dönemlerini `FreezeAPI.list_freezes()`'ten alır (salt okuma).

### 6.4 Arayüz

- **Aura sayfası** (`web/pages/aura.py`, `PAGES`'te `chat` ile `settings` arasında):
  - *Kartım*: avatar, seviye halkası ve sonraki seviyeye ilerleme, aura, unvan, altı nitelik (çubuk veya radar),
    seçili etiket, son olgular ("+10 commit · Musteri360 · 2 sa önce") — puanın nereden geldiği görülebilsin.
  - *Sıralama*: tüm zamanlar / son 30 gün / uygulamaya göre; sıra, kişi (tam görünen ad), seviye, unvan, aura,
    öne çıkan etiket. Kendi satırım vurgulu. Bir kişiye tıklayınca kartı açılır.
  - *Etiketler*: kazanılanlar ve kilitliler (ilerleme çubuğuyla). Kazanılan bir etiket "tak" ile seçilir;
    seçilmemişse seviye unvanı gösterilir.
  - Eşitleme durumu, yok sayılan kayıt sayısı, "Şimdi eşitle".
- **Başlık çubuğu**: avatarın yanında `Sv 23 · Programming Chad` rozeti (ipucunda aura ve nitelikler); tıklayınca Aura sayfası.
- **Sohbet**: kişi listesinde ve mesaj başlıklarında adın yanında aynı rozet (sunucuda, kısa süreli önbellekle).
- **Bildirim**: eşitleme sonunda seviye atlandıysa veya yeni etiket kazanıldıysa bildirim balonu.
- Menü simgesi (kupa) `templates/_icons.html`'e eklenir. Tüm yedi temada okunur olmalıdır; rozet renkleri tema değişkenlerinden gelir.

*Kabul:* örnek veriyle sıralama dolar; aynı eşitleme iki kez çalışınca olgu sayısı değişmez; 7 haneli kodla
atılmış commit kimseye puan yazmaz; `burak altınışık` ve `Burak ALTINIŞIK (… DB)` aynı kişidir; kural tablosu
değişince puanlar yeniden hesaplanır; rozet başlıkta ve sohbette görünür; veritabanı yokken sayfa "yalnızca
bu oturum" kipinde çalışır.

## 7. Fazlar

Her faz sonunda uygulama açılır, testler geçer, `scripts/` farkı yalnızca yeni dosyalardır. Fazlar ayrı commit'lerdir.

**Faz A — Hızlı düzeltmeler** (Bölüm 1–4): `web_config`, sabit port + "Tarayıcıda aç" + kalıcı çerez,
`k8s_access` (deployment'sız), titreme düzeltmeleri, pano izinleri.

**Faz B — Native terminaller** (Bölüm 5): xterm.js'in depoya alınması, `flask-sock`, `pty_local`,
`term_sessions`, `K8sExecAPI`, yeni Terminal sayfası, pod "Terminal" düğmesi, canlı log akışı.

**Faz C — Aura** (Bölüm 6): `AuraAPI` (kurallar saf fonksiyon olarak önce, sonra eşitleme ve depolama), sayfa,
başlık ve sohbet rozetleri, bildirimler.

**Faz D — Teslim**: README (yeni ayarlar, terminal, Aura kuralları, Windows'ta elle denenecekler), bu planın
"Durum" bölümü, `requirements.txt` (`flask-sock`, `pywinpty; sys_platform == "win32"`).

## 8. Test

- **Mevcut 88 test** geçmeye devam eder.
- **Port / oturum**: varsayılan port, dolu port hatası, kalıcı çerezin yeniden başlatmadan sonra geçerliliği,
  sır yenilenince geçersizliği.
- **K8s**: `deployments()` çağrılırsa hata veren sahte cluster; ingress 403 iken ad önekiyle eşleme; yetkisiz
  kaynağın ikinci kez istenmemesi.
- **Titreme** (Playwright): Bölüm 4'teki kabul ölçütleri.
- **Terminal**: POSIX PTY ile oturum (yankı, boyut, geri oynatma, bağlantısız oturumun süresi dolunca kapanması,
  kapanışta alt süreç kalmaması); WebSocket `Origin` ve çerez denetimi; sahte `pods/exec` sunucusuyla girdi /
  boyut kanalları; Prod onayı; yedek kip. Playwright ile xterm'e yazıp çıktıyı okuma.
- **Aura**: `person_key` (Türkçe harfler, parantez, kod adları), puan / seviye / unvan / nitelik / etiket
  fonksiyonları sabit olgu listeleriyle; demo istemcisiyle eşitlemenin yinelenebilirliği; günlük commit sınırı;
  veritabanı ve bellek depolarının aynı sonucu vermesi; rozetin başlık ve sohbet parçalarında kaçışlanarak görünmesi.
- Windows'a özel (ConPTY, `pywinpty`, pano, tepsi) geliştirme ortamında doğrulanamaz; README listesine eklenir.

## 9. Açık sorular ve varsayımlar

| # | Soru | Varsayım | Etkilediği yer |
|---|---|---|---|
| 1 | Revert'te kim puan kaybeder: revert commit'ini atan mı, geri alınan commit'in sahibi mi? | Revert commit'ini atan (isteğin düz okuması) | `AuraAPI.RULES`; diğer seçenek mesajdaki "This reverts commit <sha>" ile asıl yazarı bulmayı gerektirir |
| 2 | Commit yazarlarının ne kadarı ad-soyad, ne kadarı kullanıcı kodu? | Çoğu ad-soyad; kodlar yok sayılır | Kodlar çoğunluktaysa commit puanları büyük ölçüde kaybolur. Çare (istenirse): `pushes` uç noktası her itmenin AD kimliğini verir, commit o kişiye yazılabilir |
| 3 | İç PyPI'de `flask-sock` (+ `simple-websocket`, `wsproto`, `h11`) ve `pywinpty` var mı? | Var | Yoksa: WebSocket için zaten kurulu `websockets` ile ikinci portta dinleyici; `pywinpty` yoksa yerel terminal yedek kipte kalır |
| 4 | Sıralamada herkes görünsün mü? Negatif unvanlar ("Build Kıran") ekipçe kabul görür mü? | Herkes görünür; kişi kendini gizleyebilir (`hidden`) | `dr_aura_profiles`, sıralama sorgusu |
| 5 | Servis hesabı `ingresses` / `services` listeleyebiliyor mu? | Bilinmiyor; 403 hoş görülür | Eşleme etikete veya ad önekine düşer |
| 6 | Titreme hangi sayfada, ne sıklıkta? Makinede GPU var mı? | Bölüm 4'teki kaynakların birleşimi | Düzeltmelerin sırası; hepsi yapılacak |
| 7 | Aura geçmişi ne kadar geriye gitsin; dönemlik sıfırlama (sezon) istenir mi? | 180 gün; sıfırlama yok, "son 30 gün" sekmesi var | `AURA_SINCE_DAYS`, sıralama |
| 8 | Port 28657 uygun mu? | Evet | `DEVRUNNER_PORT` |

## 10. Bu turda olmayanlar

- Menü geçişlerinin tam sayfa yükleme yerine parça değişimine çevrilmesi (titreme sürerse sonraki adım).
- Uygulama içi tarayıcı sekmeleri (Bilge'yi pencere içinde açma).
- Aura için elle puan verme / alma, ödül mağazası, takım bazlı sıralama.
- Terminal oturumlarının kaydı (asciinema benzeri) ve oturum paylaşımı.
- Mevcut backend dosyalarında değişiklik gerektiren her şey.

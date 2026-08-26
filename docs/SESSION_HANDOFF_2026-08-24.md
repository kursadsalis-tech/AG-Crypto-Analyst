# AGCA Session Handoff — 2026-08-24

Bu dosya bir sonraki oturumun doğrudan devam noktasıdır. Kullanıcıdan proje
geçmişini yeniden anlatması istenmeyecek. Secret, parola veya erişim anahtarı bu
dosyada tutulmaz.

## Canlı çalışma durumu

- Google Cloud proje: `project-6a717580-e37d-48f6-832`
- VM: `agca-runtime`, zone `us-east1-b`, mevcut ücretsiz `e2-micro` yapı
- Uygulama: `/home/kursadsalis/AG-Crypto-Analyst-cloud`
- Kalıcı durum: `/var/lib/agca`
- Systemd: `agca-cloud-worker.service`, enabled ve son kontrolde `active`
- Worker health: son tam döngüden sonra `HEALTHY`
- Zorunlu çalışma modu: `AGCA_EXECUTION_MODE=PAPER_ONLY`
- Gerçek emir adaptörü/credential yolu yok; `real_orders_allowed=False`
- Mac LaunchAgent'leri devre dışı; bulut ana runtime'dır.

## Dağıtılan sürüm ve rollback

- Dağıtım etiketi: `f03006d2b21c067de41abd528626d4730fc6cf35`
- GitHub dalı: `agent/remote-runtime-scope`
- Commit mesajı: `Fail closed on unvalidated news direction`
- Bulut checkout'u Git deposu değildir; `.deployed_commit` dosyası sürümü kaydeder.
- Rollback: `/home/kursadsalis/AG-Crypto-Analyst-cloud-rollback-f03006d-20260824T012600Z`
- Mevcut bulut `RISK_POLICY_TIME_FUTURE` koruması ezilmeden commit farkı uygulandı.
- Hedef bulut testleri: 10/10 `unittest` geçti.

## Haber yönü fail-closed kanıtı

- `NEWS_DIRECTIONAL_PRODUCTION_ELIGIBLE = False`
- En güçlü sentetik forward özeti altında bile durum `SHADOW_ONLY`.
- `NEWS_DIRECTIONAL_ROBUSTNESS_REJECTED` nedeni zorunlu olarak ekleniyor.
- Robustness raporu `real_orders_allowed=False` üretiyor.
- 1h/4h/24h haber yönü sonuçlarının hiçbiri `production_eligible` olamıyor.
- Bu kapı ancak yeni bağımsız kanıt ve ayrıca incelenmiş kod değişikliğiyle açılabilir.

## Supabase ve secret durumu

- Supabase proje: `cdxqqkspgacrjmkctlhq` (`agca-runtime`, `eu-west-1`)
- Session pooler host: `aws-1-eu-west-1.pooler.supabase.com:5432`
- Bağlantı yalnız Google Secret Manager `agca-db-url` içinde tutulur.
- Secret sürüm 3 etkin; eski sürüm 1 ve 2 devre dışı.
- VM'de bağlantı root-only `/etc/agca/remote-database.env` üzerinden yüklenir.
- Son kontrolde Supabase `remote_mirror_health`: 28/28 healthy.
- `agca_runtime` içindeki 30/30 tabloda RLS açık; security advisor lint sayısı 0.
- SQLite-first davranış korunur; uzak hata yerel commit'i geri almaz.

## Veritabanı kurtarma kaydı

- Önceki çalışma sırasında ana SQLite dosyasında bozulma bulundu.
- Bozuk dosya silinmedi; `/var/lib/agca-recovery-20260823T211900Z` altında korundu.
- Sağlam bulut yedeği devreye alındı ve tam `PRAGMA integrity_check` sonucu `ok` oldu.
- Supabase'teki daha yeni aynalanmış kayıtlar silinmedi.
- Kurtarılan yedek ile bozulma anı arasında yerel geçmişte yaklaşık bir saatlik boşluk
  olabilir; bu durum başarı metriği yorumlarında dikkate alınmalıdır.

## Bilinen normal/kısıtlı durumlar

- Ürün döngüsü `legacy_forward=PARTIAL` nedeniyle `PARTIAL` dönebilir; son dağıtımda
  tek sorun buydu. Saatlik risk/paper işi `SUCCESS` ve worker genel health `HEALTHY` idi.
- Legacy Forward ve Decision adayları SHADOW_ONLY'dır; alarm veya gerçek işlem değildir.
- Forecast araştırmalarında başarısız modeller yeniden eşik ayarıyla açılmayacaktır.
- 2026-08-24 doğal İstanbul 03:00 paper raporu 03:02'de üretildi; uzak teslim
  `HEALTHY` ve `delivery_complete=1` olarak doğrulandı.
- İlk doğal 11:00 yatırım komitesi raporu 11:04'te üretildi; `SHADOW_ONLY`,
  ürün kararı `BEKLE` ve en güçlü fırsat `YOK` olarak fail-closed kaldı.
- `legacy_forward=PARTIAL`, haber #2716 içindeki `CRO` için aktif Binance Spot
  USDT eşleşmesi bulunmamasından kaynaklanır. Model/politika/eşik değiştirilmedi.
- Hourly forecast sonucundaki `datetime` değerinin worker health JSON'unu
  `DEGRADED` göstermesi düzeltildi. Bilinmeyen tipleri maskelemeyen yalnız
  datetime-ISO serileştirmesi ve iki regresyon testi eklendi; bulutta ilgili
  16/16 test ve yeniden başlatma sonrası doğal `HEALTHY` doğrulaması geçti.
- PAPER execution girdileri için NaN/sonsuz fiyat-hacim, sıfır/geçersiz
  tick-step-minimum ve eksik quote-volume fail-closed reddedilir. Çıkış dolumu
  da artık exact candle quote-volume kapasitesini zorunlu kullanır. Exact-time
  sapması ve SQLite bağlantı kesintisi regresyon testleriyle kapsandı; bulutta
  22/22 test, self-test ve doğal `HEALTHY` döngüsü geçti.
- Canlı USD-M `exchangeInfo` parity tamamlandı. Ücretsiz Google Cloud bölgeleri
  ABD ile sınırlı ve resmî endpoint orada HTTP 451 verirken mevcut Supabase
  `eu-west-1` projesi HTTP 200 verdi. Saatlik iş, mevcut gizli Postgres bağlantısı
  ve sabit URL'li `extensions.http_get` üzerinden veriyi alır; açık proxy yoktur.
  İlk doğal çevrim 527 aktif USDT perpetual kuralını kullandı. Gerçek funding
  geçmişi sıradaki açık görevdir ve aynı doğrulanmış taşıma yolunu kullanabilir.
- PAPER ajan authority sözleşmesi fail-closed kodlandı: yalnız öneri yetkisi
  açıktır; gerçek emir, model/politika terfisi, Risk override ve sermaye
  değişikliği kapalıdır. `LIVE`, politika sürümü veya 10.000 USDT sermaye
  değişikliği runtime başlangıcında reddedilir. Bulutta 26/26 test, açık LIVE
  red kanıtı ve doğal `HEALTHY`/saatlik `SUCCESS` doğrulaması geçti.
- Provider-neutral execution contract eklendi: `OrderIntent`, `MarketSnapshot`,
  `ExecutionRules`, `ExecutionDecision` ve adaptör protokolü PAPER simülatörünün
  tek yolu oldu. Eski wrapper ile contract fill/rejection parity testleri geçti;
  sözleşmede credential veya order endpoint yoktur ve canlı adaptör eklenmedi.
  Bulutta 29/29 test, self-test ve doğal `HEALTHY` + saatlik/PAPER `SUCCESS`
  doğrulandı.
- Gerçek sermaye readiness kapısı eklendi. Sıralı PAPER/dry-run trace'leri
  decision, fiyat, miktar ve ücret toleranslarıyla fail-closed karşılaştırılır;
  boş/mismatch trace reddedilir. Geçiş için ayrıca açık kullanıcı onay kaydı,
  ana orkestratör attestation'ı ve ayrı incelenmiş kod değişikliği zorunludur.
  Son kod kapısı sabit kapalıdır ve runtime/env ile açılamaz. Bulutta 33/33 test,
  self-test ve doğal `HEALTHY` + saatlik/PAPER `SUCCESS`; gerçek emir=False.
- Canlı exchange metadata cache çekirdeği eklendi. Resmî market URL provenance,
  PERPETUAL/USDT uygunluğu, tick/step/min-notional, tazelik ve payload hash'i
  fail-closed doğrulanır; atomik cache yazımı ve eksik sembol reddi vardır.
  Yerel 25/25 ve bulut 27/27 ilgili test geçti. Supabase Avrupa taşımasıyla
  gerçek refresh ve cache hash doğrulandı; doğal worker `HEALTHY`, saatlik iş
  `SUCCESS`, PAPER metadata sembol sayısı 527, mod `PAPER_ONLY` ve gerçek emir
  yetkisi `False` oldu. Supabase security/performance advisor: 0/0 bulgu.
  Rollback: `/home/kursadsalis/AG-Crypto-Analyst-cloud-metadata-live-rollback-20260825T0015Z`.
- Gerçek USD-M funding ve 1x liquidation katmanı tamamlandı. Funding geçmişi
  resmî `/fapi/v1/fundingRate` kaynağından Supabase Avrupa taşımasıyla alınır;
  her olay kendi mark price'ıyla ve LONG/SHORT yön işaretiyle muhasebeleşir.
  Eksik/bozuk/sırasız/pencere dışı veri fail-closed'dur; sabit funding proxy'si
  USD-M settlement'ta kullanılmaz. Liquidation, tutma penceresindeki ilk 1h
  low/high ihlalinde; canlı sembol liquidation ücreti ve açıkça sabitlenmiş %0,5
  maintenance-margin varsayımıyla uygulanır. Kullanıcıya özel kesin bracket
  imzalı hesap endpoint'i istediği için credential eklenmedi ve kesin borsa
  liquidation fiyatı iddia edilmez. Altı açık pozisyon backfill edildi,
  `OPEN_WITHOUT_LIQ=0`; bulut testleri 32/32, canlı BTC funding 3 olay, PAPER
  `SUCCESS`; ilk doğal worker `HEALTHY`, saatlik/PAPER `SUCCESS`, gerçek
  emir=False. Günlük rapor funding olay/maliyet ve liquidation
  nedenini içerir. Rollback:
  `/home/kursadsalis/AG-Crypto-Analyst-cloud-funding-liquidation-rollback-20260825T0040Z`.
- PAPER girişinde gerçek lookahead bulundu ve düzeltildi. Bazı legacy işlemler
  ajan seçilmeden önceki saat açılışını kullanmıştı; bu nedenle eski P&L başarı
  kanıtı değildir. 14 pozisyon silinmeden `LEGACY_LOOKAHEAD_EXCLUDED` ve
  `evidence_eligible=0` olarak korundu; altı eski açık pozisyon iptal edildi,
  legacy aktif=0. Temiz `FORWARD_CLEAN_V2_20260825` kohortu 10.000 USDT nakit,
  sıfır P&L/ücret ile başladı. İlk emir niyetleri karar sonrasındaki exact saat
  açılışına bağlandı; doğal çevrim sonrası temiz intent=5, tamamı `PENDING_ENTRY`,
  timing violation=0. Yerel/bulut testleri 34/34, SQLite quick-check=`ok`, worker
  `HEALTHY`, saatlik/PAPER `SUCCESS`, portföy tam 10.000 USDT ve gerçek
  emir=False. DB yedeği:
  `/var/lib/agca/crypto_data-pre-forward-clean-20260825T0110Z.db`; kod rollback:
  `/home/kursadsalis/AG-Crypto-Analyst-cloud-lookahead-rollback-20260825T0110Z`.
- Temiz PAPER trade-learning review artık her ajan çevriminde çalışıyor. Yalnız
  `FORWARD_CLEAN_V2_20260825`, `evidence_eligible=1`, `CLOSED` ve maliyet sonrası
  `net_pnl` kayıtları yön/vade/rejim kırılımlarında değerlendirilir. 100 toplam
  kapanış; örüntü başına 30 işlem ve 3 bağımsız hafta kapıları vardır. İlk durum
  `COLLECTING_EVIDENCE`, temiz kapanış=0, hypothesis=0 ve otomatik terfi=False.
  Supabase satırı doğrulandı; yeni tablolar RLS + client-deny ile kapalı ve
  security/performance advisor 0/0. Rollback:
  `/home/kursadsalis/AG-Crypto-Analyst-cloud-trade-learning-rollback-20260824T215732Z`;
  DB yedeği: `/var/lib/agca/crypto_data-pre-trade-learning-20260824T215732Z.db`.

## Bir sonraki oturumun ilk sırası

1. Bu dosyayı, `PROJECT_STATUS.md`, `TASKS.md` ve `CHANGELOG.md` dosyalarını oku.
2. Kullanıcının trade-learning süreci hakkındaki fikirlerini dinle; yeni eşik,
   politika veya otomatik terfi varsayımı yapmadan önce bunları mevcut
   evidence-gated sözleşmeyle karşılaştır.
3. Bulutta servis `active`, health `HEALTHY`, çalışma modu `PAPER_ONLY` ve gerçek
   emir yetkisi kapalı kontrollerini yap.
4. Supabase mirror son başarı zamanının ilerlediğini ve temiz kohort
   trade-learning review kaydını doğrula.
5. SQLite `PRAGMA quick_check` uygula; hiçbir kurtarma/yedek kopyasını silme.
6. Yalnız `FORWARD_CLEAN_V2_20260825` ve `evidence_eligible=1` işlemlerini başarı
   kanıtı say. Legacy lookahead işlemlerini hiçbir metriğe katma.
7. Yeni gerçek emir veya sermaye yolu açma; PAPER_ONLY kilidini koru.

## 2026-08-25 — Tam devam noktası

Kullanıcı trade-learning süreci hakkında yeni fikirlerini bir sonraki oturumda
paylaşmak istiyor. Bir sonraki oturumun ana konusu budur; başka açık göreve
kendiliğinden geçilmemelidir.

Mevcut sözleşme:

- PAPER ajanı ana orkestratöre hesap verir; kullanıcıya doğrulanmış özet ana
  orkestratör tarafından sunulur.
- Günlük 03:00 PAPER raporunda temiz kohort için kümülatif seçilen/bekleyen/açık/
  kapanan/iptal sayıları, başarı oranı, brüt/net P&L ve maliyet sınıfları vardır.
- Bu Codex görevinin günlük 03:15 Europe/Istanbul heartbeat otomasyonu bulut,
  PAPER_ONLY, SQLite, Supabase, işlem defteri ve trade-learning durumunu kontrol
  ederek kullanıcıya Türkçe yönetici özeti verir.
- Günlük heartbeat gözetimsizdir ve kullanıcı onayı/escalation isteyen hiçbir
  komutu çalıştırmaz. Google Cloud SSH kullanılmaz; onaysız Supabase mirror
  kanıtı kullanılır ve doğrudan bulut doğrulanmadıysa raporda açıkça belirtilir.
  Böylece kullanıcı bilgisayar başında değilken onay bekleyen uzun çalışma
  oluşmaz. Sohbette görünen “zamanlanmış görevle gönderildi” tetikleyicisi
  kullanıcı tarafından yazılmış bir mesaj değildir.
- Google Cloud artık kendi hash'li attestation kaydını Supabase'e üretir.
  `cloud_runtime_attestations` içinde worker/product/hourly/PAPER durumları,
  `PAPER_ONLY`, `may_send_real_orders=0`, SQLite quick-check, temiz kohort işlem
  sayıları ve portföy muhasebesi bulunur. Günlük Codex bildirimi bu kaydın
  tazeliğini, SHA-256 hash'ini ve işlem mirror'ıyla eşleşmesini doğrular; SSH,
  kullanıcı onayı veya Telegram kullanmaz. Eksik/eski/uyumsuz kanıt fail-closed
  `BULUT DOĞRULANAMADI` sonucudur.
- Attestation ayrıca bağımsız `agca-cloud-attestation.timer` ile beş dakikada bir
  çalışır; worker health 15 dakikadan eskiyse `STALE`, servis kapalıysa `STOPPED`
  yazar. Supabase statement/lock beklemeleri kesin zaman sınırlıdır ve yerel
  PAPER worker'ını süresiz bloke edemez.
- Attestation aktarım hatası tahmin işçisinin sağlığından ayrıdır: mirror yoksa
  Codex raporu fail-closed olur, fakat tamamlanmış tahmin döngüsü yanlışlıkla
  `DEGRADED` etiketlenmez. Eski 03:20--06:20 tekrar görevi durduruldu; kullanıcı
  bildirimi için tek yol 03:15 Codex orkestratör özetidir.
- Supabase mirror artık tam tablo kopyalamaz. SQLite tetikleyicileri değişen
  birincil anahtarları kalıcı `remote_mirror_outbox` kuyruğunda birleştirir;
  ekleme/güncelleme/PK değişimi/silme küçük paketlerle aktarılır. Başarısız iş
  süreç yeniden başlasa da kaybolmaz ve yalnız uzak transaction commit'inden
  sonra kuyruktan silinir. Şema DDL kontrolleri digest ile önbelleklidir.
- Bulutta ana worker ve attestation yalnız SQLite outbox'a yazar; uzak aktarımı
  bağımsız `agca-remote-mirror-drain.timer` dakikada bir yapar. Böylece Supabase
  gecikmesi tahmin/trade-learning döngüsünü bekletemez. Değişmeyen haber analizi
  ve sosyal gözlemler no-op olarak bırakılır, yeniden kuyruğa girmez.
- Drain önceliği cloud/PAPER kanıt tablolarındadır; geçmiş veri tablo başına ayrı
  transaction ve 25 satırlık paketlerle iki dakikada bir iyileştirilir. Büyük
  haber backlog'u kritik rapor kanıtını bloke edemez.
- Supabase `http_get` DNS varsayılanının 1 saniyede zaman aşımına uğraması,
  sınırlı connect/HTTP/TCP/PostgreSQL statement timeout'larıyla düzeltildi.
  Canlı metadata yenilemesi olmazsa yalnız provenance+SHA doğrulanmış ve 2
  saatten genç cache kullanılabilir; eski/geçersiz cache fail-closed kalır.
- Son doğal doğrulama: worker `HEALTHY`, hourly/PAPER `SUCCESS`; Supabase'teki
  son attestation `PAPER_ONLY`, gerçek emir kapalı, SQLite `ok`, temiz kohort
  doğru ve SHA-256 geçerli. Önceki tam-tablo tasarımından kalan bulk backlog
  düşük öncelikli 25 satırlık paketlerle arka planda iyileşmektedir.
- Trade-learning yalnız temiz, evidence-eligible, kapanmış işlemlerin maliyet
  sonrası `net_pnl` sonucunu kullanır. Başlangıç kapıları 100 toplam kapanış,
  örüntü başına 30 işlem ve 3 bağımsız haftadır.
- Tek işlem sonrası politika değişimi, coin kara listesi, otomatik politika
  mutasyonu ve otomatik champion terfisi kapalıdır.
- Son doğrulanan durum: 5 `PENDING_ENTRY`, 0 açık, 0 kapanmış, 10.000 USDT nakit,
  0 P&L/ücret ve trade-learning `COLLECTING_EVIDENCE`.

Bir sonraki oturumda önce kullanıcının fikirleri alınmalı; mevcut eşikler onun
onayı olmadan değiştirilmemelidir.

## Kullanıcının yeni oturum açılış cümlesi

`AGCA çalışmasına /Users/elifzilan/Documents/AG-Crypto-Analyst/docs/LAST_SESSION_HANDOFF.md
dosyasından devam et. Son oturumun tam devam noktasını doğrula; önce benim
trade-learning süreci hakkındaki fikirlerimi dinle, ben söylemeden eşik veya
politika değiştirme.`

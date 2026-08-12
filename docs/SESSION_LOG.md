# AG Crypto Analyst - Session Log

## Oturum 2026-08-12 - Probabilistic Forecast Core Kararı

Kullanıcı ürün hedefini yeniden netleştirdi: fail-closed BEKLE güvenlik
başarısıdır ancak ürün başarısı değildir. Asıl hedef, yeterli kanıt oluştuğunda
1h/4h/24h hedef fiyat aralığı ve gerçekten kalibre edilmiş güvenle AL/SAT/BEKLE
üreten başarılı bir tahmin sistemidir.

Üç uzman geliştirme ajanı mimari, model ve red-team validasyonunu bağımsız
inceledi. Ortak karar yeni runtime ajan ağı kurmamak; mevcut ajanları veri/kalite
üreticisi olarak bırakmak ve tek ölçülebilir Forecast Core oluşturmaktır.

Uygulananlar:

- Sabit Forecast Contract eklendi.
- Tek fiyat-only `POOLED_RIDGE_DISTRIBUTION_V1_ALPHA_0_05_FIXED`
  laboratuvarı eklendi.
- Gerçek veri görülmeden ridge objective mean-loss + alpha*L2 olarak
  netleştirildi; alpha=0,05 ve model sürümü V1_ALPHA_0_05_FIXED donduruldu.
- 1h/4h/24h, FIT/CALIBRATION/VALIDATION/observed-diagnostic kronolojisi kuruldu.
- Hedef fiyat decision-close, işlem olasılığı next-open referansına bağlandı.
- Güven, 10 bps maliyet + 5 bps minimum hareketi aşma olasılığı olarak tanımlandı.
- UP/DOWN/action kalibrasyonu, benchmark skill, interval ve ekonomik yön faydası
  haftalık kümeli bootstrap ile kapılandı.
- Yüksek-güven hit ve calibration bias için haftalık block interval zorunlu oldu.
- Risk'in forecast örneklemini seçmesi engellendi; yalnız risk-adjusted eylemi
  veto etmesi sağlandı.
- Spot SAT'ın short kârı gibi portföye yazılması kaldırıldı; SELL/REDUCE/AVOID
  olarak tanımlandı.
- Sahte portföy simülasyonu kaldırıldı.
- Gerçek araştırma satırları Forecast Contract doğrulamasına bağlandı.
- NaN/inf, horizon, aralık, future mutation, next-open ve hash testleri geçti.

Canlı Decision Engine, Report Generator, LaunchAgent cycle ve alarm kapısı
değişmedi. Bir sonraki tek adım laboratuvarı gerçek Mac veri tabanında
çalıştırmaktır. Başarısızlıkta fiyat-only arama kapanacak; geçişte bile sonuç
yalnız DEVELOPMENT_ONLY olacak ve yeni sealed future + forward kanıt gerekecek.

## Oturum 2026-08-11

### Oturumun Ana Amacı

News Intelligence sinyallerini yalnızca tarihsel backtest ile değil, gerçek gelecekte leakage olmadan doğrulamak ve bu doğrulama katmanını çalışan ürüne bağlamak.

### Tarihsel Araştırma Özeti

Yaklaşık 360 günlük haber/fiyat veri seti üzerinde aşağıdaki laboratuvarlar tamamlandı:

- leakage-safe walk-forward
- locked out-of-sample validation
- rolling research lab
- policy robustness
- frozen monthly stress test
- market regime analysis
- regime-aware validation
- high-confidence calibration
- news + technical confluence
- weighted multifactor champion/challenger
- multifactor robustness
- adaptive dual-champion

Base 1h news policy yaklaşık %77.6 frozen EXAM hit rate üretti.

Weighted REGIME_AWARE multifactor aday model base'i bazı frozen karşılaştırmalarda geçti ve robustness analizinde ROBUST_B statüsü aldı.

Adaptive dual-champion selector sabit multifactor modele göre daha zayıf kaldığı için reddedildi.

Aynı tarihsel veriyi daha fazla optimize ederek overfitting riskini artırmamak için model donduruldu ve forward aşamasına geçildi.

### Forward Shadow Validation

İlk Forward Shadow V1 çalıştırmasında eski haberler ilk görülme anı "şimdi" kabul edilerek 16 tahmin oluşturuldu. Bunun gerçek forward olmadığı fark edildi.

Düzeltme yapıldı:

- V1 kayıtları INVALID_PRESTART_SEED olarak geçersiz sayıldı.
- REGIME_AWARE_FORWARD_V2 oluşturuldu.
- Forward başlangıcı: 2026-08-11T01:29:56.397166+00:00
- Yalnızca bu başlangıçtan sonra ilk kez toplanan haberler tahmin doğurabilir.
- Horizon 1h.
- Cutoff 0.249 frozen.
- Ağırlıklar sonuçlara bakılarak değiştirilmeyecek.

V2 temiz scoreboard ile başladı: settled=0, open=0.

### Otomasyon

Forward cycle tek akışa bağlandı:

NEWS COLLECT -> NEWS ANALYZE -> FORWARD SHADOW SETTLE/RECORD

Mac LaunchAgent kuruldu:

- Label: com.agca.forward-shadow
- Aralık: 5 dakika
- launchctl doğrulaması: runs=2, last exit code=0
- Log üzerinden otomatik cycle başarıyla doğrulandı.

### Coin Kapsamı

Takip listesi aliasları News Analyzer'a eklendi:

ARKM, FIDA, WLD, TIA, ETHFI, BIO, LQTY, ILV, SLP, UMA, IO

Ayrıca Binance Spot USDT ticker keşfi dinamik hale getirildi. Amaç sistemin yalnızca sabit coin listesine bağlı kalmaması ve yeni coin fırsatlarını da tespit edebilmesi.

### Bildirim ve Rapor Kararı

Telegram yardımcı altyapı olarak denendi ancak ana kullanıcı bildirim kanalı olarak gerekli olmadığına karar verildi.

ChatGPT görevleri yeniden aktif edildi:

- Günlük Kripto Brifingi: 11:00
- Kripto Kritik Gelişme Takibi: saatlik condition watch

Kritik gelişme takibi yalnızca gerçekten anlamlı gelişme olduğunda bildirim gönderecek; önemli olay yoksa sessiz kalacak.

### Ürün Kararları

- Araştırma ürünü geciktirmemeli.
- Forward validation paralelde veri toplamaya devam ederken ana yol haritası ilerlemeli.
- Yeni oturumlarda kullanıcı proje geçmişini yeniden anlatmayacak.
- Resmi hafıza dosyaları ve GitHub kodu başlangıç referansı olacak.

### Bir Sonraki Oturum

Önce Forward Shadow V2 durumunu kontrol et:

1. İlk gerçek open/settled forward tahminleri oluşmuş mu?
2. LaunchAgent hâlâ sağlıklı mı?
3. NEWS BASE vs MULTIFACTOR scoreboard nasıl gelişiyor?
4. Günlük ve kritik ChatGPT görevleri aktif mi?

Ardından ürün yol haritasında v0.6.0 Social Sentiment Agent'a geçiş zamanını belirle. Forward test paralelde çalışmaya devam etsin.

### Kullanıcıyla Çalışma Şekli

Teknik işlemlerde kullanıcıya her zaman tek sonraki adım verilecek. Bir adım tamamlanmadan diğerine geçilmeyecek. Kullanıcıdan proje geçmişini yeniden anlatması istenmeyecek.

---

## Oturum 2026-08-11 - v0.6.0 Social Sentiment Agent

### Başlangıç Durumu

Forward Shadow V2'nin ilk gerçek sonuçları kontrol edildi:

- NEWS BASE: settled=13, hit_rate=%23.1, directional_return=-%0.050
- MULTIFACTOR: settled=5, hit_rate=%20.0, directional_return=-%0.013

Örneklem küçük ve performans zayıf olduğu için frozen model değiştirilmedi; Champion üretim onayı verilmedi. Forward validasyon ana yol haritasını bloke etmeden paralelde devam edecek.

### Social Sentiment Agent

v0.6.0 ilk çalışan sürüm geliştirildi:

- Reddit geniş kripto topluluk akışı
- Tek istekte 100 paylaşım
- SQLite gözlem tablosu ve global tekilleştirme
- Başlık + içerik analizi
- Coin/ticker tanıma ve belirsiz ticker koruması
- 24 saatlik coin bazlı sentiment aggregate
- Örneklem, yönlü kanıt ve fikir birliğine bağlı güven puanı
- 15 dakikalık rate-safe cadence
- 5 dakikalık mevcut canlı ürün cycle entegrasyonu

### Test ve Düzeltmeler

- Çoklu RSS isteğinde oluşan Reddit 429 sorunu birleşik tek akışla giderildi.
- İlk testlerde kaynak etiketi değişiminden doğan yinelenen kayıtlar temizlendi ve external_id tekilleştirmesi kalıcı hale getirildi.
- Sıradan "link" kelimesinin LINK coini sanılması düzeltildi.
- Paylaşım sayısına bakarak üretilen yanıltıcı 100/100 güven hesabı kaldırıldı.
- Düşük örneklem ve çelişkili sentiment için muhafazakâr güven üretimi doğrulandı.
- Social cycle zorunlu testte SUCCESS verdi.
- 15 dakika dolmadan tekrar çalışma doğru biçimde SKIPPED verdi.
- Tam NEWS -> FORWARD SHADOW -> ALERT BRIDGE -> SOCIAL SENTIMENT cycle testi başarıyla tamamlandı.

### Ürün Kararı

- v0.6.0 tamamlandı.
- Düşük güvenli sosyal sonuçlar alarm veya işlem sinyali değildir.
- Social Sentiment ve Forward Shadow paralelde veri toplamaya devam edecektir.
- Sıradaki ana ürün modülü v0.7.0 On-chain Agent'tır.

---

## Oturum 2026-08-11 - v0.7.0 On-chain Agent

### On-chain Collector

Anahtarsız ve resmî kaynaklarla ilk çalışan sürüm geliştirildi:

- mempool.space: Bitcoin mempool transaction count, vsize, total fee ve dört fee seviyesi
- DefiLlama: Ethereum, Solana, BSC, Arbitrum, Base, Avalanche ve Sui TVL
- SQLite onchain_observations tablosu
- source + chain + metric + collected_at tabanlı idempotent kayıt

İlk gerçek test: 14 metrik, kaynak hatası=0.

### Otomasyon

- onchain_cycle eklendi.
- Çalışma aralığı 15 dakika.
- Hatalar diğer canlı ürün modüllerinden izole edildi.
- Mevcut 5 dakikalık live cycle'a altıncı adım olarak eklendi.
- Cadence testi 15 dakika dolmadan SKIPPED verdi.

### Tarihsel Bootstrap ve Analyzer

DefiLlama tarihsel chain TVL endpoint'i ile sekiz zincirde 64 gözlemden oluşan son 7 günlük bootstrap tamamlandı; hata=0.

Yaklaşık 24 saatlik değişim analyzer'ı geliştirildi:

- kabul penceresi: 18-30 saat
- yeterli geçmiş yoksa WARMING_UP
- sıfır/geçersiz değer varsa NO_DATA
- hazır değişimler RISING/FALLING/STABLE olarak operasyonel sınıflandırılır
- bu sınıflar doğrulanmadan işlem/alarm sinyali sayılmaz

İlk analyzer 43.87 saatlik geçmişi yanlış biçimde 24h karşılaştırması olarak kullanıyordu. En yakın hedef baseline seçimiyle düzeltildi. Celestia sıfır TVL verisinin hazır sayılması engellendi.

Son doğrulama:

- Yedi zincir TVL: STABLE
- Bitcoin ağ metrikleri: WARMING_UP
- Celestia: NO_DATA
- On-chain cycle: SUCCESS | metrics=14 | analyzed=15 | source_errors=0

### Ürün Kararı

- v0.7.0 ilk çalışan sürüm tamamlandı.
- İlk kapsam zincir likiditesi ve Bitcoin ağ sağlığıdır.
- Token transferleri, borsa giriş/çıkışları ve büyük cüzdan hareketleri ileri genişletme/Whale Tracker kapsamındadır.
- WARMING_UP ve NO_DATA metrikleri karara dahil edilmeyecektir.
- Sıradaki ana modül v0.8.0 Macro Agent'tır.

---

## Oturum 2026-08-11 - v0.8.0 Macro Agent

### Macro Collector

Resmî FRED CSV kaynağıyla ilk çalışan sürüm geliştirildi:

- DFF: Effective Federal Funds Rate
- DGS10: ABD 10 yıllık tahvil faizi
- DTWEXBGS: geniş ABD doları endeksi
- VIXCLS: VIX
- CPIAUCSL: ABD tüketici fiyat endeksi
- 800 günlük geçmiş ve SQLite idempotent kayıt

Son collector testi 2.481 gözlem aldı, 4 yeni kayıt ekledi ve sıfır kaynak hatası verdi.

### Macro Analyzer

Şeffaf makro bileşenleri geliştirildi:

- Fed Funds 90 günlük değişim
- ABD 10Y 30 günlük değişim
- geniş dolar 30 günlük değişim
- VIX seviyesi
- CPI yıllık eğilim değişimi

Son gerçek sonuç:

- Fed Funds: %3.63, 90g 0 bp
- ABD 10Y: %4.72, 30g +16 bp
- Geniş dolar: 30g -%1.71
- VIX: 15.46
- CPI YoY: %3.46, eğilim -0.70 puan
- Bileşenler: 0, 0, -1, -1, -1
- PROVISIONAL rejim: RISK_ON, skor=-3, kapsam=5/5

Bu sonuç doğrulanmış işlem sinyali değildir.

### Otomasyon ve Entegrasyon

- macro_cycle eklendi.
- Çalışma aralığı 6 saat olarak belirlendi.
- Kaynak/analyzer hataları diğer canlı ürün modüllerinden izole edildi.
- Ana ürün döngüsüne yedinci adım olarak bağlandı.
- Zorunlu Macro cycle testi SUCCESS verdi.
- Tam ürün cycle testinde 6 saat dolmadığı için Macro doğru biçimde SKIPPED verdi.

### Ürün Kararı

- v0.8.0 tamamlandı.
- Macro rejimi PROVISIONAL kalacak; tarihsel/forward doğrulama olmadan alarm, işlem kararı veya Decision Engine girdisi olmayacak.
- Forward Shadow, Social Sentiment, On-chain ve Macro paralelde veri toplamaya devam edecek.
- Sıradaki ana modül v0.9.0 Whale Tracker'dır.


---

## Oturum 2026-08-11 - v0.9.0 Whale Tracker

### Bitcoin Kanıt Toplama

Doğrulanmış Blockchain.com blok verisi ve Binance BTCUSDT fiyatıyla 10 milyon dolar üstü tekil Bitcoin çıktıları izlenmeye başlandı.

İlk gerçek taramada:

- blok 962.051: 8 eşik üstü çıktı
- blok 962.052: 1 eşik üstü çıktı
- 24 saatlik toplam: 9 olay, 9 benzersiz işlem
- brüt çıktı tahmini: yaklaşık 308,2 milyon dolar
- en büyük tekil çıktı: yaklaşık 83,0 milyon dolar

Bu çıktılar change veya self-transfer olabileceği için borsa akışı ya da alım/satım sayılmadı.

### Ethereum Native ve ERC-20 Kanıtı

Ethereum PublicNode JSON-RPC safe bloklarıyla iki ayrı kaynak geliştirildi:

- 10 milyon dolar üstü üst-seviye native ETH transferleri
- doğrulanmış kontratlarda 10 milyon dolar üstü ERC-20 Transfer olayları

İlk ERC-20 kapsamı: ARKM, BIO, ETHFI, ILV, LQTY, SLP ve UMA. Kontrat decimals değerleri due tarama öncesinde zincir üstünde doğrulanır; fiyatlar Binance public spot verisinden alınır.

Son canlı test:

- Bitcoin blok: 0 yeni
- native ETH: 32 safe blok, 0 olay
- ERC-20: 1 safe blok, 7 kontrat doğrulandı, 0 olay
- kaynak hatası: 0
- cross-chain durum: MONITOR_ONLY
- alarm uygun olay: 0

### Analyzer, Cadence ve Ürün Entegrasyonu

- whale_events ve bağımsız whale_scan_state kayıtları eklendi.
- 24 saatlik cross-chain evidence analyzer geliştirildi.
- BTC, native ETH ve ERC-20 kaynakları birbirinden izole edildi.
- Whale Tracker 5 dakikalık cadence ile canlı ürün döngüsüne sekizinci adım olarak bağlandı.
- Ham transferlerin net akış olmadığı ve doğrulanmamış olayların alarm üretemeyeceği ürün politikasına bağlandı.

### Ürün Kararı

- v0.9.0 Whale Tracker ilk çalışan sürüm olarak tamamlandı.
- Kapsanmayan zincir/tokenlar destekleniyor gibi gösterilmeyecek.
- UNVERIFIED olaylar alarm, işlem sinyali veya Decision Engine girdisi olmayacak.
- Forward Shadow, Social Sentiment, On-chain, Macro ve Whale Tracker paralelde veri toplamaya devam edecek.
- Sıradaki ana ürün modülü v1.0.0 Risk Manager'dır.

---

## Oturum 2026-08-11 - v1.0.0 Risk Manager

### Piyasa ve Coin Risk Politikası

Binance public kapalı 1h mumlarıyla BTC, ETH ve 11 öncelikli coin için şu ölçümler eklendi:

- 24 saatlik getiri
- 24 saatlik yüksek/düşük fiyat aralığı
- son 24 saatlik saatlik getiri volatilitesi
- 7 günlük tepeye göre düşüş
- 24 saatlik spot quote-volume vekili

Bu ölçümler coin bazlı NORMAL / ELEVATED / HIGH / EXTREME sınıfı ile azami pozisyon ve volatilite-stop referansı üretir. Global politika NORMAL / CAUTION / DEFENSIVE / LOCKDOWN / DATA_BLOCKED durumlarıyla toplam maruziyet, altcoin maruziyeti, tek pozisyon, işlem riski, günlük kayıp ve yeni pozisyon sınırları verir.

### Canlı Kalibrasyon Düzeltmesi

İlk gerçek test:

- kapsam: 13/13
- BTC ve ETH risk metrikleri sakindi
- takip listesindeki düşük hacimli coinler nedeniyle global sonuç yanlış biçimde LOCKDOWN oldu

Kök neden, coin bazlı yapısal likidite riskinin global dinamik piyasa stresi sayılmasıydı. İki risk ekseni ayrıldı ve ilk canlı veri regresyon fixture'ına dönüştürüldü.

Düzeltilmiş sonuç:

- market_state=NORMAL
- market_stress=0/13
- asset_blocks=10
- liquidity_blocks=10
- BTC cap=%10
- ETH cap=%10
- WLD cap=%5
- diğer düşük hacimli 10 coin cap=%0 ve yeni pozisyon=NO

QVOL yalnızca 24h spot hacim vekilidir; emir defteri derinliği değildir.

### Cadence ve Canlı Ürün Entegrasyonu

- risk_cycle 15 dakikalık cadence ile eklendi.
- Kaynak hataları diğer modüllerden izole edildi.
- Risk Manager ürün alarm köprüsünden hemen önce konumlandırıldı.
- Risk cycle gerçek veride SUCCESS verdi.
- Tam 9/9 canlı ürün cycle testi geçti.
- Aynı 15 dakikalık pencere içinde Risk Manager doğru biçimde SKIPPED oldu.

### Signal Risk Gate

Forward ürün alarmı için fail-closed kapı geliştirildi:

- asgari settled=30
- hit rate >=%60
- %95 Wilson alt sınırı >=%50
- ortalama directional return >0
- risk snapshot yaşı <=30 dakika
- global ve coin bazlı yeni pozisyon izni

Kararlar risk_gate_decisions tablosuna idempotent kaydedilir. Ürün alarm köprüsü bu kapıya bağlandı; gate BLOCK ise Telegram veya başka ürün alarmı gönderilmez.

Canlı kapı sonucu:

- policy snapshot=3
- market_state=NORMAL
- policy age=8,4 dakika
- Forward state=SHADOW_ONLY
- settled=5
- hit=%20
- Wilson lower=%3,6
- directional return=-%0,013
- açık/izinli alarm adayı=0

Frozen REGIME_AWARE_FORWARD_V2 ağırlıkları ve 0.249 cutoff değiştirilmedi.

### Ürün Kararı

- v1.0.0 Risk Manager tamamlandı.
- Risk Manager sinyal değil, zorunlu limit ve veto katmanıdır.
- BLOCK kararı gelecek Decision Engine tarafından telafi edilemez.
- Zayıf, PROVISIONAL, WARMING_UP, NO_DATA, UNVERIFIED veya SHADOW_ONLY kanıt güçlü karara dönüştürülemez.
- Sıradaki ana ürün modülü v1.1.0 Decision Engine'dir.

---

## Oturum 2026-08-11 - v1.1.0 Decision Engine

### Açıklanabilir Gölge Karar Çekirdeği

- Decision Engine v1.1.0, DECISION_POLICY_V1_SHADOW politikasıyla eklendi.
- 13 risk-kapsamlı coin için kapalı 1h teknik bağlam ve market bağlamı üretildi.
- Forward, Social, On-chain, Macro, Whale ve Risk kanıtlarının olgunluk durumu tek snapshot'ta görünür tutuldu.
- PROVISIONAL, WARMING_UP, NO_DATA, MONITOR_ONLY, UNVERIFIED ve SHADOW_ONLY kanıtların güçlü ürün kararına çevrilmesi engellendi.
- Risk Manager BLOCK kararı nihai veto olarak uygulandı.
- Gölge AL/SAT adayı ile product_decision ayrıldı; ürün kararı SHADOW modunda BEKLE tutuldu.

### Canlı İlk Test

- Risk snapshot=4, market_state=NORMAL, age=0,7 dakika.
- Forward kanıt SHADOW_ONLY: settled=5, hit=%20, Wilson alt=%3,6, directional return=-%0,013.
- BTC SAT_ADAYI ve WLD AL_ADAYI oluştu.
- 10 coin Risk Manager tarafından bloklandı.
- 13/13 ürün kararı BEKLE kaldı.
- Kaynak hatası oluşmadı.

### Çakışmasız 1h Gölge Doğrulama

- decision_shadow_predictions tablosu eklendi.
- Aynı coin için yalnızca bir açık tahmine izin verildi.
- Açık tahmin sonuçlanmadan 15 dakikalık tekrarların yeni örnek sayılması engellendi.
- AL ve SAT yönleri için ayrı directional return ve hit hesaplaması eklendi.
- İlk çalışmada BTC ve WLD için iki açık tahmin kaydedildi; source_errors=0.
- Deterministik validator kontrolleri 18/18, cadence kontrolleri 6/6 geçti.

### Cadence ve Ana Ürün Entegrasyonu

- Decision Engine 15 dakikalık hata izolasyonlu cycle'a bağlandı.
- Canlı akış NEWS -> FORWARD SHADOW -> RISK MANAGER -> DECISION ENGINE -> ALERT BRIDGE -> SOCIAL SENTIMENT -> ON-CHAIN -> MACRO -> WHALE TRACKER oldu.
- Tam ürün döngüsü gerçek veride 10/10 tamamlandı.
- Hemen sonraki bütünleşik testte Risk Manager ve Decision Engine cadence nedeniyle güvenli biçimde SKIPPED oldu.
- Ürün alarm köprüsü gönderilen=0, bekleyen=0 kaldı.

### Ürün Kararı

- v1.1.0 Decision Engine çalışan gölge sürümü tamamlandı.
- Gölge adayları alarm, işlem önerisi veya üretim kararı değildir.
- İki açık 1h sonuç otomatik cycle ile sonuçlanacaktır.
- Küçük örneklemde politika/eşik değiştirilmeyecek ve otomatik üretim onayı verilmeyecektir.
- Ana yol haritasındaki sıradaki modül v1.2.0 Report Generator'dır; Decision Engine doğrulaması paralelde sürecektir.

---

## Oturum 2026-08-11 - v1.2.0 Report Generator

### Fail-Closed Komite Raporu

- Son Decision Engine run, Risk policy/asset snapshot, Forward scoreboard ve Decision Shadow performansı tek raporda birleştirildi.
- 13 coin için 24h bağlam, teknik yön, gölge aday, ürün kararı, risk izni ve tavan ayrı gösterildi.
- Gölge adayların fırsat gibi sunulması engellendi.
- SHADOW durumunda veritabanında yanlış product AL bulunsa dahi raporun BEKLE'ye kapandığı doğrulandı.
- Risk BLOCK bulunan coinlerin fırsat olarak seçilemediği doğrulandı.
- Risk veya Decision verisi 30 dakikadan eskiyse DATA_BLOCKED kuralı eklendi.
- Günlük Markdown dosyası ve idempotent committee_report_snapshots kaydı eklendi.

### Canlı İlk Rapor

- Report Generator v1.2.0 başarıyla çalıştı.
- Durum SHADOW_ONLY, Decision run 13 coin ve risk_blocked=10 oldu.
- Ürün aksiyonu BEKLE, en güçlü fırsat YOK oldu.
- BIOUSDT risk verisine göre en riskli coin seçildi.
- BTC SAT_ADAYI ile WLD AL_ADAYI yalnız gölge araştırma olarak gösterildi.
- reports/daily_investment_committee_report.md üretildi.
- Fail-closed testler 20/20 temel + 6/6 tutarsız veri senaryosunda geçti.

### Günlük 11:00 Cadence

- Europe/Istanbul 11:00 sonrası günde bir çalışma kuralı eklendi.
- 10:59 bekleme, 11:00 çalışma, aynı gün tekrar engeli, ertesi gün yeniden açılma ve hata tekrar deneme senaryoları 9/9 geçti.
- Manuel modül çalıştırmasının planlı slotu tüketmemesi sağlandı.
- Report Generator mevcut 5 dakikalık LaunchAgent akışına eklendi.

### 11/11 Ürün Entegrasyonu

- Tam canlı akış 11/11 adımla tamamlandı.
- Risk Manager ve Decision Engine cadence nedeniyle güvenli SKIPPED oldu.
- İstanbul saati 00:51 iken Report Generator WAITING_FOR_11_00 durumunda kaldı.
- Alarm köprüsü gönderilen=0, bekleyen=0 kaldı.
- Diğer cadence kontrollü modüller akışı durdurmadı.

### Ürün Kararı

- v1.2.0 Report Generator çalışan ilk sürümü tamamlandı.
- Ana ürün yol haritasındaki on modül tamamlandı.
- İlk doğal 11:00 üretimi otomatik LaunchAgent ile gerçekleşecek ve operasyonel olarak gözlenecek.
- Forward/Decision gölge doğrulamaları paralelde sürecek; küçük örneklemde üretim kapıları değiştirilmeyecek.
- Sıradaki faz yeni mimari değil, çalışan ürünün güvenilirliği ve ölçülmüş performansıdır.

---

## Oturum 2026-08-11 - Decision Engine Historical Replay

### Point-in-Time Replay

- 13 coin için yaklaşık 400 günlük kapalı 1h mum indirildi.
- market_klines tablosuna gerçek Binance quote_volume alanı ve geriye uyumlu
  migrasyon eklendi.
- 127.322 yeni mum, 13/13 hazır sembol, sıfır kaynak hatası ve sıfır
  point-in-time boşluğu doğrulandı.
- Kararlar yalnız kapanmış mumlarla oluşturuldu ve giriş bir sonraki mum
  açılışından yapıldı.
- 1h, 4h ve 24h sonuçlarına 10 baz puan maliyet uygulandı.
- Veri kronolojik LEARN / VALIDATION / EXAM olarak ayrıldı.
- Zaman sızıntısı, maliyet, risk veto, veri boşluğu ve split kontrolleri geçti.

### Mevcut Politika Sonucu

- 9.576 karar noktasında 23.881 aday oluştu: 11.505 AL ve 12.376 SAT.
- 55.872 coin-zaman Risk Manager tarafından veto edildi.
- EXAM 1h: n=3.556, hit=%40,0, net=-%0,142, block_LB=-%0,178.
- EXAM 4h: n=3.556, hit=%41,7, net=-%0,193, block_LB=-%0,303.
- EXAM 24h: n=3.556, hit=%46,9, net=-%0,160, block_LB=-%0,733.
- Mevcut fiyat-durum kuralı tarihsel sınavı geçemedi.

### Challenger Sonucu

- Her ufukta 36 sabit FOLLOW/CONTRARIAN kuralı değerlendirildi.
- Teknik güç, momentum ve normal piyasa filtreleri kullanıldı.
- Güven sınırları 7 günlük zaman bloklarıyla hesaplandı.
- 1h, 4h ve 24h için hiçbir kural LEARN aşamasında pozitif alt güven sınırı
  üretmedi.
- Sonuç NO_HISTORICALLY_CONFIRMED_CHALLENGER ve SHADOW_ONLY oldu.

### Ürün Kararı

- Canlı Decision Engine politikası ve üretim kapıları değiştirilmedi.
- Mevcut sürekli fiyat-durum kural ailesi üretim için reddedildi.
- Eşikleri yeniden kurcalamak yerine yalnız seyrek geçiş/kırılım olayları için
  tek sınırlı hipotez test edilecek.
- Bu hipotez de başarısızsa fiyat-teknik optimizasyonu durdurulacak.

## Sparse Transition Replay - Nihai Fiyat/Teknik Sonucu

- Seyrek skor, MACD/EMA sıfır geçişi ve kapalı-fiyat 24h kırılım hipotezleri
  coin başına 24h cooldown ile çalıştırıldı.
- Hacim ve NORMAL-piyasa korumalarıyla 36 sabit config/horizon test edildi.
- 9.576 karar noktası, 68.616 risk-izinli bağlam, 55.872 risk vetosu ve sıfır
  point-in-time boşluk oluştu; config başına 146-2.107 olay değerlendirildi.
- 1h, 4h ve 24h ufuklarının tamamı NO_LEARN_EDGE verdi.
- Sonuç NO_CONFIRMED_PRICE_TECHNICAL_EDGE ve SHADOW_ONLY oldu.
- Araştırma kararı STOP_PRICE_TECHNICAL_RULE_SEARCH olarak kilitlendi; canlı
  eşik veya politika değiştirilmedi.

## News Catalyst Replay - Kalite Kapılı Hazırlık

- Eski NEWS BASE sonucu yeniden üretim kanıtı sayılmadan GDELT veri sözleşmesi
  incelendi.
- Eski akışta maliyet, aynı-hikâye tekrarı, coin cooldown ve zaman-blok
  korelasyonunun bugünkü standartta eksik olduğu belirlendi.
- Precision-first coin eşleştirme, canonical URL + 72h başlık dedup, yaklaşık
  zaman dışlama, 60m gecikme, sonraki mum açılışı, 24h cooldown ve 10 bps
  maliyet içeren `analysis.news_catalyst_replay_lab` hazırlandı.
- Teknik yön kullanılmadan 16 sabit FOLLOW/CONTRARIAN kural, LEARN ->
  VALIDATION -> kapalı EXAM sözleşmesine bağlandı.
- Veri kalitesi geçmezse aday seçimi ve EXAM çalışmayan fail-closed kapı eklendi.
- Sentetik next-open, maliyet, cooldown ve EXAM-körlüğü testleri geçti.
- Gerçek Mac kalite kapısı 5.571 ham haberde çalıştırıldı.
- Zaman formatı kapsamı %100 ve kullanılabilir başlık oranı %99,8 olmasına
  rağmen kesin intraday timestamp oranı %0 çıktı.
- Tüm haberlerin arşiv-günü 12:00 fallback zamanı taşıdığı doğrulandı.
- Yönlü olay, dedup, span, kaynak, fiyat ve coin kapılarındaki sıfırların kök
  zaman kapısının aşağı akış sonucu olduğu kaydedildi.
- Sonuç DATA_QUALITY_BLOCKED oldu; aday seçimi ve EXAM çalıştırılmadı.
- Tarihsel saat uydurulmadı ve GDELT günlük arşivle event-time replay kapatıldı.
- Aynı tarihsel arşive dayanan eski %77,6 NEWS BASE, ROBUST_B ve provisional
  Champion etiketleri edge/ürün kanıtı olmaktan çıkarıldı.
- REGIME_AWARE_FORWARD_V2 yalnız gerçek forward veride sıfır tarihsel krediyle
  ölçülen dondurulmuş SHADOW hipotezi olarak bırakıldı.
- Haber doğrulamasının yalnız kesin timestamp taşıyan forward örneklerle devam
  etmesine karar verildi; hiçbir canlı politika değiştirilmedi.

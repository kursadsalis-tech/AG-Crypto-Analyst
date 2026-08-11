# AG Crypto Analyst - Session Log

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

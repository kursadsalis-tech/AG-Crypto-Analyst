# AG Crypto Analyst - Changelog

## Unreleased - Historical Decision Validation

### Leakage-Safe Decision Replay

- 13 coin için yaklaşık 400 günlük kapalı 1h mumla point-in-time replay
- Sonraki mum açılışında giriş; 1h, 4h ve 24h sonuç
- 10 baz puan gidiş-dönüş maliyet
- Kronolojik LEARN / VALIDATION / EXAM
- Gerçek Binance quote-volume saklama ve eski tablo migrasyonu
- 13/13 veri kapsamı, 127.322 yeni mum, sıfır kaynak ve zaman boşluğu hatası
- 9.576 karar noktasında 23.881 gölge aday

### Conservative Challenger Lab

- FOLLOW ve CONTRARIAN yönleri için önceden sabit 36 kural/horizon
- Teknik güç, 24h momentum ve normal piyasa filtreleri
- LEARN'de çoklu deneme için z=3,20; ilk üç için VALIDATION z=2,40
- Korelasyon şişmesini azaltan 7 günlük zaman blokları
- Yalnız dondurulan kazananın EXAM'a girebilmesi
- 1h, 4h ve 24h için NO_LEARN_EDGE
- Canlı Decision Engine politikasında ve ürün kapılarında değişiklik yok

Karar:

Mevcut sürekli fiyat-durum kural ailesi üretim için reddedildi. Aynı eşikler
yeniden optimize edilmeyecek. Yalnız seyrek teknik geçiş/kırılım hipotezi bir
kez sınanacak; o da başarısızsa fiyat-teknik arama durdurulacak.

---

## v1.2.0

### Report Generator - İlk Çalışan Sürüm

Tamamlandı:

- Decision Engine, Risk Manager, Forward Shadow ve Decision Shadow verilerinden günlük yatırım komitesi brifingi
- Coin karar tablosunda market, teknik, gölge aday, ürün kararı, risk ve pozisyon tavanı ayrımı
- SHADOW modunda etkili ürün kararını BEKLE'ye kapatan rapor seviyesi güvenlik
- Risk BLOCK bulunan coinlerin fırsat olarak sunulmaması
- En güçlü fırsat için PRODUCTION + product AL + Risk ALLOW zorunluluğu
- Risk/Decision snapshot eskiliği için 30 dakikalık DATA_BLOCKED kapısı
- Kanıt olgunluk tablosu ve küçük örneklem uyarıları
- Markdown günlük rapor çıktısı ve idempotent committee_report_snapshots kaydı
- Europe/Istanbul 11:00 sonrası günde tek çalışma
- Manuel testin planlı günlük slotu tüketmemesi
- 11 adımlı canlı ürün döngüsü entegrasyonu

Canlı doğrulama:

- Report state=SHADOW_ONLY
- 13 coin, 10 risk bloğu
- Ürün aksiyonu=BEKLE
- En güçlü fırsat=YOK
- En riskli coin=BIOUSDT
- BTC SAT_ADAYI ve WLD AL_ADAYI yalnız gölge araştırma olarak kaldı
- Saat 00:51 Europe/Istanbul iken 11:00 cadence doğru biçimde bekledi
- Tam canlı döngü 11/11 başarı

Not:

İlk doğal 11:00 çalışması operasyonel olarak izlenecektir. Rapor, gölge veya olgunlaşmamış kanıtı güçlü fırsata çeviremez.

---

## v1.1.0

### Decision Engine - İlk Çalışan Gölge Sürüm

Tamamlandı:

- 13 coin için açıklanabilir market, teknik ve kanıt olgunluğu kaydı
- Risk Manager BLOCK kararının nihai veto olarak uygulanması
- Gölge AL/SAT adayı ile ürün kararının ayrılması
- SHADOW politikada 13/13 ürün kararının BEKLE tutulması
- PROVISIONAL, WARMING_UP, NO_DATA, MONITOR_ONLY, UNVERIFIED ve SHADOW_ONLY kanıtların güçlü karara yükseltilmemesi
- decision_engine_snapshots kalıcı kayıtları
- Aynı coin için tek açık tahmin kuralına sahip 1h decision_shadow_predictions doğrulaması
- AL/SAT yönüne göre otomatik directional return ve hit puanlama
- 15 dakikalık Decision Engine cycle ve hata izolasyonu
- Risk Manager sonrasında, alarm köprüsünden önce 10 adımlı canlı ürün entegrasyonu

Canlı doğrulama:

- Risk snapshot 4, NORMAL, yaş 5,5 dakika
- BTC SAT_ADAYI ve WLD AL_ADAYI
- Risk tarafından engellenen 10 coin
- 13/13 ürün kararı BEKLE
- 2 açık, çakışmasız 1h gölge tahmin
- Kaynak hatası 0
- Tam ürün döngüsü 10/10 başarı

Not:

Gölge adaylar alarm veya işlem kararı değildir. Decision Engine politikası SHADOW_ONLY'dır ve gerçek forward kanıt oluşmadan otomatik olarak üretime geçemez.

---

## v1.0.0

### Risk Manager - İlk Çalışan Sürüm

Tamamlandı:

- BTC, ETH ve 11 öncelikli Binance Spot USDT coini için kapalı 1h mum tabanlı risk ölçümü
- 24h getiri, 24h fiyat aralığı, saatlik volatilite, 7 günlük düşüş ve 24h spot hacim vekili
- NORMAL / ELEVATED / HIGH / EXTREME coin risk sınıfları
- Coin bazlı azami pozisyon ve volatilite-stop referansı
- NORMAL / CAUTION / DEFENSIVE / LOCKDOWN / DATA_BLOCKED global politika durumları
- Toplam ve altcoin maruziyeti, tek pozisyon, işlem başı risk, günlük kayıp ve yeni pozisyon tavanları
- Kalıcı risk_policy_snapshots ve asset_risk_snapshots
- 15 dakikalık cadence ve canlı döngüde alarm köprüsünden önce entegrasyon
- Forward performansı için settled/hit/Wilson/directional-return üretim kapısı
- Güncel olmayan politika, kapsam dışı coin ve sıfır pozisyon tavanı için fail-closed engel
- İdempotent risk_gate_decisions
- Forward Shadow ürün alarm köprüsünün Signal Risk Gate'e bağlanması

Doğrulama:

- İlk canlı testte yapısal düşük hacimli coinler global risk sayıldığı için yanlış LOCKDOWN tespit edildi.
- Piyasa stresi ile coin bazlı işlem/likidite riski ayrıldı ve aynı canlı veri regresyon testine sabitlendi.
- Düzeltilmiş canlı sonuç: kapsam=13/13, market_state=NORMAL, market_stress=0, asset_blocks=10, source_errors=0.
- BTC ve ETH azami pozisyon tavanı %10; WLD %5; 24h spot hacmi 2 milyon doların altındaki 10 coin yeni pozisyona kapalı.
- Risk cycle SUCCESS verdi; 15 dakika içindeki tam 9/9 cycle'da doğru biçimde SKIPPED oldu.
- Signal Risk Gate canlı sonuç: SHADOW_ONLY, settled=5, hit=%20, Wilson alt=%3,6, directional return=-%0,013, izinli alarm=0.
- Deterministik testlerde zayıf kanıt, eski risk snapshot'ı ve coin bloğu reddedildi; yeterli kanıt + güncel izinli risk için ALLOW yolu doğrulandı.

Not:

Risk limitleri getiri tahmini veya işlem tavsiyesi değildir. QVOL emir defteri derinliği değil, 24h spot hacim vekilidir. Frozen Forward Shadow parametreleri değiştirilmedi; kapı yalnızca ürün alarmı için kullanım izni verir.

---

## v0.9.0

### Whale Tracker - İlk Çalışan Sürüm

Tamamlandı:

- Blockchain.com doğrulanmış bloklarından 10 milyon dolar üstü Bitcoin çıktı taraması
- Ethereum safe bloklarından 10 milyon dolar üstü native ETH transfer taraması
- ARKM, BIO, ETHFI, ILV, LQTY, SLP ve UMA için doğrulanmış Ethereum ERC-20 kontrat allowlist'i
- ERC-20 Transfer log taraması ve zincir üstü decimals doğrulaması
- Binance public spot fiyatlarıyla gözlem anı USD tahmini
- Ayrı BTC, native ETH ve ERC-20 tarama yükseklikleri
- SQLite idempotent whale event kayıtları
- 24 saatlik muhafazakâr cross-chain evidence analyzer
- Bağımsız kaynak hata izolasyonu ve 5 dakikalık cycle
- Canlı ürün döngüsüne sekizinci adım olarak entegrasyon

Doğrulama:

- İlk Bitcoin taramasında 962.051 numaralı blokta 8, sonraki blokta 1 eşik üstü çıktı kaydedildi.
- Son 24 saatlik Bitcoin kanıtı: 9 olay, 9 işlem, yaklaşık 308,2 milyon dolar brüt çıktı; en büyük tekil çıktı yaklaşık 83,0 milyon dolar.
- Ethereum native collector gerçek testte 32 safe blok taradı ve kaynak hatası vermedi.
- ERC-20 collector 7 kontratın decimals değerini zincir üzerinde doğruladı; ilk safe blok taramasında hata veya eşik üstü olay oluşmadı.
- Bütünleşik Whale Tracker cycle: SUCCESS, source_errors=0, alert_eligible=0, MONITOR_ONLY.

Not:

Brüt transfer/çıktı toplamı net akış değildir. Change, self-transfer, mint, burn veya sözleşme içi hareket olabilir. Sahiplik ile borsa giriş/çıkış yönü doğrulanmayan olaylar alarm ya da işlem sinyali değildir.

---

## v0.8.0

### Macro Agent - İlk Çalışan Sürüm

Tamamlandı:

- Resmî FRED CSV üzerinden DFF, DGS10, DTWEXBGS, VIXCLS ve CPIAUCSL toplama
- 800 günlük geçmiş ve SQLite idempotent makro gözlemleri
- Fed Funds 90 günlük baz puan değişimi
- ABD 10 yıllık tahvil faizi 30 günlük baz puan değişimi
- Geniş dolar endeksi ve VIX 30 günlük değişimleri
- CPI aylık, yıllık ve yıllık eğilim değişimi
- Beş şeffaf bileşenden PROVISIONAL RISK_ON / MIXED / RISK_OFF sınıflandırması
- Makro rejim snapshot kaydı
- 6 saatlik güvenli cadence ve hata izolasyonu
- Canlı ürün döngüsüne yedinci adım olarak entegrasyon

Doğrulama:

- Genişletilmiş collector testinde 2.481 gözlem, 4 yeni kayıt ve sıfır seri hatası.
- Analyzer kapsamı 5/5.
- Son geçici rejim RISK_ON, skor=-3.
- Entegrasyon testinde Macro adımı 6 saat dolmadan doğru biçimde SKIPPED verdi.
- Tam canlı ürün cycle'ı 7/7 adımla başarıyla tamamlandı.

Not:

Makro rejim yalnızca bağlam göstergesidir. Tarihsel ve forward doğrulama olmadan alarm, işlem sinyali veya Decision Engine girdisi değildir.

---

## v0.7.0

### On-chain Agent - İlk Çalışan Sürüm

Tamamlandı:

- Bitcoin mempool işlem sayısı, vsize ve toplam ücret metrikleri
- Bitcoin fastest/half-hour/hour/economy fee verileri
- Ethereum, Solana, BSC, Arbitrum, Base, Avalanche ve Sui güncel DeFi TVL verileri
- SQLite kalıcı ve idempotent on-chain gözlemleri
- 15 dakikalık rate-safe on-chain cycle
- Kaynak hata izolasyonu
- Sekiz zincir için son 7 günlük tarihsel TVL bootstrap
- Yaklaşık 24 saatlik (18-30h) değişim analizi
- WARMING_UP ve NO_DATA veri olgunluğu korumaları
- Collector + analyzer'ın canlı ürün döngüsüne entegrasyonu

Doğrulama:

- İlk collector testinde 14 metrik, sıfır kaynak hatası.
- Tarihsel bootstrap testinde 64 gözlem, sıfır hata.
- Yanlış 43.87 saatlik pencere tespit edilip en yakın ~24 saatlik baseline seçimiyle düzeltildi.
- Sıfır Celestia TVL verisinin hazır sayılması engellendi.
- Son analyzer testinde yedi zincir STABLE, Bitcoin ağ metrikleri WARMING_UP.
- Otomatik On-chain cycle: SUCCESS, metrics=14, analyzed=15, source_errors=0.

Not:

İlk sürüm zincir seviyesi likidite ve Bitcoin ağ sağlığını kapsar. Token transferleri, borsa giriş/çıkışları ve büyük cüzdan hareketleri ileri on-chain/Whale Tracker kapsamındadır. Olgunlaşmamış metrikler işlem veya alarm sinyali değildir.

---

## v0.6.0

### Social Sentiment Agent - İlk Çalışan Sürüm

Tamamlandı:

- Geniş Reddit kripto topluluk akışından tek istekte 100 güncel paylaşım toplama
- SQLite kalıcı kayıt ve global external_id tekilleştirme
- Başlık + gönderi içeriği sentiment analizi
- Takip listesi aliasları ve dinamik Binance Spot USDT ticker keşfi
- Belirsiz sosyal ticker yanlış eşleşmelerine karşı koruma
- Coin bazlı 24 saatlik sentiment özeti
- Pozitif / negatif / nötr yön sınıflandırması
- Örneklem, yönlü kanıt ve fikir birliğine dayalı muhafazakâr güven puanı
- 15 dakikalık rate-safe çalışma aralığı
- 5 dakikalık canlı ürün döngüsüne hata izolasyonlu entegrasyon

Doğrulama:

- Collector gerçek veride 100 paylaşım aldı.
- Tekilleştirme testi aynı akışta yeni kayıt=0 üretti.
- Yanlış LINK eşleşmesi tespit edilip düzeltildi.
- Analyzer gerçek coin bazlı sinyaller ve muhafazakâr güven puanları üretti.
- Cadence testi 15 dakika dolmadan ikinci isteği doğru biçimde atladı.
- NEWS -> FORWARD SHADOW -> ALERT BRIDGE -> SOCIAL SENTIMENT tam cycle testi geçti.

Not:

Özel takip listesindeki düşük hacimli coinler için sosyal örneklem henüz yetersizdir. Düşük güvenli sosyal sonuçlar üretim alarmı veya işlem sinyali sayılmaz; validasyon paralelde devam eder.

---

## Unreleased - v0.5.x Research / Validation Layer

### News Intelligence doğrulama ve ürünleştirme

Eklendi:

- GDELT tarihsel haber backfill ile yaklaşık 360 günlük veri seti
- Historical News Analyzer
- Historical Market Data eşleştirme
- Leakage-safe Walk-Forward Backtest
- Locked Out-of-Sample Validation
- Rolling Research Laboratory
- Policy Robustness Analyzer
- Frozen Policy Monthly Stress Test
- Market Regime Analyzer
- Regime-Aware Rolling Validation
- High-Confidence Signal Calibration
- News + Technical Confluence Lab
- Weighted Multi-Factor Champion/Challenger Lab
- Multifactor Robustness & Weight Stability Analyzer
- Adaptive Dual-Champion Lab

Araştırma sonucu:

- 1h NEWS BASE policy güçlü benchmark olarak belirlendi.
- Weighted REGIME_AWARE multifactor provisional Champion adayı oldu.
- Multifactor robustness statüsü ROBUST_B.
- Adaptive selector reddedildi.
- Overfitting riskini azaltmak için aynı tarihsel veri üzerindeki optimizasyon durduruldu ve model forward doğrulamaya geçirildi.

### Forward Shadow Validation

Eklendi:

- REGIME_AWARE_FORWARD_V2
- 1h horizon
- Frozen cutoff 0.249
- Gerçek forward-only tahmin kaydı
- 1h sonra Binance minute data ile otomatik settle
- NEWS BASE vs MULTIFACTOR scoreboard
- V1 pre-start seed hatası düzeltmesi; 16 eski kayıt geçersiz sayıldı
- Tek komutluk live cycle: collect -> analyze -> settle/record
- macOS LaunchAgent ile 5 dakikada bir otomatik çalışma

### Coin kapsamı

- Takip listesi coin aliasları eklendi: ARKM, FIDA, WLD, TIA, ETHFI, BIO, LQTY, ILV, SLP, UMA, IO
- Binance Spot USDT ticker keşfi dinamik hale getirildi
- Yeni coin/fırsatların sabit liste dışında da tespit edilebilmesi hedeflendi

### Bildirim / rapor

- ChatGPT Günlük Kripto Brifingi yeniden aktif edildi: 11:00
- ChatGPT Kritik Gelişme Takibi yeniden aktif edildi: saatlik condition watch
- Kritik olay yoksa bildirim göndermeme prensibi korundu
- Telegram notifier yardımcı/opsiyonel altyapı olarak eklendi; ana kullanıcı bildirim kanalı değildir

---

## v0.5.0

### News Intelligence Agent - İlk Çalışan Sürüm

Tamamlandı:

- CoinDesk RSS haber toplama
- Cointelegraph RSS haber toplama
- The Block RSS haber toplama
- SQLite kalıcı haber kayıt sistemi
- Haber tekrarlarının link bazında engellenmesi
- Haber analiz kayıt tablosu
- Haberlerden coin tespiti
- Olay türü sınıflandırması
- Pozitif / negatif / nötr yön sınıflandırması
- Etki skoru
- Güven skoru
- Analiz sonuçlarının SQLite'a kaydedilmesi

Not:

Bu sürüm kural tabanlı ilk News Intelligence katmanıdır. Nihai tahmin başarısı iddiasında bulunmaz. Tahmin değeri tarihsel ve forward validasyon katmanlarıyla ölçülmektedir.

---

## v0.4.0

### Technical Analysis Agent

Tamamlandı.

Eklenen özellikler:

- Binance'den 1H mum verilerinin alınması
- RSI(14) hesaplama
- MACD hesaplama
- EMA20 hesaplama
- EMA50 hesaplama
- RSI + MACD + EMA birleşik teknik skor
- Teknik sinyal oluşturma
- Teknik analiz raporu oluşturma

---

## v0.3.0

### Market Analyzer Agent

Tamamlandı.

Eklenen özellikler:

- SQLite'dan piyasa verilerinin okunması
- 24 saatlik fiyat değişimi analizi
- Hacim analizi
- 24 saatlik fiyat konumu
- Trend + hacim + fiyat konumu birleşik piyasa skoru
- Genel piyasa sinyali
- Market analysis raporu

---

## v0.2.0

### Market Data Agent v2

Tamamlandı.

Market Data Agent geliştirilerek daha kapsamlı piyasa verilerinin alınması ve kaydedilmesi sağlandı.

ARKMUSDT sembol problemi düzeltildi.

---

## v0.1.0

### Market Data Agent v1

Tamamlandı.

- Binance API bağlantısı
- İlk fiyat verisinin alınması
- SQLite kayıt sistemi
- Market Data Agent temel yapısı

---

# Sonraki Faz

Ana ürün yol haritası tamamlandı. Yeni sürüm numarası gerektiren geliştirmeler yalnız ölçülmüş ürün ihtiyacıyla açılacaktır; yakın öncelik operasyonel doğrulama, veri birikimi ve güvenilirliktir.

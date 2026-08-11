# AG Crypto Analyst - Changelog

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

# Gelecek Sürümler

## v0.8.0
Macro Agent

## v0.9.0
Whale Tracker

## v1.0.0
Risk Manager

## v1.1.0
Decision Engine

## v1.2.0
Report Generator

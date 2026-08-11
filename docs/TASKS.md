# AG Crypto Analyst - Görev Listesi

## ANA HEDEF

"Dünyanın en başarılı tahminlerini yapan, fırsatları ve riskleri erken tespit eden profesyonel bir kripto analiz ve karar destek sistemi" oluşturmak.

---

## ANA ÜRÜN YOL HARİTASI

- [x] Market Data Agent
- [x] Technical Analysis Agent
- [x] News Intelligence Agent
- [x] Social Sentiment Agent
- [x] On-chain Agent
- [x] Macro Agent
- [ ] Whale Tracker
- [ ] Risk Manager
- [ ] Decision Engine
- [ ] Report Generator

---

## TAMAMLANAN SÜRÜMLER

- [x] v0.1.0 - Market Data Agent v1
- [x] v0.2.0 - Market Data Agent v2
- [x] v0.3.0 - Market Analyzer Agent
- [x] v0.4.0 - Technical Analysis Agent
- [x] v0.5.0 - News Intelligence Agent
- [x] v0.6.0 - Social Sentiment Agent
- [x] v0.7.0 - On-chain Agent
- [x] v0.8.0 - Macro Agent

---

## v0.5.0 SONRASI DOĞRULAMA / ÜRÜNLEŞTİRME

- [x] Tarihsel haber veri setini ~360 güne genişlet
- [x] Tarihsel haberleri fiyat verisiyle eşleştir
- [x] Leakage-safe walk-forward backtest
- [x] Locked out-of-sample validation
- [x] Rolling research laboratory
- [x] Policy robustness analyzer
- [x] Monthly frozen stress test
- [x] Market regime analysis
- [x] Regime-aware rolling validation
- [x] High-confidence calibration
- [x] News + technical confluence lab
- [x] Weighted multifactor champion/challenger lab
- [x] Multifactor robustness analyzer
- [x] Adaptive dual-champion test
- [x] REGIME_AWARE multifactor adayı dondur
- [x] Forward Shadow Validation V2 kur
- [x] Pre-start seed hatasını düzelt ve V1 kayıtlarını geçersiz say
- [x] Forward cycle'ı tek komutta haber toplama -> analiz -> settle/record akışına bağla
- [x] Mac LaunchAgent ile 5 dakikada bir otomatik çalıştır
- [x] Takip listesi coin aliaslarını News Analyzer'a ekle
- [x] Dinamik Binance Spot USDT ticker keşfi ekle
- [x] Günlük 11:00 ChatGPT kripto brifingini yeniden etkinleştir
- [x] Saatlik koşullu kritik gelişme takibini yeniden etkinleştir

---

## v0.6.0 SOCIAL SENTIMENT - TAMAMLANAN

- [x] Geniş Reddit kripto topluluk akışından tek istekle 100 paylaşım topla
- [x] Sosyal gözlemleri SQLite'a tekilleştirerek kaydet
- [x] Başlık ve gönderi içeriğini birlikte analiz et
- [x] Coin/ticker yanlış eşleşmelerini azalt
- [x] 24 saatlik coin sentiment özeti üret
- [x] Güveni örneklem + yönlü kanıt + fikir birliğine bağla
- [x] 15 dakikalık güvenli çalışma aralığı ekle
- [x] Social Sentiment'i 5 dakikalık canlı ürün döngüsüne hata izolasyonuyla bağla
- [x] Gerçek verili collector, analyzer, cadence ve tam cycle testlerini geçir

---

## v0.7.0 ON-CHAIN - TAMAMLANAN

- [x] Bitcoin mempool ve ücret metriklerini anahtarsız resmî kaynaktan topla
- [x] Yedi büyük zincirin güncel DeFi TVL verisini topla
- [x] On-chain gözlemleri SQLite'a idempotent biçimde kaydet
- [x] 15 dakikalık güvenli çalışma aralığı ekle
- [x] On-chain collector'ı canlı ürün döngüsüne hata izolasyonuyla bağla
- [x] Sekiz zincir için 7 günlük tarihsel TVL bootstrap yap
- [x] Yaklaşık 24 saatlik değişim analyzer'ı ekle
- [x] WARMING_UP ve NO_DATA güvenlik durumlarını uygula
- [x] Collector + analyzer otomatik cycle testini gerçek veride geçir

---

## v0.8.0 MACRO - TAMAMLANAN

- [x] Beş resmî FRED serisini anahtarsız CSV kaynağından topla
- [x] 800 günlük tarihsel pencereyi SQLite'a idempotent kaydet
- [x] Fed Funds, ABD 10Y, geniş dolar, VIX ve CPI değişimlerini hesapla
- [x] Şeffaf bileşen skorlarıyla PROVISIONAL makro rejim üret
- [x] Makro rejim snapshot'larını kalıcı kaydet
- [x] 6 saatlik güvenli çalışma aralığı ve hata izolasyonu ekle
- [x] Macro collector + analyzer cycle testini gerçek veride geçir
- [x] Macro cycle'ı canlı ürün döngüsüne yedinci adım olarak bağla
- [x] Entegrasyon ve cadence-atlama testini geçir

---

## AKTİF GÖREVLER

- [ ] Forward Shadow V2'de ilk gerçek uygun sinyallerin oluşmasını izle
- [ ] 1h süresi dolan tahminlerin otomatik settle edildiğini doğrula
- [ ] NEWS BASE vs MULTIFACTOR gerçek forward scoreboard'u örneklem büyüdükçe değerlendir
- [ ] Kritik alarm sisteminin gereksiz bildirim üretmediğini, kritik olayda ise uyardığını takip et
- [ ] Günlük yatırım komitesi raporunda Forward Shadow performansını kullan
- [ ] Yeterli forward kanıt oluşunca provisional multifactor Champion için üretim kararı ver
- [ ] Social Sentiment örneklemini özellikle takip listesi coinlerinde büyüt
- [ ] Sosyal sinyali yeterli güven ve forward kanıt olmadan alarm/işlem kararına dahil etme
- [ ] Bitcoin on-chain metriklerinde 24 saatlik doğal geçmişin oluşmasını izle
- [ ] WARMING_UP ve NO_DATA on-chain metriklerini alarm/karar girdisi yapma
- [ ] Token bazlı on-chain kapsamı sonraki genişletme/Whale Tracker ile büyüt
- [ ] PROVISIONAL makro rejim snapshot'larını tarihsel/forward doğrulama için biriktir
- [ ] Makro rejimi doğrulanmadan alarm, işlem kararı veya Decision Engine girdisi yapma

---

## SONRAKİ ANA MODÜL

### v0.9.0 - Whale Tracker

Amaç:

Büyük cüzdan hareketlerini, yüksek değerli token transferlerini ve mümkün olan güvenilir kaynaklarda borsa giriş/çıkış bağlamını izleyerek erken risk/fırsat göstergeleri üretmek.

Geçiş koşulu:

Forward Shadow, Social Sentiment, On-chain ve Macro veri toplama süreçleri paralelde devam edecek; yalnızca doğrulanmış balina göstergeleri alarm veya ileride Decision Engine girdisi olabilecek.

---

## KAPSAM PRENSİBİ

Sistem yalnızca ARKM, FIDA, WLD, TIA, ETHFI, BIO, LQTY, ILV, SLP, UMA ve IO takip listesiyle sınırlı değildir. Bu liste özel izleme önceliğidir; teknik olarak Binance Spot USDT evrenindeki yeni coin/fırsatlar da tespit edilebilmelidir.

---

## ÇALIŞMA PRENSİBİ

Tek adım -> test -> doğrulama -> sonraki adım.

Gereksiz mimari karmaşıklık yok. Küçük örneklem üzerinde overfitting yok. Gelecek verisi geçmiş kararlara sızdırılmaz. Aynı tarihsel veri yapay biçimde başarı artırmak için sonsuza kadar optimize edilmez.

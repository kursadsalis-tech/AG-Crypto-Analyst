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
- [x] Whale Tracker
- [x] Risk Manager
- [x] Decision Engine
- [x] Report Generator

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
- [x] v0.9.0 - Whale Tracker
- [x] v1.0.0 - Risk Manager
- [x] v1.1.0 - Decision Engine (SHADOW politika; ürün kararı BEKLE)
- [x] v1.2.0 - Report Generator

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

## v0.9.0 WHALE TRACKER - TAMAMLANAN

- [x] Doğrulanmış Bitcoin bloklarında 10 milyon dolar üstü tekil çıktıları tara
- [x] Ethereum safe bloklarında 10 milyon dolar üstü native ETH transferlerini tara
- [x] Yedi doğrulanmış takip-listesi ERC-20 kontratını Transfer loglarıyla izle
- [x] ERC-20 decimals değerlerini zincir üstünde doğrula
- [x] USD değerini canlı Binance public spot fiyatıyla hesapla
- [x] Tarama yüksekliklerini ve olayları SQLite'a idempotent kaydet
- [x] Bitcoin, native ETH ve ERC-20 kaynaklarını birbirinden izole et
- [x] Cross-chain 24 saatlik kanıt özeti üret
- [x] Doğrulanmamış sahiplik/yön/borsa akışını MONITOR_ONLY tut
- [x] 5 dakikalık Whale Tracker cycle'ını canlı ürün döngüsüne sekizinci adım olarak bağla
- [x] Gerçek verili collector, cross-chain analyzer ve bütünleşik cycle testlerini geçir

---

## v1.0.0 RISK MANAGER - TAMAMLANAN

- [x] 13 coin için kapalı 1h mumlardan piyasa ve coin risk metrikleri üret
- [x] 24h getiri/aralık, saatlik volatilite, 7g düşüş ve 24h spot hacim vekilini hesapla
- [x] Coin bazlı risk sınıfı ve maksimum pozisyon tavanı belirle
- [x] Global maruziyet, altcoin maruziyeti, işlem riski, günlük kayıp ve yeni pozisyon limitlerini tanımla
- [x] Eksik kritik veride DATA_BLOCKED; piyasa kuyruğu riskinde LOCKDOWN uygula
- [x] Düşük hacimli coin bloklarını global piyasa stresinden ayır
- [x] Risk policy ve asset snapshot'larını SQLite'a kalıcı kaydet
- [x] 15 dakikalık Risk Manager cycle ve hata izolasyonu ekle
- [x] Risk Manager'ı canlı ürün akışında alarm köprüsünden önce çalıştır
- [x] Forward kanıtına örneklem, hit rate, Wilson alt sınırı ve directional return kapısı uygula
- [x] Eski/eksik risk verisini ve izin verilmeyen coini fail-closed engelle
- [x] Risk gate kararlarını idempotent kaydet
- [x] Ürün alarm köprüsünü Signal Risk Gate'e bağla
- [x] Gerçek veri, cadence, 9/9 entegrasyon ve alarm blok/izin yollarını doğrula

---

## v1.1.0 DECISION ENGINE - TAMAMLANAN

- [x] Kanıt olgunluğu durumlarını tek açıklanabilir karar kaydında görünür tut
- [x] Kapalı 1h mumlara dayalı teknik/market araştırma bağlamı üret
- [x] PROVISIONAL, WARMING_UP, NO_DATA, MONITOR_ONLY, UNVERIFIED ve SHADOW_ONLY kanıtı ürün kararından dışla
- [x] Risk Manager BLOCK kararını nihai veto olarak uygula
- [x] Gölge AL/SAT adayını ürün kararından ayır; SHADOW politikada ürünü BEKLE tut
- [x] Decision Engine snapshot'larını SQLite'a kalıcı kaydet
- [x] Gölge adayları için 1 saatlik forward sonuçlandırma ekle
- [x] Aynı coin için tek açık tahmin kuralıyla overlap şişmesini engelle
- [x] 15 dakikalık Decision Engine cycle ve hata izolasyonu ekle
- [x] Decision Engine'i Risk Manager sonrasında canlı ürün döngüsüne bağla
- [x] Gerçek verili standalone, gölge validator ve 10/10 bütünleşik cycle testlerini geçir

---

## v1.2.0 REPORT GENERATOR - TAMAMLANAN

- [x] Son Decision, Risk, Forward ve Decision Shadow kayıtlarından günlük komite raporu üret
- [x] Gölge araştırma adayını ürün kararından ayrı göster
- [x] SHADOW modunda tüm etkili ürün kararlarını BEKLE'ye kapat
- [x] Risk BLOCK bulunan coini fırsat olarak göstermeyi engelle
- [x] En güçlü fırsatı yalnız PRODUCTION + product AL + Risk ALLOW birleşiminden seç
- [x] Risk ve Decision snapshot eskiliğinde DATA_BLOCKED uygula
- [x] Kanıt olgunluklarını ayrı rapor tablosunda görünür tut
- [x] Günlük raporu Markdown dosyasına ve SQLite snapshot'a idempotent kaydet
- [x] Europe/Istanbul 11:00 sonrası günlük tek-çalışma cadence'i ekle
- [x] Manuel testin planlı 11:00 slotunu tüketmesini engelle
- [x] Report Generator'ı canlı ürün döngüsüne hata izolasyonuyla bağla
- [x] 26 fail-closed, 9 zamanlama ve 11/11 gerçek entegrasyon testini geçir

---

## AKTİF GÖREVLER

- [x] Tek merkezi Forecast Core veri sözleşmesini 1h/4h/24h için kilitle
- [x] Sabit pooled-ridge dağılım challenger'ını ve no-change/climatology benchmark'larını kodla
- [x] FIT/CALIBRATION/VALIDATION/EXAM purge, next-open maliyet ve exact quote-volume korumalarını ekle
- [x] Kalibre UP/DOWN/NEUTRAL olasılığı, medyan hedef ve %80 fiyat aralığı üret
- [x] Brier/log-loss/ECE, normalize MAE, dağılım kaybı, interval ve ekonomik kapıları sabitle
- [x] Forecast Contract ve laboratuvar deterministik self-testlerini geçir
- [x] Forecast Core laboratuvarını gerçek Mac veri tabanında bir kez çalıştır
- [x] Üç başarısız horizon için OBSERVED_DIAGNOSTIC'in açılmadığını ve canlı politikanın değişmediğini doğrula
- [x] 1h/4h/24h REJECT sonrası fiyat-only pooled-ridge ailesini kapat
- [x] Ham model aksiyonu/Risk vetosu ayrımını ve whole-week bootstrap tanısını düzelt
- [x] Sekizden az action haftasında CI'ı N/A yap; NEUTRAL ECE ve CAL-dondurulmuş baseline ekle
- [x] `DERIVATIVES_STATE_V2` tarihsel veri collector/readiness katmanını tamamla
- [x] Funding, OI/positioning, premium/basis ve taker-flow feature sözleşmesini dondur
- [x] 4h PRIMARY, 24h SECONDARY, 1h EXPLORATORY sabit V2 challenger'ı sentetik testlerden geçir
- [x] Funding interval normalizasyonu, sınırlı context-gap ve checksum revalidation politikalarını kilitle
- [x] V2 artifact JSON round-trip hash ve horizon örneklem fizibilitesi blocker'larını kapat
- [x] Collector/V1/V2 self-test, compileall, 26-feature ve 400g/10-coin readiness testlerini geçir
- [ ] Derivatives V2'yi gerçek Mac verisinde yalnız bir kez çalıştır
- [ ] Geçen horizon varsa yalnız DEVELOPMENT_ONLY artifact olarak dondur; ürün alarmına bağlama
- [ ] V2 de başarısızsa eşik/lag tarama; aileyi kapat ve sonraki bağımsız veri kampanyasına geç

- [x] Decision Engine politikasını 13 coin ve yaklaşık 400 günlük kapalı 1h mumla point-in-time replay et
- [x] Girişleri sonraki mum açılışına taşı ve 1h/4h/24h sonuçlara 10 bps maliyet uygula
- [x] Replay'i kronolojik LEARN/VALIDATION/EXAM olarak ayır
- [x] 36 sabit FOLLOW/CONTRARIAN challenger'ı 7 günlük blok güven sınırlarıyla değerlendir
- [x] Üç ufukta NO_LEARN_EDGE sonucunda mevcut fiyat-durum kuralını üretim için reddet
- [x] Başarısız tarihsel sonucu canlı politika/eşik değişikliğine çevirmeden SHADOW_ONLY tut
- [x] Tek sınırlı seyrek teknik-geçiş hipotezini 24h coin cooldown ile replay et
- [x] Üç ufukta NO_LEARN_EDGE sonrası fiyat-teknik kural optimizasyonunu durdur
- [x] Bağımsız haber-katalizör replay'ini veri-kalitesi kapıları, strict coin eşleştirme, dedup, 60m gecikme, 24h cooldown ve 10 bps maliyetle hazırla
- [x] Haber-katalizör laboratuvarında sentetik leakage, next-open, maliyet, cooldown ve EXAM-körlüğü testlerini geçir
- [x] Haber-katalizör replay'ini gerçek Mac veri tabanında bir kez çalıştır ve kalite sonucunu kaydet
- [x] %0 kesin intraday timestamp nedeniyle seçimi ve EXAM'ı çalıştırmadan DATA_QUALITY_BLOCKED olarak kapat
- [x] Tarihsel haber saatlerini uydurmama ve GDELT günlük arşivle event-time replay'i durdurma kararını kilitle
- [ ] Haber katalizörü için yalnız kesin published/collected timestamp taşıyan forward örnekleri biriktir
- [ ] Forward Shadow V2'de ilk gerçek uygun sinyallerin oluşmasını izle
- [ ] 1h süresi dolan tahminlerin otomatik settle edildiğini doğrula
- [ ] NEWS BASE vs MULTIFACTOR gerçek forward scoreboard'u örneklem büyüdükçe değerlendir
- [ ] Kritik alarm sisteminin gereksiz bildirim üretmediğini, kritik olayda ise uyardığını takip et
- [ ] Günlük yatırım komitesi raporunda Forward Shadow performansını kullan
- [x] %0 kesin tarihsel timestamp nedeniyle provisional multifactor Champion ve ROBUST_B tarihsel kredi etiketlerini geri çek
- [ ] Yalnız değişmemiş forward kapıları gerçek veride geçilirse modeli sıfırdan ürün adayı olarak yeniden değerlendir
- [ ] Forward Signal Risk Gate koşullarını kanıt biriktikçe değiştirmeden izle
- [ ] SHADOW_ONLY sinyallerin ürün alarmı üretemediğini izlemeye devam et
- [ ] Risk policy snapshot'larını ve coin bazlı blokları Decision Engine gölge doğrulamasıyla birlikte biriktir
- [ ] Decision Engine 1h gölge AL/SAT sonuçlarını örneklem büyüdükçe izle
- [ ] Gölge politika için küçük örneklemde eşik değiştirme veya üretim onayı verme
- [ ] İlk doğal 11:00 Report Generator çalışmasını gözle ve günlük rapor snapshot'ını doğrula
- [ ] Günlük raporun SHADOW veriyi fırsat diye sunmadığını operasyonel olarak izlemeye devam et
- [ ] Social Sentiment örneklemini özellikle takip listesi coinlerinde büyüt
- [ ] Sosyal sinyali yeterli güven ve forward kanıt olmadan alarm/işlem kararına dahil etme
- [ ] Bitcoin on-chain metriklerinde 24 saatlik doğal geçmişin oluşmasını izle
- [ ] WARMING_UP ve NO_DATA on-chain metriklerini alarm/karar girdisi yapma
- [x] Token bazlı on-chain kapsamın ilk bölümünü Whale Tracker ile doğrulanmış Ethereum kontratlarına genişlet
- [ ] Whale Tracker kanıtlarını biriktir; UNVERIFIED hareketleri alarm/karar girdisi yapma
- [ ] Diğer zincirlerdeki takip-listesi tokenlarını yalnızca güvenilir ve doğrudan doğrulanabilir kaynak bulunduğunda genişlet
- [ ] PROVISIONAL makro rejim snapshot'larını tarihsel/forward doğrulama için biriktir
- [ ] Makro rejimi doğrulanmadan alarm, işlem kararı veya Decision Engine girdisi yapma

---

## ANA ÜRÜN YOL HARİTASI TAMAMLANDI

v0.1.0 ile v1.2.0 arasındaki on ana ürün modülünün çalışan ilk sürümleri tamamlandı.

Sıradaki faz:

- doğal zamanlanmış çalışmaların operasyonel doğrulaması,
- gerçek forward örneklemlerin büyütülmesi,
- günlük rapor ve kritik alarm sürekliliğinin izlenmesi,
- ölçülmüş sonuçlara göre kontrollü iyileştirme,
- tek Forecast Core laboratuvarının gerçek veri sonucunu alma,
- gereksiz yeni mimari açmadan ürün güvenilirliğinin artırılması.

Decision Engine SHADOW politikasından veya Forward üretim kapısından yalnız önceden kayıtlı kanıt koşulları sağlandığında ve ayrı bir üretim kararıyla çıkılabilir.

---

## KAPSAM PRENSİBİ

Sistem yalnızca ARKM, FIDA, WLD, TIA, ETHFI, BIO, LQTY, ILV, SLP, UMA ve IO takip listesiyle sınırlı değildir. Bu liste özel izleme önceliğidir; teknik olarak Binance Spot USDT evrenindeki yeni coin/fırsatlar da tespit edilebilmelidir.

---

## ÇALIŞMA PRENSİBİ

Tek adım -> test -> doğrulama -> sonraki adım.

Gereksiz mimari karmaşıklık yok. Küçük örneklem üzerinde overfitting yok. Gelecek verisi geçmiş kararlara sızdırılmaz. Aynı tarihsel veri yapay biçimde başarı artırmak için sonsuza kadar optimize edilmez.


---

## Forecast Core V2.1 Sonrası

- [x] Binance USD-M 430 günlük backfill ve checksum/manifest doğrulamasını tamamla
- [x] Seyrek opsiyonel ratio alanlarının geçerli OI bağlamlarını silmesini düzelt
- [x] Eksik tek OI saatinin 24 saatlik gap amplification hatasını exact-anchor ile düzelt
- [x] Forecast Core V2.1'i dondurulmuş gerçek VALIDATION'da bir kez çalıştır
- [x] V2.1'i üç ufukta doğrulanmış edge olmadığı için reddet ve model ailesini kapat
- [x] %70 aksiyon eşiğini sonuçtan sonra düşürmeme kararını koru
- [x] Interaction-capable Forecast Core V3 model ve evaluation manifestini sonuç görülmeden dondur
- [x] V3 geliştirmesini yalnız V2 VALIDATION ve daha eski development verisinde yap
- [x] 2026-05-28 sonrası mühürlü dönemi model/kalibrasyon/eşik seçiminden izole et
- [ ] V3'ü mühürlü final dönemde yalnız bir kez değerlendir
- [ ] Tarihsel geçiş olsa bile fresh forward doğrulama tamamlanmadan ürün alarmını açma
- [x] V3 reddedildiği için aynı fiyat+türev veri üzerinde model/threshold aramasını durdur


## Exact-Time Catalyst Sonraki Faz

- [ ] Resmî ve doğrulanabilir catalyst kaynaklarında published_at/available_at tarihsel kapsam fizibilitesini denetle
- [ ] Gün/saat uydurulan veya yalnız arşiv günü taşıyan olayları kesin olarak dışla
- [ ] Yeterli geçmiş varsa tek event-conditioned Forecast Core adayını sonuç görülmeden dondur
- [ ] Yeterli geçmiş yoksa aynı collector sözleşmesiyle forward exact-time veri biriktir

## 2026-08-16 — Exact-time catalyst checkpoint

- [x] Audit first-party Binance exact-time listing/delisting history without reading price outcomes.
- [x] Isolate timestamp-less legacy announcements and prove 100% exact-time coverage in the protected recent window.
- [x] Collect checksum-verified targeted Binance Vision spot archives around strict catalyst events.
- [x] Implement and self-test the frozen direct catalyst event study (listing=AL, delisting=SAT/REDUCE/AVOID).
- [ ] Run the real fixed catalyst study and inspect the primary 4h validation output.
- [ ] If and only if 4h validation passes, freeze the candidate and start fresh forward probability calibration.
- [ ] If 4h validation rejects, stop this bounded catalyst hypothesis; do not open another threshold/model grid on the same data.


## Unified Forecast Runtime — 2026-08-16

- [x] 13 coin x 1h/4h/24h için 39 gerçek probabilistic forecast üret
- [x] q50 hedef, %80 aralık ve UP/DOWN/NEUTRAL olasılıklarını kalıcı kaydet
- [x] Güncel Risk Manager snapshot'ını yalnız veto/cap katmanı olarak bağla
- [x] Eski mumla retrospektif forecast ve eksik saatle gap-bridged settlement'ı engelle
- [x] İlk canlı 39/39 forecast bundle'ını gerçek Mac verisinde aç
- [ ] İlk 1h/4h/24h kayıtlarını otomatik settle et ve sonuçları doğrula
- [ ] Forecast çıktılarını günlük yatırım komitesi raporunda ana tahmin tablosu olarak göster
- [ ] Yeterli forward örneklem oluşunca horizon bazlı kalibrasyon ve başarı kapılarını değerlendir


## Forecast learning loop — completed

- [x] Persist exact named feature snapshots for every new forecast
- [x] Preserve other-agent evidence as observed-only context, not silent model input
- [x] Backfill exact model features for the first open 39-row bundle
- [x] Diagnose settled target, direction, magnitude and interval errors
- [x] Add latest 39 forecasts and previous outcomes to the daily committee report
- [x] Add horizon-level MAE, direction, interval and action scorecards to the report
- [x] Keep report compatible with pre-migration forecast databases


## Evidence-gated Forecast Self-Correction — 2026-08-16

- [x] Coin kara listesi ve tek hata sonrası ağırlık değiştirmeyi yasakla
- [x] Settlement hatalarını ufuk, rejim, tanı ve bağlam etiketleriyle grupla
- [x] En az 30 olay ve 3 bağımsız hafta olmadan hata örüntüsü açma
- [x] En az 100 settlement öncesinde öğrenme durumunu COLLECTING_EVIDENCE tut
- [x] Yön kalibrasyonu, hedef ölçeği, aralık ve bağlam-feature challenger türlerini sınırla
- [x] Forecast Learning Review'u ana canlı çevrime bağla
- [ ] Yeterli forward örneklemde ilk kararlı hata örüntüsünü doğrula
- [ ] Yalnız champion'ı untouched forward ölçümde geçen düzeltmeyi terfi ettir

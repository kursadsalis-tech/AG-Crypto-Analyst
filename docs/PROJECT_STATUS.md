# AG Crypto Analyst - Project Status

## 1. PROJENİN ANA HEDEFİ

AG Crypto Analyst'ın nihai amacı:

"Dünyanın en başarılı tahminlerini yapan, fırsatları ve riskleri erken tespit eden profesyonel bir kripto analiz ve karar destek ürünü" oluşturmak.

Sistem yalnızca sabit bir takip listesi için değil, Binance Spot USDT evreninde yeni ortaya çıkan coinleri ve yeni fırsatları da mümkün olduğunca dinamik biçimde tespit edebilmelidir.

Amaç falcılık değil; fiyat, teknik yapı, haber, piyasa rejimi, on-chain, sosyal, makro ve balina verilerini matematiksel/istatistiksel olarak birleştiren, ölçülebilir ve sürekli doğrulanan bir sistem kurmaktır.

Gereksiz mimari karmaşıklıktan kaçınılacaktır. Amaç sürekli yeni mimariler tasarlamak değil, çalışan ve giderek daha başarılı hale gelen gerçek bir ürün çıkarmaktır.

---

## 2. DEĞİŞMEZ ANA ÜRÜN YOL HARİTASI

1. Market Data Agent
2. Technical Analysis Agent
3. News Intelligence Agent
4. Social Sentiment Agent
5. On-chain Agent
6. Macro Agent
7. Whale Tracker
8. Risk Manager
9. Decision Engine
10. Report Generator

Araştırma/validasyon katmanları bu yol haritasının yerine geçmez; mevcut modüllerin doğruluğunu ölçmek ve Decision Engine'e güvenilir sinyal üretmek için kullanılır.

---

## 3. MEVCUT SÜRÜM

Proje ürün sürümü: **v1.2.0**

Tamamlanan ana ürün modülleri:

- Market Data Agent ✅
- Market Analyzer ✅
- Technical Analysis Agent ✅
- News Intelligence Agent ✅
- Social Sentiment Agent ✅
- On-chain Agent ✅
- Macro Agent ✅
- Whale Tracker ✅
- Risk Manager ✅
- Decision Engine ✅ — SHADOW modunda; ürün kararları doğrulama kazanmadıkça BEKLE
- Report Generator ✅ — fail-closed günlük yatırım komitesi brifingi

**Ana ürün yol haritasındaki 10 modülün tamamı çalışan ilk sürümleriyle tamamlandı.** Sonraki fazın tek kontrollü mimari istisnası ölçülebilir Forecast Core araştırma katmanıdır; bunun dışında öncelik doğal 11:00 çalışmasının gözlenmesi, veri birikimi, performans doğrulaması ve ürün güvenilirliğidir.

Forward Shadow V2, Decision Engine Shadow Validation, Social Sentiment, On-chain, Macro, Whale Tracker ve Risk Manager süreçleri ana yol haritasını bloke etmeden canlı ürün döngüsünde paralel çalışacaktır.

---

## 4. NEWS INTELLIGENCE + ARAŞTIRMA DURUMU

Tarihsel haber veri seti yaklaşık 360 günlük döneme genişletildi.

Geliştirilen ölçüm/araştırma katmanları:

- historical news analyzer
- historical market data eşleştirme
- leakage-safe walk-forward backtest
- locked out-of-sample validation
- rolling research laboratory
- policy robustness analyzer
- frozen monthly stress test
- market regime analyzer
- regime-aware rolling validation
- high-confidence calibration
- news + technical confluence lab
- weighted multifactor champion/challenger lab
- multifactor robustness analyzer
- adaptive dual-champion lab

Eski araştırma kayıtları ve güncel hüküm:

- Eski laboratuvarlar negative direction + confidence >= 70 NEWS BASE için
  yaklaşık %77,6 frozen EXAM hit rate raporladı.
- Weighted REGIME_AWARE model bazı eski frozen karşılaştırmalarda base'i geçti
  ve ROBUST_B etiketi aldı.
- Ancak bu çalışmaların dayandığı GDELT arşivinde kesin intraday timestamp
  oranının %0 olduğu son kalite denetiminde kanıtlandı.
- Bu nedenle %77,6, ROBUST_B ve provisional Champion etiketleri ürün veya
  tarihsel edge kanıtı olarak geri çekildi; yalnız geçmiş araştırma kaydıdır.
- REGIME_AWARE_FORWARD_V2 canlı politikaya açılmaz. Sıfır tarihsel krediyle,
  dondurulmuş bir hipotez olarak yalnız gerçek forward veride ölçülür.
- Adaptive dual-champion seçici reddedilmiş olarak kalır.

---

## 5. FORWARD SHADOW VALIDATION - AKTİF

Gerçek forward doğrulama sistemi kuruldu.

Kilitli model:

- Model: REGIME_AWARE_FORWARD_V2
- Horizon: 1 saat
- Frozen cutoff: 0.249
- Ağırlıklar forward başladıktan sonra değiştirilmez.
- Geçmiş haberlerden geriye dönük tahmin üretilmez.
- Yalnızca validator başlangıcından sonra ilk kez toplanan haberler forward tahmin doğurabilir.

V1 ilk çalıştırmada eski haberleri yanlışlıkla forward tahmin gibi seed etmişti; bu 16 kayıt INVALID_PRESTART_SEED olarak geçersiz sayıldı. V2 temiz başlangıç yaptı.

Forward başlangıç zamanı:

2026-08-11T01:29:56.397166+00:00

Mac LaunchAgent:

- Label: com.agca.forward-shadow
- Çalışma aralığı: 5 dakika
- Akış: NEWS COLLECT -> NEWS ANALYZE -> FORWARD SHADOW -> RISK MANAGER -> DECISION ENGINE -> REPORT GENERATOR -> PRODUCT ALERT BRIDGE -> SOCIAL SENTIMENT -> ON-CHAIN -> MACRO -> WHALE TRACKER
- Social Sentiment cadence: 15 dakika; diğer 5 dakikalık cycle'larda güvenli biçimde atlanır
- On-chain cadence: 15 dakika; diğer 5 dakikalık cycle'larda güvenli biçimde atlanır
- Macro cadence: 6 saat; ara cycle'larda güvenli biçimde atlanır
- Whale Tracker cadence: 5 dakika; BTC, native ETH ve Ethereum ERC-20 kaynakları birbirinden izole çalışır
- Risk Manager cadence: 15 dakika; canlı ürün akışında Decision Engine ve alarm köprüsünden önce çalışır
- Decision Engine cadence: 15 dakika; gölge adayları 1 saatlik çakışmasız forward doğrulamaya kaydeder
- Report Generator cadence: Europe/Istanbul saat diliminde 11:00'den sonra günde bir kez; aynı gün tekrar üretmez
- Tam canlı ürün akışı 11/11 adımla gerçek veride doğrulandı
- Son doğrulamada launchd çalışması başarılıydı; last exit code = 0.

---

## 5A. SOCIAL SENTIMENT AGENT - v0.6.0 TAMAMLANDI

İlk çalışan sürüm gerçek Reddit topluluk verisiyle doğrulandı.

Tamamlananlar:

- Tek istekte geniş kripto topluluk akışından 100 güncel paylaşım toplama
- SQLite kalıcı kayıt ve global external_id tekilleştirme
- Başlık + gönderi içeriği analizi
- Takip listesi aliasları ve dinamik Binance ticker keşfi
- LINK/GMT/ALT gibi belirsiz ticker yanlış eşleşmelerine karşı koruma
- Coin bazlı 24 saatlik pozitif/negatif/nötr sentiment özeti
- Örneklem, yönlü kanıt ve fikir birliğine dayalı muhafazakâr güven puanı
- 15 dakikalık rate-safe cadence
- Mevcut 5 dakikalık canlı ürün döngüsüne hata izolasyonlu entegrasyon

İlk canlı testte BTC, ETH ve bazı büyük coinlerde veri oluştu. Özel takip listesindeki düşük hacimli coinler için örneklem henüz yetersizdir. Düşük güvenli sosyal sonuçlar alarm veya işlem sinyali olarak kullanılmayacak; veri birikimi ve ileri validasyon paralelde sürecektir.

---

## 5B. ON-CHAIN AGENT - v0.7.0 TAMAMLANDI

İlk çalışan sürüm gerçek zincir ve DeFi verileriyle doğrulandı.

Tamamlananlar:

- mempool.space üzerinden Bitcoin mempool işlem sayısı, vsize, toplam ücret ve önerilen ücretler
- DefiLlama üzerinden Ethereum, Solana, BSC, Arbitrum, Base, Avalanche ve Sui TVL verileri
- SQLite kalıcı on-chain gözlem tablosu ve idempotent kayıt kimliği
- 15 dakikalık rate-safe on-chain cycle
- Kaynak hatalarının diğer ürün modüllerini durdurmamasını sağlayan izolasyon
- DefiLlama tarihsel endpoint ile sekiz zincirde 64 gözlemlik 7 günlük bootstrap
- Yaklaşık 24 saatlik (18-30h kabul pencereli) değişim analizi
- Yetersiz geçmiş için WARMING_UP, sıfır/geçersiz veri için NO_DATA koruması
- Canlı ürün döngüsüne collector + analyzer entegrasyonu

İlk doğrulamada yedi zincirin TVL değişimi STABLE bulundu. Bitcoin ağ metrikleri 24 saatlik gerçek geçmiş oluşana kadar WARMING_UP durumundadır. Bu sonuçlar tek başına işlem/alarm sinyali değildir.

İlk sürüm zincir seviyesi likidite ve Bitcoin ağ sağlığını kapsar. Token bazlı transferler, borsa giriş/çıkışları ve büyük cüzdan hareketleri sonraki genişletme/Whale Tracker kapsamındadır.

---

## 5C. MACRO AGENT - v0.8.0 TAMAMLANDI

İlk çalışan sürüm resmî FRED verileriyle doğrulandı.

Tamamlananlar:

- Anahtarsız resmî FRED CSV kaynağından DFF, DGS10, DTWEXBGS, VIXCLS ve CPIAUCSL serileri
- 800 günlük veri penceresi ve SQLite'a idempotent makro gözlem kaydı
- Fed Funds 90g, ABD 10Y 30g, geniş dolar 30g, VIX seviyesi ve CPI YoY eğilimi ölçümleri
- Beş şeffaf bileşenden RISK_ON / MIXED / RISK_OFF bağlam sınıflandırması
- Sonuçların macro_regime_snapshots tablosuna kaydı
- 6 saatlik güvenli cadence ve kaynak hata izolasyonu
- Canlı ürün döngüsüne yedinci adım olarak entegrasyon

Son gerçek doğrulamada 2.481 gözlem alındı, 4 yeni kayıt eklendi ve kaynak hatası oluşmadı. Bileşen kapsamı 5/5, geçici rejim RISK_ON ve skor -3 oldu. Bu etiket yalnızca makro bağlamdır; tarihsel ve forward doğrulama olmadan alarm, işlem sinyali veya Decision Engine girdisi değildir.

---

## 5D. WHALE TRACKER - v0.9.0 TAMAMLANDI

İlk çalışan sürüm doğrudan zincir kanıtı ve muhafazakâr alarm politikasıyla gerçek veride doğrulandı.

Tamamlananlar:

- Blockchain.com doğrulanmış bloklarından tekil Bitcoin çıktılarında 10 milyon dolar üstü hareket taraması
- Ethereum güvenli bloklarında 10 milyon dolar üstü native ETH transfer taraması
- ARKM, BIO, ETHFI, ILV, LQTY, SLP ve UMA için doğrulanmış Ethereum ana ağ kontratlarında ERC-20 Transfer log taraması
- ERC-20 kontrat ondalıklarının her due taramada zincir üstünde doğrulanması
- Binance public spot fiyatlarıyla gözlem anı USD tahmini
- SQLite tarama yüksekliği, idempotent olay kaydı ve 24 saatlik cross-chain kanıt özeti
- BTC, native ETH ve ERC-20 kaynaklarının bağımsız hata izolasyonu
- 5 dakikalık Whale Tracker cadence ve sekizinci canlı ürün adımı entegrasyonu
- Sahiplik, borsa yönü ve alım/satım niyeti doğrulanmadıkça MONITOR_ONLY ve sıfır alarm ilkesi

Son canlı doğrulamada Bitcoin yeni blok üretmedi; native ETH kaynağı 32, ERC-20 kaynağı 1 güvenli blok taradı. Yedi token kontratının ondalıkları zincir üstünde doğrulandı, kaynak hatası oluşmadı ve yeni eşik üstü Ethereum olayı görülmedi. Son 24 saatlik kanıtta Bitcoin tarafında 9 olay, yaklaşık 308,2 milyon dolar brüt çıktı ve sıfır alarm-uygun olay vardı.

Brüt transfer/çıktı toplamı net akış değildir. Change, self-transfer, mint, burn veya sözleşme içi hareket olabilir. Yalnızca doğrulanmış borsa yönü ileride alarm adayı olabilir.

---

## 5E. RISK MANAGER - v1.0.0 TAMAMLANDI

İlk çalışan sürüm, sinyal üretmek yerine diğer modüllerin aşamayacağı fail-closed risk sınırları olarak gerçek veride doğrulandı.

Tamamlananlar:

- BTC, ETH ve 11 öncelikli coin için 169 kapalı 1h mumdan 24h getiri, 24h fiyat aralığı, saatlik volatilite, 7 günlük düşüş ve 24h spot hacim vekili hesabı
- Coin bazlı NORMAL / ELEVATED / HIGH / EXTREME risk sınıfı
- Toplam maruziyet, altcoin maruziyeti, tek pozisyon, işlem başı risk, günlük kayıp ve yeni pozisyon tavanları
- Eksik BTC veya <%80 veri kapsamı için DATA_BLOCKED; aşırı piyasa stresi için LOCKDOWN
- Düşük hacimli coin riskini global piyasa stresinden ayıran kalibrasyon
- risk_policy_snapshots ve asset_risk_snapshots kalıcı kayıtları
- 15 dakikalık cadence, kaynak izolasyonu ve canlı ürün akışında alarm köprüsünden önce entegrasyon
- Forward Shadow için fail-closed Signal Risk Gate ve idempotent risk_gate_decisions kayıtları
- Ürün alarmının yalnızca güncel risk politikası, izinli coin ve yeterli forward kanıt birlikte varsa gönderilmesi

Son canlı piyasa testinde kapsam 13/13, dinamik piyasa stresi 0/13 ve global durum NORMAL oldu. BTC ve ETH için azami pozisyon tavanı %10, WLD için %5; 24h spot hacmi 2 milyon doların altında kalan 10 coin yeni pozisyona kapalı kaldı. İlk yanlış LOCKDOWN sonucu, yapısal düşük hacmin global stres sayılmasından kaynaklandığı için düzeltildi.

Forward üretim kapısı için asgari koşullar: en az 30 sonuçlanmış multifactor tahmini, en az %60 hit rate, %95 Wilson alt sınırı en az %50, pozitif ortalama directional return ve 30 dakikadan yeni risk snapshot'ıdır. Mevcut gerçek sonuçlar settled=5, hit=%20, Wilson alt=%3,6 ve directional return=-%0,013 olduğu için SHADOW_ONLY kalır; ürün alarmı üretemez.

Bu limitler getiri tahmini veya işlem tavsiyesi değildir. QVOL emir defteri derinliği değil, 24 saatlik spot hacim vekilidir. Risk kapısı onayı da işlem emri değil, yalnızca alarm gösterme iznidir.

---

## 5F. DECISION ENGINE - v1.1.0 TAMAMLANDI (SHADOW MODE)

İlk çalışan sürüm açıklanabilir kanıt olgunluğu, nihai risk vetosu ve ürün/gölge ayrımıyla gerçek veride doğrulandı.

Tamamlananlar:

- BTC, ETH ve 11 öncelikli coin için kapalı 1h mumlara dayalı teknik bağlam
- Market, teknik, Forward, Social, On-chain, Macro, Whale ve Risk kanıtlarının olgunluk durumunu tek açıklanabilir kayıtta gösterme
- PROVISIONAL, WARMING_UP, NO_DATA, MONITOR_ONLY, UNVERIFIED ve SHADOW_ONLY kanıtların puanla güçlü ürün kararına çevrilmesini engelleme
- Risk Manager BLOCK kararını telafi edilemeyen nihai veto olarak uygulama
- Araştırma amaçlı gölge AL/SAT adayı ile kullanıcıya çıkabilecek ürün kararını kesin biçimde ayırma
- Decision Engine politikası doğrulanana kadar tüm ürün kararlarını BEKLE tutma
- decision_engine_snapshots ve decision_shadow_predictions kalıcı kayıtları
- Aynı coin için bir açık 1h tahmin sonuçlanmadan yenisini açmayan overlap koruması
- 1h sonuçları AL için getiri, SAT için ters getiri üzerinden otomatik puanlama
- 15 dakikalık cadence ve canlı ürün akışında Risk Manager sonrasına entegrasyon

İlk canlı çalışmada 13 coin değerlendirildi. BTC için SAT_ADAYI, WLD için AL_ADAYI oluştu; 10 düşük likiditeli coin Risk Manager tarafından veto edildi ve 13/13 ürün kararı BEKLE kaldı. Gölge doğrulamada iki çakışmasız tahmin açıldı, kaynak hatası oluşmadı. Ardından 10/10 ana ürün döngüsü başarıyla tamamlandı ve Decision Engine cadence içinde güvenli biçimde SKIPPED oldu.

Gölge adayları alarm, işlem talimatı veya üretim kararı değildir. Decision Engine politikası yalnızca önceden belirlenmiş yeterli gerçek forward kanıt ve ayrı üretim kararıyla SHADOW modundan çıkarılabilir; sistem kendini otomatik açmaz.

---

## 5G. REPORT GENERATOR - v1.2.0 TAMAMLANDI

İlk çalışan sürüm, mevcut Decision Engine ve Risk sözleşmelerini yeniden yorumlamadan fail-closed yatırım komitesi brifingi üretir.

Tamamlananlar:

- Son Decision Engine run, Risk Manager policy/asset snapshot, Forward scoreboard ve Decision Shadow performansını tek günlük raporda birleştirme
- Coin bazında 24h bağlam, teknik yön, gölge araştırma adayı, ürün kararı, risk izni ve pozisyon tavanını ayrı sütunlarda gösterme
- SHADOW modunda bozuk bir product AL kaydı bulunsa dahi rapor seviyesinde BEKLE'ye kapatma
- Risk BLOCK bulunan coini alım fırsatı olarak göstermeme
- En güçlü fırsatı yalnız gerçek PRODUCTION + product AL + Risk ALLOW birlikteyse seçme
- En riskli coini coin bazlı risk skoru ve likidite kanıtından gösterme
- Risk veya Decision snapshot 30 dakikadan eski/eksikse DATA_BLOCKED rapor üretme
- PROVISIONAL, WARMING_UP, NO_DATA, MONITOR_ONLY, UNVERIFIED ve SHADOW_ONLY kanıtları olgunluk tablosunda açıkça etiketleme
- committee_report_snapshots tablosunda günlük idempotent rapor kaydı
- reports/daily_investment_committee_report.md çıktısı
- Europe/Istanbul 11:00 günlük tek-çalışma cadence'i
- Mevcut 5 dakikalık LaunchAgent akışına hata izolasyonlu entegrasyon

İlk canlı rapor SHADOW_ONLY durumunda üretildi: 13 coin, 10 risk bloğu, ürün aksiyonu BEKLE, en güçlü fırsat YOK ve en riskli coin BIOUSDT. Gölge BTC SAT_ADAYI ile WLD AL_ADAYI ürün fırsatı olarak sunulmadı.

Ardından 11/11 ana ürün döngüsü başarıyla tamamlandı. İstanbul saati 00:51 iken Report Generator 11:00 slotunu tüketmeden WAITING durumunda kaldı. İlk doğal 11:00 üretimi LaunchAgent tarafından otomatik yapılacaktır ve operasyonel gözlem olarak izlenecektir.

---

## 5H. DECISION ENGINE TARİHSEL REPLAY - FİYAT/TEKNİK ARAMA DURDURULDU

Decision Engine'in mevcut fiyat/teknik/risk gölge kuralı 13 coin ve yaklaşık
400 günlük kapalı 1h mum üzerinde point-in-time olarak yeniden oynatıldı.

Doğrulanan veri ve simülasyon disiplini:

- 13/13 hazır sembol, 127.322 yeni tarihsel mum, kaynak hatası 0
- Gelecek mumların geçmiş karara sızmadığını doğrulayan deterministik testler
- Karardan sonraki ilk mum açılışında giriş
- 1h, 4h ve 24h sonuçları
- 10 baz puan gidiş-dönüş maliyet
- Kronolojik %60 LEARN / %20 VALIDATION / %20 EXAM
- Binance gerçek quote-volume verisi
- Risk Manager'ın o andaki verilerle yeniden uygulanması

Mevcut kural 9.576 karar saatinde 23.881 aday üretti. EXAM sonuçları maliyet
sonrası 1h -%0,142, 4h -%0,193 ve 24h -%0,160 oldu. Bu yoğunluk erken alarm
yerine sürekli piyasa durumunun tekrar tekrar sinyal sayıldığını gösterdi.

Ardından FOLLOW/CONTRARIAN, teknik güç, 24h momentum ve normal piyasa
filtrelerinden oluşan önceden sabit 36 challenger her ufukta denendi. Güven
sınırları korelasyon şişmesini azaltmak için 7 günlük zaman bloklarıyla
hesaplandı. Hiçbir challenger LEARN aşamasında pozitif alt güven sınırı
üretemedi:

- 1h: NO_LEARN_EDGE
- 4h: NO_LEARN_EDGE
- 24h: NO_LEARN_EDGE

Son ve önceden kayıtlı fiyat-teknik hipotezi de tamamlandı. Seyrek skor,
MACD/EMA sıfır geçişi ve kapalı-fiyat 24h kırılım olayları; hacim ve NORMAL
piyasa korumalarının sabit varyantlarıyla 36 config/horizon olarak test edildi.
Coin başına 24h cooldown ve 10 baz puan maliyet uygulandı. 9.576 karar noktası,
68.616 risk-izinli bağlam, 55.872 risk vetosu ve sıfır point-in-time boşlukla
her config için 146-2.107 olay oluştu. Sonuç üç ufukta da aynıydı:

- 1h: NO_LEARN_EDGE
- 4h: NO_LEARN_EDGE
- 24h: NO_LEARN_EDGE

Karar: `STOP_PRICE_TECHNICAL_RULE_SEARCH`. Teknik göstergeler risk ve açıklama
bağlamı olarak kalabilir; doğrulanmış yön tahmini gibi kullanılamaz. Aynı fiyat
verisinde yeni eşik, indikatör veya kombinasyon araması yapılmayacak. Canlı
politika açılmadı ve ürün SHADOW_ONLY kaldı.

---

## 5I. BAĞIMSIZ HABER KATALİZÖR REPLAY - KALİTE KAPILI

Fiyat/teknik arama kapatıldıktan sonra yalnız bir bağımsız katalizör hipotezi
için `analysis.news_catalyst_replay_lab` hazırlandı. Bu laboratuvar eski
"güçlü NEWS BASE" sonucunu kanıt kabul etmez; GDELT arşivini baştan denetler.

Sözleşme:

- Başlıkta açık ve precision-first coin adı/ticker doğrulaması
- LINK/BIO/ADA/SOL/SUI/UMA gibi belirsiz kısa kelimeler için yalnız açık proje
  adı veya büyük harfli ticker kabulü
- Canonical URL ve 72 saatlik aynı-başlık tekilleştirmesi
- Gün-noon fallback zaman damgalarının replay dışında bırakılması
- Olay zamanından sonra 60 dakika güvenlik gecikmesi ve sonraki 1h mum açılışı
- Coin başına 24h cooldown ve 10 baz puan gidiş-dönüş maliyet
- Teknik yön filtresi olmadan 16 sabit FOLLOW/CONTRARIAN haber kuralı
- 7 günlük blok güven sınırlarıyla LEARN -> VALIDATION -> kapalı EXAM
- Ham veri, zaman, başlık, tekrar, kaynak, fiyat eşleşmesi, örneklem ve coin
  kapsamı için önceden sabit kalite kapıları

Kod ve sentetik leakage/cost/cooldown testleri başarılıdır. Gerçek Mac veri
tabanında kalite kapısı çalıştırıldı ve simülasyonu doğru biçimde durdurdu:

- Ham haber: 5.571
- Geçerli zaman formatı: %100
- Kesin intraday zaman: %0
- Kullanılabilir başlık: %99,8
- Sonuç: DATA_QUALITY_BLOCKED

5.571 kaydın tamamı gerçek haber saati yerine arşiv gününün 12:00 fallback
zamanını taşıyor. Bu nedenle olay sırası bilinemez; haber öncesi hareketi haber
sonrası gibi sayma riski giderilemez. Yönlü makale, tekrar, span, kaynak ve
fiyat-eşleşme kapılarındaki sıfırlar bu kök engelin aşağı akış sonucudur; ayrı
veri kayıpları gibi yorumlanmaz.

Karar: Tarihsel haber saatleri uydurulmayacak, EXAM çalıştırılmayacak ve bu
arşivle event-time replay kapatılacaktır. Haber katalizörü yalnız gerçek
toplama/yayın zamanını taşıyan forward gözlemlerle doğrulanacaktır. Canlı
politika değişmedi ve ürün SHADOW_ONLY kaldı.

---

## 5J. PROBABILISTIC FORECAST CORE - KONTROLLÜ ARAŞTIRMA FAZI

Ürün hedefi netleştirildi: sistem yalnız zayıf kanıtta BEKLE dememeli; kanıt
kazandığında 1h, 4h ve 24h için medyan hedef fiyat, %80 tahmin aralığı,
maliyet-üstü UP/DOWN/NEUTRAL olasılığı ve AL/SAT/BEKLE üretebilmelidir.

Bu hedef için tek ve sınırlı mimari değişiklik uygulanmıştır:

- `decision_engine.forecast_contract`: fiyat, ufuk, olasılık, maliyet,
  referans fiyat, next-open giriş, risk vetosu ve model artifact alanlarını
  fail-closed doğrulayan sabit sözleşme
- `analysis.forecast_core_lab`: 13 coin, exact kapalı 1h OHLCV ve tek sabit
  `POOLED_RIDGE_DISTRIBUTION_V1_ALPHA_0_05_FIXED` challenger; mean-loss + L2
  objective ve alpha=0,05 gerçek veri sonucu görülmeden donduruldu
- 1h/4h/24h için ayrı FIT -> CALIBRATION -> VALIDATION -> yalnız geçerse
  OBSERVED_DIAGNOSTIC akışı
- No-change fiyat/aralık ve geçmiş base-rate olasılık benchmark'ları
- Brier, log-loss, classwise/action ECE, normalize MAE, dağılım kaybı,
  interval coverage, coin/rejim kapsamı ve haftalık kümeli bootstrap kapıları;
  yüksek-güven hit ve calibration bias da haftalık bloklarla sınanır
- Güven olayı ile hit-rate olayının aynı tanımı: next-open'dan horizon close'a
  10 bps maliyet + 5 bps minimum hareket eşiğini aşma
- SAT semantiği Spot yatırımcı için SELL/REDUCE/AVOID'dır; short pozisyon veya
  hayalî portföy kârı olarak sayılmaz
- Forecast tüm geçerli fiyat bağlamlarında ölçülür; Risk Manager yalnız
  risk-adjusted eylemi BEKLE'ye veto eder

Kod derleme, sözleşme, NaN/sonsuz değer, horizon süresi, fiyat aralığı,
future-feature mutation, next-open ayrımı ve artifact hash testlerinden geçti.
Gerçek Mac veri tabanı VALIDATION sonucu üç ufukta da REJECT oldu:

- 1h: n=24.947, Brier skill +%1,2, MAE skill -%0,5, aksiyon 0.
- 4h: n=6.227, Brier skill +%0,1, MAE skill -%0,7, aksiyon 0.
- 24h: n=1.027, Brier skill -%1,2, MAE skill -%2,5, class ECE %10,7;
  17 aksiyon, %64,7 hit ve pozitif utility; fakat yetersiz kapsam/hafta/coin/
  rejim desteği ve baseline başarısızlığı.

OBSERVED_DIAGNOSTIC hiçbir ufukta açılmadı. Canlı Decision Engine, cycle,
rapor ve alarm akışı değişmedi. 24h tail sonucu yalnız yeni bağımsız veriyle
sınanacak bir hipotezdir; başarı kanıtı değildir.

Bu laboratuvarın aynı tarihsel dönemi daha önce görülmüştür. Bu nedenle tüm
kapılar geçse bile sonuç yalnız DEVELOPMENT_ONLY'dır; kullanıcıya güven oranı
ve ürün önerisi çıkarmak için yeni sealed future dönem ve gerçek forward
kalibrasyon zorunludur. Tek challenger VALIDATION'da kalırsa yeni
fiyat/indikatör araması açılmaz.

---

## 5K. DERIVATIVES STATE V2 - TEK KONTROLLÜ SONRAKİ KAMPANYA

Fiyat-only V1 reddedildi ve bu model ailesi kapatıldı. Forecast Contract ve
canlı ürün mimarisi korunarak spot fiyattan bağımsız vadeli piyasa konumlanma
kanıtı için V2 araştırma paketi kodlandı:

- Binance USD-M futures OHLCV + taker-buy hacmi
- gerçekleşmiş funding
- open-interest ve positioning metrikleri
- perpetual premium/basis

İlk kampanya yalnız bu dondurulmuş veri paketini kullanır. Tarihsel order-book,
liquidation veya yaklaşık zamanlı haber sonradan karıştırılmaz. Hedef ufuk
sırası 4h PRIMARY, 24h SECONDARY ve 1h EXPLORATORY'dir. Ham model edge'i Risk
Manager'dan önce; ürün uygulanabilirliği Risk vetosundan sonra ayrı ölçülür.

Hazır uygulama:

- Checksum doğrulamalı Binance USD-M tarihsel collector ve SQLite şeması
- 400 günlük deney + 430 günlük warmup/funding toplama sözleşmesi
- Funding'i geçmiş interval ile 8h eşdeğerine normalize eden point-in-time feature
- Kapanmış futures mumundan türetilen taker imbalance; belirsiz Vision taker
  timestamp alanı model dışında
- 26 dondurulmuş türev özelliği ve tek sabit histogram additive-stump model
- En az 10 coin + BTC/ETH, %98 complete-row readiness ve imputasyonsuz sınırlı
  context-gap politikası
- CAL-A/CAL-B kronolojik kalibrasyon ayrımı
- 4h geçmeden 24h observed diagnostic açmayan hiyerarşik endpoint politikası
- JSON round-trip doğrulanabilir sealed artifact ve horizon-feasibility testleri

Derleme, collector/V1/V2 self-testleri, 26-feature point-in-time testi ve 400
günlük 10-coin readiness integration testi geçti. Henüz gerçek Mac V2 sonucu
yoktur; başarılı veya başarısız olduğuna dair iddia oluşturulmamıştır.

V1 reddi sonrası validator tanısı da sıkılaştırılmıştır: forecast haftaları ve
aksiyon haftaları ayrı, sekiz bloktan az güven aralığı N/A, bootstrap event-
weighted whole-week, constant-direction baseline CALIBRATION'da dondurulmuş ve
UP/DOWN/NEUTRAL ECE birlikte ölçülür.

Amaç yalnız başarısızlığı doğru ölçmek değildir. Dürüst ölçüm değişmez güvenlik
kısıtıdır; ürün başarısı baselineları ve maliyetleri geçen, yararlı sıklıkta,
kalibre, çoklu coin/rejim desteği olan ve fresh forward dönemde tekrarlanan
tahminlerle tanımlanır. “Kanıt yok/BEKLE” nihai başarı sayılmaz.

## 6. COIN KAPSAMI

Takip listesi:

ARKM, FIDA, WLD, TIA, ETHFI, BIO, LQTY, ILV, SLP, UMA, IO

News Analyzer bu coinler için açık alias desteği içerir. Whale Tracker ilk token kapsamı Ethereum ana ağındaki doğrulanmış ARKM, BIO, ETHFI, ILV, LQTY, SLP ve UMA kontratlarıdır; diğer zincirlerdeki takip-listesi varlıkları destekleniyor gibi gösterilmez. Risk Manager BTC, ETH ve 11 coinlik öncelikli listenin Binance Spot USDT verisini kapsar; kapsam dışı dinamik coin, risk profili oluşmadan üretim alarmına giremez.

Ek olarak Binance Spot USDT ticker keşfi dinamik hale getirildi. Hedef, sistemin yalnızca önceden belirlenmiş coinlere bağlı kalmaması; yarın yeni bir coin piyasaya çıktığında ve ilgili fırsat/haber oluştuğunda da tespit edebilmesidir.

---

## 7. RAPOR VE KRİTİK ALARM DURUMU

ChatGPT görev sistemi ana kullanıcı bildirim kanalıdır.

Aktif görevler:

- Günlük Kripto Brifingi: her gün 11:00
- Kripto Kritik Gelişme Takibi: saatlik koşullu kontrol

Kritik alarm prensibi:

- Önemli gelişme yoksa bildirim gönderilmez.
- Büyük token unlock, güvenlik olayı, listing/delist, olağanüstü balina/on-chain hareketi, piyasa yapısını değiştirecek haber veya güçlü AGCA/Forward Shadow sinyali varsa kullanıcı uyarılır.
- Ham büyük transfer veya çıktı tek başına alarm değildir; balina alarmı için yön ve borsa bağlamı doğrulanmalıdır.
- Forward Shadow üründe SHADOW_ONLY'dır; Signal Risk Gate izin vermedikçe yönlü ürün alarmı gönderilmez.
- Decision Engine gölge AL/SAT adayları ürün alarmı veya işlem kararı değildir; ürün kararı SHADOW politikada BEKLE kalır.
- Günlük komite raporu İstanbul saatiyle 11:00'den sonra günde bir kez üretilir; SHADOW adayları fırsat olarak sunmaz.
- Saatlik görev gerçek zamanlı saniyelik izleme değildir; kritik gelişme bir sonraki saatlik kontrolde bildirilir.

Telegram katmanı yardımcı/opsiyonel altyapıdır; ana kullanıcı bildirim kanalı olarak zorunlu değildir.

---

## 8. SONRAKİ SOMUT ÇALIŞMA

Yeni oturumda sıfırdan mimari konuşulmayacak.

Öncelik sırası:

1. Checksum doğrulamalı `data.derivatives_market_data` ile Mac'te tarihsel
   USD-M backfill'i tamamla; yarıda kesilirse manifest üzerinden idempotent sürdür.
2. Readiness en az 10 coin ve BTC/ETH için geçerse
   `analysis.forecast_core_v2_lab` gerçek Mac verisinde yalnız bir kez çalıştır.
3. 4h PRIMARY hiyerarşisini koru; 24h SECONDARY ve 1h EXPLORATORY sonuçlarını
   bağımsız başarı gibi sunma. Sonuca göre threshold/model/paket değiştirme.
4. Geçiş varsa yalnız DEVELOPMENT_ONLY artifact dondur ve fresh forward fazını
   ayrıca tasarla; reddedilirse V2 model ailesini kapat.
5. Fiyat-teknik eşik aramasını ve gün-saat belirsiz tarihsel haber replay'ini yeniden açma.
6. Haber katalizörünü yalnız kesin published/collected timestamp taşıyan gerçek forward örneklerle doğrula.
7. Forward Shadow V2 örneklemi büyüdükçe gerçek performansı izle; mevcut küçük örneklemde Champion onayı verme.
8. Social, On-chain, Macro, Whale, Risk ve Decision gölge verilerini kendi olgunluk sınırlarıyla biriktir.
9. İlk doğal 11:00 Report Generator çalışmasını doğrula ve yalnız yeterli kanıtı raporla.

---

## 9. ÇALIŞMA PRENSİPLERİ

1. Ürün odaklı geliştirme yapılır.
2. Bir adım tamamlanmadan sonraki adıma geçilmez.
3. Kullanıcı teknik işlemlerde tek tek yönlendirilir.
4. Her modül gerçek verilerle test edilir.
5. Leakage kesinlikle engellenir.
6. Challenge/Exam verisi kendi seçiminden önce kullanılamaz.
7. Küçük örneklem ve overfitting'e karşı temkinli olunur.
8. Başarı düşükse parametre/ağırlıklar LEARN tarafında optimize edilebilir; ancak unseen validation başarısı olmadan üretime alınmaz.
9. Aynı tarihsel veri sonsuza kadar kurcalanmaz; uygun noktada model dondurulur ve forward test başlatılır.
10. Her teknik karar şu soruyla değerlendirilir: "Bu geliştirme bizi daha başarılı kripto tahminleri yapan çalışan bir ürüne gerçekten yaklaştırıyor mu?"
11. Tüm çalışmalar birbirleriyle uyumlu olmalı; eski kararlar ve hedefler unutulmamalıdır.
12. Günlük rapor ve kritik erken alarm sistemleri aksatılmamalıdır.

---

## 10. PROJE HAFIZA DOSYALARI

Resmi hafıza dosyaları:

- docs/PROJECT_STATUS.md
- docs/TASKS.md
- docs/CHANGELOG.md
- docs/SESSION_LOG.md
- docs/DECISIONS.md
- docs/SESSION_HANDOFF_2026-08-11.md

Yeni oturumlarda önce bu dosyalar ve mevcut GitHub kodu kontrol edilir; kullanıcıdan proje geçmişini yeniden anlatması istenmez.

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

Proje ürün sürümü: **v1.0.0**

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

Ana ürün yol haritasındaki sıradaki yeni modül: **v1.1.0 Decision Engine**.

Forward Shadow V2, Social Sentiment, On-chain, Macro, Whale Tracker ve Risk Manager süreçleri ana yol haritasını bloke etmeden canlı ürün döngüsünde paralel çalışacaktır.

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

Önemli sonuçlar:

- Güçlü 1h NEWS BASE policy: negative direction + confidence >= 70.
- Tarihsel frozen EXAM temel başarı seviyesi yaklaşık %77.6 hit rate.
- Weighted REGIME_AWARE multifactor aday model bazı frozen karşılaştırmalarda base'i geçti.
- Multifactor robustness statüsü: ROBUST_B.
- Aynı tarihsel veriyi daha fazla optimize ederek yapay başarı üretmemek için model donduruldu.
- Adaptive dual-champion seçici şu haliyle reddedildi; sabit multifactor daha güçlü kaldı.

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
- Akış: NEWS COLLECT -> NEWS ANALYZE -> FORWARD SHADOW -> RISK MANAGER -> PRODUCT ALERT BRIDGE -> SOCIAL SENTIMENT -> ON-CHAIN -> MACRO -> WHALE TRACKER
- Social Sentiment cadence: 15 dakika; diğer 5 dakikalık cycle'larda güvenli biçimde atlanır
- On-chain cadence: 15 dakika; diğer 5 dakikalık cycle'larda güvenli biçimde atlanır
- Macro cadence: 6 saat; ara cycle'larda güvenli biçimde atlanır
- Whale Tracker cadence: 5 dakika; BTC, native ETH ve Ethereum ERC-20 kaynakları birbirinden izole çalışır
- Risk Manager cadence: 15 dakika; canlı ürün akışında alarm köprüsünden önce çalışır
- Tam canlı ürün akışı 9/9 adımla gerçek veride doğrulandı
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
- Saatlik görev gerçek zamanlı saniyelik izleme değildir; kritik gelişme bir sonraki saatlik kontrolde bildirilir.

Telegram katmanı yardımcı/opsiyonel altyapıdır; ana kullanıcı bildirim kanalı olarak zorunlu değildir.

---

## 8. SONRAKİ SOMUT ÇALIŞMA

Yeni oturumda sıfırdan mimari konuşulmayacak.

Öncelik sırası:

1. Forward Shadow V2 örneklemi büyüdükçe NEWS BASE vs MULTIFACTOR gerçek performansını izlemeye devam et; mevcut küçük örneklemde Champion onayı verme.
2. Social Sentiment veri kapsamını ve takip listesi coinlerinde oluşan örneklemi paralel izle; düşük güvenli sonuçları alarm/işlem sinyali sayma.
3. Günlük 11:00 yatırım komitesi raporunda yalnızca yeterli güvene sahip doğrulanmış sinyalleri kullan.
4. Kritik alarm sisteminin yalnızca anlamlı olaylarda bildirim verdiğini izlemeye devam et.
5. On-chain 24h geçmişini ve veri kalitesini paralel izlemeye devam et; WARMING_UP/NO_DATA metriklerini karara dahil etme.
6. Macro rejim snapshot'larını paralel biriktir; PROVISIONAL etiketi alarm veya işlem sinyali olarak kullanma.
7. Whale Tracker kanıtlarını paralel biriktir; UNVERIFIED hareketleri alarm veya işlem sinyali sayma.
8. Risk Manager snapshot ve kapı kararlarını paralel biriktir; limitleri sinyal gücü gibi yorumlama.
9. Ana ürün yol haritasında **v1.1.0 Decision Engine** geliştirmesine geç.

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

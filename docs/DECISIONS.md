# DECISIONS

## Karar 001 - Proje Hafızası

Her geliştirme oturumu sonunda aşağıdaki dosyalar güncellenecek:

- PROJECT_STATUS.md
- TASKS.md
- CHANGELOG.md
- SESSION_LOG.md
- DECISIONS.md (gerekiyorsa)
- LAST_SESSION_HANDOFF.md

Bu dosyalar projenin resmi hafızasıdır.

---

## Karar 002 - Ürün Odağı

Amaç sürekli yeni mimariler tasarlamak değil, çalışan ve giderek daha başarılı tahminler yapan gerçek bir ürün çıkarmaktır.

Araştırma katmanı yalnızca ürün kararlarını güçlendirmek için vardır; ürün yol haritasının yerine geçmez ve ürünü gereksiz yere geciktiremez.

---

## Karar 003 - Dinamik Coin Evreni

Takip listesi özel izleme önceliğidir ancak sistem yalnızca bu coinlerle sınırlı olmayacaktır.

Binance Spot USDT evrenindeki yeni coin/fırsatlar da mümkün olduğunca dinamik olarak tespit edilmelidir. Yarın yeni bir coin piyasaya çıktığında ilgili haber/fırsat oluşursa sistem bunu yakalayabilmelidir.

---

## Karar 004 - Leakage ve Backtest Disiplini

Her karar anında yalnızca o ana kadar sonucu tamamlanmış veriler öğrenme için kullanılabilir.

Challenge ve EXAM sonuçları kendi seçimlerinden önce kullanılamaz.

Aynı tarihsel veri başarı oranını yapay biçimde yükseltmek için sonsuza kadar optimize edilmez. Yeterli araştırma sonrası aday model dondurulur ve gerçek forward validation'a geçirilir.

---

## Karar 005 - Forward Champion Adayı

Tarihsel araştırma sonunda 1h NEWS BASE güvenli benchmark olarak korunacaktır.

Weighted REGIME_AWARE multifactor model provisional Champion adayıdır.

Forward model:

- REGIME_AWARE_FORWARD_V2
- horizon: 1h
- frozen cutoff: 0.249
- forward başladıktan sonra ağırlık/cutoff sonuçlara göre değiştirilmez.

İlk V1 pre-start seed kayıtları geçersizdir ve forward performansına dahil edilmez.

---

## Karar 006 - Bildirim Kanalı

Ana kullanıcı bildirim kanalı ChatGPT görev/bildirim sistemidir.

Aktif ürün bildirimleri:

- Günlük 11:00 yatırım komitesi brifingi
- Saatlik koşullu kritik gelişme takibi

Kritik gelişme yoksa bildirim gönderilmez. Anlamlı gelişme varsa bir sonraki saatlik kontrolde alarm üretilir.

Telegram yardımcı/opsiyonel altyapıdır; ana kanal olarak zorunlu değildir.

---

## Karar 007 - Oturum Devri

Yeni oturumlarda kullanıcıdan projenin hedefini, geçmişini ve önceki kararları sıfırdan anlatması istenmeyecektir.

Önce resmi hafıza dosyaları ve mevcut GitHub kodu kontrol edilerek kaldığı yerden devam edilecektir.

---

## Karar 008 - Social Sentiment Güven ve Üretim Kullanımı

Social Sentiment Agent v0.6.0 canlı ürün döngüsünde 15 dakikalık cadence ile veri toplayacaktır.

Sosyal sinyal güveni yalnızca paylaşım sayısına göre hesaplanamaz. Güven puanı birlikte şu üç kanıta dayanacaktır:

- yeterli örneklem,
- yönlü paylaşım oranı,
- pozitif/negatif fikir birliği.

Nötr veya çelişkili yüksek hacim yüksek güven sayılmaz. Düşük güvenli sosyal sonuçlar alarm, işlem sinyali veya Decision Engine girdisi olarak kullanılmayacaktır. Veri birikimi ve forward doğrulama ana ürün yol haritasını bloke etmeden paralelde sürdürülecektir.

---

## Karar 009 - On-chain Veri Olgunluğu ve Kullanım Sınırı

On-chain Agent v0.7.0 canlı ürün döngüsünde 15 dakikalık cadence ile çalışacaktır.

Yaklaşık 24 saatlik değişim için kabul edilen karşılaştırma aralığı 18-30 saattir. Bu aralığa ulaşmayan metrik WARMING_UP; sıfır veya karşılaştırılamayan veri NO_DATA sayılır.

WARMING_UP ve NO_DATA metrikleri alarm, işlem önerisi veya Decision Engine girdisi olamaz. RISING/FALLING/STABLE sınıfları da forward doğrulama olmadan tek başına alım-satım kararı değildir.

v0.7.0 ilk kapsamı zincir seviyesi TVL ve Bitcoin ağ/mempool sağlığıdır. Token bazlı transfer, borsa giriş/çıkış ve büyük cüzdan hareketleri ileri on-chain genişletmesi ile Whale Tracker modülünde ele alınacaktır.

---

## Karar 010 - Macro Rejimi ve Üretim Kullanım Sınırı

Macro Agent v0.8.0 canlı ürün döngüsünde 6 saatlik cadence ile çalışacaktır.

İlk makro bileşenleri:

- Fed Funds 90 günlük değişim,
- ABD 10 yıllık faiz 30 günlük değişim,
- geniş dolar endeksi 30 günlük değişim,
- VIX seviyesi,
- CPI yıllık eğilim değişimi.

Her bileşen risk-off için +1, risk-on için -1, eşik içindeyse 0 üretir. Toplam skor geçici RISK_ON / MIXED / RISK_OFF bağlamı sağlar.

Bu rejim PROVISIONAL'dır. Tarihsel ve forward doğrulama tamamlanmadan alarm, işlem sinyali veya Decision Engine girdisi olamaz. Kaynak gözlem tarihleri farklı olabilir ve FRED serileri revize edilebilir; sonuçlar bu veri gerçekliğiyle birlikte değerlendirilir.


---

## Karar 011 - Whale Kanıtı ve Alarm Sınırı

Whale Tracker v0.9.0 canlı ürün döngüsünde 5 dakikalık cadence ile çalışacaktır.

İlk güvenilir kapsam:

- doğrulanmış Bitcoin bloklarında büyük tekil çıktılar,
- Ethereum safe bloklarında büyük native transferler,
- doğrulanmış Ethereum ana ağ kontratlarında takip-listesi ERC-20 Transfer olaylarıdır.

Ham zincir olayı yalnızca kanıttır. Brüt çıktı/transfer net akış değildir; change, self-transfer, mint, burn veya sözleşme içi hareket olabilir. Cüzdan sahipliği, borsa adresi ve akış yönü doğrulanmadıkça direction=UNKNOWN, exchange_flow=UNVERIFIED ve sonuç MONITOR_ONLY kalacaktır.

UNVERIFIED whale olayı alarm, işlem sinyali veya Decision Engine girdisi olamaz. Yalnızca güvenilir adres kaynağıyla doğrulanmış borsa giriş/çıkış yönü ileride alarm adayı olabilir. Kapsanmayan zincir veya token destekleniyor gibi gösterilmeyecektir.

---

## Karar 012 - Risk Manager Veto Yetkisi ve Forward Üretim Kapısı

Risk Manager v1.0.0 canlı ürün döngüsünde 15 dakikalık cadence ile, ürün alarm köprüsünden önce çalışacaktır.

Risk Manager sinyal üretmez. Toplam/altcoin maruziyeti, tek pozisyon, işlem başı risk, günlük kayıp, yeni pozisyon ve coin bazlı risk tavanlarını tanımlar. Eksik BTC verisi veya <%80 kapsam DATA_BLOCKED olur; eksik veri hiçbir koşulda daha gevşek limite yol açmaz.

Yapısal düşük hacim coin bazlı işlem riskidir ve yalnızca ilgili coini kapatır. Global piyasa stresi BTC/ETH referansları ile dinamik getiri, aralık, volatilite ve düşüş kanıtından hesaplanır. Düşük hacimli coin sayısı tek başına global LOCKDOWN üretemez. QVOL emir defteri derinliği değil, 24 saatlik spot hacim vekilidir.

Forward multifactor ürün alarmının izin kazanması için birlikte şu asgari koşullar gerekir:

- en az 30 sonuçlanmış gerçek forward multifactor tahmini,
- hit rate en az %60,
- %95 Wilson alt sınırı en az %50,
- ortalama directional return pozitif,
- risk policy snapshot yaşı en fazla 30 dakika,
- global yeni pozisyon izni,
- coin için pozitif azami pozisyon tavanı.

Koşullardan biri eksikse Signal Risk Gate kararı BLOCK'tur. Risk Manager BLOCK kararı gelecek Decision Engine için nihai vetodur; diğer puanlar bu vetoyu telafi edemez. Gate eşikleri frozen model ağırlıklarını veya cutoff'u değiştirmez.

---

## Karar 013 - Decision Engine Gölge Politikası ve Çakışmasız Doğrulama

Decision Engine v1.1.0 ilk olarak SHADOW modunda çalışacaktır. Araştırma amaçlı gölge AL/SAT adayı ile kullanıcıya gösterilebilecek ürün kararı ayrı alanlarda tutulur. Decision Engine politikası ayrı forward doğrulama kazanana kadar product_decision daima BEKLE'dir.

Market ve kapalı 1h teknik verisi araştırma bağlamı oluşturabilir; ancak doğrulanmamış bileşenler puan toplanarak üretim kararına çevrilemez. PROVISIONAL, WARMING_UP, NO_DATA, MONITOR_ONLY, UNVERIFIED ve SHADOW_ONLY durumları açıkça görünür kalır ve production_usable=false olur.

Risk Manager BLOCK kararı nihai vetodur. Risk BLOCK bulunan coin gölge AL/SAT adayı da üretemez. Risk izni, tek başına alım/satım kararı değildir; yalnızca diğer politikaların aşamayacağı tavanı tanımlar.

Gölge performansı bir saatlik forward pencerede ölçülür. Aynı coin için bir açık tahmin sonuçlanmadan yenisi kaydedilmez; böylece 15 dakikalık tekrarlar bağımsız tahmin gibi sayılarak örneklem şişirilmez. AL için gerçek getiri, SAT için ters getiri directional return sayılır. Sonuçlar politikayı otomatik değiştirmez veya üretime açmaz.

Decision Engine 15 dakikalık cadence ile Risk Manager sonrasında ve ürün alarm köprüsünden önce çalışır. Gölge adayları alarm köprüsüne iletilmez.

---

## Karar 014 - Günlük Komite Raporu Fail-Closed Politikası

Report Generator v1.2.0 yeni sinyal üretmez; kalıcı Decision Engine, Risk Manager, Forward Shadow ve Decision Shadow sözleşmelerini raporlar. Gölge araştırma adayı ile product_decision farklı sütunlarda tutulur.

Raporun en güçlü fırsat seçebilmesi için Decision Engine modu PRODUCTION, product_decision AL, Risk kararı ALLOW ve pozisyon tavanı pozitif olmalıdır. SHADOW veya DATA_BLOCKED raporda etkili ürün kararı BEKLE'dir. Veritabanında tutarsız biçimde SHADOW + product AL bulunsa bile rapor AL göstermez.

Risk BLOCK bulunan coin fırsat olarak sunulamaz. Risk policy veya Decision snapshot 30 dakikadan eski/eksikse rapor DATA_BLOCKED olur. PROVISIONAL, WARMING_UP, NO_DATA, MONITOR_ONLY, UNVERIFIED ve SHADOW_ONLY kanıtlar olgunluk durumlarıyla görünür kalır; birleşik puanla yükseltilmez.

Planlı rapor Europe/Istanbul saat diliminde 11:00'den sonra günde bir kez üretilir. Makine 11:00'de çalışmıyorsa aynı gün sonraki canlı cycle raporu üretir. Manuel modül testi planlı günlük slotu tüketmez. Başarısız üretim son başarılı zamanı ilerletmez ve sonraki cycle yeniden dener.

Ana ürün yol haritasının on modülü v1.2.0 ile tamamlanmıştır. Bundan sonraki geliştirmeler ölçülmüş ihtiyaç, operasyonel güvenilirlik ve gerçek forward kanıt tarafından yönlendirilecektir; sırf yeni mimari eklemek için yeni modül açılmayacaktır.

---

## Karar 015 - Tarihsel Replay Sonucu ve Fiyat-Durum Kuralının Reddi

Decision Engine'in mevcut teknik yön + 24h piyasa yönü + Risk Manager izni
birleşimi, geleceğe bilgi sızdırmayan tarihsel replay ile ölçülmüştür.

Replay sözleşmesi:

- 13 takip sembolü ve yaklaşık 400 günlük kapalı 1h mum
- yalnız karar anına kadar kapanmış veriler
- sonraki mum açılışında giriş
- 1h, 4h ve 24h sonuç
- 10 baz puan gidiş-dönüş maliyet
- kronolojik LEARN / VALIDATION / EXAM
- gerçek Binance quote volume ve point-in-time Risk Manager

Mevcut politika EXAM'da üç ufukta da negatif net getiri üretmiştir. Önceden
sabit 36 FOLLOW/CONTRARIAN challenger; teknik güç, momentum ve piyasa durumu
filtreleriyle, 7 günlük blok güven sınırları kullanılarak denenmiş ve üç
ufukta da NO_LEARN_EDGE sonucu vermiştir.

Bu nedenle:

- Mevcut fiyat-durum kuralı üretim adayı değildir.
- Eşikler ve canlı Decision Engine politikası değiştirilmez.
- Sonuç SHADOW_ONLY kalır.
- Aynı sürekli-durum kural ailesi üzerinde yeni kombinasyon araması yapılmaz.
- Fiyat-teknik tarafta yalnız bir önceden tanımlı seyrek geçiş/kırılım
  hipotezi daha test edilir.
- Bu hipotez de LEARN edge üretmezse fiyat-teknik optimizasyonu durdurulur ve
  sonraki araştırma bağımsız katalizör verisine yönelir.

Challenger ailesi tanımlanmadan önce mevcut politikanın toplu EXAM sonucu
görülmüştür. Bu nedenle challenger EXAM mekanik olarak seçimden saklansa da
tamamen bakir kanıt sayılmaz; herhangi bir tarihsel başarı yine fresh forward
doğrulama gerektirir.


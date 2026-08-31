# DECISIONS

## Karar 019 - Remote Runtime Store Geçiş Politikası

Canlı runtime verisinin GitHub üzerinden taşınmaması; SQLite'ın yerel
source-of-truth olarak korunup PostgreSQL/Supabase'e best-effort dual-write
yapılması kararlaştırıldı. Uzak servis hiçbir zaman yerel committen önce gelmez
ve uzak hata canlı cycle'ı durdurmaz. Gerçek bağlantı bilgileri yalnız ortam
değişkenlerinde tutulur. Remote store kurulması ürün politikasını veya SHADOW
kapılarını değiştirmez; uzaktan okuma/karar kullanımı ayrıca doğrulanacaktır.

---

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

## Karar 005 - Forward Champion Adayı (Karar 018 ile Tarihsel Temeli Geçersiz)

Tarihsel araştırma sonunda 1h NEWS BASE güvenli benchmark olarak korunacaktır.

Weighted REGIME_AWARE multifactor model provisional Champion adayıdır.

Forward model:

- REGIME_AWARE_FORWARD_V2
- horizon: 1h
- frozen cutoff: 0.249
- forward başladıktan sonra ağırlık/cutoff sonuçlara göre değiştirilmez.

İlk V1 pre-start seed kayıtları geçersizdir ve forward performansına dahil edilmez.

Karar 018 sonrası not: Bu adaylığın dayandığı GDELT geçmişinde kesin intraday
timestamp oranı %0 bulundu. NEWS BASE %77,6, ROBUST_B ve provisional Champion
etiketleri tarihsel edge kanıtı olarak geri çekilmiştir. Dondurulmuş V2 yalnız
gerçek forward gözlemlerde sıfırdan ölçülen SHADOW hipotezidir; eski sonuçlardan
üretim kredisi alamaz.

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

---

## Karar 016 - Seyrek Geçiş Sonucu ve Fiyat/Teknik Aramanın Durdurulması

Önceden kayıtlı son fiyat-teknik hipotezi; skor, MACD/EMA sıfır geçişi ve
kapalı-fiyat 24h kırılım olaylarını coin başına 24 saat cooldown ile sınadı.
Hacim ve NORMAL piyasa korumalarının sabit varyantları dahil 36 config/horizon,
10 baz puan maliyet ve 7 günlük blok güven sınırları kullanıldı.

9.576 karar noktasında 68.616 risk-izinli bağlam oluştu; 55.872 bağlam Risk
Manager tarafından veto edildi, point-in-time boşluk oluşmadı ve config başına
146-2.107 olay değerlendirildi. 1h, 4h ve 24h ufuklarının hiçbirinde bir config
LEARN aşamasında pozitif alt güven sınırı üretmedi: üç sonuç da NO_LEARN_EDGE.

Bu nedenle:

- Fiyat/teknik yön kuralı araması durdurulur.
- Aynı veri üzerinde yeni RSI/MACD/EMA eşiği veya kombinasyonu aranmaz.
- Teknik veri yalnız risk, piyasa bağlamı ve açıklanabilirlikte kullanılabilir.
- Canlı Decision Engine politikası değişmez ve ürün SHADOW_ONLY kalır.
- Sonraki araştırma, fiyat göstergelerinden bağımsız ve kendi zaman damgasına
  sahip katalizör kanıtıyla sınırlanır.

---

## Karar 017 - Haber Katalizörü İçin Fail-Closed Tarihsel Sözleşme

Eski tarihsel NEWS BASE sonucu; maliyet, aynı-hikâye tekrarı, coin cooldown ve
zaman-blok korelasyonunu bugünkü standartta taşımadığı için üretim kanıtı kabul
edilmez. Bağımsız haber katalizörü tek bir önceden sabit laboratuvarla yeniden
ölçülür.

Laboratuvar yalnız açık coin adı veya güvenli ticker eşleşmesini kullanır;
belirsiz kısa kelimeleri reddeder. Canonical URL ve 72 saatlik aynı başlık
tekilleştirilir. GKG gün-noon fallback zamanları dışlanır; kesin olay zamanına
60 dakika güvenlik gecikmesi eklenir ve sonraki 1h mum açılışında giriş yapılır.
Coin başına 24 saat cooldown, 10 baz puan maliyet ve 7 günlük blok alt güven
sınırı zorunludur. Teknik yön seçim filtresi değildir.

Ham veri, zaman, başlık, tekrar, kaynak, fiyat eşleşmesi, örneklem ve coin
kapsamı önceden sabit kalite kapılarının tamamını geçmezse aday seçimi ve EXAM
çalışmaz; sonuç DATA_QUALITY_BLOCKED olur. Kapılar geçerse 16 sabit
FOLLOW/CONTRARIAN config önce LEARN, sonra en fazla ilk üçle VALIDATION ve tek
dondurulmuş kazananla EXAM akışına girer. Tarihsel geçiş yalnız SHADOW adaydır;
canlı Risk Manager ve fresh forward kanıt zorunluluğunu aşamaz.

---

## Karar 018 - GDELT Tarihsel Haber Replay'inin Veri Kalitesi Nedeniyle Kapatılması

Kalite kapısı gerçek Mac veri tabanındaki 5.571 GDELT haberinde çalıştırıldı.
Zaman alanlarının tamamı biçimsel olarak geçerli ve başlıkların %99,8'i
kullanılabilir olsa da kesin intraday timestamp oranı %0 çıktı. Her kayıt,
gerçek haber saati bulunamadığında collector tarafından atanan arşiv-günü
12:00 fallback değerini taşıyor.

Bu veriyle event-time replay yapmak, gün içinde haberden önce oluşan fiyat
hareketini haber sonrası sonuç gibi sayabilir. Bir saat güvenlik gecikmesi bu
belirsizliği çözmez. Yönlü olay, tekrar, tarih aralığı, kaynak, fiyat eşleşmesi
ve coin kapsamındaki sıfır değerler kesin zaman kapısının aşağı akış sonucudur.

Bu nedenle:

- Sonuç DATA_QUALITY_BLOCKED olarak kilitlenir.
- Aday seçimi, VALIDATION ve EXAM çalıştırılmaz.
- Haber saatleri rastgele, gün ortası veya URL tahminiyle doldurulmaz.
- Aynı GDELT günlük arşiviyle event-time tarihsel kural araması durdurulur.
- Eski "güçlü NEWS BASE" sonucu üretim kanıtı değildir.
- Eski %77,6 NEWS BASE, ROBUST_B ve provisional Champion etiketleri tarihsel
  edge kanıtı olarak geri çekilir.
- Haber katalizörü yalnız gerçek published/collected timestamp taşıyan forward
  gözlemler üzerinde doğrulanır.
- Canlı politika değişmez; ürün SHADOW_ONLY ve Risk Manager vetosu nihai kalır.

---

## Karar 019 - Tek Forecast Core ve Mimari Değişiklik Sınırı

Ürünün asıl hedefi yalnız kötü sinyali susturmak değil; 1h, 4h ve 24h için
ölçülebilir derecede başarılı fiyat tahmini üretmektir. Mevcut Decision Engine
bu sözleşmeyi karşılamaz: teknik AL/SAT yön adayı üretir, ancak kalibre edilmiş
olasılık, hedef fiyat veya tahmin aralığı üretmez.

Bu nedenle yalnız bir kontrollü mimari değişiklik açılır:

- Tek merkezi Forecast Core; runtime ajan oylaması veya yeni ajan ağı yoktur.
- Veri ajanları feature/kalite üreticisi olarak kalır; verdikleri confidence
  değeri fiyat olasılığı sayılmaz.
- Forecast çıktısı medyan hedef fiyat, %80 tahmin aralığı, maliyet + minimum
  anlamlı hareket üstü UP/DOWN/NEUTRAL olasılıkları, beklenen net getiri ve
  AL/SAT/BEKLE içerir.
- Risk Manager BLOCK nihai vetodur.
- İlk ve tek fiyat-only challenger, sabit özellikli pooled ridge dağılım
  modelidir. RSI/MACD/EMA eşik grid'i veya yeni kural kombinasyonu aranmaz.
- Gerçek veri sonucu görülmeden yapılan red-team incelemesinde ridge objective
  örneklem büyüklüğünden bağımsız `mean loss + alpha*L2` olarak netleştirildi;
  alpha=0,05 ve model sürümü
  `POOLED_RIDGE_DISTRIBUTION_V1_ALPHA_0_05_FIXED` olarak donduruldu. Sonuca
  bakılarak tekrar değiştirilemez.
- FIT, CALIBRATION, VALIDATION ve EXAM kronolojik ayrılır; hedefi segment
  sınırını aşan kayıt dışlanır. Ölçekleyici/model yalnız FIT, olasılık ve
  aralık kalibrasyonu yalnız CALIBRATION verisinden öğrenir.
- Bir horizon VALIDATION kapılarının tamamını geçmezse EXAM açılmaz.
- Olasılık kalibrasyonu UP/DOWN ve aksiyon seçkisi için ayrı ölçülür; güven
  oranının tanımladığı olay ile raporlanan hit aynı olaydır.
- Cross-coin ve zaman korelasyonu haftalık kümeli bootstrap ile hesaba katılır;
  Wilson yalnız yardımcı göstergedir.
- Seçilmiş %70+ aksiyonlarda hit alt sınırı ve tahmin-gerçekleşme kalibrasyon
  farkı da haftalık blok bootstrap ile geçmek zorundadır.
- İlerideki üretim consumer'ı yalnız hash biçimini değil, sealed artifact
  içeriğini yeniden hashleyerek eşleşmeyi ve ayrı promotion attestation'ı
  doğrulamak zorundadır.
- SAT, Spot yatırımcı için satış/azaltma/kaçınma önerisidir; short portföy kârı
  veya otomatik işlem gibi raporlanmaz.
- Forecast örneklemi Risk ile önceden seçilmez; Risk yalnız son eylemi veto eder.
- Aynı tarihsel EXAM dönemi önceki çalışmalarda görüldüğü için olası geçiş
  yalnız DEVELOPMENT_ONLY sayılır; ürün kanıtı değildir.
- Canlı Decision Engine, rapor veya alarm akışı bu laboratuvar sonucu
  doğrulanmadan değiştirilmez.
- Tek challenger başarısızsa fiyat-only model araması durur. Sonraki araştırma
  yeni indikatör değil, kesin availability timestamp taşıyan dışsal veri veya
  gerçek forward örnek olur.

Ürün ekranındaki “güven %X”, bir kural/ajan puanı değil, ayrı kalibrasyon
verisinde ölçülmüş maliyet-üstü hareket olasılığıdır. Tek nokta fiyat için
kesinlik iddia edilmez; medyan hedef ve tahmin aralığı birlikte gösterilir.

---

## Karar 020 - Forecast Core V1 Reddi ve Sonuç-Odaklı V2 Geçişi

`POOLED_RIDGE_DISTRIBUTION_V1_ALPHA_0_05_FIXED` gerçek Mac veri tabanında
önceden sabit 1h, 4h ve 24h VALIDATION kapılarının tamamında reddedildi.
OBSERVED_DIAGNOSTIC hiçbir ufuk için açılmadı ve canlı Decision Engine, alarm
ve rapor politikası değiştirilmedi.

Ölçülen ana sonuçlar:

- 1h: Brier skill +%1,2; MAE skill -%0,5; risk-sonrası aksiyon 0.
- 4h: Brier skill +%0,1; MAE skill -%0,7; risk-sonrası aksiyon 0.
- 24h: Brier skill -%1,2; MAE skill -%2,5; class ECE %10,7; 17 aksiyonda
  11 doğru. Pozitif directional utility yalnız zayıf bir tail hipotezidir;
  küçük örneklem, %1,7 kapsam ve baseline başarısızlığı nedeniyle başarı
  kanıtı değildir.

Bu sonuç yalnız fiyat-only V1 ailesini kapatır; Forecast Contract'ı ve ürün
hedefini kapatmaz. `BEKLE` veya “kanıt yok” güvenli ara davranıştır, nihai ürün
başarısı değildir. Ölçüm dürüstlüğü değişmez bir kısıttır; ürün hedefi ise
kalibre edilmiş, yararlı sıklıkta, baselineları ve maliyetleri geçen, coin/zaman/
rejim boyunca tekrarlanan ve fresh forward dönemde ayakta kalan başarılı
tahminler üretmektir.

Bir sonraki ve tek kontrollü araştırma kampanyası `DERIVATIVES_STATE_V2` olarak
dondurulur:

- Yeni RSI/MACD/EMA veya threshold araması yapılmaz.
- Exact point-in-time Binance USD-M funding, open-interest/positioning,
  perpetual premium/basis ve taker akışı Forecast Core'a feature provider
  olarak eklenir.
- Derin ve güvenilir tarihçesi olmayan liquidation, order-book ve haber
  katalizörleri bu kampanyaya sonradan eklenmez; forward olarak birikmeye devam
  edebilir.
- 4h PRIMARY, 24h SECONDARY, 1h EXPLORATORY endpoint'tir; her ufuk ayrı geçer
  veya reddedilir.
- Ham model aksiyonu ile Risk Manager sonrası uygulanabilir aksiyon ayrı
  ölçülür. Risk yön üretmez, yalnız veto eder.
- İlk V1 validator'ındaki tanısal kusurlar düzeltilir: forecast/action haftası
  ayrımı, 8 haftadan az blokta N/A, event-weighted whole-week bootstrap,
  CALIBRATION'da dondurulmuş constant-direction baseline ve NEUTRAL dahil üç
  sınıf ECE.
- Tarihsel geçiş yalnız DEVELOPMENT_ONLY'dır; ürün güven oranı için yeni sealed
  future ve fresh forward doğrulama zorunludur.

Çalışma orkestrasyonunun sorumluluğu asistandadır. Bağımsız ajanlar yalnız veri
fizibilitesi, model uygulaması ve red-team denetimi gibi sınırları belli işleri
paralelleştirmek için kullanılır; ajan görüşleri fiyat olasılığına çevrilmez ve
kullanıcı ajan koordinasyonuyla uğraştırılmaz.

---

## Karar 021 - Derivatives State V2 Veri ve Seçim Sözleşmesi

V2, Forecast Core mimarisini yeniden değiştiren yeni bir sistem değil; aynı
Forecast Contract'a exact point-in-time türev piyasa özellikleri sağlayan tek
sınırlı challenger'dır. Model ve veri sözleşmesi gerçek Mac sonucu görülmeden
dondurulmuştur.

- Deney 400 gün kullanır; collector warmup ve funding öncülüyle 430 gün toplar.
- Kapsam performansa göre seçilmez. En az 10 hazır coin ile BTC ve ETH zorunludur.
- Funding oranı gerçekleşen geçmiş event aralığıyla 8 saat eşdeğerine normalize
  edilir. Eksik veya belirsiz event zinciri imputasyonla doldurulmaz.
- Availability anlamı açık olmayan Vision taker ratio kullanılmaz. Taker akışı
  yalnız kapanmış futures kline quote-volume alanlarından hesaplanır.
- Tek tük source gap bütün deneyi gereksiz yere kapatmaz; eksik context atlanır.
  Global ve coin gap oranı en fazla %2, haftalık gap oranı en fazla %10 olabilir.
- 4h tek PRIMARY endpoint'tir. 24h SECONDARY ancak 4h VALIDATION ve observed
  diagnostic'i çelişkisiz geçerse açılır. 1h yalnız EXPLORATORY'dir ve artifact
  adaylığı yaratamaz.
- 24h için minimum 750, 1h/4h için minimum 1.000 validation olayı gerekir.
- CALIBRATION iki kronolojik parçaya ayrılır: CAL-A point/probability calibration,
  CAL-B residual dağılım ve tahmin aralığı calibration.
- JSON'a yazılan artifact hash yeniden okununca da doğrulanmalıdır. Data-quality
  artifact'i desteklenen cohort, pencere, manifest digest ve hata nedenini taşır.
- Tarihsel geçiş yalnız DEVELOPMENT_ONLY'dır. Canlı ürün, kullanıcıya güven
  yüzdesi veya alarm için yeni sealed future ve gerçek forward kanıt gerektirir.

Red-team bulguları gerçek koşudan önce kapatılmıştır: ulaşılmaz 24h sample gate,
JSON key/hash farkı, unsupported cohort gap'leri, %98 readiness ile %100 runtime
çelişkisi, funding interval semantiği, raw/Risk aksiyon ayrımı ve az blokta
yanıltıcı confidence interval düzeltilmiştir. Sonuca göre eşik, model veya veri
paketi değiştirilmeyecektir.


---

## Karar 022 - Forecast Core V2.1 Reddi ve Tek V3 Model Kampanyası

2026-08-16 gerçek VALIDATION sonucu, veri kalitesi geçtikten sonra V2.1
futures/microstructure additive-stump modelinde doğrulanmış edge olmadığını
gösterdi. 4h PRIMARY baseline'a yakın kaldı; 24h'deki +%3,8 Brier izi MAE,
kalibrasyon ve aksiyon kapılarını geçmedi; 1h baseline'dan kötüydü. Hiçbir
ufukta %70 olasılık eşiğini aşan ham aksiyon oluşmadı.

Karar:

- FUTURES_MICROSTRUCTURE_GBDT_V2_1_FIXED reddedildi ve yeniden eşik/model
  ayarı için açılmayacak.
- %70 eşiği düşürülmeyecek; sıfır aksiyon başarı olarak sunulmayacak.
- Forecast Contract, point-in-time türev veri katmanı, Risk veto ve validation
  kapıları korunacak. Mimari yeniden yazılmayacak.
- Bir sonraki tek kontrollü model kampanyası, etkileşimleri öğrenebilen gerçek
  gradient-boosting Forecast Core V3'tür. V2'nin additive stump sınırlamasını
  kaldırır; yeni bir veri/threshold grid kampanyası açmaz.
- V2 VALIDATION dönemi bundan sonra development verisidir. V2 tarafından
  açılmayan 2026-05-28 sonrası OBSERVED_DIAGNOSTIC dilimi V3 sonucu için
  mühürlü tutulur; model, kalibrasyon veya eşik seçimine giremez.
- V3 hyperparameter seçimi yalnız development içindeki purged walk-forward
  bloklarda, küçük ve önceden kayıtlı bir aralıkta yapılabilir. Son mühürlü
  dilim yalnız bir kez açılır.
- V3 de reddedilirse aynı fiyat+türev veri üzerinde yeni model/threshold araması
  durdurulur. Sonraki ilerleme exact-time catalyst veya yeni forward veri
  gerektirir.
- Tarihsel geçiş olsa bile ürün SHADOW_ONLY kalır ve fresh forward doğrulama
  olmadan kullanıcıya güven yüzdesiyle AL/SAT alarmı verilmez.


### Karar 022A - V3 Ön Kayıtlı Model Sözleşmesi

V3 yalnız 4h PRIMARY endpoint için tek modeldir:
scikit-learn HistGradientBoostingClassifier/Regressor, max_depth=4,
max_leaf_nodes=15, max_iter=180, learning_rate=0.05, min_samples_leaf=80,
L2=1.0, early_stopping=False ve random_state=20260816.

- FIT 2025-07-12..2026-01-08; CAL 2026-01-08..2026-03-09; development gate
  2026-03-09..2026-05-28'dir.
- Sealed 2026-05-28..2026-08-16 verisi development modülünce sorgulanmaz.
- CAL-A point/probability, CAL-B residual/interval kalibrasyonu için korunur.
- %70 aksiyon eşiği ve V2 validation kapıları değişmez.
- V3 hem piyasa/V1 baselinelarını hem frozen V2.1 Brier, log-loss, MAE ve
  distribution loss sonuçlarını geçmelidir.
- Development gate başarısızsa sealed evaluator açılmaz. Geçerse model pickle
  SHA-256 ile mühürlenir; sealed evaluator ayrı commit ve tek koşu olur.


---

## Karar 023 - Fiyat + Türev Model Aramasını Durdur

Forecast Core V3 interaction modeli development gate'te reddedildi:
Brier skill -%3,3, MAE skill %0,0, class ECE %9,1; 34 aksiyonda hit %47,1
ve utility -%0,295. Sealed dönem açılmadı.

Bağlayıcı karar:

- Aynı spot fiyat ve Binance USD-M türev veri setinde V4, yeni model ailesi,
  hyperparameter grid'i veya %70 altı threshold araması yapılmayacak.
- V1, V2.1 ve V3 sonuçları bu veri ailesinde doğrulanmış tahmin edge'i
  bulunmadığını gösterir. BEKLE veya sıfır aksiyon başarı sayılmaz.
- Forecast Contract, validator, Risk veto ve canlı SHADOW ürün mimarisi korunur.
- Yeni tarihsel araştırma ancak kesin event-time/availability-time taşıyan,
  sonuçlardan bağımsız toplanmış yeni bir catalyst veri ailesiyle açılabilir.
- Yaklaşık gün/saat atanmış eski haber arşivi tekrar kullanılmayacak.
- Exact-time catalyst verisi yeterli tarihsel kapsam sağlayamazsa uydurma veya
  imputasyon yapılmaz; forward birikim sürdürülür.
- Nihai hedef değişmedi: yararlı sıklıkta, kalibre, maliyet sonrası pozitif ve
  yeni forward dönemde tekrarlanan yüksek güvenli tahminler.

## 2026-08-16 — Exact-time catalyst checkpoint

1. Exact-time catalyst research is permitted because the protected recent Binance announcement window passed the source-quality contract (1336/1336 exact timestamps). Timestamp-less legacy rows remain excluded.
2. A known-time pinned announcement is handled as an ordering outlier through a 99.9% chronological-backbone gate; no timestamp tolerance or invented time is used.
3. Price readiness is judged by matched independent events/coin-events, coverage, coin breadth and both catalyst directions. The redundant pre-outcome raw ticker-event threshold was aligned from 100 to 75 before any return was read.
4. The only allowed historical hypothesis is frozen: STRICT_LISTING=AL and STRICT_DELISTING=SAT/REDUCE/AVOID, with 4h primary, 24h secondary, 1h exploratory and 10bps cost.
5. Multi-coin announcements are clustered by article and cannot cross the chronological DEVELOPMENT/VALIDATION split. No model family, feature search or threshold grid is allowed.
6. Even a historical pass remains SHADOW_ONLY and cannot produce a confidence claim or product alert without fresh forward probability calibration.


---

## Karar 024 - Kanıta Dayalı Kendini Düzeltme

Tahmin motoru hiçbir coini geçmiş yanılgılar nedeniyle kara listeye almayacak ve
tek bir settlement hatasından sonra canlı model ağırlıklarını değiştirmeyecek.

Bağlayıcı öğrenme politikası:

- Her tahmin anındaki model feature snapshot'ı ve observed-only ajan bağlamı
  korunur; sonuç geldiğinde hata yön, hedef büyüklüğü, aralık, rejim ve bağlam
  etiketleriyle sınıflandırılır.
- "Kök neden" yalnız tekrarlanan, farklı zaman bloklarına yayılan istatistiksel
  örüntü anlamına gelir; feature importance veya tek olay nedensellik kanıtı
  sayılmaz.
- En az 30 olay ve 3 bağımsız hafta olmadan challenger hipotezi oluşturulmaz;
  toplam 100 settlement öncesinde sistem yalnız kanıt toplar.
- Öğrenme katmanı canlı champion'ı değiştiremez. Sınırlı challenger, değişmemiş
  champion'ı untouched forward veride kalibrasyon, hedef hatası ve maliyet
  sonrası faydada yenmeden terfi edemez.
- Haber ve sosyal sinyaller çöpe atılmaz, fakat doğrulanana kadar model girdisi
  değil observed-only teşhis bağlamıdır.
- Orderbook "manipüle edilemez gerçek" kabul edilmez; spoofing ve kısa ömürlü
  emir riski nedeniyle ayrı veri-kalitesi ve forward kanıtı olmadan modele
  eklenmez.


---

## Karar 025 - Kripto Kazanç Ajanı Sanal Portföy Sözleşmesi

Unified Forecast Engine yanında çalışan ayrı bir Kripto Kazanç Ajanı kurulacaktır.
Ajan yalnız sanal para kullanır; gerçek borsa emri, para yatırma/çekme veya trade
API yetkisi kullanamaz.

İlk politika sonuç görülmeden sabitlenmiştir:

- 10.000 USDT başlangıç sermayesi,
- Binance 1h spot fiyatlarıyla exact next-open giriş ve exact due-close çıkış,
- 1x sanal LONG/SHORT,
- girişte ve çıkışta 10'ar baz puan ücret,
- işlem başına azami %10, toplam azami %60 açık risk,
- aynı anda azami 6 pozisyon ve cycle başına azami 3 yeni işlem,
- yalnız 4h/24h; yön olasılığı >=%52, yön farkı >=5 puan ve q50 yön uyumu,
- Risk Manager izin vermediğinde yeni pozisyon yok,
- aynı coinde açık/giriş bekleyen işlem varken overlap yok.

Amaç Forecast Engine'in olasılıklarını gerçekçi ücret ve sermaye muhasebesinde
ölçmektir; işlem üretmek için eşik zorla düşürülmez. Politika değişikliği aynı
portföy geçmişini geriye dönük yeniden yazamaz; yeni sürüm/challenger olarak
başlar. İstanbul 03:00 kapanışında ayrıntılı günlük rapor kaydedilir ve Telegram
yapılandırılmışsa kısa özet gönderilir.

---

## Karar 026 - Kripto Kazanç Ajanı Yetki Sınırı ve Gerçek İşlem Paritesi

Unified Forecast Engine ve bütün AG Crypto Analyst sisteminin nihai yönetim, model/politika terfi, risk limiti, sermaye kullanımı ve gerçek işlem yetkisi ana orkestratörde kalır. Bu yetkiler Crypto Profit Agent ile paylaşılmaz.

Crypto Profit Agent öğrenebilir, gerekçeli öneri sunabilir, sonuçları hakkında hesap verebilir ve kanıtla itiraz edebilir. Ancak kendi modelini/politikasını üretime terfi ettiremez; ücret, risk, sermaye veya işlem kurallarını değiştiremez; Risk Manager vetosunu aşamaz ve gerçek emir gönderemez. Ajanın eğitimi, denetimi, performans değerlendirmesi ve önerilerinin kabul/reddi ana orkestratörün sorumluluğundadır.

Paper simülasyon gelecekteki gerçek işlem ortamıyla aynı execution contract'a yaklaşacaktır. En az şu gerçeklikler açıkça modellenir:

- seçilen borsa, piyasa ve enstrüman semantiği; spot ile futures/short ayrımı,
- exact exchange timestamp, yalnız o anda erişilebilir veri ve lookahead yasağı,
- bid/ask, spread, maker/taker ücretleri, dinamik slippage ve emir gecikmesi,
- tick size, step size, minimum notional, kısmi dolum ve reddedilen emirler,
- likidite/kapasite, açık pozisyon ve gerçek sermaye muhasebesi,
- futures kullanılırsa funding, borrow ve liquidation kuralları,
- veri boşluğu, bağlantı kesintisi, stale fiyat ve borsa kesintileri.

Güvenilir biçimde modellenemeyen koşul sessizce iyimser varsayılmaz; açık ESTIMATED/DATA_BLOCKED etiketiyle fail-closed ele alınır. Simülatör ile ilerideki canlı emir adaptörü aynı karar ve execution sözleşmesini kullanacak; sermaye açılmadan önce shadow/dry-run ve simülasyon-canlı reconciliation testleri tamamlanacaktır.

Gerçek para kullanımı ancak kullanıcının açık onayı, ana orkestratörün ayrı terfi kararı ve önceden tanımlı performans/risk kapılarının birlikte geçilmesiyle mümkündür.

---

## Karar 027 - Haber-Yönü Değil Davranış-Tepkisi Önceliği

Kripto piyasasında haberin geniş kitlelere erişim hızı arttığı için AGCA'nın
avantaj hipotezi "haberi önce duymak" veya haber metnini doğrudan fiyat yönüne
çevirmek değildir. Test edilecek hipotez, haber öncesi konumlanma ile haberden
sonra gerçekleşen kalabalık refleksinin, likidite ve güçlü karşı taraf davranışı
üzerinden ölçülebilir olmasıdır.

Bu karar piyasanın gizli eller tarafından ayarlandığı varsayımına dayanmaz.
Bilgi asimetrisinin haber erişiminden; pozisyon, akış, likidite, absorpsiyon ve
tepkinin devam edip etmediğini görebilme kapasitesine kaydığı hipotezidir.

Bağlayıcı ölçüm sözleşmesi:

- İyi/kötü haber etiketi tek başına işlem yönü veya yetki üretmez.
- Her kesin-zamanlı olayda önce olaydan önceki son kapanmış dakika sabitlenir.
- Olay dakikasındaki emir defteri dengesizliği, agresif işlem baskısı, spread,
  açık pozisyon değişimi, funding ve tasfiyeler birlikte korunur.
- Fiyat yolu olay öncesi orta fiyata göre 1, 5, 15, 60, 240 ve 1.440 dakikada
  ölçülür. Gelecek ufuk kapanmadan sonuç yazılmaz.
- "Kötü habere dayanıklılık", "iyi habere zayıflık", absorpsiyon, tükenme veya
  tersine dönüş gibi yorumlar sabit eşik icat edilerek atanmaz; önce ham tepki
  yolu ve yeterli doğal olay çeşitliliği birikir.
- Aynı global olayda birçok coin satırı korelasyonludur ve bağımsız örnek gibi
  sayılmaz. Olay/dakika ana bağımsızlık birimidir.
- Gerçek yapısal kırılma olasılığı korunur; her kötü haber otomatik alım fırsatı
  veya her iyi haber dağıtım kabul edilmez.
- Bu katman observed-only ölçümdür. Yeni model, eşik, strateji terfisi veya emir
  yetkisi için mevcut forward kanıt ve yönetişim kapıları aynen geçerlidir.

---

## Karar 028 - Değişmez Davranış/Olay Forward Sonuç Defteri

Canlı market-behavior observer'ın topladığı kapanmış dakikalar ile yalnız
`available_at` saatinde görünen kesin-zamanlı dış olaylar, otomatik ve kalıcı
bir PAPER sonuç defterinde eşleştirilir.

Bağlayıcı sözleşme:

- Sonuçlar yalnız kapanmış uygun davranış dakikalarından yazılır.
- Olay öncesi son kapanmış dakika değişmez fiyat tabanıdır.
- 1, 5, 15, 60, 240 ve 1.440 dakikalık sonuç, ilgili hedef dakika gerçekten
  kapandıktan sonra yazılır; eksik gelecek veri doldurulmaz.
- Aynı olay/dakika/enstrüman/ufuk satırı ilk settlement sonrasında değişmez;
  tekrar çalışma sonucu geriye dönük yeniden yazamaz.
- Global olayın farklı enstrüman satırları aynı `independence_unit` değerini
  taşır ve bağımsız olay sayılmaz.
- Coin kapsamlı olay yalnız ilgili temel sembollere uygulanır.
- Defter özetlenmiş fiyat ve davranış özellikleri taşır; ham payload saklamaz.
- Defter SQLite'ta yerel-only kalır ve Supabase mirror/outbox kapsamına girmez.
- Bu bir öğrenme kanıtı deposudur; tek başına model, eşik, sinyal, strateji
  terfisi veya işlem yetkisi üretmez.

---

## Karar 029 - Dış Trader Karar Gerekçesi Araştırma Sözleşmesi

Kamuya açık copy-trader işlemleri kopyalanacak talimatlar değil, piyasa
davranışını anlamaya yardım eden karantinalı araştırma örnekleridir.

- Olası gerekçe yalnız işlem açıkken, girişten önce kapanmış piyasa ve türev
  verisinden oluşturulur ve sonuç görülmeden değişmez biçimde dondurulur.
- Trader'ın gerçek niyeti biliniyormuş gibi sunulmaz; her gerekçe
  `POSSIBLE_NOT_PROVEN` veya yalnız bağlam olarak işaretlenir.
- Kazanan ve kaybeden işlemler aynı kuralla değerlendirilir. Kazanç nedensellik
  kanıtı değildir; kayıp da tek başına hatanın kaynağını kanıtlamaz.
- Yalnız kapanmış history içinde sonradan görülen işleme geriye dönük giriş
  hikâyesi yazılmaz. Forward değerlendirme için işlem önce açık görülmelidir.
- Bilinen açık işlemlerin kapanışları korunur; geriye dönük örnek yükü çalışma
  sağlığını bozmayacak şekilde sınırlanabilir.
- Bu katman kopyalama, otomatik politika/model değişikliği, terfi, eşik araması
  veya gerçek emir yetkisi üretemez; kayıtları SQLite'ta yerel-only kalır.

---

## Karar 030 - PAPER İşlemi Bir Öğrenme Deneyidir; Ajanın Hafızası Korunur

AGCA ajanı yeni piyasa-davranışı vizyonuna geçerken sıfırlanmayacak, geçmiş
PAPER portföyü ve bütün karar/sonuç kayıtları korunacaktır. Eski dönemin pozitif
ekonomik sonucu model becerisi gibi sunulmayacak; zayıf tahmin kalitesi, tek
yön/rejim yoğunlaşması, korelasyon ve birkaç büyük kazancın etkisiyle birlikte
kalıcı tecrübe olarak yorumlanacaktır.

Bağlayıcı öğrenme ve işlem sözleşmesi:

- Ajanın PAPER işlem açma eli yalnız işlem sayısını azaltmak amacıyla bağlanmaz.
  İşlem, ajanın yeteneklerini gerçek zamanda sınayan bir deneydir.
- İş yapmış görünmek, veri üretmek veya rapor doldurmak için işlem açılmaz.
  Her işlemden önce kullanılan kanıt, gerekçe, beklenen yön/büyüklük/süre,
  riskler ve sınanan yetenekler sonuç görülmeden dondurulur.
- Her kapanıştan sonra yalnız P&L değil; tahmin kalitesi, zamanlama, maliyet,
  rejim uyumu, korelasyon/yoğunlaşma, karşı-olgusal basit referanslar ve
  beceri-rejim-şans ayrımı değerlendirilir.
- Kazanç otomatik başarı, kayıp otomatik başarısız strateji sayılmaz. Ajan;
  kazanan ve kaybeden kendi işlemlerinden, başarılı ve başarısız dış
  trader'lardan aynı outcome-blind giriş sözleşmesiyle öğrenir.
- Ham geçmiş değişmez kalır; çıkarılan ders ayrıca sürümlü tecrübe kaydında
  `ne öğrenildi`, `hangi koşullarda geçerli`, `kanıt gücü` ve `sonraki sınav`
  alanlarıyla tutulur. Sonradan görülen sonuca uygun gerekçe yazılmaz.
- Aynı ajan kimliği ve yaşam boyu kasa muhasebesi sürer. Yeni karar çekirdeği
  ayrı politika sürümü/kohortuyla ölçülür; böylece geçmiş unutulmaz fakat eski
  sonuç yeni sistemin performansını maskeleyemez.
- Eski Unified Forecast yeni çekirdeğin zorunlu öğretmeni veya girdisi değildir;
  yalnız tarafsız karşılaştırma kollarından biridir. Yeni davranış-temelli
  çekirdek kendi başına ve basit mekanik referanslara karşı da sınanır.
- Doğrulanmış dersler PAPER kararlarına taşınabilmelidir; öğrenme yalnız raporda
  kalmaz. Bununla birlikte otomatik kendini kandırma, eşik gevşetme, gerçek emir
  ve kanıtsız politika terfisi mevcut fail-closed kapılarla engellenir.

---

## Karar 031 - Davranış Çekirdeği PAPER Yürütmenin Tek Aktif Karar Kaynağıdır

Unified Forecast geçmiş karşılaştırma baseline'ı olarak korunur; yeni PAPER
pozisyon seçiminin zorunlu öğretmeni veya yürütme girdisi değildir. Aktif karar
yolu kapanmış ve kalite kapısından geçmiş piyasa davranışı ile point-in-time dış
olay kanıtından öğrenir.

- Emir defteri dengesi ve agresif taker akışı yön desenini oluşturur. Spread
  doğrudan maliyet hesabına; OI, funding, liquidation ve kaynak kalite kapıları
  karar tezine ve deney bağlamına girer.
- Haber, on-chain, whale ve exact-macro olayları yalnız AGCA tarafından gerçekten
  erişilebilir oldukları kapanmış dakikada kullanılabilir. Gelecek outcome veya
  geç gözlenmiş olay geçmiş karara sızamaz.
- Öğrenme bir global olayın çok sayıdaki coin satırını bağımsız örnek saymaz;
  `independence_unit` başına tek örnek kullanır.
- Bir davranış deseni PAPER deneyi açmadan önce en az 30 bağımsız örnek, en az
  üç UTC günü, en az %55 masraf-sonrası kazanma oranı ve en az +%0,05 ortalama
  net edge göstermelidir. Bu kapılar faaliyet üretmek için gevşetilemez.
- Kanıt yeterli değilse ajan eski Unified Forecast'a geri dönmez; piyasayı
  okumaya ve outcome toplamaya devam eder, işlem açmaz.
- Aynı ajan, aynı yaşam boyu kasa ve aynı gerçekçi execution muhasebesi kullanılır.
  Davranış kararları açık kaynak/reference kimliğiyle ve sonuç-kör tezle bağlanır.
- Eski ve yeni performans politika/karar kaynağına göre ayrı değerlendirilir.
  Tek kümülatif kasa korunurken eski kazanç yeni çekirdeğin becerisi sayılamaz.
- PAPER_ONLY, risk veto, freshness, exact-entry, maliyet, liquidation ve gerçek
  emir kapıları değişmez; gerçek para yetkisi üretilmez.

---

## Karar 032 - PAPER Keşif İşlemi ile Güven/Terfi Kapısı Ayrıdır

Karar 031'deki 30 bağımsız örnek, üç UTC günü, %55 masraf-sonrası kazanma
oranı ve +%0,05 ortalama net edge şartlarının bütün PAPER işlemlerini engellemesi,
Karar 030'daki “işlem bir öğrenme deneyidir” sözleşmesiyle çelişmiştir. Bu yorum
geçersizdir ve aşağıdaki ayrım onun yerini alır:

- Sonucu kapanmış en az beş bağımsız örnekten oluşan, en az bir UTC gününde
  gözlenmiş ve ihtiyatlı maliyet modeli sonrası ortalama edge'i pozitif olan
  davranış hipotezi; taze/kaliteli canlı kanıt ve risk izni bulunduğunda küçük
  bir PAPER öğrenme deneyi açabilir.
- Keşif deneyi özkaynağın en fazla %2'sini kullanır. Her işlemden önce sonuç-kör
  tez dondurulur ve aynı yaşam boyu kasa içinde ayrı politika sürümüyle izlenir.
- 30 örnek, üç UTC günü, %55 kazanma ve +%0,05 net edge şartları işlemi öğrenme
  amacıyla açmanın değil, deseni güvenilir/terfiye hazır saymanın kapısıdır.
  Terfiye hazır desen mevcut normal %10 PAPER boyutunu kullanabilir.
- Kazanma oranı tek başarı hedefi değildir. Masraflar sonrası beklenti, toplam
  getiri, profit factor, drawdown, bağımsız işlem ve rejim dayanıklılığı birlikte
  değerlendirilir.
- Unified Forecast baseline kalır. PAPER_ONLY, veri kalitesi, risk, freshness,
  exact-entry, maliyet, liquidation ve gerçek-emir kapıları değişmez.
- Bu keşif/terfi ayrımı kullanıcının açık onayı olmadan yeniden birleştirilemez
  veya keşif işlemlerini sessizce durduracak biçimde değiştirilemez.

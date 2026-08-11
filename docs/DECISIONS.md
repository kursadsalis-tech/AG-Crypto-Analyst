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

# AG Crypto Analyst - Changelog

## v0.5.0

### News Intelligence Agent - İlk Çalışan Sürüm

Tamamlandı:

- CoinDesk RSS haber toplama
- Cointelegraph RSS haber toplama
- SQLite kalıcı haber kayıt sistemi
- Haber tekrarlarının link bazında engellenmesi
- Haber analiz kayıt tablosu
- Haberlerden coin tespiti
- Olay türü sınıflandırması
- Pozitif / negatif / nötr yön sınıflandırması
- Etki skoru
- Güven skoru
- Analiz sonuçlarının SQLite'a kaydedilmesi

Başarılı testler:

- 55 haber başarıyla toplandı.
- 55 haber SQLite'a kaydedildi.
- Collector ikinci kez çalıştırıldığında haber sayısı 55 olarak kaldı; duplicate kayıt oluşmadı.
- 55 haberin tamamı analiz edildi.
- 55 analiz sonucu SQLite'a kaydedildi.

Not:

Bu sürüm kural tabanlı ilk News Intelligence katmanıdır. Nihai tahmin başarısı iddiasında bulunmaz. Bir sonraki geliştirme aşamasında haber sinyallerinin gerçek fiyat hareketleriyle ölçülmesi ve sınıflandırma doğruluğunun geliştirilmesi hedeflenmektedir.

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

13 coin üzerinde başarıyla test edildi.

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

## v0.6.0
Social Sentiment Agent

## v0.7.0
On-chain Agent

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

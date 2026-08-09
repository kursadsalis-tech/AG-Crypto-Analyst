# AG Crypto Analyst - Project Status

## 1. PROJENİN ANA HEDEFİ

AG Crypto Analyst'ın nihai amacı:

"Dünyanın en başarılı tahminlerini yapan bir kripto tahmin ve analiz sistemi" oluşturmak.

Bu proje yalnızca piyasa verilerini gösteren veya teknik indikatör hesaplayan bir uygulama değildir.

Uzun vadeli hedef; farklı veri ve analiz kaynaklarını bir araya getirerek kripto piyasasında mümkün olan en yüksek tahmin başarısını hedefleyen, ölçülebilir, test edilebilir ve sürekli geliştirilebilir profesyonel bir AI analiz ve tahmin ürünü oluşturmaktır.

Her geliştirilen modül bu nihai hedefe doğrudan hizmet etmelidir.

Gereksiz mimari karmaşıklıktan kaçınılacaktır.
Amaç sürekli yeni mimariler tasarlamak değil, çalışan ve giderek daha başarılı hale gelen gerçek bir ürün çıkarmaktır.

---

## 2. DEĞİŞMEZ ANA ÜRÜN YOL HARİTASI

Projenin ana geliştirme sırası:

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

Bu yol haritası projenin ana ürün planıdır.

Yeni bir çalışma oturumunda yol haritası yeniden tasarlanmayacak veya keyfi olarak değiştirilmeyecektir.

Bir modülün tamamlanması, gerektiğinde o modülün geliştirilmesini engellemez. Ancak yeni mimariler veya yeni modüller yalnızca ürünün nihai hedefine gerçek ve ölçülebilir katkı sağlıyorsa eklenecektir.

---

## 3. MEVCUT SÜRÜM

Proje Sürümü: v0.4.0

Mevcut Durum:

Technical Analysis Agent tamamlandı ve başarıyla test edildi.

---

## 4. TAMAMLANAN MODÜLLER

### v0.1.0
Market Data Agent v1 ✅

### v0.2.0
Market Data Agent v2 ✅

### v0.3.0
Market Analyzer Agent ✅

### v0.4.0
Technical Analysis Agent ✅

---

## 5. TECHNICAL ANALYSIS AGENT DURUMU

Technical Analysis Agent aşağıdaki işlemleri başarıyla gerçekleştiriyor:

- Binance'den 1H mum verilerini alma
- RSI(14) hesaplama
- MACD hesaplama
- EMA20 hesaplama
- EMA50 hesaplama
- RSI + MACD + EMA teknik skorunu oluşturma
- Teknik sinyal oluşturma
- Teknik analiz raporu oluşturma

Son başarılı testte 13 coin için analiz başarıyla gerçekleştirildi.

Sonuçlar:

- RSI(14)
- MACD
- EMA20 / EMA50
- Birleşik teknik skor
- Teknik sinyal

başarıyla üretildi ve:

reports/technical_analysis.txt

dosyasına kaydedildi.

---

## 6. ŞU ANDA YAPILACAK İŞ

Sıradaki sürüm:

v0.5.0

Sıradaki modül:

News Intelligence Agent

v0.5.0'ın amacı kripto piyasasını etkileyebilecek önemli haberleri tespit etmek, analiz etmek ve mevcut analiz sistemine anlamlı bir veri kaynağı olarak kazandırmaktır.

---

## 7. PROJEDEKİ TEMEL PRENSİPLER

1. Ürün odaklı geliştirme yapılacaktır.

2. Her modül gerçek ve ölçülebilir bir işlev sağlamalıdır.

3. Gereksiz mimari karmaşıklıktan kaçınılacaktır.

4. Büyük adımlar yerine küçük ve test edilebilir adımlar uygulanacaktır.

5. Bir adım tamamlanmadan sonraki adıma geçilmeyecektir.

6. Her modül tamamlandığında test edilecektir.

7. Başarılı test sonrasında proje sürümü artırılacaktır.

8. Her çalışma oturumunun sonunda proje hafıza dosyaları güncellenecektir.

9. Yeni oturumlarda proje geçmişi yeniden tasarlanmayacak; bu dosya temel başlangıç noktası olarak kullanılacaktır.

10. Projenin ana hedefi ve ana ürün yol haritası korunacaktır.

---

## 8. PROJE HAFIZA DOSYALARI

Aşağıdaki dosyalar projenin resmi hafızasıdır:

- docs/PROJECT_STATUS.md
- docs/TASKS.md
- docs/CHANGELOG.md
- docs/SESSION_LOG.md
- docs/DECISIONS.md

Her oturum sonunda ilgili dosyalar güncel tutulmalıdır.

Özellikle PROJECT_STATUS.md:

- projenin nihai hedefini,
- ana yol haritasını,
- mevcut sürümü,
- tamamlanan modülleri,
- üzerinde çalışılan modülü,
- bir sonraki somut görevi

açıkça göstermelidir.

---

## 9. ÇALIŞMA YÖNTEMİ

Her yeni çalışma oturumunda:

PROJECT_STATUS.md
↓
Mevcut durum
↓
Sıradaki modül
↓
Son tamamlanan adım
↓
Tek sonraki geliştirme adımı
↓
Test
↓
Doğrulama

şeklinde ilerlenir.

Yeni oturumlarda daha önce alınmış ürün kararları tekrar tartışmaya açılmaz; yalnızca gerçek bir ihtiyaç veya açık bir problem varsa değiştirilir.

---

## 10. MEVCUT DOSYALAR

- data/binance_client.py
- database/repository.py
- database/database.py
- database/models.py
- analysis/market_analyzer.py
- technical_analysis/technical_analyzer.py
- reports/market_analysis.txt
- reports/technical_analysis.txt

---

## 11. SONRAKİ ANA YOL HARİTASI

v0.5.0 → News Intelligence Agent
v0.6.0 → Social Sentiment Agent
v0.7.0 → On-chain Agent
v0.8.0 → Macro Agent
v0.9.0 → Whale Tracker
v1.0.0 → Risk Manager
v1.1.0 → Decision Engine
v1.2.0 → Report Generator

Not:

Sürüm numaraları geliştirme sırasında gerektiğinde revize edilebilir; ancak ana ürün sırası korunacaktır.

---

## 12. KALICI PROJE NOTU

AG Crypto Analyst'ın amacı yalnızca çalışan agent'lar üretmek değildir.

Amaç:

"Dünyanın en başarılı tahminlerini yapan bir kripto tahmin ve analiz sistemi"

oluşturmaktır.

Bu nedenle gelecekte yapılacak her teknik karar şu soruyla değerlendirilmelidir:

"Bu geliştirme bizi daha başarılı kripto tahminleri yapan bir ürüne gerçekten yaklaştırıyor mu?"

Cevap hayırsa, geliştirme gereksiz kabul edilmelidir.
# AG Crypto Analyst - Project Status

## 1. PROJENİN ANA HEDEFİ

AG Crypto Analyst'ın nihai amacı:

"Dünyanın en başarılı tahminlerini yapan, fırsatları ve riskleri erken tespit eden profesyonel bir kripto analiz ve karar destek ürünü" oluşturmak.

Sistem yalnızca sabit bir takip listesi için değil, Binance Spot USDT evreninde yeni ortaya çıkan coinleri ve yeni fırsatları da mümkün olduğunca dinamik biçimde tespit edebilmelidir.

Amaç falcılık değil; fiyat, teknik yapı, haber, piyasa rejimi ve ileride eklenecek on-chain, sosyal, makro ve balina verilerini matematiksel/istatistiksel olarak birleştiren, ölçülebilir ve sürekli doğrulanan bir sistem kurmaktır.

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

Proje ürün sürümü: **v0.5.0**

Tamamlanan ana ürün modülleri:

- Market Data Agent ✅
- Market Analyzer ✅
- Technical Analysis Agent ✅
- News Intelligence Agent ✅

Ana ürün yol haritasındaki sıradaki yeni modül: **v0.6.0 Social Sentiment Agent**.

Ancak şu an aktif çalışma önceliği, v0.5.0 News Intelligence sinyallerinin gerçek tahmin değerini doğrulayan Forward Shadow Validation verisinin birikmesi ve bu çıktının günlük rapor/kritik alarm sistemine entegre edilmesidir.

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
- Akış: NEWS COLLECT -> NEWS ANALYZE -> FORWARD SHADOW SETTLE/RECORD
- Son doğrulamada launchd çalışması başarılıydı; last exit code = 0.

---

## 6. COIN KAPSAMI

Takip listesi:

ARKM, FIDA, WLD, TIA, ETHFI, BIO, LQTY, ILV, SLP, UMA, IO

News Analyzer bu coinler için açık alias desteği içerir.

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
- Saatlik görev gerçek zamanlı saniyelik izleme değildir; kritik gelişme bir sonraki saatlik kontrolde bildirilir.

Telegram katmanı yardımcı/opsiyonel altyapıdır; ana kullanıcı bildirim kanalı olarak zorunlu değildir.

---

## 8. SONRAKİ SOMUT ÇALIŞMA

Yeni oturumda sıfırdan mimari konuşulmayacak.

Öncelik sırası:

1. Forward Shadow V2'nin gerçek sinyal üretmeye başladığını ve 1h sonuçların düzenli settle edildiğini kontrol et.
2. Yeterli forward örneklem oluşunca NEWS BASE vs MULTIFACTOR gerçek forward performansını değerlendir.
3. Günlük 11:00 yatırım komitesi raporunda Forward Shadow performansını karar bağlamına dahil et.
4. Kritik alarm sisteminin yalnızca anlamlı olaylarda bildirim verdiğini izlemeye devam et.
5. Araştırma ürünü geciktirmeyecek şekilde ana yol haritasındaki v0.6.0 Social Sentiment Agent'a geçiş zamanını belirle.

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
- docs/LAST_SESSION_HANDOFF.md

Yeni oturumlarda önce bu dosyalar ve mevcut GitHub kodu kontrol edilir; kullanıcıdan proje geçmişini yeniden anlatması istenmez.

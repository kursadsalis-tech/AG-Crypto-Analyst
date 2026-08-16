# Kripto Kazanç Ajanı V1

Durum: `PAPER_ONLY` — gerçek emir vermez, API işlem yetkisi kullanmaz.

## Amaç

Unified Forecast Engine tahminlerini gerçek bir sanal portföy üzerinde sınamak;
tahmin doğruluğu ile ücret sonrası yatırım sonucunu ayrı ayrı ölçmek.

## Sabit politika

- Başlangıç: 10.000 USDT (env: `AGCA_PAPER_INITIAL_USDT`)
- Veri/fiyat: Binance 1h spot mumları
- İşlem: 1x sanal LONG veya SHORT
- Ücret: girişte 10 bps + çıkışta 10 bps
- İşlem başına azami büyüklük: özsermayenin %10'u
- Toplam açık risk: azami %60
- Aynı anda azami 6 pozisyon; cycle başına azami 3 yeni pozisyon
- İşlem ufukları: 4h ve 24h
- Aday eşiği: yön olasılığı >=%52, karşı yön farkı >=5 puan ve q50 hedefi yönle uyumlu
- Risk Manager izni olmayan coinde yeni sanal pozisyon açılmaz
- Aynı coinde açık veya giriş bekleyen işlem varken yenisi açılmaz

Bu eşikler ilk ileri dönem sonucuna göre sessizce değiştirilmez. Yeni politika ayrı
sürüm/challenger olarak değerlendirilir.

## Gerçekçi fiyatlama

Seçim anında geriye dönük dolgu yapılmaz. Pozisyon önce `PENDING_ENTRY` olur:

- giriş: tahmin saatinden sonraki tam 1h mum açılışı,
- çıkış: tahminin tam due-time 1h mum kapanışı,
- eksik timestamp: işlem uydurulmaz; giriş iptal edilir veya çıkış bekler.

## Otomasyon

Mevcut `com.agca.hourly-forecast` LaunchAgent her saat:

1. Risk Manager'ı günceller.
2. Forecast Engine sonuçlarını settle eder ve yeni 39 tahmini açar.
3. Kripto Kazanç Ajanı eski işlemleri doldurur/kapatır ve yeni adayları seçer.
4. Forecast Learning Review'u çalıştırır.

LaunchAgent'i yeniden kurmak gerekmez; güncel kod çekildiğinde aynı görev ajanı da
çalıştırır.

## 03:00 İstanbul raporu

İlk 03:00 sonrası saatlik çalışmada günlük pencere kapanır. Ayrıntılı rapor:

`reports/daily_crypto_profit_agent_report.md`

Rapor şunları içerir:

- başlangıç ve güncel özsermaye,
- nakit ve açık risk,
- gerçekleşmiş/gerçekleşmemiş P&L,
- toplam ve günlük ücret,
- açılan/kapanan işlem sayısı,
- her işlemin seçim, giriş, çıkış, fiyat, yön, notional ve net sonucu.

Telegram yapılandırılmışsa kısa özet de otomatik gönderilir.

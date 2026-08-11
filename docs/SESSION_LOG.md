# AG Crypto Analyst - Session Log

## Oturum 2026-08-11

### Oturumun Ana Amacı

News Intelligence sinyallerini yalnızca tarihsel backtest ile değil, gerçek gelecekte leakage olmadan doğrulamak ve bu doğrulama katmanını çalışan ürüne bağlamak.

### Tarihsel Araştırma Özeti

Yaklaşık 360 günlük haber/fiyat veri seti üzerinde aşağıdaki laboratuvarlar tamamlandı:

- leakage-safe walk-forward
- locked out-of-sample validation
- rolling research lab
- policy robustness
- frozen monthly stress test
- market regime analysis
- regime-aware validation
- high-confidence calibration
- news + technical confluence
- weighted multifactor champion/challenger
- multifactor robustness
- adaptive dual-champion

Base 1h news policy yaklaşık %77.6 frozen EXAM hit rate üretti.

Weighted REGIME_AWARE multifactor aday model base'i bazı frozen karşılaştırmalarda geçti ve robustness analizinde ROBUST_B statüsü aldı.

Adaptive dual-champion selector sabit multifactor modele göre daha zayıf kaldığı için reddedildi.

Aynı tarihsel veriyi daha fazla optimize ederek overfitting riskini artırmamak için model donduruldu ve forward aşamasına geçildi.

### Forward Shadow Validation

İlk Forward Shadow V1 çalıştırmasında eski haberler ilk görülme anı "şimdi" kabul edilerek 16 tahmin oluşturuldu. Bunun gerçek forward olmadığı fark edildi.

Düzeltme yapıldı:

- V1 kayıtları INVALID_PRESTART_SEED olarak geçersiz sayıldı.
- REGIME_AWARE_FORWARD_V2 oluşturuldu.
- Forward başlangıcı: 2026-08-11T01:29:56.397166+00:00
- Yalnızca bu başlangıçtan sonra ilk kez toplanan haberler tahmin doğurabilir.
- Horizon 1h.
- Cutoff 0.249 frozen.
- Ağırlıklar sonuçlara bakılarak değiştirilmeyecek.

V2 temiz scoreboard ile başladı: settled=0, open=0.

### Otomasyon

Forward cycle tek akışa bağlandı:

NEWS COLLECT -> NEWS ANALYZE -> FORWARD SHADOW SETTLE/RECORD

Mac LaunchAgent kuruldu:

- Label: com.agca.forward-shadow
- Aralık: 5 dakika
- launchctl doğrulaması: runs=2, last exit code=0
- Log üzerinden otomatik cycle başarıyla doğrulandı.

### Coin Kapsamı

Takip listesi aliasları News Analyzer'a eklendi:

ARKM, FIDA, WLD, TIA, ETHFI, BIO, LQTY, ILV, SLP, UMA, IO

Ayrıca Binance Spot USDT ticker keşfi dinamik hale getirildi. Amaç sistemin yalnızca sabit coin listesine bağlı kalmaması ve yeni coin fırsatlarını da tespit edebilmesi.

### Bildirim ve Rapor Kararı

Telegram yardımcı altyapı olarak denendi ancak ana kullanıcı bildirim kanalı olarak gerekli olmadığına karar verildi.

ChatGPT görevleri yeniden aktif edildi:

- Günlük Kripto Brifingi: 11:00
- Kripto Kritik Gelişme Takibi: saatlik condition watch

Kritik gelişme takibi yalnızca gerçekten anlamlı gelişme olduğunda bildirim gönderecek; önemli olay yoksa sessiz kalacak.

### Ürün Kararları

- Araştırma ürünü geciktirmemeli.
- Forward validation paralelde veri toplamaya devam ederken ana yol haritası ilerlemeli.
- Yeni oturumlarda kullanıcı proje geçmişini yeniden anlatmayacak.
- Resmi hafıza dosyaları ve GitHub kodu başlangıç referansı olacak.

### Bir Sonraki Oturum

Önce Forward Shadow V2 durumunu kontrol et:

1. İlk gerçek open/settled forward tahminleri oluşmuş mu?
2. LaunchAgent hâlâ sağlıklı mı?
3. NEWS BASE vs MULTIFACTOR scoreboard nasıl gelişiyor?
4. Günlük ve kritik ChatGPT görevleri aktif mi?

Ardından ürün yol haritasında v0.6.0 Social Sentiment Agent'a geçiş zamanını belirle. Forward test paralelde çalışmaya devam etsin.

### Kullanıcıyla Çalışma Şekli

Teknik işlemlerde kullanıcıya her zaman tek sonraki adım verilecek. Bir adım tamamlanmadan diğerine geçilmeyecek. Kullanıcıdan proje geçmişini yeniden anlatması istenmeyecek.

---

## Oturum 2026-08-11 - v0.6.0 Social Sentiment Agent

### Başlangıç Durumu

Forward Shadow V2'nin ilk gerçek sonuçları kontrol edildi:

- NEWS BASE: settled=13, hit_rate=%23.1, directional_return=-%0.050
- MULTIFACTOR: settled=5, hit_rate=%20.0, directional_return=-%0.013

Örneklem küçük ve performans zayıf olduğu için frozen model değiştirilmedi; Champion üretim onayı verilmedi. Forward validasyon ana yol haritasını bloke etmeden paralelde devam edecek.

### Social Sentiment Agent

v0.6.0 ilk çalışan sürüm geliştirildi:

- Reddit geniş kripto topluluk akışı
- Tek istekte 100 paylaşım
- SQLite gözlem tablosu ve global tekilleştirme
- Başlık + içerik analizi
- Coin/ticker tanıma ve belirsiz ticker koruması
- 24 saatlik coin bazlı sentiment aggregate
- Örneklem, yönlü kanıt ve fikir birliğine bağlı güven puanı
- 15 dakikalık rate-safe cadence
- 5 dakikalık mevcut canlı ürün cycle entegrasyonu

### Test ve Düzeltmeler

- Çoklu RSS isteğinde oluşan Reddit 429 sorunu birleşik tek akışla giderildi.
- İlk testlerde kaynak etiketi değişiminden doğan yinelenen kayıtlar temizlendi ve external_id tekilleştirmesi kalıcı hale getirildi.
- Sıradan "link" kelimesinin LINK coini sanılması düzeltildi.
- Paylaşım sayısına bakarak üretilen yanıltıcı 100/100 güven hesabı kaldırıldı.
- Düşük örneklem ve çelişkili sentiment için muhafazakâr güven üretimi doğrulandı.
- Social cycle zorunlu testte SUCCESS verdi.
- 15 dakika dolmadan tekrar çalışma doğru biçimde SKIPPED verdi.
- Tam NEWS -> FORWARD SHADOW -> ALERT BRIDGE -> SOCIAL SENTIMENT cycle testi başarıyla tamamlandı.

### Ürün Kararı

- v0.6.0 tamamlandı.
- Düşük güvenli sosyal sonuçlar alarm veya işlem sinyali değildir.
- Social Sentiment ve Forward Shadow paralelde veri toplamaya devam edecektir.
- Sıradaki ana ürün modülü v0.7.0 On-chain Agent'tır.

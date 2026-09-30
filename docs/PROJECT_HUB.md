# AGCA Proje Merkezi

Bu dosya AGCA'nın tek giriş kapısıdır. Amaç geçmişi tek bir dev dosyada
tekrarlamak değil; kodu, kararları, kanıtı, operasyonu ve oturum geçmişini tek
kanonik çatı altında buluşturmaktır.

## Tek kaynak

Kaynak otoritesi: GitHub `kursadsalis-tech/AG-Crypto-Analyst`.
Kanonik runtime checkout: `/home/kursadsalis/AG-Crypto-Analyst-cloud`
(`agca-runtime`, GCP `project-6a717580-e37d-48f6-832`, `us-east1-b`).

Geliştirme, bu GitHub deposunun doğrulanmış branch/SHA'sından alınan bir Git
checkout'unda yapılır. Runtime yolu yerel makinede bulunmak zorunda değildir;
Work görevleri eski `/Users/...` checkout'larını aramamalıdır. ChatGPT proje
aynaları ve sohbetler yalnız bağlamdır, düzenlenecek kaynak depo değildir.
Bu hardening geliştirme checkout'u: `/Users/elifzilan/Developer/AG-Crypto-Analyst-hardening`.
Bu yol runtime veya genel gelecek-oturum varsayımı değildir.

## Belge yetki sırası

Birbiriyle çelişen bilgi görülürse aşağıdaki sıra uygulanır:

1. Kök `AGENTS.md`: değişmez çalışma, güvenlik ve ürün misyonu.
2. `docs/PROJECT_HUB.md`: proje haritası ve tek-çatı düzeni.
3. `docs/LAST_SESSION_HANDOFF.md`: en yeni tamamlanan iş ve canlı dış durum.
4. `docs/DECISIONS.md`: kabul edilmiş mimari ve ürün kararları.
5. `docs/PROJECT_STATUS.md`: bileşenlerin geniş durum özeti.
6. `docs/TASKS.md`: kanıt kapılı iş kuyruğu.
7. Tarihli handoff'lar ve `docs/SESSION_LOG.md`: tarihsel kayıt; güncel talimat
   değildir.

Git commit geçmişi, bir değişikliğin gerçekten kanonik koda girip girmediğinin
nihai teknik kanıtıdır. Bir sohbet iddiası Git/dosya/test kanıtıyla uyuşmuyorsa
tamamlanmış iş kabul edilmez.

## Ana hedef

AGCA; sabit coin listesinde haber yorumlayan bir rapor sistemi değil, piyasa
genelinde fırsat arayan, kamuya açık piyasa davranışını okuyan, teknik ve temel
bağlamı emir akışı, türev, tasfiye, on-chain, balina, makro ve kritik haber
kanıtlarıyla birleştiren, risk yöneten ve ölçülen sonuçlardan öğrenen uyarlanabilir
bir PAPER trader olmalıdır.

Bilginin yayılma hızının eşitlendiği ortamda haber etiketi tek başına yön veya
işlem üretmez. Ölçülmek istenen avantaj; olay öncesi konumlanma, ilk kalabalık
refleksi, likidite absorpsiyonu, tükenme, dayanıklılık ve tepkinin devam/tersine
dönüş yoludur. Bu yaklaşım gizli el veya manipülasyon varsayımı değildir.

Başarı; rapor sayısı, backtest, tahmin doğruluğu veya birkaç kazanç değil,
farklı piyasa rejimlerinde yeterli sayıda canlı PAPER işlemde komisyon, spread,
kayma, funding ve icra gerçekçiliği sonrası tekrarlanabilir getiri ile kontrollü
drawdown'dur. Gerçek para uzun vadeli hedeftir; kanıt kapıları ve kullanıcının
ayrı açık onayı olmadan gerçek emir mümkün değildir.

## Sistem haritası

| Katman | Kanonik yer | Görevi |
| --- | --- | --- |
| Piyasa ve dış kanıt | `data/` | Fiyat, davranış, haber, makro, on-chain ve balina gözlemi |
| Kalıcı tarih | `database/` | Otoritatif SQLite ve sınırlı Supabase audit mirror |
| Araştırma | `analysis/` | Yeniden üretilebilir, lookahead-safe ölçüm ve challenger çalışmaları |
| Karar | `decision_engine/` | Tahmin ve karar orkestrasyonu |
| PAPER yürütme | `paper_trading/` | Gerçekçi maliyet, risk, muhasebe ve öğrenme |
| Canlı süreç | `runtime/` | Bulut worker, attestation ve mirror drain |
| Operasyon | `deploy/`, `scripts/` | Service/timer, kurulum ve sınırlı operatör araçları |
| Doğrulama | `tests/` | Regresyon ve fail-closed sözleşme testleri |
| Kanıt çıktısı | `reports/` | Üreticisi/provenance'ı bulunan yeniden üretilebilir raporlar |
| Kurumsal hafıza | `docs/` | Kararlar, durum, görevler, runbook ve handoff |

## Mevcut birleşme durumu

- Kanonik Git geçmişi ilk çalışan ajanlardan davranış-önce observer dağıtımına
  kadar ürün kodunun esas gelişim çizgisini taşıyor.
- `docs/SESSION_LOG.md` ile tarihli handoff'lar eski oturumların önemli karar ve
  doğrulamalarını koruyor.
- ChatGPT proje aynasında görünen eski AGCA görevleri bağlam-only kabul edildi;
  içlerindeki kod kanonik Git'te yoksa otomatik olarak içeri alınmayacak.
- `Documents/Codex/...` altındaki eski referenced-conversation checkout'ları da
  aynı şekilde arşiv/provenance kaynağıdır; kanonik depo ile dosya birleştirmesi
  yapılmayacak.
- Sohbet başlıkları tek başına başarı kanıtı değildir. Yeni ve maddi bir iddia
  bulunursa önce kanonik Git, SQLite şeması, rapor provenance'ı ve testlerle
  karşılaştırılacak; yalnız doğrulanan eksik bilgi uygun `docs/` belgesine
  işlenecek.

Bu düzen geçmiş sohbetleri silmez veya arşivlemez. Onların dağınık kaynak kod
otoritesi olmasını sona erdirir.

## Güncel çalışma noktası

Main tabanı: `56f85d11a757732bfbf34307ebf39aa3b23fb814`.
Hardening branch'i `codex/agca-comprehensive-hardening`, PR #103'ün
`b30f18e48e1b389620d56b51fefba4262d678da2` head'ini kapsar. #103 merge edilmeyecek.
Yeni adayın test/PR kanıtı `LAST_SESSION_HANDOFF.md`, canary protokolü
`HARDENING_CANARY.md` içindedir. Bu değişiklik runtime'a dağıtılmadı; canlı
sağlık veya ekonomik performans kabulü yapılmış sayılmaz.

## Her çalışma için kısa yönlendirme

1. Kanonik dizini ve Git durumunu doğrula.
2. Kök `AGENTS.md`, bu merkez ve `docs/LAST_SESSION_HANDOFF.md` dosyalarını oku.
3. İşi `docs/TASKS.md` ve kabul edilmiş kararlarla karşılaştır.
4. Sohbet/ayna bilgisini yalnız provenance olarak kullan; doğrulanmamış kodu
   kopyalama.
5. Test, diff, credential taraması, handoff ve tek checkpoint protokolünü uygula.

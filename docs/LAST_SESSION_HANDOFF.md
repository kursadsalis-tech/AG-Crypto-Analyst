# Latest AGCA Session Handoff

Aktif devam dosyası: [SESSION_HANDOFF_2026-08-24.md](SESSION_HANDOFF_2026-08-24.md)

Yeni oturum önce bu dosyayı, sonra `PROJECT_STATUS.md`, `TASKS.md` ve
`CHANGELOG.md` dosyalarını okumalıdır.

Güncel devam konusu: Kullanıcının PAPER ajanının trade-learning süreci
hakkındaki fikirleri. 2026-08-26'da kamuya açık Bybit/OKX copy-trader kanıtları
için ayrı, lookahead-korumalı araştırma karantinası uygulandı. Dış kanıt temiz
PAPER P&L kanıtına karışmaz; otomatik kopya, politika değişikliği, terfi ve gerçek
emir veremez. Sıradaki iş, yalnız izinli/resmî erişim yolu doğrulanmış kaynaktan
ilk tam liderlik tablosu cohort'unu toplamaktır. Ayrıntılı mevcut sözleşme ve son
doğrulanmış durum aktif handoff dosyasının `2026-08-25 — Tam devam noktası`
bölümündedir. Kullanıcı konuşmadan mevcut PAPER eşikleri değiştirilmemelidir.

2026-08-26 bulut doğrulaması: katman Google Cloud `agca-runtime` worker'ına
geri alınabilir yedekle kuruldu; hedefli 18/18 ve yerel tam 89/89 test geçti.
Servis `active/running`, ortam `AGCA_EXECUTION_MODE=PAPER_ONLY`; doğal döngü üç
yeni tabloyu oluşturdu ve `COLLECTING_UNBIASED_TRADER_COHORT` (0 trader, 0
kapanan, 0 gün, mutasyon=0, terfi=0) kaydetti. Bybit/OKX'in belgeli copy-trade
API yolları özel/hesap erişimli; kamu liderlik sayfasına tarayıcı erişimi yönetim
güvenlik politikası tarafından engellendi. Güvenlik kontrolü aşılmadı ve arama
sonuçlarından winner-only örnekler cohort diye alınmadı. İlk gerçek toplama,
yalnız izinli/resmî tam-cohort erişimi doğrulanınca başlatılmalıdır.

Bu engel aynı oturumda aşıldı: Google Cloud'dan OKX'in kendi kamu sayfasının
kullandığı kimlik doğrulamasız web endpoint'i doğrulandı. Günlük timer aktif;
ilk koşu tek `dataVersion` ile 255/255 trader, 594 açık ve 1.870 kapanmış işlem
gözlemi kaydetti. Kapanmışların 313'ü kaynak+maliyet+notional+<=5x kapısından
geçti fakat 1.870 geçmiş kaydın tamamı `retrospective_only=1`. Durum artık
`COLLECTING_FORWARD_TIME_COVERAGE`: bugünden itibaren açıkken ilk görülen
pozisyonlar kapandıkça gerçek forward dış-trader kanıtı oluşacak. Mutasyon ve
terfi 0, gerçek emir kapalıdır.
# 2026-08-26 continuation point

Implemented `paper_trading/continuous_improvement.py` and integrated it with the OKX collector and PAPER trade-learning review. It creates trader-diversified retrospective hypotheses, freezes a forward start time, evaluates only positions known open before that time and closed afterward, and records one-trader/one-vote consensus. It has no execution or policy mutation authority. Local full suite: 98/98 passing. Next operational step: confirm the deployed collector creates candidates/consensus and continue accumulating forward closures for orchestrator review.
# 2026-08-26 — virtual-capital application enabled

The user's intended learning contract is now explicit: learned strategies must be applied in PAPER trading, not merely reported for review. `paper_trading/strategy_allocator.py` creates baseline/LONG/SHORT virtual sleeves inside the same 10,000 USDT portfolio. Eligible AGCA forecasts are assigned to compatible sleeves; allocation changes only after at least 10 closed evidence-eligible trades and uses net-after-all-cost return. No real-order authority exists. User-facing reporting should prioritize portfolio trades, net P&L, equity, and return—not research-candidate narration.

# NEXT SESSION — CLEAN CHECKPOINT

Canonical checkout: `/Users/elifzilan/Documents/AG-Crypto-Analyst`.
Do not develop in the ChatGPT project mirror. Read the root `AGENTS.md` and
`docs/REPOSITORY_HYGIENE.md` before changing files.

Supabase verification incident is resolved as of 2026-08-26:

- Root cause was a quota-threatening `crypto_news_analysis` mirror inflated to
  384,317 rows plus a stopped remote-drain timer; the VM already used the IPv4
  Supabase Session Pooler.
- The remote table was rebuilt exactly from 750 canonical SQLite rows. Database
  size fell from 425 MB to 348 MB and read/write mode was restored.
- Critical evidence uses a larger bounded drain batch. The persisted 524-row
  attestation backlog drained in one run; the two-minute drain timer is active.
- A fresh connector-only run, without Google Cloud SSH, verified attestation
  freshness and canonical SHA-256, `HEALTHY`, `PAPER_ONLY`, no real-order
  authority, SQLite `ok`, hourly/PAPER `SUCCESS`, and exact position/portfolio
  reconciliation. Result: `BULUT DOĞRULANDI`.
- Daily automation `agca-g-nl-k-orkestrat-r-zeti` is active again. The obsolete
  repeated 03:00 delivery automation remains paused.
- Full local suite: 103/103 passing.

Repository hygiene is also restored. The completed work is recorded on local
branch `checkpoint/agca-20260826-supabase-recovery`. Start future development
from this checkpoint, keep one coherent change per commit, and update this
handoff at the end of operational sessions.

The user made session closeout a permanent requirement. Every future
implementation session must update this handoff, leave Git clean or at one
named checkpoint, and finish by giving the user the canonical project path plus
one exact copyable next-session opening prompt. Missing/empty project files must
not be diagnosed before verifying the canonical checkout and Codex project
mapping.

Next product work remains PAPER trade-learning/strategy evidence accumulation.
Do not change thresholds, grant real-order authority, or promote a challenger
without the existing evidence gates and explicit user direction.

# 2026-08-27 — Codex project mapping and closeout convention

The canonical checkout is now registered in the Codex desktop app as the local
project **AGCA**, mapped to `/Users/elifzilan/Documents/AG-Crypto-Analyst` and
recognized as a Git repository. The separate **AGCA System** entry is a ChatGPT
project mirror and must not be used for implementation or file diagnosis.

The user phrase **“oturum bitti”** is the permanent closeout command. On receipt,
apply the root `AGENTS.md` end-of-session procedure automatically: update this
handoff, verify repository state and proportionate checks, leave a clean tree or
one named checkpoint, and provide one exact next-session opening prompt. Do not
ask the user to repeat the longer closeout instruction or perform unnecessary UI
steps.

Exact next-session opening prompt:

> AGCA yerel projesinde çalış. Önce kök AGENTS.md ile
> docs/LAST_SESSION_HANDOFF.md dosyasını oku, Git durumunu kontrol et ve NEXT
> SESSION bölümündeki PAPER trade-learning/strategy evidence accumulation
> işinden devam et. Kanıt eşiklerini değiştirme ve gerçek emir yetkisi verme.

# 2026-08-27 — forward closure accumulation checkpoint

PAPER external-trader evidence accumulation içinde operasyonel bir boşluk
düzeltildi. OKX collector daha önce history endpoint'ini yalnız hiç CLOSED kaydı
olmayan trader'lar için çağırıyordu. Bu, retrospektif geçmişi daha önce alınmış
bir trader'ın AGCA tarafından açıkken gözlenen pozisyonu sonradan kapandığında
kapanışın yeniden görülememesine ve forward kanıtın birikmemesine yol
açabiliyordu.

Collector artık bilinen açık pozisyon sahiplerini değişmeyen günlük 30-profile
history bütçesinde önceliklendiriyor. Açık trader sayısı bütçeyi aşarsa günlük
deterministik rotasyon hepsini günlere yayıyor; boş kapasite ilk retrospektif
cohort backfill'ine devam ediyor. İlk-görülme zamanı, <=5x/maliyet/notional
kapıları, 30 trader/200 kapanış/56 gün dış-kanıt eşiği, forward candidate kapıları
ve PAPER allocation eşiği değiştirilmedi. Otomatik terfi/politika mutasyonu ve
gerçek emir yetkisi hâlâ yoktur.

Doğrulama: tam yerel suite 105/105 geçti. Bulut dağıtımı bu yerel oturumda
yapılmadı; sıradaki tek öncelik bu checkpoint'i mevcut rollback prosedürüyle
`agca-runtime` üzerine dağıtmak, collector timer'ının başarılı koşusunu ve
forward kapanış sayacının zaman içinde ilerleyebildiğini doğrulamaktır. Mevcut
bulut/Supabase sağlık durumu bu oturumda yeniden sorgulanmadı; son doğrulanmış
durum önceki CLEAN CHECKPOINT bölümündeki gibidir.

Canonical checkout: `/Users/elifzilan/Documents/AG-Crypto-Analyst`

Exact next-session opening prompt:

> AGCA yerel projesinde çalış. Önce kök AGENTS.md ile
> docs/LAST_SESSION_HANDOFF.md dosyasını oku ve Git durumunu kontrol et. Son
> forward-closure accumulation checkpoint'ini rollback korumasıyla buluta dağıt;
> OKX collector timer, PAPER_ONLY, gerçek emir kapalı durumu ve forward kanıt
> sayaçlarını doğrula. Kanıt eşiklerini veya strateji politikasını değiştirme.

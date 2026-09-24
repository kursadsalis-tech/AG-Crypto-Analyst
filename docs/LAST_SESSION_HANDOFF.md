# 2026-09-24 — Observer/SQLite live acceptance remains open

Canonical worktree `.worktrees/observer-phase-profile`, branch
`fix/observer-phase-profile`, draft PR #94. Preserve the dirty primary checkout.
VM checkout is candidate `0f1948b`; canonical main is `ed39047` pending acceptance.
PAPER_ONLY, Binance canonical, canonical DB `/var/lib/agca/crypto_data.db`.

Measured and repaired: observer historical counts consumed 46.466 s of a
48.263 s persistence phase; WS close took 5.5–9.8 s. Live paths omit counts,
read causal evidence without mirror DDL, bound WS close, and expose SQL/WS
phases. Observer is continuously supervised (old timer retired). Technical
writer uses bounded transactions; technical seed/tail reads use existing index
order; insufficient contiguous bars are rejected before reading full history.
Prior full regression passed 505 tests; subsequent focused suites and PR CI pass.

Acceptance is NOT complete: on Sep 24 08:33 observer total reached 178.374 s,
persistence 69.811 s. At 08:35 total 119.504 s, persistence 8.732 s, 12/12
eligible. Realtime restarted on 0f1948b and connected; startup SQLITE_BUSY
occurred in legacy summary setup. Selection advances, latest blocker is
PORTFOLIO_VALUATION_STALE_OR_MISSING. Do not weaken this or freshness gates.

New direct profiling: technical writer held WAL writer lock in 75/120 samples
across 30 seconds, while trade learner read 38 MB from disk. Bounded read-only
profiling of learner isolated ~18 s legacy-audit scan and ~25 s position-review
scan before another eligible-history scan exceeded the 50 s budget. Existing
immutable legacy audit was already present. Current change reuses that audit,
drives reviews from frozen theses, indexes eligible positions, and runs worker
bootstrap once per process instead of each learning cycle. Deploy and measure
before declaring success. Profiling always restored the learning service.

Spot acceptance already passed using production Binance transport: 5 complete
BTCUSDT states, quote ages 123/123/354 ms, vision fallback, zero stale receives,
no DB mutation or order authority. Latest realtime health also confirms Spot
and USD-M canonical WS ingest. Do not repeat this acceptance without a new need.

Next: finish acceptance of fresh eligible observer minutes, regular selection,
no SQLITE_BUSY/stall; investigate current actionable blocker with live evidence.
Then clean final handoff/PR, merge and return VM to canonical main. Do not rerun
finished work or remove existing user changes.

---

# 2026-09-23 — P1 shadow technical context live-accepted

P1 is live-accepted on the canonical VM as observation-only technical context.

Accepted runtime state:
- process/checkout SHA:
  `22debf3daa5f6d242aa4e3aecc1e73a12f531fe0`
- worker: `CONNECTED`
- live stale symbols: none
- risk policy: `NORMAL`, new positions allowed, source errors empty
- canonical risk timer active
- legacy `agca-binance-risk-refresh.timer` masked
- deployment-local `crypto_data.db` absent
- PAPER_ONLY / real orders disabled

P1 context uses the already-fetched 169 closed 1h risk candles. It computes the
existing advanced 1h feature pack plus completed 4h structural context with zero
additional market-data requests. The latest context per symbol is local-only and
is not added to the Supabase runtime mirror. A fresh context can be frozen into
a selected PAPER thesis after selection, but it is explicitly not a direction,
ranking, confidence, sizing or stop input.

Live cost benchmark: 23 contexts consumed 119.863 ms total in one 15-minute risk
support cycle; the latest-cache checkpoint contained 25 symbols, 124.826 ms
aggregate compute and 22.353 ms maximum per-symbol compute. This is outside the
realtime decision hot path.

Acceptance also closed two support defects: a legacy 15-minute risk timer could
recreate a local DB with old path semantics, and transient SQLite writer
contention could be mislabeled as BTC cross-source validation failure. The old
timer is retired/masked, canonical risk refresh is env-bound, risk snapshots
older than 30 minutes fail closed, and risk support now waits up to 30 seconds
for transient SQLite contention while genuine source failures still fail closed.

Next gate: collect natural forward PAPER theses carrying
`technical_context_observed` and evaluate realized net-after-cost outcome by
technical alignment/structure. Do not grant technical authority from backtest
evidence, retrospective fit or a small forward sample.

---

# 2026-09-23 — P0 live acceptance complete

P0 live acceptance is complete on canonical VM `agca-runtime`.

Accepted runtime evidence:
- canonical checkout and worker process source:
  `483a8b5d9c818cf60d5f717665d14e124b8be69b`
- worker health: `CONNECTED`
- execution mode: `PAPER_ONLY`
- real orders: disabled
- worker checkout dirty state: false
- process CWD: `/home/kursadsalis/AG-Crypto-Analyst-cloud`
- zombie IOSTUSDT SPOT pending intent was cancelled by realtime housekeeping
- no failed systemd units were observed; canonical timers were active

Three operational defects were discovered and fixed during acceptance rather than
papered over: installer self-update could continue executing stale script text,
checkout SHA could masquerade as running-process SHA, and realtime pending intents
could survive indefinitely when no market callback arrived.

Supabase mirror state lagged canonical SQLite by a few minutes at the acceptance
checkpoint. Canonical SQLite remains authoritative; mirror freshness is a
separate operational item and must not be confused with runtime health.

Next product step: P1 research gate. Inventory and benchmark the existing causal
technical/multi-timeframe feature infrastructure, then design the smallest
candidate-only integration that improves decision context without making 4h a
direction authority or adding material hot-path latency/cloud cost.

---

# 2026-09-22 — P0 execution/economics correctness checkpoint

Canonical `main` now contains the five mandatory P0 repairs discovered from
the 22 September live PAPER audit:

- `c4e393229b0b579288162c36d8e3153d8c26c438`: stop-risk evidence plumbing;
- `2c26912ca852d074614bb8919f755d667dd26c88`: evidence-mature
  negative-expectancy suspension;
- `6b25fb41017b2cebf28339e46c2d46b3948a7eb9`: evidence-aware adverse
  same-setup re-entry gate;
- `0bd91c3b82bc938163b10327faff1695440d0b75`: pending SPOT subscription and
  runtime-source attestation;
- `42645f47a196224f0ce860dd55f61c30f2c90851`: venue-specific shared PAPER
  fee economics.

The last P0 PR passed 107 focused PAPER integration tests and 384 broad offline
regression tests. One first-attempt broad-suite failure was a newly added test
reading a nonexistent thesis column; the product patch was not merged until the
test was corrected to inspect `thesis_json.execution_risk` and the full suite
passed.

No claim is made that `42645f47...` is deployed on the canonical VM. The next
operational step is one controlled deployment to `agca-runtime`, followed by a
fresh cloud attestation proving the exact git SHA/clean checkout and runtime
health. Do not begin P1 decision-path changes on the VM before this P0 acceptance
is complete.

After P0 live acceptance, P1 is research-gated: reuse existing causal technical
and multi-timeframe feature infrastructure for candidate-only analysis, benchmark
latency/resources first, keep 4h as context rather than direction authority, and
do not promote historical technical/multi-timeframe research models merely
because they already exist.

---

# 2026-09-21 — Atomic realtime JSON writer race fix

Current priority supersedes the historical instructions below.
Base: main `b0348cd`; branch: `fix/atomic-json-write`.
Canonical repository: `/Users/elifzilan/Documents/AG-Crypto-Analyst`.
Isolated worktree: `/Users/elifzilan/Documents/AG-Crypto-Analyst-worktrees/atomic-json-write`.
Pre-existing canonical checkout changes were preserved.

`_atomic_json_write` now creates a unique temporary file in the destination
folder, writes the same JSON format with mode 0600, closes it, then atomically
replaces the destination. Best-effort cleanup preserves the original exception.
No PAPER_ONLY, trading, decision, or execution-authority behavior changed.

Validation: 364 local tests passed. The synchronized eight-writer regression
fails against b0348cd with FileNotFoundError and passes with this fix, verifying
complete final JSON, mode 0600, and no leftover temporary files. An injected
replace failure preserves the existing destination and cleans up the temp file.
CI must be green on the final PR head before merge. No VM deployment was made;
after merge, deployment and fresh worker health remain operational acceptance.

--- Historical session records ---

# 2026-09-21 — PR #25 quote freshness regression: flow thread isolation

Current work supersedes the historical next-session priorities below.
Base: main `40d07643f06d9be9d2a47f4d39e4f68764dbfd16`.
Branch: `fix/quote-flow-thread-isolation`.
Canonical repository: `/Users/elifzilan/Documents/AG-Crypto-Analyst`.
Isolated Git worktree: `/Users/elifzilan/Documents/AG-Crypto-Analyst-worktrees/quote-flow-thread`.
The canonical checkout contains pre-existing staged/unstaged work; it was not altered.

Raw USD-M trade and SPOT aggregate-trade sockets now run in a dedicated thread
with their own asyncio event loop. They publish immutable flow-only snapshots
through a locked latest cache and never dispatch PAPER decisions. Dynamic
subscription input is copied from the main loop; worker/database callbacks are
never invoked by the trade thread. Quote sockets remain on the main loop and
snapshot flow on accepted quotes. Missing/stale flow cannot support a thesis;
flow cannot refresh quote, liquidity, or market timestamps. Task cancellation
joins the flow thread; socket tasks are cancelled before reconnecting.
PAPER_ONLY, execution authority, and stale-state rejection are unchanged.

Validation: full local offline suite passed 359 tests before the final two
regression cases; final focused realtime suite passed 21 tests. Tests cover
quote progress under a blocked trade loop (under one second), distinct thread
and event-loop ownership, flow-only cache updates with no decision callbacks,
immutable/market-separated/expired snapshots, stale queued quote rejection,
and cancellation without a surviving trade thread. Final full CI must pass
on the PR head before merge. No VM deployment or live quote-age measurement
has been performed in this task; those remain the operational acceptance step.

--- Historical session records ---

# Latest AGCA Session Handoff

# 2026-09-02 — Oturum kapanışı: doğal SPOT ve mobil teslimat kanıtı

Bu oturumun maddi uygulaması tamamlandı. Checkpoint `9c33d52` per-trade SPOT
PAPER yolunu, `f53c8a8` dinamik türev risk veri yolunu içerir; canlı kabul ve
operasyon kanıtları `43b70bf` handoff kaydındadır. Yerel tam suite 308/308 geçti,
çalışma ağacı temizdir. Aktif dal
`checkpoint/agca-20260826-supabase-recovery`, son commit `43b70bf`.

Son canlı incelemede ana worker ve realtime worker active; realtime health 02
Eylül 2026 18:26:17 İstanbul (15:26:17 UTC) anında CONNECTED/HOLD ve
PAPER_ONLY idi. Yaşam boyu portföyde 99 CLOSED, 6 OPEN USD-M pozisyon, 74
işlem-sonrası deneyim incelemesi ve 50 `COLLECTING_EVIDENCE` öğrenme incelemesi
vardı. Kasa 10.000 USDT başlangıç, 9.874,2064 USDT cash, +178,9885 USDT
gerçekleşmiş net P&L ve 47,5883 USDT fee; pending funding ve gerçek-emir event
0. Promotion/skill-proven/policy-mutation yetkileri kapalıdır.

Tamamlanmış gibi sunulmaması gereken iki doğal/operasyonel kanıt kalır:

1. Deployment öncesinden gelen altı açık USD-M pozisyon kapasiteyi doldurduğu
   için ilk doğal 1x SPOT LONG henüz seçilmedi. Kapasite boşaldığında seçimin
   SPOT market type, Binance Spot giriş kanıtı, OKX Spot gerçek-zamanlı yönetim,
   sıfır funding ve sıfır liquidation ile kapanış/öğrenmeye ilerlediği
   SQLite'tan doğrulanmalıdır.
2. Günlük raporlar SQLite'ta üretiliyor fakat son kayıtlar
   `remote_mirror_status=PENDING`, `delivery_complete=0`; mobil push teslimatı
   henüz kanıtlanmadı. Ayrı AGCA sağlık görevindeki bir sonraki 11:15 İstanbul
   koşusunda raporun bu geliştirme oturumuna yazılmadığı ve telefona push olarak
   ulaştığı gözlenmelidir. Ana worker yeniden başlatma sonrası active/running
   olsa da kapanış anında yeni tam-cycle attestation henüz oluşmamıştı; uzun
   süre STARTING kalırsa piyasa bekleyişi değil operasyon arızası sayılmalıdır.

Canonical checkout:
`/Users/elifzilan/Documents/AG-Crypto-Analyst`. Codex'te başka proje/mirror
seçilmemeli; yalnız bu yerel canonical checkout kullanılmalıdır.

Exact next-session opening prompt:

> AGCA yerel projesinde çalış: /Users/elifzilan/Documents/AG-Crypto-Analyst.
> Kök AGENTS.md, docs/PROJECT_HUB.md ve docs/LAST_SESSION_HANDOFF.md dosyalarını
> tamamen oku; Git durumunu kontrol et. Önce deployment sonrası taze tam-cycle
> attestation'ı doğrula. Ardından kapasite boşaldığında ilk doğal 1x SPOT LONG
> işlemin seçim, Binance Spot dolum, OKX Spot yönetim, fonlama/likidasyon yokluğu,
> kapanış ve sonuç-kör öğrenme zincirini canlı SQLite'tan izle. Bir sonraki 11:15
> İstanbul günlük raporunun geliştirme oturumuna yazılmadığını ve mobil push
> teslimatını doğrula. Unified Forecast'ı yürütmeye bağlama; tek yaşam boyu kasa,
> PAPER_ONLY ve gerçek-emir kapılarını koru. Yeni maddi arıza yoksa kod değiştirme.

# 2026-09-02 — Per-trade SPOT PAPER yolu canlıya alındı

Düşük güvenli tek-kaynak LONG öğrenme deneyi artık gerçek `SPOT` market type
ile açılır. Tek-kaynak SHORT 1x USD-M'de kalır; bağımsız sinyal sayısı arttıkça
mevcut 2x-5x USD-M risk kademesi değişmeden uygulanır. Bu karar sonuç-kördür ve
Unified Forecast yürütme girdisi değildir.

SPOT pozisyon; doğrudan Binance Spot fiyat/24h likidite kanıtı, hash-doğrulamalı
Binance Spot exchangeInfo filtresi ve OKX `BASE-USDT` spot WebSocket ticker,
books5 ve trades akışı kullanır. SWAP akışıyla karışmaz. SPOT kapanışında funding
segmenti ve liquidation threshold üretilmez; maliyet, stop, TP1/TP2, trailing ve
tek yaşam boyu kasa muhasebesi aynen çalışır. Sembol spotta resmî ve taze işlem
kuralına sahip değilse pozisyon dolmadan açık nedenle CANCELLED olur. Gerçek
emir yolu eklenmedi.

Checkpoint `9c33d52`; tam yerel suite 308/308 geçti. Sekiz canlı çalışma
dosyasının SHA-256 değeri yerelle birebir eşleşir. Rollback:
`/var/lib/agca/rollback-9c33d52-spot-paper-20260902T1525Z`. İlk dağıtım adımı
spot metadata kasası yokken fail-closed kaldı ve realtime servis başlamadı;
resmî Binance Spot endpoint'inden 1.362 işlem kuralı doğrulanıp
`/var/lib/agca/exchange-info-spot.json` oluşturulduktan sonra iki worker da
active oldu.

Bellek içi canlı BTCUSDT SPOT kabulü 02 Eylül 2026 18:23:35 İstanbul
(15:23:35 UTC) anında PASS: OKX SPOT book/taker, stop initialization ve TP1
kısmi realizasyonu çalıştı; `authoritative_database_mutated=false` ve bütün
eventlerde `real_order_allowed=0`. 02 Eylül 2026 18:23:34 İstanbul
(15:23:34 UTC) gerçek worker health CONNECTED/HOLD idi. Otoritatif kasa:
10.000 USDT başlangıç, 9.874,2064 USDT cash, +178,9885 USDT gerçekleşmiş net
P&L, 47,5883 USDT fee; pending funding 0, gerçek-emir event 0. Mevcut altı açık
pozisyon deployment öncesi USD-M'dir; ilk doğal SPOT seçim henüz kapasite
boşalınca oluşacaktır ve sonucu ayrıca ölçülmelidir.

# 2026-09-02 — Dinamik risk veri yolu ve mobil rapor ayrımı

Canlı `product_cycle_status=PARTIAL` kök nedeni gizlenmeden kapatıldı. Dinamik
OKX SWAP davranış evrenindeki `LITUSDT` ve `ZORAUSDT`, risk katmanında yanlış
biçimde Binance Spot mumlarına yönlendiriliyordu; bu nedenle LIT için
`LATEST_CANDLE_STALE`, ZORA için HTTP 400 oluşuyordu. Sabit 13 sembollü küresel
referans sepeti spot kaynağını korur. Dinamik SWAP fırsatları artık önce Binance
USD-M, orada yoksa OKX SWAP kapalı 1h mumlarıyla ölçülür. İki resmî kaynak da
yoksa yalnız ilgili varlık fail-closed kalır. Risk/gerçek-emir kapıları
gevşetilmedi.

Checkpoint `f53c8a8` için yerel tam suite 304/304 geçti. Dosya canlı
`agca-runtime` worker'a dağıtıldı; yerel/canlı SHA-256
`f7279b8febc79eb9a0e95fa0291b19f781cc4de7adc474a13e802a99d3ecb60e`
eşleşir. Rollback:
`/var/lib/agca/rollback-f53c8a8-risk-data-20260902T1110Z`. Kontrollü canlı risk
çevrimi 02 Eylül 2026 14:09:35 İstanbul (11:09:35 UTC) anında `SUCCESS`,
coverage 13/13, market state NORMAL ve source errors 0 kaydetti. Ana worker
yeniden başlatıldı ve doğal uzun ürün çevrimi sürerken realtime worker active;
02 Eylül 2026 14:13:21 İstanbul (11:13:21 UTC) sağlık kaydı CONNECTED/HOLD,
XRPUSDT, PAPER_ONLY ve real_order_allowed=false idi. Çevrim tamamlanmadan yeni
attestation'ın `SUCCESS` olduğu varsayılmamalıdır.

Günlük Codex özeti bu geliştirme oturumundan ayrılarak ayrı mevcut AGCA sağlık
görevine taşındı; günlük 11:15 İstanbul planı ACTIVE ve bildirim politikası
açıktır. Beş dakikalık arıza görevi de ayrı sağlık görevinde kalır. Rapor
sözleşmeleri düzeltildi: OPEN/PENDING yokken eski IDLE aksiyon zamanı realtime
arızası değildir; `PARTIAL` etiketi tek başına maddi alarm değildir, somut
SQLite/PAPER/servis etkisi gerekir. Codex görevi ile ChatGPT Scheduled Task aynı
ürün değildir; cihaz push izni/teslimatı bu depodan doğrulanamaz. Bir sonraki
doğal raporda ayrı görev görünümü ve telefon push teslimatı gözlenmelidir.

Sıradaki ürün işi, düşük güvenli LONG deneyleri için gerçek per-trade SPOT
PAPER yolunu; spot fiyatı, spot exchange metadata, fonlama yokluğu ve
likidasyon yokluğuyla uçtan uca kurmaktır. Mevcut düşük risk kademesi hâlâ 1x
USD-M'dir; SPOT tamamlanmış gibi raporlanmamalıdır.

# 2026-09-02 — Bağımsız sinyal ve 1x-5x PAPER risk kademesi

Kullanıcının açık onayıyla PAPER öğrenme yolu `PAPER_BEHAVIOR_DECISION_CORE_V3`
ve `PAPER_BEHAVIOR_POLICY_V3_RISK_TIERED_LEARNING` olarak ayrıldı. Maliyet
sonrası pozitif ve sonuç-kör geçmiş hipotezi bulunan PRICE_ACTION, saf
MARKET_BEHAVIOR/tahta-akış veya causal external event (haber, makro, on-chain,
balina vb.) tek başına küçük PAPER deney açabilir. Grafik, haber ve tahtanın aynı
anda onayı artık giriş şartı değildir; aynı yönlü bağımsız birleşme yalnız
boyut/kaldıraç girdisidir. Yinelenen ve ters kaynaklar güven sayısını büyütmez.

Yeni `PAPER_RISK_TIER_1..5` tek kaynakta 1x ve özsermayenin %0,5'i marjinle
başlar; bağımsız sinyal ve promotion adımlarıyla en fazla 5x ve %2 marjine çıkar.
Marjin ile brüt maruziyet SQLite'ta ayrıdır. Kasa marjin + brüt giriş ücretini
ayırır; miktar, fee, spread, slippage ve funding brüt maruziyetten hesaplanır;
kapanış kalan marjini ve maliyet sonrası P&L'yi döndürür. Likidasyon eşiği gerçek
leverage ile oluşturulur; stopu aşan gap eşiği geçerse liquidation fee ve LOSS
öğrenmeye yazılır. Leverage ve risk-tier kohortları öğrenme gruplarına eklendi.

Yerel tam suite son düzeltmelerle 301/301 geçti. Canlı OKX WebSocket + canlı resmî exchange
metadata ile yalnız bellek içi 5x BTCUSDT kabulü 02 Eylül 2026 13:31:56 İstanbul
(10:31:56 UTC) anında PASS oldu: stop kuruldu, TP1 kısmi realizasyonu ve maliyet
muhasebesi çalıştı; otoritatif SQLite değişmedi ve `real_order_allowed=false`
kaldı. Checkpoint `da7a602`, takip düzeltmeleri `0e00b89` ve `227ad8f` canlı
worker'a rollback korumasıyla dağıtıldı. Dört ana çalışma dosyasının SHA-256
değerleri yerelle birebir eşleşir. Eski pozisyonların tamamı 1x ve
`margin_allocated=notional` taşındı; migrasyon uyumsuzluğu 0'dır. 02 Eylül 2026
13:44:21 İstanbul (10:44:21 UTC) itibarıyla ana ve realtime worker active,
realtime health IDLE, açık pozisyon 0, pending funding 0 ve gerçek-emir olayı
0'dır. İlk doğal V3 seçim sonucu henüz oluşmadı; aşağıdaki çevrimler açık
machine-readable nedenle sıfır kaldı ve bu eksiklik başarı gibi sunulmamalıdır.

Dağıtım sonrası ilk doğal V3 çevrimleri taze veri üzerinde
`NO_ACTIONABLE_PATTERN` üretti; sonuç-kör eşleşme yokken işlem uydurulmadı.
Bu kontrolde eski gerçek-zamanlı ters-tez sayacının haber-only/grafik-only
işlemleri de book/taker ile kapatacağı görüldü. Kaynak bağlamı yönetime taşındı:
saniyelik flow invalidasyonu yalnız tek ve bütün tezi `MARKET_BEHAVIOR` olan
pozisyonda çalışır; NEWS, PRICE_ACTION veya çoklu kaynak tezi ilgisiz tahta
işaretiyle üç saniyede kapanmaz. Tam suite bu düzeltmeyle 301/301 geçti.
PRICE_ACTION geçmişi ayrıca aynı dakikadaki çoklu haberlerle çoğaltılmaz; yalnız
örtüşmeyen dört saatlik `MARKET_STRUCTURE` bağımsızlık birimlerinden öğrenilir.

Rollback dizinleri:
`/var/lib/agca/rollback-da7a602-risk-tiered-leverage-20260902T1036Z`,
`/var/lib/agca/rollback-0e00b89-independent-thesis-20260902T1042Z` ve
`/var/lib/agca/rollback-227ad8f-price-independence-20260902T1043Z`.

# 2026-09-02 — Gerçek zamanlı çıkış seli giderildi ve canlı kabul geçti

İlk canlı kabulde süre sonu kapanışlarının OKX olay hızıyla tekrarlandığı ve
reddedilen denemelerin dahi fonlama segmenti ürettiği görüldü. Worker hemen
durduruldu; 71 BTCUSDT ve 72 ENAUSDT üzerinde oluşan olaylar incident kanıtı
olarak korundu, silinmedi. Kök nedenler; çıkış retry'sinin sınırsız olması,
fonlama segmentinin dolumdan önce yazılması, reduce-only bakiyelerin minimum
notional/görünen likidite kurallarıyla yeni emir gibi ele alınması ve SHORT
kapanışta adverse BUY fiyatının son miktar adımını sıfıra yuvarlamasıydı.

Checkpoint'ler `b380bc5`, `921d0e9`, `fd1dc2d` ve `8cab224` ile çıkış retry'si
saniyede en fazla bire indirildi; throttled olay kalıcı audit seli üretmez;
yalnız gerçek dolum fonlama segmenti yaratır; pending segmentler pozisyon başına
tek resmî funding çağrısıyla ve olay anındaki miktarla topluca uzlaştırılır.
Reduce-only son bakiye minimum notional altında kalabilir, gerçekten görünen
defter likiditesini kullanabilir ve SHORT adverse fiyat tamponu sonrası mevcut
miktardan fazla kapatılamaz. Retler artık genel etiket yerine exact filtre
nedenini korur. Yerel tam suite 293/293 geçti.

Canlıdaki 1.622 eski pending segment silinmeden iki kaynak çağrısıyla çözüldü.
BTCUSDT #71 son 0,001 SHORT bakiyesi 02 Eylül 2026 08:47:16 İstanbul
(05:47:16 UTC) anında 77.745,6 dolumla kapandı ve funding uzlaştırıldı. ENAUSDT
#72 de kapalıdır. Ardından doğal MARKET_BEHAVIOR_CORE seçimleri AAVEUSDT #76,
ENAUSDT #77 ve ETHUSDT #78 aynı dakika içinde doldu, gerçek zamanlı ters tezle
kapandı ve tüm maliyetler sonrası sırasıyla -1,6881, -3,3358 ve -2,5715 USDT
LOSS incelemesi aldı. Üç tezin de `outcome_known_at_capture=0`; politika mutasyon
yetkisi sıfırdır. 02 Eylül 2026 08:49 İstanbul (05:49 UTC) itibarıyla açık
pozisyon 0, pending funding 0, kasa 10.289,5561 USDT, gerçekleşmiş sonuç
+289,5561 USDT, toplam ücret 22,8561 USDT ve bütün yönetim olaylarında
`real_order_allowed=0`dır. Öğrenme 37 kapanış ve iki bağımsız haftayla
`COLLECTING_EVIDENCE`; promotion kapalıdır. Worker `active/CONNECTED`, PAPER_ONLY
ve Unified Forecast yürütmeden ayrıdır.

Mobil rapor eksikliğinin ayrıca somut konfigürasyon nedeni bulundu: aktif günlük
özet ve beş dakikalık arıza heartbeat'leri SQLite'ı yanlış
`/var/lib/agca/crypto_data.db` yolunda arıyordu. İki mevcut otomasyon kopya
oluşturmadan yerinde güncellendi; doğru otoritatif yol
`/home/kursadsalis/AG-Crypto-Analyst-cloud/crypto_data.db` oldu. Günlük özet
11:15 İstanbul zamanında ACTIVE ve her koşuda NOTIFY kalır. Arıza nöbeti artık
gerçek zamanlı worker/health, yönetimsiz OPEN, pending funding ve beş dakikada
10'dan fazla aynı-pozisyon EXIT_REJECTED selini de kritik sayar. İlk görev
güncelleme öncesi başlamış olduğundan, yeni sözleşmenin ilk doğal beş dakikalık
koşusu ayrıca teslimat kanıtı olarak izlenmelidir.

Canlı rollback dizinleri:
`/var/lib/agca/rollback-b380bc5-exit-throttle-20260902T0540Z`,
`/var/lib/agca/rollback-921d0e9-reduce-only-liquidity-20260902T0545Z`,
`/var/lib/agca/rollback-fd1dc2d-exit-reasons-20260902T0547Z` ve
`/var/lib/agca/rollback-8cab224-short-dust-20260902T0548Z`.

# 2026-09-02 — Gerçek zamanlı PAPER pozisyon yaşam döngüsü kuruldu

Saatlik araştırma mimarisinin açık pozisyonu vade sonuna kadar kör tutan yolu
yeniden düzenlendi. Dinamik semboller için OKX public WebSocket `tickers`,
`books5` ve `trades` kanallarını izleyen ayrı PAPER worker eklendi. İki saniyeden
eski olay reddedilir; aktif sembol kümesi bağlantı kesilmeden değişebilir.

Her OPEN pozisyon girişteki risk snapshot'ından %0,5-%5 aralığında zorunlu stop
alır. Politika her taze olayda koruyucu stop, üç ayrı saniyeye yayılmış ters tez,
1R/2R kademeli realizasyon, maliyet üstü breakeven, trailing stop ve yalnız son
çare azami süreyi değerlendirir. Kısmi kapanışta kalan miktar/maliyet tabanı,
giriş ücreti payı, çıkış ücreti, spread ve slippage yaşam boyu kasaya atomik
işlenir. Eski saatlik kapatıcı yönetilmiş pozisyona dokunamaz. Aynı/eski olay
nakdi iki kez değiştiremez.

Funding taşıması stopu geciktiremez: çıkış anında exact zaman ve miktar segmenti
dondurulur; uzlaştırılana kadar işlem öğrenmeden çıkarılır ve ayrı bakım görevi
kasa/net sonucu düzeltir. Yönetim ve arıza geçişleri yerel SQLite'ta kalıcıdır;
gerçek emir yetkisi sıfırdır. Canlı kaynaklı, yalnız bellek içi kabul aracı
`scripts/paper_realtime_readiness.py` eklendi. Yerel tam suite 287/287 geçti.
Checkpoint, canlı dağıtım, systemd sağlık ve canlı acceptance sonucu henüz bu
kaydın devamında doğrulanmalıdır; tamamlanmadan sistem sağlıklı sayılmayacaktır.

# 2026-09-01 — Davranış fırsatı aynı dakikalık çevrimde PAPER doluma bağlandı

Canlı denetim, davranış çekirdeğinin fırsatı dakikası içinde seçmesine rağmen
`next_tradable_hour_open()` ile girişi sonraki saat başına ötelediğini ve dakikalık
worker yolunun yalnız seçim yapıp dolum/kapanış/öğrenmeyi saatlik işe bıraktığını
gösterdi. Bu piyasa zorunluluğu değil, eski saatlik araştırma sözleşmesinin
yürütmeye sızmış yapay gecikmesiydi.

PAPER davranış çevrimi artık aynı dakikada mevcut bekleyenleri işler, vadesi
gelenleri kapatır, yeni fırsatı seçer ve yeni seçimi aynı çevrimde doldurmayı
dener. Giriş kanıtı karar zamanından sonra alınan, en fazla 30 saniye eski,
değişmez OKX USDT-swap ticker'ıdır; Binance spot ticker ile fiyat sapması %2
içinde doğrulanır. Kanıt yoksa veya bozuksa fail-closed kalır. Eski bir sonraki
saat açılışı kapısı bağlayıcı kayıttan kaldırıldı. Unified Forecast yürütmeye
bağlanmadı; tek ajan, tek yaşam boyu kasa, PAPER_ONLY ve gerçek-emir=0 korundu.
Odak testleri 17/17, tam suite 270/270 geçti. Checkpoint `8f9ca68` canlı
`agca-runtime` worker'ına üç çalışma dosyasıyla dağıtıldı; rollback
`/var/lib/agca/rollback-8f9ca68-immediate-paper-20260901T1829Z` altındadır.
Worker 21:30:11 İstanbul (18:30:11 UTC) itibarıyla `HEALTHY`, tek ajan ve
gerçek-emir=0 kaldı. Dağıtım öncesinde sonuç-kör biçimde seçilmiş CRV/SOL/FIL/ZEC
kayıtlarının değişmez planı geriye dönük yeniden yazılmadı; bunlar 22:00 İstanbul
(19:00 UTC) girişini bekliyor. İlk V3 doğal seçimin aynı çevrimde dolumu ve daha
sonraki kapanış→öğrenme kaydı ayrıca doğrulanmalıdır.

# 2026-09-01 — Dinamik PAPER yürütme sabit mum kohortundan ayrıldı

Canlı denetim dört davranış işleminin yalnız BTCUSDT ayağının dolduğunu;
DASHUSDT, CRVUSDT ve UNIUSDT'nin `EXACT_NEXT_OPEN_UNAVAILABLE` ile iptal
edildiğini gösterdi. Aynı gün 21 iptalin tamamı aynı nedendeydi. Kök neden dış
veri yokluğu değil, piyasa-geneli davranış/risk evreninin PAPER dolumunda eski
sabit `market_klines` araştırma kohortuna yeniden bağlanmasıydı. Bekleyen
kayıtlar iki saat kapasite tutarak öğrenme deneylerini ayrıca engelliyordu.

PAPER yürütme artık her seçilen sembol için pozisyon-bazlı, değişmez giriş ve
çıkış gözlemi kaydeder. Fiyat ve likidite davranış kaynağıyla uyumlu resmî OKX
USDT-swap mumundan alınır; resmî Binance spot mumu bağımsız fiyat çapraz
kontrolüdür. Sapma %2'yi aşarsa veya kaynak kanıtı bozuksa işlem fail-closed
kalır. İlk gözlem sonradan değiştirilemez, uzak mirror'a açılmaz ve gerçek emir
yetkisi sıfırdır. Giriş kanıtı on dakika içinde yakalanamazsa kayıt açık nedenle
iptal edilerek kapasite serbest bırakılır; iki saatlik görünmez bekleme kaldırıldı.

Sabit 13 sembol yalnız bağlayıcı sözleşmedeki global stres/referans çıpası ve
Unified Forecast araştırma kohortu olarak kalır; davranış fırsatı, risk kapsamı
veya PAPER yürütme evreni değildir. Unified Forecast yürütmeye bağlanmadı; tek
ajan, yaşam boyu kasa, PAPER_ONLY ve gerçek-emir kapıları değişmedi. Dinamik
CRV/DASH/UNI/ARB/OP/ENA örneklerinde iki resmî veri yolu canlı VM'den erişilebilir
doğrulandı. Odaklı testler 23/23, tam suite 270/270 geçti. Checkpoint `ce5b70f`
dosya-seviyesinde canlıya dağıtıldı; iki kaynak hash'i yerelle birebir eşleşti.
Rollback `/var/lib/agca/rollback-ce5b70f-dynamic-paper-execution-20260901T1752Z`
altındadır. Worker kontrollü yeniden başlatma sonrası active kaldı. Dağıtımdan
önce giriş saati on dakikadan fazla geçmiş ARB/CRV/OP/ENA kayıtları geriye dönük
işlem uydurulmadan yeni açık nedenle iptal edildi ve aktif kapasite 6'dan 2'ye
indi. Dağıtılmış modül canlı CRV için içinde bulunulan saatin tam OKX swap açılışını,
pozitif likiditeyi, Binance çapraz kontrolünü ve 64 karakter kaynak hash'ini
başarıyla üretti. İlk dağıtım-sonrası doğal yeni adayın zamanında dolumu ayrıca
izlenmelidir; geçmiş iptaller başarı gibi yeniden yazılmayacaktır.

“Doğal adayı bekleme” artık dağıtım doğrulaması değildir. Yeni zorunlu
`scripts/paper_execution_readiness.py` canlı kaynakları kullanıp yalnız bellek
içi portföyde exact giriş, iki-kaynak kontrolü, değişmez gözlem, simüle dolum ve
muhasebeyi uçtan uca sınar. Canlı VM koşusu CRVUSDT, DASHUSDT ve UNIUSDT için
`2026-09-01 21:00 İstanbul (2026-09-01 18:00 UTC)` girişinde üçte üç geçti;
üçü de test portföyünde OPEN oldu, otoritatif SQLite değişmedi ve gerçek emir
yetkisi sıfır kaldı. Bu preflight geçmeden gelecek dağıtımlar “sağlıklı” diye
sunulamaz; doğal işlemler yalnız ekonomik/öğrenme sonucu olarak izlenecektir.

# 2026-09-01 — Oturum kapanışı: ortak vizyon ve sonraki doğrulama

Kullanıcının bağlayıcı beklentisi nettir: AGCA güvenli görünmek için işlem ve
öğrenmeyi sıfırlayan bir sistem olmayacak. Geçerli, likit ve sonuç-kör tezi olan
fırsatlar kontrollü PAPER boyutuyla sınanacak; risk katmanı fırsata ikinci kaba
karar motoru olmayacak. Sıfır işlem yalnız açık, makine-okunur nedenle kabul
edilebilir. Sistem piyasa-geneli ve bağlamsal çalışacak; kullanıcı aynı vizyonu
her oturumda yeniden anlatmak zorunda bırakılmayacaktır.

Aktif `AGCA günlük durum özeti` Codex heartbeat'i her gün `11:15 İstanbul
(08:15 UTC)` için ACTIVE durumdadır. Mobil özet; sistem sağlığı, seçilen/giriş
bekleyen/açık/kapanan/iptal PAPER sayıları, sanal kasa ve açık risk, tüm maliyetler
sonrası gerçekleşmiş sonuç, varsa sorun ve hedefe ilerleme değerlendirmesini kısa
Türkçe verir. Giriş bekleyen işlem “işlem yok” diye sunulamaz. Eski gereksiz
brifingler kapalıdır; kritik arıza nöbeti yalnız gerçek sorunlarda bildirim verir.

Oturum kapanırken dört sonuç-kör davranış işlemi `PENDING_ENTRY` durumundadır:
BTCUSDT, DASHUSDT, CRVUSDT ve UNIUSDT SHORT; her biri yaklaşık 205,54 USDT.
Planlı exact girişleri `2026-09-01 04:00 İstanbul (01:00 UTC)` zamanıdır. Dördünün
tez/hash ve exchange metadata bağları doğrulandı. Sonraki oturumun ilk işi bu
dört kaydın OPEN/CANCELLED durumunu, exact fill fiyat/maliyetlerini, portföy
muhasebesini ve seçim audit nedenlerini canlı SQLite'tan doğrulamaktır. 0G/ARB
kararlarının eski gizli %1 tabanıyla kaçırıldığı unutulmamalı; yeni sabit 10 USDT
tabanı sonraki mikro PAPER adaylarında canlı kanıtlanmalıdır.


# 2026-09-01 — PAPER kapı envanteri gizli öğrenme vetolarını kaldırdı

Aktif karar–risk–yürütme yolu uçtan uca denetlendi. En önemli bulgu, risk
yöneticisinin 0G gibi uç bağlama verdiği %0,25 iznin PAPER yürütücüdeki
`max(10 USDT, equity*%1)` tabanıyla tekrar sıfırlanmasıydı. Ayrıca ilk küçük aday
`break` ile kendisinden sonraki bütün adayları susturuyordu. Asgari notional artık
sabit 10 USDT ve yürütülemeyen aday yalnız kendisi için `continue` edilir.

Her seçim çevrimi `paper_behavior_selection_runs` içine çekirdek durumu,
aday/seçim sayısı, kapasite, maruziyet ve ret nedenlerini yazar; tablo local-only
ve gerçek emir yetkisi sıfırdır. Testte %0,25 tavan 25 USDT pozisyona dönüştü;
5 USDT'lik ilk aday reddedilirken sonraki 100 USDT aday seçilmeye devam etti.

İkinci kapsam denetimi, karşıt book/taker akışının tamamen atıldığını ve dış olay
eşleşmesinin saf piyasa yapısı fırsatlarını yok ettiğini gösterdi. Karşıt işaretler
artık iki absorption deseni olarak öğrenilir. Her örtüşmeyen dört saatlik blokta
en güçlü kalite-uygun davranış için outcome-blind `MARKET_STRUCTURE` örneği
oluşur; hipotez yeterli olduğunda dış haber/on-chain/whale olayı olmadan da PAPER
adayı üretilebilir. Unified Forecast bu yola bağlanmadı.

Global `LOCKDOWN` da geçerli veride sıfır öğrenme yerine en fazla %2 toplam,
%1 alt, %0,25 tek pozisyon ve bir yeni işlem zarfına dönüştürüldü. `DATA_BLOCKED`,
eksik/bozuk veri ve yürütülemez likidite kesin veto kalır. Bağlayıcı envanter
`docs/PAPER_EXECUTION_GATE_REGISTER.md` dosyasındadır. Tam suite 262/262 geçti.
Checkpoint `2b589bf` rollback korumalı canlıya dağıtıldı; rollback
`/var/lib/agca/rollback-2b589bf-paper-gates-20260901T0035Z` altındadır. Retained
canlı geçmişte yedi kapanmış/örtüşmeyen market-structure dakikası bulundu ve
yedi outcome idempotent olarak üretildi. Henüz tek desen için beş örneğe
ulaşmadığından endogenous aday doğal biçimde oluşmadı.

Canlı seçim audit'i ilk satırını `NO_ACTIONABLE_PATTERN`, aday=0, seçilen=0,
kapasite=2 ve gerçek_emir=0 olarak yazdı; sıfır artık açıklamasız değil. Daha
önemlisi, önceki doğal çevrimler davranış çekirdeğinden dört sonuç-kör işlem
seçmiş durumda: BTCUSDT SHORT, DASHUSDT SHORT, CRVUSDT SHORT ve UNIUSDT SHORT.
Her biri 205,54 USDT (%2 keşif boyutu), `PENDING_ENTRY` ve 01:00 UTC kesin açılış
zamanına bağlıdır. Dördünün de ayrı 64 karakter tez hash'i,
`outcome_known_at_capture=0`, davranış karar referansı ve canlı exchange metadata
kuralı doğrulandı; eksik tez sayısı sıfır. Ayrıca 0G/ARB kararları oluşmuş fakat
eski %1 yürütme tabanı yüzünden pozisyona dönüşmemiştir; yeni taban sonraki küçük
adaylarda bu engeli kaldırır. Worker ve bütün sağlık timer'ları active. İlk dört
işlemin 01:00 UTC exact fill sonucu henüz doğrulanmalıdır.

Kullanıcı saatinin İstanbul olduğu kalıcılaştırıldı. Canonical SQLite zamanları
UTC kalır; PAPER raporu, bekleyen giriş ve kullanıcıya dönük tüm çalışma saatleri
İstanbul saati önce ve UTC karşılığı parantez içinde gösterilir. Bu dört işlemin
planlı girişi kullanıcı saatinde `2026-09-01 04:00:00 İstanbul
(2026-09-01 01:00:00 UTC)` şeklindedir.
Checkpoint `3c87b3a` rollback korumalı canlıya dağıtıldı; rollback
`/var/lib/agca/rollback-3c87b3a-istanbul-time-20260901T0042Z` altındadır. Dört
bekleyen girişin tamamı canlı fonksiyonda İstanbul 04:00 önce, UTC 01:00
parantez içinde gösterildi. Worker ve bütün sağlık timer'ları active; tam suite
263/263 geçti.


# 2026-09-01 — Bağlamsız risk vetosu PAPER öğrenmesinden çıkarıldı

Canlı 0G örneği mevcut risk katmanının geçerli ve yüksek hacimli bir davranış
fırsatını yalnız geniş 24h range nedeniyle tamamen engellediğini gösterdi. Bu,
risk katmanını kaybı/boyutu yöneten katman olmaktan çıkarıp ikinci ve kaba karar
motoruna dönüştürüyordu; adaptif, işlemle öğrenen ajan hedefiyle uyumsuzdu.

`MARKET_RISK_POLICY_V3_CONTEXT_SIZES_PAPER_RISK` ile geçerli ve likit bir
varlıkta volatilite, range, drawdown ve yakın dönem hareketi artık yalnız boyutu
küçültür. En uç `EXTREME` altcoin bağlamında tavan %0,25'tir. Yetersiz likidite,
eksik/bozuk veri ve global veri doğrulama hataları kesin veto kalır. Unified
Forecast baseline-only, tek ajan, yaşam boyu kasa, PAPER_ONLY ve gerçek-emir
kapıları değişmedi. Canlı ilk V3 turunda 0G ve ARB `EXTREME`, `%0,25`, `YES`
olarak doğrulandı; ZKP de geniş range'e rağmen `%0,25` PAPER izni aldı. Düşük
hacimli sekiz referans varlık sıfır tavanla veto kalmaya devam etti.

Ardından canlı seçim, aynı davranış kararının değişen risk snapshot'ıyla tekrar
değerlendirilmesinde eski değişmez tezi yeni hash ile arayan idempotency hatasını
açığa çıkardı. Seçim artık doğal karar anahtarıyla mevcut tezi bulur ve ilk
sonuç-kör kanıt/hash/risk bilgisini aynen yeniden kullanır; sonradan tez yazmaz.
Canlı inceleme karar satırının rollback nedeniyle oluşmadığını da gösterdi.
Genel `INSERT OR IGNORE` kaldırıldı; yalnız doğal karar kimliği çatışması
idempotent kabul edilir, diğer bütün constraint/veri sorunları görünür hata olur.
Hedefli testler 34/34, tam suite 255/255 geçti. Nihai ekleme düzeltmesinin canlı
dağıtımı checkpoint `947e30c` ile tamamlandı; rollback
`/var/lib/agca/rollback-947e30c-visible-inserts-20260901T0000Z` altındadır.
Worker kontrollü I/O kapanışı sonrasında yeni PID ile active oldu; observer,
attestation ve integrity timer'ları da active. Canlı seçim hata vermeden
`SUCCESS` tamamlandı. Son olaylı davranış dakikası 71 saniye tazeydi fakat
çekirdek `NO_ACTIONABLE_PATTERN` verdi: öğrenmeye uygun NEWS/ONCHAIN/WHALE
desenleri mevcut olsa da o dakikada olay + coin kapsamı + uyumlu flow birleşimi
yoktu. Karar/işlem sayısı bu nedenle sıfır kaldı; bir sonraki doğal eşleşmede
satır ekleme hatası artık sessizce yutulamaz ve karar–tez–işlem zinciri açıkça
doğrulanmalıdır.


# 2026-09-01 — Dinamik fırsat evreni risk kapsamına bağlandı

23:21 UTC doğal olayında 0G/ENA/POL/ZEC/ZKP için davranış akışı uyumlu olduğu
halde sabit risk sembol listesi nedeniyle değerlendirme yapılamadığı kanıtlandı.
Sabit liste artık PAPER fırsat evreni değildir. En güncel öğrenmeye uygun USDT
swap davranış evreni risk yöneticisinin istediği sembollere dinamik eklenir;
BTC/ETH dahil mevcut 13 sembol yalnız global stres ve veri kapsamı çıpası olarak
kalır. Dinamik bir coinin kaynak hatası global izni yapay biçimde değiştirmez,
fakat o coin fail-closed kalır.

Test edilebilir ve akış uyumlu bir fırsat `RISK_COVERAGE_MISSING` veya
`RISK_VETO` nedeniyle alınmazsa artık yerel `paper_behavior_missed_opportunities`
kaydına girer. Dört saatlik outcome kapandığında yön ve maliyetler uygulanarak
karşı-olgusal net sonuç eklenir. Böylece ajan sonradan işlem uydurmadan kaçırdığı
fırsatlardan da ölçülebilir biçimde öğrenir.

Unified Forecast baseline-only, tek ajan, yaşam boyu kasa, PAPER_ONLY ve gerçek
emir kapıları değişmedi. Tam yerel suite 252/252 geçti. Checkpoint `865b0ad`
rollback korumalı olarak `agca-runtime` üzerine dağıtıldı; rollback
`/var/lib/agca/rollback-865b0ad-dynamic-risk-20260901T0000Z` altındadır.

Zorlanan ilk canlı tur `MARKET_RISK_POLICY_V2_DYNAMIC_BEHAVIOR_UNIVERSE`,
`NORMAL`, global referans kapsamı 13/13 ve toplam 20 değerlendirilmiş varlık
üretti. Sabit listenin dışındaki 0G/ARB/CRV/DASH/NOT/OP/POL snapshot'a girdi;
CRV, DASH, NOT, OP ve POL risk izni aldı. 0G ve ARB aşırı hareket/range nedeniyle
coin bazında veto edildi. Binance spot kaynağında bulunmayan HYPE/USELESS/ZORA
fail-closed kaynak hatası olarak kaldı ve global referans stresini bozmadı.

Canlı PAPER seçimi `SUCCESS`, gerçek emir yetkisi false ve açık pozisyon sıfır
olarak tamamlandı; o anda taze, tüm kapıları geçen aday olmadığından seçilen
işlem sıfırdı. Worker, behavior observer, günlük integrity ve attestation
timer'ları active. Günlük tam SQLite bütünlük kanıtı `result=ok`,
`checked_at=2026-08-31T02:44:37.471198+00:00` ve hash-korumalıdır.


# 2026-09-01 — PAPER keşif işlemi yeniden açıldı; terfi kapısı ayrıldı

Karar 031'in 30 örnek/üç gün/%55 kazanma/+%0,05 net edge şartlarını ilk PAPER
işleminden önce zorunlu tutması, Karar 030'daki “işlem bir öğrenme deneyidir”
sözleşmesini fiilen kilitledi. Bu çelişki Karar 032 ile düzeltildi.

`PAPER_BEHAVIOR_DECISION_CORE_V2`, sonucu kapanmış en az beş bağımsız örneği,
en az bir UTC günü ve ihtiyatlı maliyet sonrası pozitif ortalama edge'i olan bir
hipotezi küçük PAPER keşif deneyine açar. Keşif boyutu özkaynağın %2'sidir.
Eski 30 örnek/üç gün/%55/+%0,05 şartları artık yalnız güven/terfi kapısıdır;
terfiye hazır desen mevcut %10 PAPER boyutunu kullanabilir. Her iki yol da taze
ve kaliteli davranış/dış olay kanıtı, risk izni, exact-entry, gerçekçi maliyet ve
sonuç-kör tez ister.

Unified Forecast yürütmeye geri bağlanmadı; yalnız baseline'dır. Tek ajan, tek
yaşam boyu kasa, PAPER_ONLY ve gerçek-emir kapıları korunur. Kök `AGENTS.md`,
keşif işlemlerinin terfi/kanıtlanmış-beceri eşikleriyle tekrar engellenmesini
kullanıcının açık onayına bağlayan kalıcı sözleşmeyi taşır.

İlk canlı V2 koşusu politika düzeyinde `PAPER_EXPLORATION_AVAILABLE` ve
`execution_allowed=1` üretti; ancak aynı tazelik penceresindeki 23:00 olaylı
dakikayı, 23:02'deki daha yeni olaysız dakika gölgeledi. Seçim sorgusu bu ikinci
engeli de kapatacak biçimde tazelik penceresindeki en yeni olaylı ve öğrenmeye
uygun davranış dakikasını seçer. Regresyon testi, daha yeni olaysız dakikanın
taze olaylı fırsatı gizleyemeyeceğini zorunlu tutar.

Canlı doğrulama ayrıca dakika düzeyindeki davranış/olay tazeliğinin yalnız
saatlik PAPER seçimiyle uyumsuz olduğunu gösterdi. Aynı `CRYPTO_PROFIT_AGENT_V1`
ve aynı SQLite/kasa içinde yalnız hafif ve idempotent davranış seçimi dakikada
bir çalışır. Pozisyon dolumu/kapanışı, execution maliyetleri, experience review
ve tam trade-learning mevcut saatlik akışta kalır.

Canlı dağıtım sonrası V2 politika `PAPER_EXPLORATION_AVAILABLE`,
`execution_allowed=1` ve dakikalık seçim `SUCCESS` oldu. 23:21 UTC'deki 14
doğal ONCHAIN olayı yeni yoldan değerlendirildi. O dakikada risk izni olan BTC
ve ETH'nin book/taker işaretleri uyuşmadığı için doğru biçimde işlem açılmadı.
Uyumlu 0G/ENA/POL/ZEC/ZKP desenleri ise mevcut sabit risk evreninde yoktu. Bu,
eşik/cadence kilidinin kapandığını fakat piyasa-geneli davranış evreni ile risk
kapsamı arasında sonraki dar ürün boşluğu bulunduğunu gösterir; risk veto
aşılmadı ve yapay işlem üretilmedi. İlk doğal karar–tez–işlem zinciri uygun
olay + uyumlu akış + risk kapsamı birleştiğinde doğrulanmalıdır.


# 2026-08-31 — Unified Forecast yürütmeden çıkarıldı; davranış çekirdeği aktif yol

Kullanıcının tekrar vurguladığı ana sözleşme uygulandı. PAPER pozisyon seçimi
artık `latest_candidates()`/Unified Forecast kullanmaz. Unified Forecast kayıt ve
fonksiyonu baseline araştırması için korunur; aktif yürütme yalnız
`PAPER_BEHAVIOR_DECISION_CORE_V1` adaylarını kabul eder. Aynı ajan, aynı portföy
ve 10.000 USDT'den başlayan yaşam boyu kasa devam eder.

Yeni çekirdek kapanmış ve uygun market-behavior dakikalarındaki order-book,
taker flow, spread, OI, funding ve liquidation kanıtını; yalnız `available_at`
saatinde bilinen news/on-chain/whale/exact-macro olayıyla birleştirir. 240 dakika
sonra gerçekten kapanmış outcome'lardan, global olayları `independence_unit`
başına tek sayarak continuation/reversal yönünü öğrenir. Masraf modeli ücret,
spread ve ek güvenlik tamponunu sonuçtan düşer.

İşlem kapısı: en az 30 bağımsız örnek, üç UTC günü, %55 masraf-sonrası kazanma
oranı ve +%0,05 ortalama net edge. Risk/freshness/quality kapıları ayrıca zorunlu.
Kanıt yetmezse eski forecast'a dönülmez ve işlem açılmaz. Her davranış kararı
ayrı `paper_behavior_decisions` kaydı, source/reference kimliği ve sonuç-kör tez
taşır. Trade learning yeni/eski politikayı ve karar kaynağını ayrı gruplar.

Canlı veride salt-okunur şema doğrulaması başarılıdır: beş öğrenilebilir desen,
en büyük desende 56 bağımsız örnek fakat yalnız bir UTC günü; bu yüzden hazır
desen ve yeni işlem sıfırdır. Bu faaliyet eksikliği değil, farklı piyasa koşulu
kapısının doğru çalışmasıdır. Model/strateji görünüşü için eşik gevşetilmedi.
Odaklı sözleşme testleri 23/23, nihai tam suite 244/244 geçti.

Checkpoint adayı rollback korumalı olarak `agca-runtime` VM'ine dağıtıldı;
rollback `/var/lib/agca/rollback-9f426d0-behavior-core-20260830T2220Z`
altındadır. Doğal worker ve saatlik PAPER çevrimi `SUCCESS` tamamlandı. Canlı
policy review `PAPER_BEHAVIOR_DECISION_CORE_V1`,
`COLLECTING_INDEPENDENT_EVIDENCE`, beş desen ve `execution_allowed=0` kaydetti.
Davranış kararı/tezi ve açık-bekleyen pozisyon sıfır; eski 31 pozisyon yalnız
`UNIFIED_FORECAST_BASELINE` geçmişi olarak duruyor. Kasa 10.276,9678579129
USDT, gerçekleşmiş P&L +276,96785791286 ve ücret 14,804558465 USDT olarak
değişmedi. Worker, attestation ve behavior timer active; PAPER sonucu SUCCESS,
PAPER_ONLY ve `may_send_real_orders=false` doğrulandı.


# 2026-08-31 — Ana worker'da yinelenen attestation I/O blokajı kaldırıldı

Tecrübe belleği dağıtımı sonrasında kritik nöbet, ana worker sağlık kaydının
`2026-08-30T21:28:23Z` sonrasında yaklaşık 15 dakika yenilenmediğini bildirdi.
Süreç ölmemişti; son saatlik/PAPER işi `SUCCESS`, açık pozisyon sıfır ve
PAPER_ONLY kapıları sağlamdı. Süreç `D` durumunda SQLite/I/O bekledi ve ancak
`21:43:49Z` civarında sonraki ürün çevrimine ilerledi.

Kök neden ana worker'ın her sonuçtan sonra senkron
`cloud_attestation.record()` çalıştırmasıydı. Aynı attestation zaten ayrı,
beş dakikalık `agca-cloud-attestation.timer` tarafından yürütülüyordu. Ana
worker içindeki yinelenen ve bloklayıcı çağrı kaldırıldı; hash-korumalı
attestation, quick-check ve Supabase audit mirror görevi bağımsız timer'da aynen
devam eder. Model, strateji, eşik, kasa, PAPER_ONLY ve gerçek emir kapıları
değişmedi. Odaklı runtime/attestation/bellek testleri 14/14, nihai tam suite
232/232 geçti.

Canlı ilk doğrulama ikinci fiziksel darboğazı açığa çıkardı: bağımsız
attestation servisi 16 dakikada yaklaşık 1,24 GB okuyarak 347 MB SQLite üzerinde
tam `PRAGMA quick_check` yürütüyor ve e2-micro diskte worker'ı yine `D` beklemeye
sokuyordu. Mevcut tarama kesildi; worker hemen çalışır `S` durumuna döndü.

Nihai mimaride tam quick-check ayrı `agca-sqlite-integrity-check.timer` ile
günde bir, `Nice=19` ve idle I/O sınıfında çalışır ve hash-korumalı atomik sağlık
kaydı üretir. Beş dakikalık attestation bu kaydı doğrular; eksik, hash'i bozuk
veya 25 saatten eski kanıtı fail-closed `MISSING/INVALID/STALE` olarak taşır.
Önceki hash-geçerli `sqlite_quick_check=ok` attestation yalnız ilk cache'i
bootstrap etmek için kullanılabilir; sonrasında günlük bağımsız tam tarama
yeniler. İkinci katman odaklı testleri 12/12, nihai tam suite 236/236 geçti.

İzolasyon VM'e dağıtıldı; rollback
`/var/lib/agca/rollback-ca5d5c2-integrity-isolation-20260830T2152Z` altındadır.
İlk integrity cache, `2026-08-30T21:28:23Z` tarihli hash-geçerli son tam
quick-check attestation'dan `ok` olarak başlatıldı. Yeni hafif attestation
`21:52:13Z`'de `HEALTHY`, `sqlite_quick_check=ok`, hourly/PAPER `SUCCESS`
tamamlandı. Worker sağlık kaydı `21:52:27Z`'ye ilerledi; süreç `S`, worker ve
iki timer active kaldı. PAPER kasası değişmedi: 10.000 başlangıç,
10.276,9678579129 nakit ve +276,96785791286 gerçekleşmiş P&L.

# 2026-08-31 — Tek ajan için sonuç-kör tez ve tecrübe belleği

Karar 030 uygulandı. PAPER ajanı, portföyü, yaşam boyu kasası, mevcut model,
strateji ve eşikleri değişmedi. Bundan sonra seçilen her PAPER pozisyon için
sonuç bilinmeden önce değişmez bir karar tezi yazılır. Tez; gerçekten kullanılan
olasılık, hedef yönü, risk kapısı, model/artifact kimliği ve maliyet-sonrası
icrayı sınanacak yetenekler olarak kaydeder. Market-behavior, order book ve dış
trader kanıtları mevcut karar yolunda henüz girdi değilse açıkça
`observed_but_not_decision_input` olarak işaretlenir; ajan kullanmadığı yeteneği
kullanmış gibi öğrenemez.

Pozisyon kapandığında yalnız önceden dondurulmuş tez üzerinden idempotent
tecrübe review'u oluşur. Net P&L, toplam icra maliyetleri, liquidation ve sınanan
yeteneklerin o işleme özgü sonucu saklanır. Tek bir kazanç veya kayıp beceri,
nedensellik ya da politika terfisi sayılmaz; skill/rejim/şans ayrımı bağımsız
işlem ve farklı rejim kohortu birikene kadar `UNRESOLVED` kalır. Öğrenme özeti
artık en büyük üç mutlak P&L yoğunlaşmasını ve pozitif P&L'nin tek başına beceri
kanıtı olmadığını da taşır.

Yeni bellek ikinci ajan veya ikinci kasa değildir; `memory_version` yalnız karar
provenance etiketidir. Eski, tezsiz kapanmış işlemler geriye dönük gerekçe
uydurulmadan tek retrospektif özette korunur: ekonomik sonuç saklı, beceri ve
terfi kanıtı yok. Retrospektif V2 yalnız kanıt-uygun 17 temiz işlemi kullanır;
8 lookahead-karantina işlemi tecrübe/öğrenme kanıtına alınmaz. İlk canlı V1
oluşumunun 25 satırlık sayımı V2 tarafından açıkça supersede edilmiştir ve terfi
yetkisi zaten sıfırdır. Yeni tablolar SQLite'ta yerel-only tutulur ve uzak audit mirror
yetki listesine eklenmemiştir. Odaklı doğrulama 19/19, nihai tam suite 232/232
geçti; karantina regresyonu eklendikten sonraki nihai suite 233/233'tür.

Kod dosya-seviyesinde ve rollback korumalı biçimde `agca-runtime` VM'ine
dağıtıldı. Rollback dizini
`/var/lib/agca/rollback-6559e72-experience-memory-20260831T0023Z` konumundadır.
Worker yeni PID ile active oldu ve doğal saatlik PAPER aşaması çalıştı. Kasa
sıfırlanmadı: başlangıç 10.000 USDT, nakit 10.276,9678579129 USDT, gerçekleşmiş
P&L +276,96785791286 USDT; açık ve bekleyen pozisyon sıfırdı. Tecrübe tabloları
oluştu; sonuç-kör yeni tez ve review sayısı doğal olarak sıfır. Temiz V2
retrospektif özetin 17 işlem, skill=0 ve promotion=0 olduğu doğrulandı;
sonraki doğal yeni işlemde tez sayısı pozisyon seçimiyle atomik artmalıdır.

# Önceki plan — tamamlandı: Tecrübeyi karara taşıyan gerçek öğrenme çekirdeği

Kullanıcının nihai ürün talebi `docs/DECISIONS.md` Karar 030 olarak
kalıcılaştırıldı. Ajan sıfırlanmayacak ve eski PAPER kasası/hafızası
korunacaktır. PAPER işlem açması yasaklanmayacak; fakat her işlem sonuçtan önce
dondurulmuş gerekçe, kanıt, beklenti, risk ve sınanacak yetenekleri olan bir
öğrenme deneyi olacaktır. İşlem sayısı veya rapor üretimi hedef değildir.

Eski +276,97 USDT döneminin tecrübe yorumu henüz ajan belleğinde kodlanmış
değildir: pozitif ekonomik sonuç; zayıf genel yön tahmini; 17/17 24h SHORT;
tek rejim/korelasyon; yaklaşık %80 üç işlem yoğunlaşması; yeni vizyona kanıt
değil. Sıradaki uygulama bu yorumu geriye dönük P&L silmeden sürümlü tecrübe
kaydına almak ve bundan sonraki her PAPER işlemde işlem-öncesi tez ile
işlem-sonrası beceri/rejim/şans değerlendirmesini otomatik üretmektir.

Aynı ajan ve yaşam boyu kasa devam eder; yeni davranış karar çekirdeği ayrı
politika sürümü/kohortuyla ölçülür. Eski Unified Forecast yalnız karşılaştırma
koludur, zorunlu öğretmen/girdi değildir. Uygulamadan önce mevcut öğrenme,
portfolio ve karar tabloları tek seferde incelenip en küçük kalıcı şema/akış
tasarlanmalı; art arda dar deneme veya gereksiz tam suite çalıştırılmamalıdır.
PAPER_ONLY ve gerçek emir kapıları değişmez.

# NEXT SESSION — PAPER başarı yorumu ve eşik sözleşmesi denetimi

Kullanıcı bir sonraki oturumda şu konunun öncelikle değerlendirilmesini istedi:
eski raporlarda Unified Forecast yön sınıfı sonuçları bazı paketlerde 11'de 0
veya 11'de 1 görünürken temiz PAPER kasanın nasıl +276,97 USDT ürettiği.

Salt-okunur denetim mevcut kârın 17 kapanmış işlemin tamamı 24h SHORT iken
oluştuğunu gösterdi: 12 kazanç, 5 kayıp, ortalama kayıtlı yön güveni %55,31.
Baseline kolu 12 işlem/7 kazanç/+174,85 USDT; dış-yön SHORT kolu 5 işlem/5
kazanç/+102,12 USDT üretti. En büyük üç kazanç toplam kârın yaklaşık %80'idir.
Bu işlemler korelasyonlu, aynı yönlü ve tek kısa rejime yoğun olduğu için 17
bağımsız başarı veya sürdürülebilir model avantajı sayılamaz.

Kod sözleşmesinde Unified Forecast araştırma/aksiyon eşiği %70 iken PAPER ajanı
ayrı olarak en az %52 yön olasılığı, en az 5 yüzde-puan UP/DOWN farkı ve hedef
fiyat yön uyumu ile 4h/24h aday seçebiliyor. Eşikler bu oturumda değiştirilmedi;
fark mevcut tasarımda zaten vardı fakat kullanıcıya yeterince açık sunulmamıştı.
Bir sonraki oturum önce bu iki sözleşmenin ürün amacı, ekonomik doğrulama ve
raporlama açısından tutarlı olup olmadığını incelemeli. Kârı model başarısı gibi
sunmamalı; rejim yoğunlaşması, bağımsız işlem birimi, seçilim yanlılığı ve
masraf-sonrası karşı-olgusal kıyas kurulmadan eşik/politika değişikliği önermemeli.

Temiz PAPER portföyü günlük sıfırlanmaz. Tek temiz başlangıç
`2026-08-24T21:47:27.779873+00:00`, başlangıç sermayesi 10.000 USDT'dir. Eski
lookahead işlemleri karantinadadır. 17 işlemin tamamı piyasa-geneli davranış
misyonu kök sözleşmeye yazılmadan önce seçildi; dolayısıyla +276,97 USDT yeni
hedefin veya yeni observer'ın performansı değildir. Yeni hedef sonrası seçilen
temiz işlem ve gerçekleşmiş P&L halen sıfırdır.

# Kalıcı çalışma disiplini düzeltmesi

Kullanıcının tekrar etmek zorunda kaldığı iki çalışma tercihi kök `AGENTS.md`
sözleşmesine eklendi. Sorunlarda önce ölçüm ve kök-neden ayrımı yapılacak; art
arda dar parametre denemeleri yerine farklı hipotezler karşılaştırılacak. Ara
aşamalarda yalnız odaklı test, nihai adayda bir kez tam suite kullanılacak;
aynı kanıtı üretmeyen uzun/canlı koşular tekrarlanmayacak.

Kullanıcı bildirimlerinde varsayılan kanal Codex heartbeat/automation'dır;
kullanıcı açıkça istemedikçe Telegram gündeme getirilmeyecektir. Kullanıcı
`oturum bitti` demeden kapanış, handoff özeti veya sonraki oturum açılış mesajı
gönderilmeyecektir.

# 2026-08-30 — Market-behavior sürekliliği ve anlık arıza nöbeti

Salt-okunur gözlemde market-behavior observer'ın OKX istek gecikmeleri sırasında
tek bir `curl` alt sürecinde 20–25 saniye beklediği, bazı çevrimlerin 150 saniye
service timeout'una ulaştığı ve bazı kapanmış dakikalarda 12 satırın tamamının
minimum örnek kapısından kaldığı görüldü. Ana PAPER worker durmadı ve uygunsuz
dakikalar fail-closed biçimde öğrenmeye alınmadı; fakat canlı mikro-yapı dakikası
sonradan eksiksiz geri getirilemeyeceği için bu operasyonel boşluk kapatıldı.

OKX'in aynı resmî HTTPS endpoint'leri artık her çağrıda ayrı `curl` süreci
başlatmak yerine bağlantı havuzlu `requests.Session` üzerinden, sekiz sınırlı
işçi ve üç saniyelik istek bütçesiyle paralel toplanıyor. Tek endpoint hatası
bütün çevrimi düşürmez; yalnız ilgili kaynak/enstrüman eksik kalır ve mevcut
kalite kapısı onu reddeder. Eksik spread artık dakika toplamasında tür hatası
üretmez. Kapsam, dört örnek, 10 saniye cadence, minimum örnek/gap/source
kapıları, model, strateji ve eşikler değiştirilmedi.

Servis failure'ı için `OnFailure` alarmı ve dakikalık deduplikasyonlu sağlık
monitorü eklendi. Servis düşüşü anında; missing/invalid/stale sağlık kaydı veya
üç ardışık bozuk kontrol tek alarm açar, toparlanma tek recovery üretir. VM'de
Telegram credential'ı bulunmadığı doğrulandı; dolayısıyla sunucu alarm kaydı
kalıcıdır fakat doğrudan Telegram teslimi yoktur. Kullanıcıya ulaşan kanal olarak
bu Codex görevine bağlı `AGCA kritik sistem arıza nöbeti` beş dakikalık
heartbeat'i aktif edildi. Nöbet ana worker, kritik timer'lar, attestation,
SQLite, PAPER saatlik/ajan sonucu, mirror tazeliği ve iki observer'ı kapsar;
sağlıklıyken rutin mesaj üretmez, yalnız kalıcı arıza/doğrulanamama ve toparlanma
bildirir. Günlük kısa yönetici özeti değişmedi; eski raporlar yeniden
etkinleştirilmedi.

Yerel odaklı testler ve tam suite 227/227 geçti; deployment preflight sıfır
hatalıdır. Canlı bağlantı-havuzlu collector art arda üç doğal dakikada 12/12
`HEALTHY` tamamlandı; son iki doğal çevrimin CPU tüketimi 1,58 ve 1,66 saniyeydi
(önceki bozuk çevrimlerde yaklaşık 46 saniye). Alarm state recovery ile kapandı.
Ana worker `HEALTHY`, PAPER_ONLY ve gerçek emir kapalı kaldı. Rollback:
`/var/lib/agca/rollback-observer-resilience-20260830T1605Z`. Doğal çevrimler
alarm nöbetiyle izlenmeye devam etmelidir; kalite kapısı gevşetilmemelidir.

# 2026-08-30 — Dış trader gerekçe öğrenmesi ve kalıcı küçük profil kuyruğu

Kamuya açık OKX copy-trader gözlemleri yalnız sonuçlarına göre puanlanmak yerine
işlem açıkken dondurulan giriş-öncesi bağlamla araştırılıyor. Yeni yerel-only
gerekçe defteri; son kapanmış 1h fiyat/hacim/taker akışını, varsa point-in-time
pozisyonlanma ve funding kanıtını kullanarak olası momentum, tersine dönüş,
kalabalık uyumu/karşıtlığı ve aktivite gerekçeleri üretir. Bunlar trader niyeti
veya nedensellik iddiası değildir. Sonuç sonradan ayrı review'a eklenir;
kazanan/kaybeden aynı kurala tabidir. Yalnız kapanmış history'de sonradan görülen
işleme geriye dönük gerekçe yazılmaz.

10 dakikada kesilen günlük monolit kolektör kalıcı küçük kuyruğa çevrildi. Tam
liderlik cohort'u günde bir doğrulanır; her 30 dakikada 6 güncel profil ve 1
history profili işlenir. Bilinen açık işlemlerin bütün kapanışları korunur;
yalnız unrelated retrospektif history profil başına 20 satırla sınırlıdır.
Kuyruk indeksi ancak başarılı SQLite commit'inden sonra ilerler. Ağır review ve
continuous-improvement hesapları kolektörde tekrar çalışmaz; ana saatlik PAPER
trade-learning adımında tek kez çalışır.

Aşama telemetrisi gerçek darboğazın ağ değil, bütün dış işlem tablosunu gruplayan
latest-open sorgusu olduğunu gösterdi. İndeksli `OPEN ve sonrasında CLOSED yok`
sorgusuna geçildikten sonra kontrollü bulut koşusu 1,709 saniyede; attestation
çakışmalı ilk doğal timer koşusu 24,582 saniyede `SUCCESS` tamamlandı. Doğal
koşuda 6 profil, 11 açık pozisyon ve 17 history işlemi işlendi; profil hatası
yoktu. Kuyruk 12. sıraya ilerledi ve sonraki timer çalışması planlandı.

Bulutta ana worker ile copy-trader, market-behavior, exact-macro ve attestation
timer'ları active; copy-trader timer enabled. Gerekçe defterinde 225 giriş
bağlamı ve 27 sonuç review'u bulunuyor; outcome-at-capture, intent, copy ve
policy-mutation bayraklarının toplamı sıfır. Yeni üç yerel tablo için mirror
outbox satırı sıfır. Rollback kopyası
`/var/lib/agca/rollback-7f22be3-copy-rationale-20260830T0500Z` altındadır.

Tam test suite 222/222 geçti. PAPER_ONLY, kanıt eşikleri, strateji/model
politikası ve gerçek emir yetkisi değişmedi; yeni model/eşik araması açılmadı.
Sıradaki doğru iş kod genişletmek değil, kuyruğun doğal turlarda tam cohort'u
tamamlamasını ve dondurulmuş gerekçelerin farklı rejimlerde sonuçlanmasını
izlemektir.

# 2026-08-30 — Davranış/olay sonuçları otomatik ve değişmez öğrenme defterine bağlandı

`602fba2` tek-çatı checkpoint'inden devam edildi. Market-behavior observer'ın
topladığı kapanmış davranış dakikaları ile yalnız gerçek `available_at`
dakikasında görünen dış olaylar artık yerel-only
`behavior_event_forward_outcomes` tablosunda otomatik settlement üretir. Her
observer koşusu bütün geçmişi taramaz; yalnız o kapanan dakikada vadesi dolan
1/5/15/60/240/1.440 dakikalık sonuçları sorgular.

Her sonuç olay öncesi son kapanmış orta fiyatı, olay dakikası fiyat/davranış
özelliklerini, hedef kapanış fiyatını ve ham yüzde tepkiyi taşır. Olayın oluşma
ve gerçek erişim saatleri ayrı korunur. Aynı olay/dakika/enstrüman/ufuk ilk
settlement sonrasında `ON CONFLICT DO NOTHING` ile değişmez; yeniden çalışma
geçmiş sonucu yazamaz. Coin kapsamı uygulanır. Global olayın farklı coin
satırları ortak `independence_unit` taşır ve bağımsız kanıt gibi sayılamaz.
Ham payload, yorum etiketi, model, eşik, sinyal veya işlem yetkisi eklenmedi.

Observer sağlık kaydı artık o koşuda kapanan forward sonuç sayısını, toplam
sonuç satırını ve bağımsız olay sayısını taşır; mevcut self-hash/attestation
sözleşmesi bu ek alanları da kurcalamaya karşı korur. Salt-okunur coverage
raporu kalıcı defter satırlarını ve ufuk dağılımını ayrıca gösterir. Deployment
preflight dördüncü tabloyu ve yeni modül hash'ini zorunlu tutar.

Değişiklik rollback korumalı olarak çalışan `agca-runtime` VM'ine dağıtıldı.
Geri dönüş kopyası
`/var/lib/agca/rollback-602fba2-behavior-outcomes-20260830T0435Z` altındadır.
Aktarım öncesindeki eski observer koşusu attestation SQLite taramasıyla
çakışarak 150 saniyelik timeout aldı; yeni kod henüz kurulu değildi. Attestation
timer'ı geçici durdurulup I/O boşaltıldıktan sonra yeni kodun kontrollü tek
koşusu 0 exit ile `HEALTHY` tamamlandı. Yeni tablo bulutta oluştu; outbox satırı
ve mirror trigger'ı sıfırdır. Doğal exact-minute olay henüz bulunmadığı için
sonuç satırı sıfırdır; sistem geçmişe veya geleceğe yapay sonuç yazmadı.

Market-behavior, exact-macro ve cloud-attestation timer'ları sonunda yeniden
enabled/active; ana PAPER worker active bırakıldı. PAPER_ONLY,
`may_send_real_orders=false`, kanıt eşikleri, strateji/model politikası ve gerçek
emir yetkisi değişmedi. Yeni model/eşik araması açılmadı.

Lookahead, kapanmamış ufuk, idempotence/değişmezlik, coin kapsamı, global olay
korelasyonu ve local-only izolasyon testleri eklendi. Odaklı testler 11/11 ve
5/5; tam suite 212/212 geçti. Yerel ve bulut deployment preflight'leri sıfır
hatalıdır. Sıradaki doğru iş kod genişletmek değil, doğal olay ve farklı piyasa
rejimi birikimini izlemektir; yeterli bağımsız kanıt oluşmadan davranış etiketi,
model veya eşik araması yapılmamalıdır.

# 2026-08-30 — Dağınık AGCA oturumları kanonik Proje Merkezi altında toplandı

Kod ve sohbet aynalarını fiziksel olarak birbirine karıştırmadan tek-çatı düzeni
kuruldu. Yeni `docs/PROJECT_HUB.md`; kanonik checkout'u, belge yetki sırasını,
ürün misyonunu, sistem katmanlarını, geçmiş oturumların nasıl içeri alınacağını
ve her çalışmanın kısa başlangıç yolunu tek yerde tanımlıyor. Kök `AGENTS.md`
artık her çalışmanın başında bu merkezi de okumayı zorunlu tutuyor;
`docs/REPOSITORY_HYGIENE.md` ve `docs/PROJECT_STATUS.md` merkeze bağlandı.

Codex/ChatGPT görev envanterinde kanonik depoda çalışan AGCA görevlerinin yanı
sıra ChatGPT proje aynasında iki eski görev ve `Documents/Codex/...` altında
tarihsel referenced-conversation checkout'ları görüldü. Bunlar silinmedi,
taşınmadı veya kod kaynağı olarak birleştirilmedi. Bağlam/provenance olarak
korunuyorlar; içlerinden gelecek yeni iddia ancak kanonik Git, test, SQLite şema
ve rapor provenance'ıyla doğrulanırsa uygun belgeye alınacak. Böylece eski
oturum bilgisi kaybolmazken ikinci bir kaynak doğrusu oluşmuyor.

Bu düzenleme yalnız dokümantasyon ve çalışma sözleşmesidir. Bulut servisleri,
SQLite, Supabase, PAPER_ONLY, kanıt eşikleri, strateji/model politikası ve gerçek
emir yetkisi değiştirilmedi. Sıradaki ürün işi değişmedi: doğal davranış ve
kesin-zamanlı olay kapsamını biriktirip ölçmek; yeterli kanıt olmadan performans
veya model terfisi iddiası üretmemek.

# 2026-08-30 — Davranış ve kesin-makro observer'ları kontrollü olarak buluta alındı

Kullanıcının açık bulut onayıyla `e8705a9` checkpoint'i VM `agca-runtime`
(`us-east1-b`, `e2-micro`) üzerine geri alınabilir biçimde dağıtıldı. Yerel ve
bulut preflight'leri sıfır hatayla geçti; bütün kapsanan kaynakların SHA-256
değerleri eşleşti. Geri dönüş kopyası bulutta
`/var/lib/agca/rollback-e8705a9-observer-deploy-20260830T0245Z` altında duruyor.
`websockets 16.1.1`, kilitli `requirements-runtime.txt` üzerinden mevcut sanal
ortama eklendi. İki observer service/timer'ı ve güncel cloud attestation service
kuruldu; `agca-market-behavior-observer.timer` ile
`agca-exact-macro-observer.timer` artık enabled/active. Önceden enabled olan
`agca-cloud-attestation.timer` doğrulama sırasında geçici durduruldu ve sonunda
yeniden active bırakıldı.

İlk kontrollü ve ilk timer koşuları başarılıdır. Son kontrolde market davranış
sağlığı `2026-08-30T03:58:13Z`, exact-macro sağlığı `03:57:27Z` ve ana worker
sağlığı `03:56:42Z` zamanında `HEALTHY` idi. Market observer doğal olarak 12
uygun satır/döngü üretti; yerel tabloda doğrulama sırasında 24 satır vardı.
Aktif FOMC yayın penceresi olmadığı için exact-macro olay sayısı sıfırdır; bu
beklenen sonuçtur.

Dağıtım sırasında ana PAPER worker'daki bağımsız bir veri-zaman sınırı sorunu
fail-closed kapıyı doğru biçimde durdurdu: Binance funding API'si `startTime`
sınırını dahil ettiği için pozisyonun tam giriş anındaki olay geri dönüyor,
AGCA'nın mevcut `(giriş, çıkış]` sözleşmesi ise bunu reddediyordu.
`paper_trading/futures_costs.py` sorgusu sözleşmeyi API tarafında doğrudan
uygulamak için başlangıcı yalnız 1 ms ileri aldı; strateji, eşik veya maliyet
politikası değişmedi. Sınır testi eklendi. Engel olan ETHFIUSDT PAPER pozisyonu
5 geçerli funding olayıyla kapandı; worker ürün/saatlik/PAPER işleri `SUCCESS`
ve genel sağlık `HEALTHY` oldu.

Son kontrollü attestation: `execution_mode=PAPER_ONLY`,
`may_send_real_orders=false`, `sqlite_quick_check=ok`, worker/ürün/saatlik/PAPER
durumları `HEALTHY/SUCCESS`, iki observer `CURRENT` ve payload SHA-256 geçerli.
`market_behavior_minute_features`, `causal_minute_external_evidence` ve
`exact_time_macro_events` için Supabase outbox satırı ve mirror trigger'ı
sıfırdır; SQLite otoritesi korunur. Supabase audit mirror bazı büyük
`asset_risk_snapshots` partilerinde mevcut `backlog_pending` durumunu taşımaya
devam ediyor, fakat diğer audit tabloları drain koşularında ilerliyor. Yeni
observer tabloları mirror'a açılmadı.

Yerel odaklı testler 5/5, tam suite 210/210 geçti; `git diff --check` temizdi.
PAPER_ONLY, kanıt eşikleri, strateji politikası, model terfisi ve gerçek emir
yetkisi değiştirilmedi; yeni model/eşik araması açılmadı. Küçük VM'de 1,2 GB
SQLite `quick_check` taraması I/O sınırı nedeniyle birkaç dakika sürebiliyor;
tanılamada aynı taramayı paralel başlatma. Sıradaki iş yeni yetki vermek değil,
doğal davranış ve kesin-zamanlı olay verisinin birikimini gözlemlemek ve yeterli
olay/dakika oluşmadan performans iddiası üretmemektir.

# 2026-08-30 — Observer dağıtım preflight'i deterministik ve sıfır hatalı

`5ada909` checkpoint'inden devam edildi. Bulut onayı öncesi son operasyonel
boşluk kapatıldı: `scripts/observer_deployment_readiness.py` iki observer'ın
service güvenlik/resource sözleşmelerini, runtime bağımlılıklarını, health sürüm
eşleşmelerini, üç tablonun Supabase allowlist dışında kalmasını, PAPER yönetişim
yetkisini ve aktarılması gereken bütün doğrudan/yerel yardımcı kaynakların
SHA-256 değerlerini tek raporda doğruluyor. Ana dosya yeni fakat yardımcı modül
eski kalmış bir parçalı dağıtım hash karşılaştırmasıyla yakalanabilir.

Gerçek yerel preflight sonucu: `failure_count=0`,
`ready_for_controlled_cloud_install=true`, `paper_authority_safe=true`;
`cloud_install_performed=false` ve `timers_started=false`. Eksik dosyalı sahte
paketin fail-closed reddedildiği de test edildi. Preflight komutu ve transfer
sonrası aynı hash kontrolü `docs/CLOUD_PAPER_RUNTIME.md` kurulum sırasına eklendi.

Buluta hiçbir dosya aktarılmadı; service/timer kurulmadı veya başlatılmadı.
Runtime DB, Supabase, PAPER_ONLY, kanıt eşikleri, strateji ve gerçek emir yetkisi
değişmedi. Sıradaki dış adım hâlâ kullanıcının açık bulut onayıdır.

# 2026-08-30 — Davranış hattı uçtan uca ve sağlık kaydı kurcalamaya karşı doğrulandı

`234f537` checkpoint'inden devam edildi. Bulut öncesi sentetik entegrasyon testi
tek SQLite üzerinde bütün yolu doğruladı: olay öncesi kapanmış davranış dakikası,
FOMC'nin resmî yayın saati, AGCA'nın beş saniye sonraki gerçek gözlemi, olay
dakikasındaki defter/işlem/OI/tasfiye kanıtı ve yalnız 5 dakikalık ufuk kapandıktan
sonra oluşan fiyat tepkisi. Olay geçmiş dakikaya yazılmadı; tepki olay öncesi
fiyata göre tam +%2 ölçüldü. Bu sentetik değer başarı kanıtı değildir, yalnız
zaman ve veri sözleşmesi doğrulamasıdır.

Market-behavior sağlık kaydı artık yalnız çıktı raporunun SHA-256 değerini değil,
kendi kanonik içeriğinin `health_sha256` değerini de taşıyor. Cloud attestation
bu self-hash'i yeniden hesaplıyor; satır sayısı gibi tek bir alan sonradan
değiştirilirse kayıt taze görünse bile `INVALID` oluyor. Exact-macro sağlık
kaydındaki mevcut self-hash sözleşmesiyle iki observer aynı kurcalama korumasına
geldi.

Tam suite 208/208 geçti. Bulut, service/timer, runtime DB, Supabase, PAPER_ONLY,
kanıt eşikleri, strateji politikası ve gerçek emir yetkisi değişmedi. Observer
paketi hâlâ yalnız yerelde hazırdır; kurulum için açık bulut onayı beklenir.

# 2026-08-30 — Davranış-önce piyasa okuma ilkesi ölçüm sözleşmesine dönüştürüldü

Kullanıcının bilgi asimetrisinin haber erişiminden davranış/akış okumasına
kaydığı ürün tezi `docs/DECISIONS.md` Karar 027 olarak kalıcılaştırıldı. Bu tez
gizli el/manipülasyon varsayımı değildir: haber geniş biçimde aynı anda
bilinirken avantajın haber öncesi konumlanma, ilk kalabalık refleksi, likidite
absorpsiyonu, tükenme ve tepkinin devam/tersine dönüşünü ölçmekte olabileceği
test edilebilir hipotezidir. Haber etiketi tek başına yön veya işlem üretmez.

`BEHAVIOR_EVENT_FORWARD_MEASUREMENT_V2`, olaydan önceki son kapanmış davranış
dakikasını fiyat tabanı yapıyor. Olay dakikasında 20 kademe defter dengesizliği,
agresif işlem baskısı, spread, OI değişimi, funding ve iki yönlü tasfiye notionalı
birlikte korunuyor. Ham fiyat tepkisi olay öncesi orta fiyata göre 1, 5, 15, 60,
240 ve 1.440 dakikada ölçülüyor; hedef dakika kapanmadan sonuç yok. Önceki 1h
hesabındaki bir dakikalık fazla ileri kayma doğal veri başlamadan düzeltildi.

Absorpsiyon, dayanıklılık, tükenme veya tersine dönüş etiketi/eşiği icat edilmedi.
Aynı global olayda farklı coin satırlarının korelasyonlu olduğu raporda açıkça
işaretleniyor; bağımsız kanıt birimi olay/dakikadır. Mevcut yerel runtime DB'de
observer tabloları hâlâ kurulmadığı için ölçüm sonucu sıfır ve blocker görünürdür.
Yeni model/eşik araması açılmadı.

Tam suite 206/206 geçti. Bulut, service/timer, runtime DB, PAPER_ONLY, kanıt
eşikleri, strateji politikası ve gerçek emir yetkisi değişmedi. Bulut onayı
gelirse `6d49e8d` ile hazırlanmış kontrollü kurulum planına bu davranış-önce
ölçüm sözleşmesiyle devam edilir; doğal veri oluşmadan performans iddiası yoktur.

# 2026-08-30 — Bulut onayına kadar exact-macro/behavior gözlem paketi hazır

`1bed153` checkpoint'inden devam edildi. Bulut onayı verilmeden yapılabilecek
yerel hazırlık tamamlandı. Resmî FOMC takvimini kaynak olarak kullanan ayrı
`observe_exact_macro_events.py` gözlemcisi eklendi. Normal günlerde takvimi en
fazla 24 saatte bir kontrol eder; yalnız planlı karar zamanının 15 dakika öncesi
ile 90 dakika sonrası arasında dakikada bir takvim kontrolü yapar. Resmî toplantı
sayfası görünürse yalnız ilgili karar gününün sayfasını alır ve açık kesin saatli
belgeleri kaydeder. Ağ hatası normal zamanda 15 dakikalık geri çekilmeye girer.
Ham sayfa/payload saklanmaz.

2026 resmî takvimi canlı kaynaktan yeniden üretilebilir raporla doğrulandı:
8 düzenli karar zamanı; Ocak ve Aralık kış saatiyle 19:00 UTC, diğer kararlar
yaz saatiyle 18:00 UTC. Rapor
`reports/federal_reserve_fomc_schedule_2026.json` içindedir. Gözlemcinin erken,
zamanında, geç, sayfa-yok ve kaynak-hata senaryoları sahte saatle test edildi.
Resmî yayın saati ve gerçek AGCA gözlem saati ayrı kalır; nedensel erişim zamanı
her zaman geç olanıdır.

`agca-exact-macro-observer.service/.timer` 40 saniye timeout, 128 MB bellek,
%25 CPU, tekil kilit, özel dosya izinleri ve yalnız PAPER_ONLY ile hazırlandı.
Timer kalıcı catch-up yapmaz. Sağlık kaydı atomik ve SHA-256 korumalıdır; mevcut
cloud attestation artık exact-macro observer durumunu da `CURRENT/MISSING/STALE/
INVALID` olarak taşır. Market-behavior ve exact-macro servisleri için kontrollü
ilk koşu, tablo/outbox/hash/PAPER doğrulama, enable ve fail-closed rollback
adımları `docs/CLOUD_PAPER_RUNTIME.md` içine yazıldı.

Hiçbir service/timer buluta kurulmadı, etkinleştirilmedi veya başlatılmadı;
runtime DB ve dış servis durumu değişmedi. PAPER_ONLY, kanıt eşikleri, strateji
politikası, allocation ve gerçek emir yetkisi değişmedi. Tam yerel suite 206/206
geçti. Mac'teki ağsız/fail-closed test mesajları beklenen senaryolardır.

Bulut onayı gelirse sıradaki işlem yalnız dokümante edilmiş geri alınabilir
kurulumdur: önce checkpoint adlı rollback kopyası, sonra iki servisin kontrollü
tek koşusu, sağlık/hash/yerel-only tablo/outbox doğrulaması ve ancak hepsi geçerse
timer enable. Doğal veri birikmeden performans veya öğrenme iddiası yapılmaz.

# 2026-08-30 — FOMC resmî saati ile gerçek gözlem saati nedensel olarak ayrıldı

`955bb08` checkpoint'inden devam edildi. Kesin-zamanlı makro sözleşmesinde kritik
bir lookahead riski kapatıldı: resmî belge yayın saati ile AGCA'nın kaydı ilk
kez gördüğü saat artık ayrı saklanıyor. Nedensel `available_at` bu iki zamanın
geç olanıdır. Böylece önceden bilinen takvim olayı yayın anına kadar görünmez;
yayından sonra geç bulunan belge ise geçmiş yayın dakikasını yeniden yazamaz ve
yalnız gerçek gözlem dakikasında kullanılabilir olur. Eski tablo iki nullable
kolonla geriye uyumlu yükselir; aynı olay tekrar görülürse en erken gerçek gözlem
korunur.

Mevcut macro cycle altı saatlik cadence kullanıyor. FOMC kaynağını bu cycle'a
doğrudan bağlamak olayı saatler geç yakalayabilir; bu nedenle anlık kanıt gibi
yanlış sunacak bir bağlantı yapılmadı. Sıradaki dar iş, resmî FOMC yayın
pencerelerini düşük istek bütçesiyle izleyen ayrı bir cadence tasarlamak ve
yerelde sahte saatle erken/ zamanında/ geç gözlem senaryolarını doğrulamaktır.
Kullanıcının açık onayı olmadan hiçbir timer buluta kurulmayacak veya
başlatılmayacaktır.

Bulut ve runtime DB durumu değiştirilmedi. PAPER_ONLY, kanıt eşikleri, strateji
politikası ve gerçek emir yetkisi değişmedi; model/eşik araması açılmadı.

# 2026-08-30 — Forward olay/davranış ölçüm zemini hazır, doğal kapsam henüz sıfır

`fdc8fac` checkpoint'inden temiz devam edildi. Kanonik yerel
`crypto_data.db` salt-okunur ve değişmez modda denetlendi. Yeni
`market_behavior_minute_features` ve `causal_minute_external_evidence` tabloları
bu veritabanında henüz oluşmamış; dolayısıyla doğal davranış dakikası, dış olay,
aynı-dakika eşleşmesi ve kapanmış forward sonuç sayısı sıfırdır. Bu durum sonuç
veya başarı gibi sunulmadı; `reports/behavior_event_forward_coverage.json`
dosyasında açık blocker olarak kaydedildi.

Forward sonucu ileride ölçebilmek için kapanmış davranış dakikasına ilk ve son
alış/satış orta fiyatı eklendi. Orta fiyat yalnız geçerli en iyi alış ve satıştan
üretiliyor; yoksa satırın mevcut kalite kapısı kapanıyor. Ham payload saklama,
işlem sinyali veya model girdisi eklenmedi. Eski SQLite şeması iki nullable
kolonla geriye uyumlu biçimde yükseltilebiliyor.

`BEHAVIOR_EVENT_FORWARD_MEASUREMENT_V1` salt-okunur denetimi, dış olayın
`available_at` dakikasındaki uygun davranış satırını ve değişmeyen mevcut
1h/4h/24h ufuklarda kapanmış fiyat satırı bulunup bulunmadığını sayıyor. Coin
etiketli olay yalnız ilgili coinle; global makro olay bütün gözlenen piyasalarla
eşleşiyor. Bu yalnız kapsam/ölçülebilirlik sayımıdır; yeni eşik, model, sinyal,
işlem veya politika araması değildir.

Buluta kurulum/başlatma ve runtime DB mutasyonu yapılmadı. PAPER_ONLY, kanıt
eşikleri, strateji politikası ve gerçek emir yetkisi değişmedi. Sıradaki doğru
adım yeni araştırma açmak değil, kullanıcının açık bulut onayı olmadan yerel
sözleşme/test kalitesini korumak ve doğal gözlem verisi oluşmadığı sürece
forward performans sonucu üretmemektir.

# 2026-08-30 — Resmî FOMC saatleri gerçek örnekle doğrulandı ve dakika deposuna eklendi

`526277b` checkpoint'inden devam edildi. Federal Reserve'in resmî FOMC toplantı
sayfalarındaki açık `Released <tarih> at <saat>` satırlarını ve aynı satırdaki
resmî belge bağlantısını zorunlu tutan `FEDERAL_RESERVE_EXACT_EVENTS_V1`
toplayıcısı eklendi. ABD Doğu saati `America/New_York` kurallarıyla UTC'ye
çevriliyor; böylece kış saati 14:00 yayını 19:00 UTC, yaz saati yayını 18:00 UTC
oluyor. Kaynak toplantı sayfası, belge URL'si, zaman dayanağı ve indirilen HTML
SHA-256 değeri korunuyor. Açık kesin saat yoksa sayfa fail-closed reddediliyor.

Resmî kaynaktan gerçek, küçük ve yeniden üretilebilir 2026 örneği üretildi:
3 toplantı sayfası, 10 belge; 3 FOMC açıklaması, 3 uygulama notu, 3 tutanak ve
1 projeksiyon belgesi. Örnek `reports/federal_reserve_exact_events_2026_sample.json`
dosyasındadır. Runtime SQLite'a tarihsel backfill yapılmadı. Uçtan uca test,
resmî olayın `exact_time_macro_events` sözleşmesinden geçip yalnız 18:00-18:01
UTC erişilebilirlik dakikasına girdiğini doğruladı.

Nedensel dış olaylar artık yalnız kapanmış dakika JSON'unda kalmıyor:
`causal_minute_external_evidence` yerel SQLite tablosuna olay kimliği + dakika
ile idempotent yazılıyor ve davranış özellikleriyle aynı 30 günlük saklama
sınırında budanıyor. Tablo Supabase mirror allowlist'ine eklenmedi. Bu veriler
halen yalnız gözlemseldir; model, sinyal veya işlem kararına bağlanmadı.

Buluta kurulum, servis/timer başlatma veya runtime veritabanı yazımı yapılmadı.
PAPER_ONLY, kanıt eşikleri, strateji politikası, allocation ve gerçek emir
yetkisi değişmedi; yeni model/eşik araması açılmadı. Odaklı testler 12/12, tam
suite 194/194 geçti. Tam testteki ağsız/fail-closed mesajları beklenen test
senaryolarıdır.

Sıradaki ürün işi: canlıya yetki vermeden, kesin-zamanlı dış kanıt satırlarıyla
aynı dakikadaki piyasa davranışı arasında yalnız ölçüm amaçlı bir forward
eşleşme/sonuç tablosu tasarlamak. Önce yeterli doğal dakika ve farklı olay türü
birikimini say; veri yetersizse model veya eşik araması yapma. Kullanıcının açık
onayı olmadan hazırlanmış service/timer'ı buluta kurma veya başlatma.

# 2026-08-30 — Gözlem sağlığı attestation'a, dış kanıtlar nedensel dakikaya bağlandı

Temiz `6610102` piyasa-davranışı checkpoint'inden devam edildi. Ayrı ve atomik
`market-behavior-health.json` kaydı artık mevcut cloud attestation payload'ında
yer alıyor. Sözleşme sürümü, PAPER_ONLY, gerçek emir kapalı, ham payload
saklanmıyor ve SHA-256 biçimi birlikte doğrulanıyor; sonuç `CURRENT`, `MISSING`,
`STALE` veya `INVALID` olarak fail-closed raporlanıyor. Bu gözlem sağlığı worker
sağlığını veya emir yetkisini değiştirmiyor.

Haber, makro, on-chain ve balina kanıtları için tek
`CAUSAL_MINUTE_EVIDENCE_V1` çizgisi eklendi. Olaylar gerçekleşme zamanına göre
değil, AGCA'nın gerçekten erişebildiği `available_at` dakikasına yazılıyor.
Geç gelen olay geçmiş dakikayı değiştiremez; gün düzeyindeki veya saat dilimsiz
zamanlar reddedilir. Mevcut haberlerde yayın/toplama zamanının geç olanı,
on-chain'de toplama zamanı, balinada blok/gözlem zamanının geç olanı kullanılır.
Kesin zamanlı makro için değişmez kimlikli yerel giriş sözleşmesi hazırdır;
yalnız gün taşıyan mevcut FRED seri gözlemleri kesin dakikaya yükseltilmemiştir.
Dakika sorguları bütün geçmişi okumaz, yalnız ilgili 60 saniyelik pencereyi
getirir. Bu kanıtlar gözlemseldir; model veya işlem girdisine terfi edilmedi.

Hazırlanmış market-behavior service/timer buluta kurulmadı veya başlatılmadı;
dış servis durumu değiştirilmedi. PAPER_ONLY, kanıt eşikleri, strateji
politikası, allocation kapıları ve gerçek emir yetkisi değişmedi; yeni
model/eşik araması yapılmadı. Odaklı testler 12/12, tam yerel suite 189/189
geçti. Mac'teki tam testte beklenen ağsız/fail-closed senaryo mesajları görüldü;
test sonucu temizdir.

Sıradaki iş: bu yerel sözleşmelerin kod incelemesini sürdürmek; resmî Federal
Reserve yayın takviminden gerçek `occurred_at`/`available_at` taşıyan küçük,
yeniden üretilebilir bir örnekle kesin makro girişini doğrulamak. Kullanıcının
açık onayı olmadan service/timer'ı buluta kurma veya başlatma. Kanıtları model
ya da politika girdisine terfi etme.

# 2026-08-29 — Piyasa-geneli davranış okuma zemini ve hafif canlı gözlem hazır

Ana ürün amacı kök `AGENTS.md` çalışma sözleşmesine kalıcı olarak yazıldı:
AGCA sabit coin listesini tahmin eden bir rapor sistemi değil; teknik/temel
analiz, emir akışı, vadeli veriler, tasfiyeler, on-chain, balina, makro ve kritik
haber kanıtlarını birleştirerek piyasayı okuyan, fırsatı kendisi bulan, risk
yöneten ve sonuçlarından öğrenen piyasa-geneli bir trader sistemi olmalıdır.
Başarı; rapor, backtest veya tekil kazanç değil, farklı piyasa rejimlerinde
yeterli sayıda canlı PAPER işlemde bütün masraflar sonrası getiri ve kontrollü
düşüşle kanıtlanacaktır. Gerçek para hedeflenen son aşamadır; garanti veya mevcut
yetki değildir. Kanıt kapıları ve kullanıcının ayrıca açık onayı olmadan gerçek
emir mümkün değildir.

Bu oturumda önce ücretsiz veri kaynakları gerçek dosya/API örnekleriyle
araştırıldı. OKX'in resmi ücretsiz arşivinde Mart 2023'ten beri 400 seviyeli L2
emir defteri bulundu. Gerçek LQTY arşivi tam tarandı: 3.491.606 olay, 1.440 tam
snapshot, 400 alış + 400 satış seviyesi, 24 saat, en büyük zaman boşluğu 1.099
ms, sıfır bozuk/geri giden zaman damgası. Gelecek olayın kapanmış dakikayı
değiştiremediğini kanıtlayan nedensel 1 dakikalık özellik çıkarıcı yazıldı.
Altı LQTY günü ve yedi coinlik ilk boru-hattı örneklemi üretildi; bunlar başarı
kanıtı veya hedef coin evreni değildir. Aynı veride model/eşik araması açılmadı.

Örnek coin listesi bağımlılığı kaldırıldı. OKX'in bütün aktif USDT sürekli
vadeli piyasalarından, yalnız anlık işlem yapılabilirlik ölçüleriyle dinamik
kripto evreni kuruluyor; OKX `instCategory` kullanılarak hisse/emtia bağlantılı
sözleşmeler dışlanıyor. Son gerçek gözlem 74 geniş piyasa buldu. Hafif tarayıcı
BTC/ETH bağlamı ve 10 dinamik fırsat adayı olmak üzere yalnız 12 piyasayı derin
incelemeye yönlendiriyor. Bu dikkat yönlendirmesidir, işlem sinyali değildir.

Seçilen piyasalarda ücretsiz resmi OKX REST verisinden 50 kademe emir defteri,
son 100 işlem, OI ve funding birlikte alındı; 12/12 kaynak sözleşmesi geçti.
Tasfiye `liquidation-orders` WebSocket kanalı gerçek bağlantıda aboneliği kabul
etti ve olay da gözlendi. Kanal erişilemiyor / kanal sağlıklı fakat olay yok /
olay var durumları ayrıldı; böylece sağlıklı boş pencere artık `VERİ EKSİK`
sayılmaz. Tasfiye coin, yön, iflas fiyatı, miktar, zaman ve yaklaşık notional
olarak ayrıştırılır; derin listenin dışındaki olay yeni dikkat yükseltmesi olur.

Kayan canlı pencere ham payload saklamadan kapanmış dakika üretir: 5/20/50
kademe dengesizliği, son işlem baskısı snapshot'ları, spread, OI değişimi,
funding ve tekilleştirilmiş tasfiyeler. Gelecek örnek geçmiş dakikayı değiştiremez;
eksik kaynak, büyük örnek boşluğu veya tasfiye kanal arızası satırı öğrenmeye
kapatır. İki ayrı gerçek dakikada 12/12 satır kalite kapılarını geçti. Nihai JSON
yaklaşık 13 KB; geçici SQLite deneyi 12 satırla 36 KB idi, Supabase outbox'a bu
tablodan sıfır satır girdi ve geçici DB silindi.

Yerel-only `market_behavior_minute_features` SQLite tablosu idempotent upsert ve
30 günlük otomatik saklama sınırıyla hazırlandı; `REMOTE_RUNTIME_TABLES` dışında
olduğu için Supabase'e aynalanmaz. Sürekli gözlem servis/timer dosyaları depoda
hazırdır: her çevrimde canlı evreni yeniler, tek WebSocket'i dakika boyunca açık
tutar, çakışmayı kilitler, 384 MB bellek ve %50 CPU sınırı kullanır, atomik özel
izinli SHA-256 sağlık kaydı üretir ve yalnız PAPER_ONLY kabul eder. Kullanıcı
onayı olmadan Google Cloud'a kurulmadı veya başlatılmadı. Son okuma: mevcut GCE
`agca-runtime` 30 GB disk, 11 GB kullanım, 18 GB boş alan. Ham tarihsel arşivler
bu üretim diskine veya Supabase'e konmayacaktır; gerekirse yerelde/geçici olarak
tek tek işlenip silinecektir.

Ücretsiz tarihsel kaynak durumu: OKX L2 kullanılabilir; Coin Metrics community
tasfiye kataloğu gerçek çağrıda boş döndüğü için ücretsiz geçmiş tasfiye kabul
edilmedi. CoinGlass geçmiş tasfiye sunar fakat plan/geçmiş sınırları vardır;
Tardis tick tasfiye/funding/L2 sunar ve yaklaşık 350 USD/aydan başlar. Hiçbir
abonelik/satın alma yapılmadı. Federal Reserve kesin yayın saati veren ücretsiz
birinci kaynak olarak doğrulandı. Haber/on-chain/balina zaman çizgisi bir sonraki
uygulama aşamasıdır.

Güvenlik ve doğrulama: PAPER_ONLY, kanıt eşikleri, strateji politikası ve gerçek
emir yetkisi değiştirilmedi. `may_send_real_orders=False`. `requirements-runtime`
içine `certifi` ve `websockets` eklendi. Yeni odaklı testler 43/43, tam proje
testleri 184/184 geçti; `git diff --check` temizdi. Mac'te `systemd-analyze`
bulunmadığı için systemd birimleri o araçla yerelde doğrulanamadı. Bulut dış
durumu bu servis açısından değişmedi.

Başlangıç checkpoint'i `a5d2df7`; dal
`checkpoint/agca-20260826-supabase-recovery`. Bu bölümün bulunduğu temiz kapanış
commit'i oturumun nihai checkpoint'idir; `git rev-parse HEAD` ile doğrulanmalıdır.

Bir sonraki oturumda yapılacaklar: önce bu handoff ve kök `AGENTS.md` tamamen
okunmalı, Git'in temiz checkpoint'te olduğu doğrulanmalı. Piyasa davranış gözlem
sağlığını mevcut cloud attestation sözleşmesine bağla; ardından kesin zamanlı
haber/makro, on-chain ve balina kanıtlarını aynı nedensel dakika çizgisine ekle.
Kullanıcının açık onayı olmadan hazırlanan market-behavior service/timer'ı buluta
kurma veya başlatma. Yeni model/eşik araması açma ve tarihsel boru-hattı
örneklerini başarı gibi sunma.

Tam kopyalanabilir açılış mesajı:

> AGCA yerel projesinde çalış: /Users/elifzilan/Documents/AG-Crypto-Analyst. Kök AGENTS.md ile docs/LAST_SESSION_HANDOFF.md dosyasını tamamen oku ve Git durumunu kontrol et. 2026-08-29 temiz piyasa-davranışı checkpoint'inden devam et. Önce hafif market-behavior gözlem sağlığını mevcut cloud attestation sözleşmesine bağla; sonra kesin zamanlı haber/makro, on-chain ve balina kanıtlarını aynı nedensel dakika çizgisine ekle. Hazırlanmış service/timer dosyalarını açık onayım olmadan buluta kurma veya başlatma. PAPER_ONLY, kanıt eşikleri, strateji politikası ve gerçek emir yetkisini değiştirme; yeni model/eşik araması açma; sade Türkçe kullan.

# 2026-08-29 — Çoklu zaman dilimi ve vadeli veri araştırması kapandı

Kullanıcıya rapor verirken sade Türkçe kullanılmalı; teknik bir ölçü yazılırsa
ne anlama geldiği de açıklanmalıdır. Kullanıcı açıkça `oturum bitti` demeden
kapanış raporu veya sonraki oturum mesajı gönderilmemelidir.

Bu oturumda aylık → haftalık → günlük → 4 saat → 1 saat kapalı mum bağlamı,
piyasa türüne göre ayrı uzmanlar ve geçmiş ayın masraf sonrası sonucuna göre
işlem seçimi sınandı. Bütün sonuçlar tarihsel geliştirme kanıtıdır; canlı başarı
olarak sunulmamalıdır.

- Piyasa türüne göre uzman modeller 4 saatlik yön isabetini altı dönemin
  altısında artırdı (%44,96 → %46,01) fakat olasılık kalitesini bozdu ve masraf
  sonrası ortalama sonuç -%0,083 kaldı. İleri PAPER sınavına alınmadı.
- Geçmiş 30 günde açıkça olumlu kalan piyasa türü/yön/güven koşullarını sonraki
  30 güne taşıyan seçici yöntem 4 saatte 1.822 işlem seçti (%13,2 kapsam), yön
  isabeti %48,52 ve masraf sonrası ortalama -%0,049 üretti. Altı dönemin yalnız
  ikisi artıydı; ileri PAPER sınavına alınmadı.
- Mevcut 23 vadeli piyasa ölçüsü (fonlama, açık pozisyon, spot-vadeli farkı,
  alıcı/satıcı baskısı; coin ve BTC) çoklu zaman dilimli 4 saatlik modele eklendi.
  12 coin ve 82.664 örnekte yön isabeti %45,38 → %45,69 oldu; yalnız 3/6 dönem
  kazandı, olasılık kalitesi 0,6048 → 0,6116 kötüleşti (düşük daha iyi) ve masraf
  sonrası sonuç -%0,094 kaldı. Aday reddedildi.
- Binance arşivindeki tek sıfır BTC açık-pozisyon saati uydurma değerle
  doldurulmadı; yalnız ona bağlı bağlamlar dışlandı. Toplam eksik bağlam %0,046.

Canlı Unified Forecast, PAPER trader, strateji politikası, kanıt eşikleri ve
gerçek emir yetkisi değiştirilmedi. PAPER_ONLY korunuyor. Son tam yerel test
141/141 geçti. Son temiz checkpoint `5c946ae`; dal
`checkpoint/agca-20260826-supabase-recovery`.

Sonraki araştırmanın konusu yeni model/ayar aramak değil, mevcut veride olmayan
ve hareketin nedenini zamanında açıklayabilecek yeni bilgi kaynaklarının
uygulanabilirliğidir: tarihsel tasfiyeler, geçmiş emir defteri dengesizlikleri
ve kesin zamanlı ekonomik/haber olayları. Önce ücretsiz ve güvenilir kaynakların
zaman damgası, geçmiş derinliği, coin kapsamı, yeniden üretilebilirliği ve kullanım
şartları araştırılmalı; veri yeterli değilse kullanıcıya ücretli seçeneklerin
maliyeti ve farkı sunulmalı, onay alınmadan satın alma/abonelik yapılmamalıdır.
Yeni veri bulunmadan aynı fiyat/teknik/vadeli özelliklerle yeni model veya eşik
araması açılmamalıdır.

Bu oturumda değiştirilmemesi gerekenler: PAPER_ONLY, üretim/eylem yetkisi,
kanıt eşikleri, strateji dağıtım politikası ve gerçek emir kapıları.

# 2026-08-28 — Supabase RLS security alert resolved

The “AGCA kritik altyapı güvenlik uyarısı” came from the hourly monitoring task,
which correctly reported 10 `agca_runtime` tables with RLS disabled. A direct
Postgres audit established that `anon` and `authenticated` had neither schema
USAGE nor table grants, so there was no evidence of public disclosure or
unauthorized mutation. The configuration was nevertheless incomplete hardening.

Migration `database/migrations/20260828_private_runtime_rls.sql` revoked all
Data API role access and enabled RLS on all 10 missing tables without policies;
the private mirror owner remains the sole writer. Post-change audit: 43/43
runtime tables have RLS enabled, zero anon/authenticated table grants, and both
roles lack schema USAGE. Runtime table creation now carries a versioned
`PRIVATE_RLS_V1` schema signature, enables RLS and revokes Data API roles so a
new mirrored table cannot regress silently. Full suite: 124/124. Checkpoint
`56122d5` was deployed with rollback
`/home/kursadsalis/AG-Crypto-Analyst-cloud-supabase-rls-rollback-56122d5` and
matching runtime-store SHA-256. Direct post-hardening evidence returned a
HEALTHY `PAPER_ONLY` attestation (`may_send_real_orders=0`, SQLite `ok`), remote
mirror success at 2026-08-28T07:10:03Z, active worker/attestation/drain timers,
and zero local outbox backlog. The security alert is resolved.

# 2026-08-28 — Advanced technical analysis entered the 1h direction model

The former RSI/MACD/EMA research-only context was not an advanced technical
prediction layer. `ADVANCED_TECHNICAL_FEATURES_V1_FIXED` now derives 24 causal
features from closed 1h OHLCV: RSI, multi-EMA spreads, normalized MACD,
Bollinger position/width, ATR, ADX, Stochastic, Williams %R, CCI, MFI, VWAP
deviation, OBV slope, volume anomaly, dynamic support/resistance distance,
72h Fibonacci/range position, trend efficiency, candle body/wicks, breakout
and 168h range position. A test proves a future candle cannot change them.

The rolling 1h HistGradientBoosting model now learns a nonlinear
`NONLINEAR_TECHNICAL_DIRECTION_COMPOSITE` from the existing 12 market features
plus those 24 technical features. This is the bounded, auditable indicator
synthesis mechanism: it relearns combinations on the preceding 120 days at
the existing 30-day artifact refresh, but cannot invent data, change thresholds,
mutate strategy policy or obtain order authority.

Six sequential 30-day walk-forward blocks (54,301 predictions) measured the old
rolling model at 42.426% accuracy / 0.630754 Brier and the advanced technical
model at 42.727% / 0.628099. Accuracy improved in 4/6 blocks and Brier in 6/6.
Only the already-selected 1h 50/50 direction ensemble changed; 4h/24h, price
targets, 70% action threshold, costs, evidence gates, PAPER policy and real-order
authority remain unchanged. Retraining used 35,750 causal rows and the artifact
round-trip passed. Full suite: 123/123. Checkpoint `ae4a354` was deployed with
rollback `/home/kursadsalis/AG-Crypto-Analyst-cloud-advanced-technical-rollback-ae4a354`.
All four deployed runtime-file SHA-256 values match locally. The worker completed
a graceful restart and is active/running in `PAPER_ONLY`. The first natural
advanced-technical forecast and its forward settlements remain to be verified;
historical lift is not live forward proof.

# 2026-08-28 — Measured 1h direction ensemble implemented

The production-facing Unified Forecast 1h direction probabilities now blend the
unchanged incumbent 50/50 with one fixed rolling HistGradientBoosting model.
The challenger trains only on the preceding 120 days, uses a fixed 45-day
half-life and the first 12 existing price/market features. Six sequential
30-day walk-forward blocks measured incumbent accuracy 41.58% versus 42.34%
rolling and 42.37% blended; the blend improved accuracy in 4/6 blocks and Brier
score in 6/6 blocks (0.63281 to 0.62975). The same approach was rejected for 4h
and 24h, so those horizons remain untouched.

This changes direction probabilities only. Price targets, the 70% action
threshold, 10 bps cost assumption, risk veto, PAPER strategy policy, evidence
gates and real-order authority are unchanged. The sklearn artifact is stored as
once-serialized bytes with its own SHA-256 so cache verification is stable.
Local retraining used 37,011 causal 1h rows from 2026-04-30 through 2026-08-26;
the saved artifact reloaded with both outer and inner hashes valid. Full local
suite: 122/122. Checkpoint `d7f25b2` was deployed to `agca-runtime` with rollback
copy `/home/kursadsalis/AG-Crypto-Analyst-cloud-rolling-1h-direction-rollback-d7f25b2`.
Local/deployed SHA-256 values match for both changed runtime files. The worker
restarted active/running with `AGCA_EXECUTION_MODE=PAPER_ONLY`. The first
naturally issued and then settled live bundle remains to be verified; do not
describe the historical lift as live forward performance.

# 2026-08-27 — Trader success KPI corrected from class accuracy to economics

A read-only audit found that the alarming low three-class direction scores were
not the PAPER trader's realized performance. The clean evidence cohort currently
has 9 closed trades, all 24h SHORT: 5 winners, +108.5039 USDT net P&L,
147.2592 USDT positive net versus 38.7553 USDT negative net, profit factor 3.80.
This is promising but still one trade below the unchanged 10-closed-trade
allocation evidence gate.

The committee report now leads with action count/hit, net-after-cost action
utility and profit factor. UP/DOWN/NEUTRAL direction hit remains visible only as
a model diagnostic and is explicitly not labeled trader success. The PAPER
daily report also records lifetime profit factor and closed-trade maximum
drawdown. No model, selection rule, evidence threshold, allocation policy or
order authority changed. The follow-up completed the previously partial report
split: `paper_trader_performance()` reads only clean, evidence-eligible CLOSED
`paper_agent_positions` and renders a separate primary PAPER economics table by
horizon/direction. It cannot confuse Forecast Engine `shadow_action` (currently
zero on the audited 24h slice) with the PAPER agent's actual nine trades.
Full local suite: 121/121. The first live render caught and rejected a stale
hard-coded cohort identifier; the report now imports the PAPER agent's canonical
`FORWARD_COHORT` constant. Active evidence advanced to 10 closed trades and
+111.3071 USDT net P&L. Checkpoint `0471e8a` was deployed using rollback
`/home/kursadsalis/AG-Crypto-Analyst-cloud-paper-economics-rollback-f8a4a73`.
The worker returned active in `PAPER_ONLY`. The live v1.6 report now renders:
24h SHORT, 10 closed, 6 winners, +111.31 USDT net P&L, profit factor 3.87 and
37.64 USDT maximum closed-trade drawdown. `committee_report_snapshots` outbox
is zero. A direct Supabase read returned report date 2026-08-28, created at
2026-08-28T06:10:17.885471+00:00, version 1.6.0, and confirmed both the PAPER
economics heading and exact row. The previously partial task is complete.

# 2026-08-27 — Spot Flow Feature Challenger V1 implemented

The next product step was implemented: collected money-flow evidence is now a
real model input in a separate point-in-time challenger, not merely report
decoration. Official Binance Spot closed candles were re-fetched for the fixed
13-symbol cohort to populate 405 days of taker-buy quote volume without changing
labels or prices. Minimum per-symbol valid flow coverage is 99.19%.

The frozen feature set adds own-symbol 1h/4h/24h taker imbalance, 4h flow
acceleration, 24h price confirmation, and BTC 1h/4h/24h taker imbalance to the
audited price/volume/regime features. Tests prove that a future candle cannot
change the feature vector. The final EXAM segment was not opened.

Development evidence was negative and is intentionally preserved: 1h Brier
skill -1.18%, 4h -0.41%, and 24h +0.24%; normalized MAE skill was 0.00%, -0.05%
and -0.53% respectively. No horizon produced a >=70% probability action, so all
three were `DEVELOPMENT_REJECT`. Existing gates were not weakened. The model is
frozen only to collect honest forward ablation evidence and to inform a later
regime-selective challenger; it cannot enter PAPER allocation.

`forecast_feature_challenger_predictions` records probabilities, class,
shadow-only action, direction hit, Brier, action hit and net-after-cost utility.
Every row has `production_eligible=0` and `action_authority=0`. Full local suite:
119/119. Candidate artifact:
`reports/forecast_feature_challenger_candidate.json`; model hash:
`523bb8e1669f48f4830b1c0ada5ccb951e8c0eb1bbdee8a3ddad2b0ae7d3f4fd`.
Checkpoint `b7b2a97` was deployed to `agca-runtime` with rollback copy
`/home/kursadsalis/AG-Crypto-Analyst-cloud-feature-challenger-rollback-b7b2a97`.
The deployed model hash exactly matches the local artifact and reports
`production_eligible=False`, `action_authority=False`. The worker returned
active under `AGCA_EXECUTION_MODE=PAPER_ONLY`. Cloud SQLite has 26 valid closed
flow candles for every one of the 13 symbols and the new forward table contract
was created successfully. The already-issued 20:00 forecast bundle was not
backfilled after deployment because that would be a retrospective seed. The
first causally valid 39-row feature bundle must be created by the next natural
hourly forecast cycle; then verify its SQLite rows, zero authority flags,
settlement metrics when due, and Supabase mirror/outbox state.

# 2026-08-27 — Unified Forecast direction-collapse correction started

The repeated 0/11 and 1/11 direction batches were traced to a product/model
gap, not an infrastructure outage. The live Unified Forecast still uses the
old price-only pooled-ridge artifact; decision-agent context is captured for
diagnosis with `used_by_model=false`. Its latest rolling class predictions had
collapsed toward DOWN (especially 4h and 24h), while the live actual class mix
had drifted. The 13 symbols in a common hourly batch are correlated and must not
be treated as 13 independent votes.

`analysis/forecast_drift_challenger.py` now records a separate, lookahead-safe
class-prior drift challenger. It uses only settlements available before issue
time, requires 10 hourly batches, caps the window at 24 batches, and gives each
batch one vote. Its probabilities, class hit and Brier are stored separately in
`forecast_drift_challenger_predictions` and included in the Supabase runtime
audit allowlist. Unified Forecast output reports incumbent and challenger
performance separately.

A reconstructible read-only audit indicated that this fixed correction would
have improved the observed 1h slice from 43.6% to 48.7% and 24h from 35.6% to
43.9%, but reduced 4h from 52.0% to 49.2%. These are diagnostic results, not
promotion evidence. The implementation therefore remains
`FORWARD_SHADOW_ONLY`, `production_eligible=0`, `action_authority=0`; no evidence
threshold, strategy allocation policy, PAPER gate, or real-order authority was
changed. New money-flow and other agent features still require sufficient
point-in-time history before a separate feature challenger may use them.

Verification: full local suite 117/117 passed; `git diff --check` passed before
documentation closeout. Checkpoint `de7ba57` was deployed to `agca-runtime`
with rollback copy
`/home/kursadsalis/AG-Crypto-Analyst-cloud-direction-drift-rollback-de7ba57`.
The worker restarted active with `AGCA_EXECUTION_MODE=PAPER_ONLY`. Its first
natural hourly run completed `SUCCESS`, created 39/39 forecasts and settled 39
older forecasts. The first challenger bundle contains 13 rows per horizon, each
using 24 prior hourly batches, with zero production eligibility and zero action
authority. The class collapse was absent in that bundle: 1h emitted 10 UP and
3 NEUTRAL, while 4h and 24h emitted 13 UP each. These predictions are still
unsettled and therefore are not evidence of improved accuracy.

The Supabase drain completed with the challenger table marked succeeded and a
zero local outbox backlog for that table. A direct read-only Supabase query
returned 13 rows for each of 1h/4h/24h, all with summed
`production_eligible=0` and `action_authority=0`, latest creation time
2026-08-27T20:02:39.098816+00:00. Next product work is to compare the new
challenger against the unchanged incumbent on naturally settled forward
batches, horizon by horizon, and to build separately validated point-in-time
feature challengers for money-flow/agent evidence. Do not promote from the
reconstructed diagnostic or from one live bundle.

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

# 2026-08-27 — live spot money-flow observation enabled

The first fast money-flow layer is deployed. Binance Spot klines already expose
taker-buy quote volume, but AGCA previously discarded it. `market_klines` now
persists that field and `analysis/money_flow.py` computes closed-candle 1h/4h/24h
aggressor buy quote, sell quote, net USDT flow, buy ratio, price return and
price-direction confirmation. Incomplete, invalid, future-close or stale data
fails closed.

Committee report v1.5 shows the 4h flow table for all 13 tracked symbols. A
controlled 26-hour official Binance Spot backfill populated every symbol. The
first live report showed price-confirmed sell pressure in BTC/ETH/ETHFI and
price-confirmed buy pressure in ARKM/WLD/TIA/LQTY; conflicting cases such as
FIDA/BIO/IO were explicitly unconfirmed. These are point-in-time observations,
not claims of predictive edge.

This layer measures executed spot aggressor flow, not on-chain exchange deposits
or withdrawals. Verified on-chain exchange direction still requires trusted
address ownership evidence. The new flow evidence remains `OBSERVATION_ONLY` and
cannot change strategy weights, promote a model or send orders until its
lookahead-safe forward contribution is measured.

Deployment rollback:
`/home/kursadsalis/AG-Crypto-Analyst-cloud-money-flow-rollback-20260827`.
Full local suite passed 114/114 and cloud target tests passed 3/3. No existing
evidence threshold, strategy policy, allocation gate or real-order authority
changed.
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

# 2026-08-27 — forward closure collector deployed and verified

Checkpoint `8552eac` içindeki OKX forward-closure collector düzeltmesi mevcut
Google Cloud `agca-runtime` VM'ine dosya-seviyesinde ve geri alınabilir biçimde
dağıtıldı. Yerel ve bulut kaynak SHA-256 değeri eşleşti:
`cb59cbfb6f9f8cd7261fbe6ca4358e1b8d59a112c6f760ccbb335495c079cc45`.
Bulut derleme kontrolü geçti. Rollback kopyası:
`/home/kursadsalis/AG-Crypto-Analyst-cloud-forward-closure-rollback-8552eac`.

Kontrollü collector koşusu `SUCCESS` tamamlandı: tek data version ile 259/259
trader, 585 açık gözlem, 2.487 history gözlemi ve sıfır profil hatası. Düzeltme
amacına ulaştı; forward değerlendirme 0'dan LONG için 1 işlem/1 trader ve SHORT
için 9 işlem/3 trader seviyesine ilerledi. İki aday da hâlâ
`FORWARD_COLLECTING`; promotion=0, policy mutation=0 ve real orders=false.
Dış araştırma `COLLECTING_FORWARD_TIME_COVERAGE` durumunda; 264 gözlenmiş trader,
1.694 uygun kapanış ve henüz 0 tam gün coverage kaydetti. Hiçbir eşik değişmedi.

Dağıtım sonrası bağımsız attestation 2026-08-26T22:30:12Z zamanında `HEALTHY`,
`PAPER_ONLY`, `may_send_real_orders=0`, SQLite `ok`, hourly `SUCCESS` ve PAPER
agent `SUCCESS` kaydetti. Canonical payload SHA-256 yerelde yeniden hesaplanarak
geçerli bulundu. Temiz kohort portföyü: 1 pending, 2 open, 7 closed, 0 cancelled;
10.000 USDT başlangıç sermayesi, 8.319,56 USDT nakit, +122,02 USDT gerçekleşmiş
net P&L ve 7,23 USDT toplam ücret. Strateji allocation tablolarında gerçek emir
bayrağı toplamı 0.

Supabase audit mirror aynı taze attestation zamanını/hash'ini ve aynı güvenlik
durumunu gösterdi; 2 candidate ve 2 forward evaluation kritik kayıtları aynaya
ulaştı. Yeni toplu veriden 7.417 trade observation ve 1.167 consensus snapshot
SQLite outbox'ta bekliyor. Bu arıza değildir: büyük tarihsel/araştırma tabloları
kritik kanıtı bloke etmemek için mevcut iki dakikalık timer ile düşük öncelikli,
sınırlı paketlerde drain edilir. Drain timer aktiftir; batch/evidence eşikleri
değiştirilmemelidir.

Bulut servis durumu: worker active, OKX collector timer active+enabled; sıradaki
doğal koşu günlük timer tarafından yürütülecek. Sıradaki ürün işi yeni politika
aramak değil, mevcut PAPER işlemleri ve forward dış-trader kapanışları doğal
olarak birikirken net-after-cost P&L, equity, return, trader diversity ve forward
coverage sayaçlarını değişmeyen kapılarla izlemektir.

Canonical checkout: `/Users/elifzilan/Documents/AG-Crypto-Analyst`

Exact next-session opening prompt:

> AGCA yerel projesinde çalış. Önce kök AGENTS.md ile
> docs/LAST_SESSION_HANDOFF.md dosyasını oku ve Git durumunu kontrol et. Dağıtılmış
> PAPER trade-learning/strategy evidence pipeline'ında doğal işlem ve forward
> kapanış birikimini doğrula; portföy net P&L/equity/return, trader diversity,
> forward coverage, worker/timer ve Supabase kritik kanıt tazeliğini raporla.
> Kanıt eşiklerini, strateji politikasını veya gerçek emir yetkisini değiştirme.

# 2026-08-27 — runtime freshness and Supabase log incident closed

The active runtime incidents reported during the day were reproduced and
closed. `forecast_core_lab.ensure_historical_coverage` previously accepted a
trailing candle up to two hours old, conflicting with the unchanged 15-minute
forecast freshness gate. Coverage now requires the exact final closed hourly
candle; the listing/start boundary alone retains its existing tolerance. Full
local suite: 107/107. Cloud target regression tests: 2/2.

The code was deployed to `agca-runtime` with rollback copy
`/home/kursadsalis/AG-Crypto-Analyst-cloud-freshness-rollback-20260827`.
The restarted worker completed the natural hourly job with `SUCCESS`: all 13
active symbols had the closed candle `2026-08-26T22:59:59.999Z`, and 39
forecasts were created at `2026-08-26T23:00:27Z` from that common close.
The stale `STALE_COMMON_CANDLE` condition is no longer active.

Supabase remained `ACTIVE_HEALTHY` and writable. The repeated
`pg_pgrst_no_exposed_schemas` error was Supabase's known disabled-Data-API
PostgREST behavior. The documented empty sentinel schema and authenticator role
setting were applied with all access revoked from public/client/service roles.
Data API remains disabled, `agca_runtime` was not exposed, and the last schema
error occurred before the change at `2026-08-26T22:58:08Z`; none followed.
Reversal instructions are recorded in `docs/CLOUD_PAPER_RUNTIME.md`.

Fresh attestation `2026-08-26T23:03:59Z` is present in both authoritative
SQLite and the Supabase audit mirror with SHA-256
`ba9f117d0f041071f0cbcdb0a8e541bc6ff9717012df6b3523ec1259e4a710f5`:
worker `HEALTHY`, `PAPER_ONLY`, `may_send_real_orders=0`, SQLite `ok`, hourly
and PAPER agent `SUCCESS`. Portfolio evidence: 10,000 USDT initial capital,
7,307.81 USDT cash, +122.02 USDT realized net P&L, 7.73 USDT fees, 3 open and
7 closed virtual positions. The product's remaining `PARTIAL` label is the
existing quarantined legacy news-forward shadow's one unmatched spot item; it
does not block the unified forecast or PAPER trader and grants no authority.
The old disabled `agca-live-cycle.timer` remained disabled; only its week-old
systemd failed-state marker was cleared to prevent a false active-incident
interpretation.

No evidence threshold, strategy policy, allocation gate, or real-order
authority changed. Next product work is continued natural PAPER trade and
forward external-trader closure accumulation under the existing gates.

Canonical checkout: `/Users/elifzilan/Documents/AG-Crypto-Analyst`

Exact next-session opening prompt:

> AGCA yerel projesinde çalış. Önce kök AGENTS.md ile
> docs/LAST_SESSION_HANDOFF.md dosyasını oku ve Git durumunu kontrol et. Sağlıklı
> PAPER_ONLY runtime üzerinde doğal PAPER işlem ve forward dış-trader kapanış
> birikimini doğrula; net P&L/equity/return, trader diversity, forward coverage,
> worker/timer ve Supabase kritik kanıt tazeliğini raporla. Kanıt eşiklerini,
> strateji politikasını veya gerçek emir yetkisini değiştirme.

# 2026-08-27 — on-chain/whale committee-report gaps resolved

The daily committee report's supporting-evidence labels were corrected at the
data-contract boundary. Live diagnosis showed that on-chain and whale collectors
were healthy, but the report language collapsed four different cases into
"missing data": unsupported token-level coverage, covered assets with no event,
observed events with unverified direction, and genuine source failure/staleness.

Report v1.4 now emits a per-symbol coverage table and reserves `VERI_EKSIK` only
for a failed, stale or unverifiable source cycle. BTC's large UTXO outputs are
shown as observed with `UNKNOWN` direction, not missing; supported ETH/ERC-20
assets with no qualifying event are `KAPSAMDA_OLAY_YOK`; unsupported assets are
`KAPSAM_DISI`. TIA now receives direct Celestia network context, whose current
zero-TVL observation remains correctly fail-closed as `NO_DATA`.

The separate operational defect was an ERC-20 scanner roughly 20,000 blocks
behind the Ethereum safe head because its 40-block ceiling healed deployment
gaps too slowly. Its bounded budget is now 2,000 blocks per run in 500-block log
chunks. A controlled cloud catch-up scanned every intervening range and reached
the safe head; the seven verified contracts remain ARKM, BIO, ETHFI, ILV, LQTY,
SLP and UMA. No >=10M USD event was found in that catch-up, which is reported as
covered/no-event rather than missing.

Deployment rollback:
`/home/kursadsalis/AG-Crypto-Analyst-cloud-report-evidence-rollback-20260827`.
Full local suite passed 111/111; cloud target tests passed 3/3. The worker and
critical timers are active. Live report v1.4 was generated from a fresh 13-coin
decision run, mirrored exactly to Supabase, and the critical outbox was zero.
No evidence threshold, strategy policy, allocation gate or real-order authority
changed.

Canonical checkout: `/Users/elifzilan/Documents/AG-Crypto-Analyst`.

# 2026-08-27 — legacy product PARTIAL closed

The final persistent `product_cycle_status=PARTIAL` was traced to one expected
fail-closed unsupported Binance Spot asset tag in the quarantined legacy news
forward shadow. It was not a collector, forecast, learning, PAPER, or execution
failure. The unsupported asset is still skipped; the result now reports the
explicit `unsupported_asset_quarantined` count without classifying the entire
runtime step as failed.

Full local suite passes 108/108 and the cloud target test passes 1/1. The change
was deployed with rollback copy
`/home/kursadsalis/AG-Crypto-Analyst-cloud-legacy-status-rollback-20260827`.
The first natural worker product cycle at `2026-08-27T07:29:55Z` completed
`SUCCESS` while retaining `desteklenmeyen varlık karantinası=1`; the following
hourly/PAPER job also completed `SUCCESS`. Worker remains active, risk coverage
is 13/13, forecast count is 39 for the current hour, and PAPER authority remains
`may_send_real_orders=0`.

Fresh post-change attestation `2026-08-27T07:31:11Z` is identical in SQLite and
the Supabase audit mirror: worker `HEALTHY`, product/hourly/PAPER all `SUCCESS`,
SQLite `ok`, `PAPER_ONLY`, real orders disabled, canonical SHA-256
`5fc9579a9577679f6ddf926851a0612ee765f2f1276525658f4290d43f538b1b`.

No evidence threshold, symbol substitution, strategy policy, allocation gate,
or real-order authority changed. Continue natural PAPER trade and forward
external-trader closure evidence accumulation.

Canonical checkout: `/Users/elifzilan/Documents/AG-Crypto-Analyst`

Exact next-session opening prompt:

> AGCA yerel projesinde çalış. Önce kök AGENTS.md ile
> docs/LAST_SESSION_HANDOFF.md dosyasını oku ve Git durumunu kontrol et. Başarılı
> PAPER_ONLY runtime üzerinde doğal PAPER işlem ve forward dış-trader kapanış
> birikimini doğrula; net P&L/equity/return, trader diversity, forward coverage,
> worker/timer ve Supabase kritik kanıt tazeliğini raporla. Kanıt eşiklerini,
> strateji politikasını veya gerçek emir yetkisini değiştirme.

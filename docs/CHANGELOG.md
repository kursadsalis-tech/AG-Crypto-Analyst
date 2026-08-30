# Unreleased — Truth Engine / Data Validation Gate

## 2026-08-30 external trader rationale and resumable collection

- Added immutable, outcome-blind pre-entry context hypotheses for public OKX
  trader positions and symmetric later win/loss reviews without intent claims.
- Replaced the timeout-prone monolithic profile run with a persistent queue of
  six current profiles plus one history profile every 30 minutes.
- Preserved every causally known open-position closure while bounding unrelated
  retrospective history to 20 trades per selected profile.
- Removed a full-table latest-position grouping bottleneck; verified controlled
  completion in 1.7 seconds and a timer/attestation-overlap run in 24.6 seconds.
- Kept copying, policy mutation, promotion, threshold search, and real orders
  disabled; rationale and queue tables remain outside the remote mirror.

## 2026-08-30 behavior-first event response contract

- Recorded the product decision that exact-time news direction is context, not
  a direct trading instruction. The testable edge hypothesis is the market's
  positioned, liquidity-constrained response to broadly available information.
- Upgraded read-only event measurement to use the final pre-event closed minute
  as baseline and measure raw response paths at 1m, 5m, 15m, 1h, 4h, and 24h.
- Added event-minute order-book imbalance, taker pressure, spread, OI change,
  funding, and liquidation summaries. No absorption/resilience label, threshold,
  model search, policy mutation, or action authority was introduced.
- Explicitly marks instruments sharing one global event/minute as correlated,
  preventing market-wide rows from being presented as independent evidence.
- Added an end-to-end causal integration test covering pre-event baseline,
  actual macro observation time, event-minute behavior persistence, and a
  result that appears only after its fixed forward horizon closes.
- Added a canonical self-hash to market-behavior health records. Cloud
  attestation now rejects a fresh-looking but modified observer health payload.
- Added a deterministic observer deployment preflight covering all transferred
  source/dependency SHA-256 values, unit resource/safety contracts, runtime
  dependencies, health-version alignment, local-only tables, and PAPER
  governance. It reports readiness but cannot install or start cloud units.

## 2026-08-30 causal minute evidence and observer attestation

- Bound the separate market-behavior observer health record into the canonical
  cloud attestation payload as `CURRENT`, `MISSING`, `STALE`, or `INVALID`.
- Added one exact-time causal minute contract for news, macro, on-chain, and
  whale evidence. Events are assigned by the time they became available to
  AGCA; day-only and timezone-free timestamps fail closed.
- Projected existing exact news collection times, on-chain collection times,
  and whale observed times into the same closed-minute line. Added an immutable
  local exact-macro event contract; ordinary day-level FRED observations are
  deliberately not promoted into intraday evidence.
- Added a first-party Federal Reserve FOMC collector that accepts only explicit
  `Released <date> at <time>` evidence, converts America/New_York time to UTC
  with daylight-saving rules, and records source URLs and HTML SHA-256 values.
  A three-page 2026 sample yielded 10 exact-time documents.
- Separated an official macro release timestamp from AGCA's actual observation
  timestamp. Causal availability is now the later value: preloaded scheduled
  events wait for release, while late discoveries cannot rewrite past minutes.
- Added a schedule-aware exact-macro observer. It refreshes the first-party FOMC
  calendar at most daily outside decision windows, checks once per minute only
  from 15 minutes before through 90 minutes after a scheduled release, fetches
  only the matching meeting page, and uses a 15-minute error backoff.
- Added bounded systemd service/timer definitions and bound the observer's
  hash-valid health record into cloud attestation. The units remain uninstalled
  and disabled pending explicit user approval.
- Persisted exact external events idempotently beside local behavior minutes
  under the same 30-day retention boundary; this table is not remotely mirrored.
- Added observational first/last midpoint prices to closed behavior minutes so
  future event outcomes can eventually be measured without reconstructing raw
  payloads. Added a read-only coverage audit for exact-minute joins and the
  already established 1h/4h/24h horizons; it opens no model or threshold search.
- This evidence remains observational. PAPER_ONLY, action thresholds, strategy
  policy, allocation gates, and real-order authority are unchanged.

## 2026-08-27 trader KPI correction

- Temiz PAPER kohortu doğrudan denetlendi: 9 kapanmış 24h SHORT işlem,
  5 kazanan, +108.50 USDT net P&L ve 3.80 profit factor. Düşük üç-sınıf
  direction-hit değerinin çalışan PAPER trade yolunu yanlış temsil ettiği
  kanıtlandı.
- Committee Forecast Performansı tablosunda aksiyon hit, net-after-cost utility
  ve profit factor birincil sütunlara taşındı; UP/DOWN/NEUTRAL isabeti yalnız
  model teşhisi olarak etiketlendi.
- PAPER günlük raporuna profit factor ve kapanmış-işlem max drawdown eklendi.
- Strateji, allocation eşiği ve emir yetkisi değiştirilmedi. Tam suite 120/120.
- Follow-up tamamlandı: komite raporu artık Forecast `shadow_action` sayacından
  bağımsız olarak doğrudan temiz `paper_agent_positions` kohortundan ufuk/yön,
  kapanan, kazanan, net P&L, profit factor ve max drawdown tablosu üretir.
  Tam suite 121/121.
- Canlı render kontrolü eski/hard-coded kohort adını yakaladı. Sorgu PAPER
  ajanının tek kaynak `FORWARD_COHORT` sabitine bağlandı; aktif temiz kohort artık
  doğru okunuyor.
- Checkpoint `0471e8a` buluta dağıtıldı. Canlı v1.6 rapor 24h SHORT için
  10 kapanan, 6 kazanan, +111.31 USDT net P&L, 3.87 profit factor ve 37.64 USDT
  max drawdown gösterdi. Supabase direct query exact satırı doğruladı; ilgili
  outbox sıfırlandı.

## 2026-08-27 point-in-time Spot Flow Feature Challenger V1

- Sabit 13 coin için resmî Binance Spot kaynağından 405 günlük kapalı saatlik
  mum yeniden okundu ve eski satırlardaki taker-buy quote volume alanı
  tamamlandı; en düşük coin kapsaması %99,19 oldu.
- Tahmin anındaki 1h/4h/24h spot taker imbalance, 4h flow acceleration,
  24h fiyat teyidi ve eşzamanlı BTC flow özellikleri eklendi.
- Son EXAM segmenti açılmadan sabit histogram challenger geliştirildi. Üç ufuk
  da geliştirme kapısında reddedildi; zayıf sonucu gizlemek veya eşiği düşürmek
  yerine model yalnız forward ablation tanısı için donduruldu.
- Forward tablo direction hit, Brier, shadow action, action hit ve gerçek
  maliyet sonrası utility'yi saklar. `production_eligible=0` ve
  `action_authority=0` değişmezdir.
- Tam yerel suite 119/119 geçti.
- Checkpoint `b7b2a97` rollback korumasıyla buluta dağıtıldı; frozen model hash'i
  doğrulandı ve worker `PAPER_ONLY`/active döndü. Bulutta 13/13 coin için 26'şar
  kapalı flow mumu hazırdır. Retrospektif seed yapılmadı; ilk kayıt sıradaki
  doğal saatlik tahmin paketinde oluşacaktır.

## 2026-08-27 lookahead-safe direction-drift challenger

- Birleşik Forecast Core'un canlı sınıf dağılımındaki DOWN çöküşü doğrulandı;
  ek ajan kanıtlarının mevcut modele girdi olmadığı açıkça korundu.
- Tahmin anından önce sonuçlanmış son 24 saatlik paketi kullanan, en az 10 paket
  isteyen ve her `as_of` paketine tek ağırlık veren sınıf-prior drift challenger'ı
  eklendi. Böylece 13 korelasyonlu coin 13 bağımsız piyasa oyu gibi sayılmıyor.
- Challenger olasılıkları, actual class, direction hit ve Brier ayrı tabloda
  tutuluyor ve Supabase operasyonel audit mirror kapsamına alındı.
- Challenger `FORWARD_SHADOW_ONLY`, `production_eligible=0` ve
  `action_authority=0`; PAPER allocation, kanıt eşikleri ve gerçek emir yetkisi
  değişmedi.
- Yerel tam suite 117/117 geçti.
- Checkpoint `de7ba57` rollback korumasıyla `agca-runtime` üzerine dağıtıldı.
  İlk doğal cycle 39/39 forecast ve 39 settlement ile `SUCCESS` tamamlandı;
  39 challenger satırının tamamı Supabase aynasında doğrudan sorgulandı.

## 2026-08-27 live spot money-flow evidence

- Persist Binance closed-candle taker-buy quote volume in `market_klines` and
  validate it fail-closed against total quote volume.
- Added 1h/4h/24h aggressor-flow analysis: taker buy/sell USDT, net flow, buy
  ratio, price return and price-direction confirmation.
- Committee report v1.5 presents the 4h flow table for all 13 symbols and labels
  it `OBSERVATION_ONLY`; it is not exchange deposit flow or execution authority.
- Backfilled 26 hours from the official Binance Spot endpoint for all 13 symbols.
  Open candles are explicitly excluded from analysis.
- Full local suite: 114/114; cloud target tests: 3/3.


## 2026-08-27 on-chain and whale report coverage diagnosis

- Daily committee report v1.4 now distinguishes actual source failure/staleness
  (`VERI_EKSIK`) from unsupported coin coverage, supported assets with no event,
  and observed whale events whose exchange direction is unverified.
- Added per-symbol on-chain network mapping, whale coverage, 24-hour event count,
  and verified-direction fields; BTC unknown-direction outputs are no longer
  described as missing data.
- Added direct Celestia network context for TIA while keeping its current zero-TVL
  observation fail-closed as `NO_DATA`.
- Increased the verified Ethereum ERC-20 scanner's bounded catch-up capacity from
  40 to 2,000 blocks per run with 500-block log chunks. The cloud backlog was
  drained to the current safe head without skipping blocks; no threshold changed.
- Full local suite: 111/111. Cloud target tests: 3/3. Live report v1.4 and its
  Supabase audit-mirror row match exactly; critical mirror outbox is empty.

## 2026-08-27 legacy forward quarantine status

- Stopped an expected fail-closed unsupported-asset quarantine in the legacy
  news forward shadow from degrading every otherwise healthy product cycle to
  `PARTIAL`.
- Preserved the skip and exposed it as
  `unsupported_asset_quarantined`; no symbol mapping, evidence threshold,
  model rule, or execution permission was relaxed.
- Full local suite passes 108/108; the deployed target test passes and the
  first natural cloud product cycle completed `SUCCESS`.

## 2026-08-27 runtime freshness and disabled-Data-API recovery

- Fixed historical coverage readiness to require the exact final closed hourly
  candle. The earlier two-hour tolerance is now limited to a symbol's listing
  boundary and can no longer allow a stale trailing candle into forecasting.
- Added regression tests for both stale-tail repair and the no-refetch case;
  the full local suite passes 107/107 and the deployed target tests pass 2/2.
- Kept Supabase Data API disabled while applying Supabase's documented
  `pgrst_no_exposed_schemas` sentinel configuration. Client roles have no usage
  on that schema, AGCA schemas were not exposed, and PostgreSQL remains writable
  for the private audit mirror.
- Reset the stale failed state of the already-disabled legacy live-cycle unit;
  the active cloud worker and all enabled timers remain unchanged.

## 2026-08-27 forward copy-trader closure accumulation

- Fixed the bounded OKX history refresh so traders with positions previously
  observed open are revisited after they already have retrospective history.
- Rotate more than 30 known-open traders across daily batches, preventing a
  fixed rank subset from starving while keeping the existing 30-profile request
  ceiling and using spare capacity for untouched cohort backfill.
- Preserved immutable first-observed timestamps, all evidence thresholds,
  research quarantine, disabled promotion and disabled real-order authority.
- Full local suite passes 105/105.

## 2026-08-26 external copy-trader research quarantine

- Added a public copy-trader research layer for allowlisted Bybit and OKX
  evidence. It stores immutable raw payloads and hashes with the exact time at
  which AGCA first observed them.
- Quarantined winner-only anecdotes, incomplete leaderboard samples,
  non-allowlisted provenance, extreme leverage and trades whose costs are not
  disclosed. Retrospective closed trades can inform research but can never be
  represented as forward signals.
- Kept exchange-specific ROI definitions separate and prohibited automatic
  copying, PAPER-policy mutation, champion promotion and real orders.
- Integrated the evidence-sufficiency review into every PAPER trade-learning
  cycle. External evidence remains separate from clean-cohort P&L and requires
  at least 30 traders, 200 distinct closed trades and 56 forward observation
  days before human research review.
- Added normalized JSON ingestion and SQLite/Supabase outbox coverage for all
  three external-research tables. Authenticated, private and order-capable
  exchange endpoints are never used.
- Added a daily Google Cloud collector using the same unauthenticated public
  OKX web endpoints as OKX's own copy-trading page. It atomically snapshots all
  43 pages/255 traders, observes every public open position with a first-seen
  timestamp, and backfills 30 traders' disclosed closed histories per run.
- The first live run stored 594 open observations and 1,870 historical closed
  observations. Of the latter, 313 passed provenance, cost, notional and <=5x
  leverage gates; all historical rows remain explicitly retrospective. The
  review advanced to `COLLECTING_FORWARD_TIME_COVERAGE`, never promotion.

## 2026-08-25 unattended cloud attestation

- Added append-only, canonical SHA-256 cloud runtime attestations covering
  worker/product/hourly/PAPER status, PAPER_ONLY authority, disabled real
  orders, SQLite integrity, clean-cohort counts and portfolio accounting.
- Mirrored cloud evidence to a private Supabase table with revoked client
  grants, RLS and explicit deny policies.
- Updated the daily Codex notification to verify attestation freshness/hash and
  mirror agreement without Google Cloud SSH, interactive approval or Telegram;
  missing or inconsistent evidence fails closed.
- Limited attestations to new result windows instead of the worker's 10-second
  internal loop.
- Added an independent five-minute systemd attestation timer with stale/service
  checks, plus hard PostgreSQL statement and lock timeouts so remote mirror
  slowness cannot hang the authoritative SQLite worker indefinitely.
- Separated attestation transport failures from forecast-worker health: a slow
  mirror cannot relabel a completed forecast cycle as degraded, while the
  reporting side still fails closed without fresh mirrored evidence.
- Paused the older repeated 03:20--06:20 delivery heartbeat; the single 03:15
  Codex orchestrator summary is now the unattended notification path.
- Replaced full-table PostgreSQL mirroring with a durable SQLite outbox and
  primary-key incremental upserts/deletes. Schema DDL is signature-cached,
  batches are bounded, failed deliveries remain queued across process restarts,
  and remote acknowledgement happens only after the PostgreSQL transaction
  commits.
- Moved outbox delivery out of the forecast process into the independent
  `agca-remote-mirror-drain.timer`; Supabase latency can no longer block PAPER
  forecasting or trade learning. Suppressed no-op news/social upserts so
  unchanged observations never enter the outbox.
- Prioritized cloud/PAPER evidence ahead of bulk history, isolated each table
  in its own remote transaction, and throttled backlog recovery to 25 rows per
  batch every two minutes so historical repair cannot starve live metadata.
- Hardened official exchangeInfo refresh with bounded DNS/connect, HTTP, TCP
  and PostgreSQL statement timeouts. Hourly PAPER work may use only a
  provenance/hash-validated cache younger than two hours when live refresh is
  unavailable; stale or invalid cache remains fail-closed.

## 2026-08-25 clean forward PAPER trade learning

- Added an evidence-gated review to every PAPER-agent cycle using only eligible,
  closed clean-forward trades and net P&L after all simulated costs.
- Added direction, horizon, regime and direction+horizon scorecards with gates
  of 100 total closed trades, 30 trades per pattern and three independent weeks.
- Prohibited single-trade updates, coin blacklists, automatic policy mutation
  and automatic champion promotion; current cloud state is
  `COLLECTING_EVIDENCE` with zero clean closed trades and zero hypotheses.
- Mirrored immutable reviews and bounded hypotheses to Supabase; enabled RLS
  with explicit client-deny policies. Security and performance advisors are
  both clear.
- Expanded the 03:00 PAPER report with an orchestrator-accountability section:
  cumulative clean-cohort selections, pending/open/closed/cancelled counts,
  wins/losses/breakevens, win rate, gross/net P&L and each execution-cost class.

## 2026-08-24 cloud deployment and news fail-closed gate

- Deployed GitHub branch state through `f03006d` to the existing Google Cloud
  worker with a file-level rollback snapshot and without resetting persistent data.
- Preserved the cloud runtime's future-risk-policy timestamp rejection while
  adding the news-direction robustness rejection.
- Verified 10/10 targeted cloud tests, hard `PAPER_ONLY`, disabled real orders,
  `SHADOW_ONLY` under artificially strong forward evidence, and no
  production-eligible news-direction horizon.
- Connected the worker to Supabase through Google Secret Manager, disabled stale
  secret versions, and verified continuously advancing remote mirror health.
- Enabled RLS on all 30 `agca_runtime` tables; Supabase security advisor returned
  no findings.
- Recovered the runtime from a healthy SQLite backup after preserving the corrupt
  database for forensic recovery; full integrity check returned `ok`.

## Mac-independent PAPER runtime and execution realism

- Added a provider-neutral Docker worker for the five-minute product cycle and
  hourly forecast/settlement/learning/paper-agent job.
- Persisted SQLite, model artifacts, reports, worker health, and the exclusive
  process lock on a mounted state volume.
- Added a hard `PAPER_ONLY` startup gate; no live exchange order adapter or
  credential path exists.
- Replaced perfect candle-price fills with adverse spread, taker fee, latency,
  volume-aware slippage, capacity/partial-fill, minimum-notional, tick/step,
  rejection, and perpetual-funding simulation.
- Added explicit SPOT (LONG-only) and USD-M perpetual (LONG/SHORT) semantics,
  deterministic execution tests, Compose packaging, and cloud runbook.
- Added a market-pinned, atomic exchangeInfo metadata cache with strict
  perpetual/USDT filter parsing, freshness, provenance, digest and missing-symbol
  rejection. The existing Supabase `eu-west-1` Postgres connection now provides
  a private fixed-URL GET transport to Binance's official USD-M endpoint; no
  public proxy is exposed. Local 25/25 and cloud 27/27 related tests passed.
  The first natural worker cycle was `HEALTHY`, hourly status was `SUCCESS`, and
  the PAPER agent consumed 527 active symbol rules with real orders still false.
- Replaced the fixed USD-M funding proxy with official Binance funding-history
  events fetched through the private Supabase Europe transport. Funding uses
  event mark prices and correct LONG/SHORT debit-credit signs, with malformed or
  missing histories rejected fail-closed.
- Added causal 1x isolated PAPER liquidation checks over held hourly candle
  lows/highs, live symbol liquidation fees, explicit conservative 0.5%
  maintenance-margin assumption, zero-floor recovery, audit columns, legacy
  open-position backfill and daily-report disclosure. No signed Binance account
  endpoint or credential path was added. Local/cloud tests pass 32/32; six open
  positions were backfilled with none missing a threshold. The first natural
  worker cycle was `HEALTHY` with hourly/PAPER `SUCCESS` and real orders false.
- Fixed a real PAPER-entry lookahead defect: historical hourly opens can no
  longer be used when they precede the agent decision. New intents commit to
  the first exact full-hour open strictly after selection, and prior/non-exact
  candles are rejected. Preserved all 14 legacy rows as evidence-ineligible,
  cancelled six legacy active positions, backed up SQLite, and restarted a
  clean 10,000 USDT `FORWARD_CLEAN_V2_20260825` cohort. Its first five pending
  intents have zero timing violations. Related tests pass 34/34; the first
  natural worker/hourly/PAPER result is `HEALTHY`/`SUCCESS`/`SUCCESS`, SQLite
  quick-check is `ok`, and the real-order prohibition is unchanged.

- Added a shared fail-closed validation contract for decision-bearing market data.
- Block malformed, non-finite, non-positive, OHLC-inconsistent, negative-volume,
  non-contiguous, open/future and stale Binance 1h candles.
- Routed Risk Manager and Legacy Forward Shadow candle inputs through the gate.
- Added deterministic regression tests for valid, stale, inconsistent, gapped and
  still-open candle payloads.
- Added source-specific contracts for news, social, on-chain, macro, whale and
  derivative data; invalid historical rows can no longer disappear silently.
- Added persistent PASS/DATA_BLOCKED audit evidence and remote runtime mirroring.
- Added independent Binance/CoinGecko BTC reference validation with a fixed 2%
  maximum deviation; failure forces the market risk policy to DATA_BLOCKED.
- Exclude social/on-chain/macro/whale evidence from Decision Engine when its most
  recent source-cycle validation is missing, failed or stale.
- Reject future-dated risk policies and Decision snapshots in Signal Gate,
  Decision Engine and committee reporting paths.
- Added multi-provider Ethereum JSON-RPC fallback after a live 403 rate-limit;
  fallback ordering is unit-tested and a live ERC-20 run verified seven contract
  decimals plus 40 safe blocks without source errors on 2026-08-23.
- Isolated every live product-cycle step so a missing news source is recorded as
  DATA_BLOCKED instead of terminating the entire scheduler process.
- Skip Decision Engine and product alert delivery when the current Risk step is
  ERROR/DATA_BLOCKED, preventing accidental reuse of a stale policy snapshot.
- Made remote mirror health telemetry best-effort so a telemetry disk I/O error
  cannot invalidate an already successful SQLite-first commit.
- Cache the last healthy Ethereum RPC for the rest of the collector process and
  cap each provider attempt at 15 seconds to prevent fallback latency from
  blocking the five-minute scheduler cadence.

# v1.2.1

## Remote Runtime Store

- SQLite'ı source-of-truth bırakan opsiyonel PostgreSQL/Supabase dual-write
- Ortam değişkeni tabanlı bağlantı; repoda secret yok
- macOS Keychain desteği ve parolayı görünmeden alan güvenli kurulum aracı
- Operasyonel tablo allowlist'i ve mevcut runtime kayıtları için bootstrap komutu
- Otomatik uzak schema, tablo ve sütun oluşturma; primary-key upsert
- Uzak servis yokken kesintisiz SQLite fallback, retry cooldown ve secret-safe log
- Ortak runtime connection entegrasyonu, birim testleri ve kullanım dokümanı

# AG Crypto Analyst - Changelog

## 2026-08-24 — Cloud natural-cycle verification and health serialization fix

- Verified the deployed `f03006d` news-direction fail-closed gate, mandatory
  `PAPER_ONLY` runtime mode and `real_orders_allowed=False` on the live VM.
- Verified SQLite `quick_check=ok` and the direct Supabase mirror at 28/28
  healthy tables with zero consecutive failures.
- Verified the first natural Istanbul 03:00 paper report and 11:00 investment
  committee report; remote delivery completed and the committee output stayed
  `SHADOW_ONLY` / `BEKLE`.
- Diagnosed `legacy_forward=PARTIAL` as the persistent non-Spot-USDT `CRO`
  mention in news #2716; no model, threshold or policy was changed.
- Fixed worker health JSON serialization for nested runtime datetimes while
  preserving fail-fast behavior for unsupported types. Added regression tests;
  16/16 related tests passed locally and in cloud, followed by a natural
  `HEALTHY` worker result.

## 2026-08-24 — PAPER execution input fail-closed hardening

- Reject non-finite price/liquidity, missing required quote liquidity and
  invalid tick/step/min-notional inputs instead of simulating an optimistic fill.
- Apply exact candle quote-volume capacity to exit fills as well as entries.
- Add exact-timestamp, missing-liquidity and database-outage regression tests.
- Pass 22/22 related cloud tests and the Profit Agent self-test, then verify a
  natural `HEALTHY` worker cycle with 13/13 Risk coverage and zero source errors.
- Keep live USD-M metadata and funding parity open because the official endpoint
  is region-blocked from cloud and unavailable from the Mac; Spot/testnet rules
  are not substituted.

## 2026-08-24 — PAPER agent authority contract

- Add an explicit fail-closed authority contract that permits recommendations
  but denies real orders, model/policy promotion, Risk override and capital
  changes.
- Reject non-PAPER mode, unreviewed policy versions and changes to the locked
  10,000 USDT virtual capital before the agent opens its database.
- Expose the authority contract in every hourly PAPER result and operational
  log for auditability.
- Pass 26/26 related tests in cloud, prove `LIVE` is rejected, and verify a
  natural `HEALTHY` worker cycle with hourly `SUCCESS`.

## 2026-08-24 — Common execution contract and PAPER parity

- Add provider-neutral order intent, market snapshot, exchange rules, decision,
  fill and adapter protocol types without adding a live order adapter.
- Route the existing conservative PAPER simulator through that shared contract
  while preserving the backward-compatible fill API.
- Prove identical wrapper/contract fills and fail-closed rejections with parity
  tests; keep credentials and exchange order endpoints outside the contract.
- Pass 29/29 related cloud tests and the Profit Agent self-test, followed by a
  natural `HEALTHY` worker result with hourly and PAPER `SUCCESS`.

## 2026-08-24 — Real-capital reconciliation and approval gate

- Add ordered PAPER-versus-dry-run execution trace reconciliation across
  decision, price, quantity and fee fields with explicit tolerances.
- Fail closed on empty traces, count differences, decision mismatches or
  non-finite/numerically divergent execution results.
- Require independent user approval and primary-orchestrator attestation records
  in addition to a passing reconciliation.
- Keep a separate reviewed-code gate hard-coded closed, so approvals and runtime
  configuration still cannot activate real capital or orders.
- Pass 33/33 related cloud tests and self-test, then verify a natural `HEALTHY`
  worker result with hourly/PAPER `SUCCESS` and real-order authority false.

## Unreleased - Historical Decision Validation

### Probabilistic Forecast Core Lab

- Tek merkezi Forecast Contract; yeni runtime ajan ağı veya ajan oylaması yok
- 1h/4h/24h medyan hedef, %80 fiyat aralığı ve UP/DOWN/NEUTRAL olasılık sözleşmesi
- Son kapanış fiyat hedefi ile next-open işlem olasılığı referanslarının açık ayrımı
- Tek sabit `POOLED_RIDGE_DISTRIBUTION_V1_ALPHA_0_05_FIXED` challenger;
  mean-loss + alpha*L2 objective, RSI/MACD/EMA grid araması yok
- Kronolojik FIT/CALIBRATION/VALIDATION ve yalnız geçişte observed diagnostic
- Exact quote-volume, label purge, horizon cooldown ve Risk-veto ayrımı
- No-change/climatology benchmark; Brier/log-loss/ECE/MAE/pinball/interval kapıları
- Confidence olayı ve hit-rate olayının aynı maliyet + minimum hareket tanımı
- Haftalık kümeli bootstrap ile cross-coin/zaman bağımlılığı koruması
- %70+ aksiyon hit alt sınırı ve kalibrasyon bias aralığı için ayrıca haftalık
  blok bootstrap kapısı
- SAT=spot SELL/REDUCE/AVOID; short veya portföy kârı değildir
- Contract gerçek araştırma satırlarında çalışır; NaN/inf, horizon, fiyat aralığı,
  feature-mutation, next-open ve artifact hash self-testleri başarılı
- Canlı Decision Engine, rapor, cycle ve alarm politikası değiştirilmedi
- Gerçek Mac VALIDATION sonucu: 1h/4h/24h REJECT; OBSERVED_DIAGNOSTIC açılmadı
- 1h/4h fiyat tahmini baseline'a yakın veya kötü ve aksiyonsuz; 24h 17 tail
  aksiyonla ilginç fakat yetersiz, genel Brier/MAE ve kalibrasyon başarısız
- Fiyat-only pooled-ridge ailesi kapatıldı; eşik/indikatör taraması açılmadı
- Validator tanısı: raw model/Risk-sonrası aksiyon ayrımı, forecast/action hafta
  sayıları, minimum sekiz blok, event-weighted whole-week bootstrap,
  CAL-dondurulmuş direction baseline ve UP/DOWN/NEUTRAL ECE
- Sonraki tek kampanya `DERIVATIVES_STATE_V2`: funding, OI/positioning,
  premium/basis ve futures taker flow; 4h PRIMARY, 24h SECONDARY, 1h EXPLORATORY
- Dürüst ölçüm ürün hedefi değil değişmez kısıttır; BEKLE nihai başarı sayılmaz

### Derivatives State V2 - Önceden Dondurulmuş Araştırma Paketi

- Binance USD-M kapalı 1h futures/premium mumları, Vision positioning arşivi
  ve funding olayları için checksum doğrulamalı, idempotent tarihsel collector
- 400 günlük deney penceresi; feature warmup ve funding öncülüyle varsayılan
  430 günlük toplama aralığı
- Son 30 günlük arşivleri yeniden checksum doğrulama; aynı timestamp'te farklı
  cross-archive payload için deterministik saklama ve fail-closed kalite hatası
- Belirsiz headerless funding arşivini sessizce yanlış yorumlamak yerine reddetme
- Funding oranını geçmiş olay aralığından point-in-time türetilen 8 saat eşdeğerine
  normalize etme; eksik funding periyodunda feature satırını dışlama
- Resmî availability anlamı belgelenmeyen Vision taker oranı modelden çıkarıldı;
  taker akışı yalnız kapanmış futures mumunun quote/taker-buy quote hacminden üretildi
- Exact futures/spot basis, premium, OI 1h/4h/24h değişimi, positioning oranları,
  funding ve taker imbalance içeren 26 sabit türev özelliği
- En az 10 coin ve BTC/ETH zorunlu ortak pencere; eksik bağlamda imputasyon yok,
  global/coin bazında en fazla %2 ve hafta bazında en fazla %10 açık kalite sınırı
- Tek sabit pure-stdlib histogram additive-stump V2 challenger; model grid'i yok
- CALIBRATION kronolojik CAL-A model kalibrasyonu ve CAL-B dağılım kalibrasyonu
  olarak ayrıldı
- 4h PRIMARY geçmeden 24h SECONDARY observed diagnostic açılmaz; 1h EXPLORATORY
  hiçbir koşulda candidate artifact'e girmez
- 24h validation örneklem kapısı 400 günlük pencereyle matematiksel olarak
  ulaşılabilir 750 olaya sabitlendi; diğer ufuklar 1.000 olay
- Artifact JSON round-trip hash, horizon gate fizibilitesi, Risk-veto yönü,
  collector şeması ve CAL-A/CAL-B ayrımı self-testleri
- Derleme, V1/V2/collector self-testleri, 26-feature point-in-time smoke ve
  400 günlük 10-coin readiness integration smoke başarılı
- Gerçek Mac backfill ve tek V2 lab koşusu sonraki oturuma bırakıldı; canlı ürün
  politikası değiştirilmedi

### Leakage-Safe Decision Replay

- 13 coin için yaklaşık 400 günlük kapalı 1h mumla point-in-time replay
- Sonraki mum açılışında giriş; 1h, 4h ve 24h sonuç
- 10 baz puan gidiş-dönüş maliyet
- Kronolojik LEARN / VALIDATION / EXAM
- Gerçek Binance quote-volume saklama ve eski tablo migrasyonu
- 13/13 veri kapsamı, 127.322 yeni mum, sıfır kaynak ve zaman boşluğu hatası
- 9.576 karar noktasında 23.881 gölge aday

### Conservative Challenger Lab

- FOLLOW ve CONTRARIAN yönleri için önceden sabit 36 kural/horizon
- Teknik güç, 24h momentum ve normal piyasa filtreleri
- LEARN'de çoklu deneme için z=3,20; ilk üç için VALIDATION z=2,40
- Korelasyon şişmesini azaltan 7 günlük zaman blokları
- Yalnız dondurulan kazananın EXAM'a girebilmesi
- 1h, 4h ve 24h için NO_LEARN_EDGE
- Canlı Decision Engine politikasında ve ürün kapılarında değişiklik yok

### Sparse Transition Replay Lab

- Sürekli fiyat durumundan farklı, seyrek skor/MACD/EMA geçişleri ve 24h kırılımlar
- Hacim ve NORMAL-piyasa korumalarıyla 36 sabit config/horizon
- Coin başına 24h cooldown, 10 bps maliyet ve point-in-time Risk Manager
- 9.576 karar noktası, 68.616 risk-izinli bağlam, 55.872 risk vetosu ve sıfır zaman boşluğu
- Config başına 146-2.107 olay
- 1h, 4h ve 24h için NO_LEARN_EDGE
- Nihai araştırma kararı: STOP_PRICE_TECHNICAL_RULE_SEARCH

### News Catalyst Replay Lab

- Fiyat/teknik aramadan bağımsız, GDELT başlıklarına dayalı tek sınırlı katalizör hipotezi
- Precision-first coin aliasları; belirsiz kısa kelime/ticker yanlış eşleşmelerine koruma
- Canonical URL ve 72h aynı-başlık tekilleştirmesi
- Yaklaşık gün-noon zamanlarının dışlanması; 60m güvenlik gecikmesi ve sonraki 1h açılış
- Coin başına 24h cooldown ve 10 bps gidiş-dönüş maliyet
- Teknik yön filtresi olmadan 16 sabit FOLLOW/CONTRARIAN kural/horizon
- Ham veri ve fiyat eşleşmesini kapsayan fail-closed veri kalite kapısı
- 7 günlük bloklarla LEARN -> VALIDATION -> mekanik olarak kapalı EXAM
- Sentetik next-open, maliyet, cooldown ve EXAM-körlüğü testleri başarılı
- Gerçek Mac kalite sonucu: 5.571 ham haber, %100 geçerli format, %99,8 kullanılabilir başlık
- Kesin intraday timestamp oranı %0; tüm kayıtlar arşiv-günü 12:00 fallback
- Kök zaman engeli nedeniyle yönlü olay, dedup, span, kaynak ve fiyat eşleşme aşamaları çalıştırılmadı
- Sonuç DATA_QUALITY_BLOCKED; aday seçimi ve EXAM bilinçli olarak çalışmadı
- Tarihsel saat uydurulmadı; GDELT günlük arşivle event-time replay durduruldu
- Aynı arşive dayanan eski %77,6 NEWS BASE, ROBUST_B ve provisional Champion
  etiketleri tarihsel edge/ürün kanıtı olmaktan çıkarıldı
- Haber katalizörü doğrulaması kesin timestamp taşıyan forward örneklerle sürecek
- Canlı politika değiştirilmedi; ürün SHADOW_ONLY kaldı

Karar:

Mevcut sürekli fiyat-durum kural ailesi ve son seyrek geçiş/kırılım hipotezi
üretim için reddedildi. Aynı fiyat/teknik verisinde yeni eşik veya indikatör
kombinasyonu aranmayacak. Araştırma yalnız kalite kapılı bağımsız haber
katalizörüyle bir kez devam edecek; sonuç ne olursa olsun canlı politika fresh
forward kanıt olmadan açılmayacak.

---

## v1.2.0

### Report Generator - İlk Çalışan Sürüm

Tamamlandı:

- Decision Engine, Risk Manager, Forward Shadow ve Decision Shadow verilerinden günlük yatırım komitesi brifingi
- Coin karar tablosunda market, teknik, gölge aday, ürün kararı, risk ve pozisyon tavanı ayrımı
- SHADOW modunda etkili ürün kararını BEKLE'ye kapatan rapor seviyesi güvenlik
- Risk BLOCK bulunan coinlerin fırsat olarak sunulmaması
- En güçlü fırsat için PRODUCTION + product AL + Risk ALLOW zorunluluğu
- Risk/Decision snapshot eskiliği için 30 dakikalık DATA_BLOCKED kapısı
- Kanıt olgunluk tablosu ve küçük örneklem uyarıları
- Markdown günlük rapor çıktısı ve idempotent committee_report_snapshots kaydı
- Europe/Istanbul 11:00 sonrası günde tek çalışma
- Manuel testin planlı günlük slotu tüketmemesi
- 11 adımlı canlı ürün döngüsü entegrasyonu

Canlı doğrulama:

- Report state=SHADOW_ONLY
- 13 coin, 10 risk bloğu
- Ürün aksiyonu=BEKLE
- En güçlü fırsat=YOK
- En riskli coin=BIOUSDT
- BTC SAT_ADAYI ve WLD AL_ADAYI yalnız gölge araştırma olarak kaldı
- Saat 00:51 Europe/Istanbul iken 11:00 cadence doğru biçimde bekledi
- Tam canlı döngü 11/11 başarı

Not:

İlk doğal 11:00 çalışması operasyonel olarak izlenecektir. Rapor, gölge veya olgunlaşmamış kanıtı güçlü fırsata çeviremez.

---

## v1.1.0

### Decision Engine - İlk Çalışan Gölge Sürüm

Tamamlandı:

- 13 coin için açıklanabilir market, teknik ve kanıt olgunluğu kaydı
- Risk Manager BLOCK kararının nihai veto olarak uygulanması
- Gölge AL/SAT adayı ile ürün kararının ayrılması
- SHADOW politikada 13/13 ürün kararının BEKLE tutulması
- PROVISIONAL, WARMING_UP, NO_DATA, MONITOR_ONLY, UNVERIFIED ve SHADOW_ONLY kanıtların güçlü karara yükseltilmemesi
- decision_engine_snapshots kalıcı kayıtları
- Aynı coin için tek açık tahmin kuralına sahip 1h decision_shadow_predictions doğrulaması
- AL/SAT yönüne göre otomatik directional return ve hit puanlama
- 15 dakikalık Decision Engine cycle ve hata izolasyonu
- Risk Manager sonrasında, alarm köprüsünden önce 10 adımlı canlı ürün entegrasyonu

Canlı doğrulama:

- Risk snapshot 4, NORMAL, yaş 5,5 dakika
- BTC SAT_ADAYI ve WLD AL_ADAYI
- Risk tarafından engellenen 10 coin
- 13/13 ürün kararı BEKLE
- 2 açık, çakışmasız 1h gölge tahmin
- Kaynak hatası 0
- Tam ürün döngüsü 10/10 başarı

Not:

Gölge adaylar alarm veya işlem kararı değildir. Decision Engine politikası SHADOW_ONLY'dır ve gerçek forward kanıt oluşmadan otomatik olarak üretime geçemez.

---

## v1.0.0

### Risk Manager - İlk Çalışan Sürüm

Tamamlandı:

- BTC, ETH ve 11 öncelikli Binance Spot USDT coini için kapalı 1h mum tabanlı risk ölçümü
- 24h getiri, 24h fiyat aralığı, saatlik volatilite, 7 günlük düşüş ve 24h spot hacim vekili
- NORMAL / ELEVATED / HIGH / EXTREME coin risk sınıfları
- Coin bazlı azami pozisyon ve volatilite-stop referansı
- NORMAL / CAUTION / DEFENSIVE / LOCKDOWN / DATA_BLOCKED global politika durumları
- Toplam ve altcoin maruziyeti, tek pozisyon, işlem başı risk, günlük kayıp ve yeni pozisyon tavanları
- Kalıcı risk_policy_snapshots ve asset_risk_snapshots
- 15 dakikalık cadence ve canlı döngüde alarm köprüsünden önce entegrasyon
- Forward performansı için settled/hit/Wilson/directional-return üretim kapısı
- Güncel olmayan politika, kapsam dışı coin ve sıfır pozisyon tavanı için fail-closed engel
- İdempotent risk_gate_decisions
- Forward Shadow ürün alarm köprüsünün Signal Risk Gate'e bağlanması

Doğrulama:

- İlk canlı testte yapısal düşük hacimli coinler global risk sayıldığı için yanlış LOCKDOWN tespit edildi.
- Piyasa stresi ile coin bazlı işlem/likidite riski ayrıldı ve aynı canlı veri regresyon testine sabitlendi.
- Düzeltilmiş canlı sonuç: kapsam=13/13, market_state=NORMAL, market_stress=0, asset_blocks=10, source_errors=0.
- BTC ve ETH azami pozisyon tavanı %10; WLD %5; 24h spot hacmi 2 milyon doların altındaki 10 coin yeni pozisyona kapalı.
- Risk cycle SUCCESS verdi; 15 dakika içindeki tam 9/9 cycle'da doğru biçimde SKIPPED oldu.
- Signal Risk Gate canlı sonuç: SHADOW_ONLY, settled=5, hit=%20, Wilson alt=%3,6, directional return=-%0,013, izinli alarm=0.
- Deterministik testlerde zayıf kanıt, eski risk snapshot'ı ve coin bloğu reddedildi; yeterli kanıt + güncel izinli risk için ALLOW yolu doğrulandı.

Not:

Risk limitleri getiri tahmini veya işlem tavsiyesi değildir. QVOL emir defteri derinliği değil, 24h spot hacim vekilidir. Frozen Forward Shadow parametreleri değiştirilmedi; kapı yalnızca ürün alarmı için kullanım izni verir.

---

## v0.9.0

### Whale Tracker - İlk Çalışan Sürüm

Tamamlandı:

- Blockchain.com doğrulanmış bloklarından 10 milyon dolar üstü Bitcoin çıktı taraması
- Ethereum safe bloklarından 10 milyon dolar üstü native ETH transfer taraması
- ARKM, BIO, ETHFI, ILV, LQTY, SLP ve UMA için doğrulanmış Ethereum ERC-20 kontrat allowlist'i
- ERC-20 Transfer log taraması ve zincir üstü decimals doğrulaması
- Binance public spot fiyatlarıyla gözlem anı USD tahmini
- Ayrı BTC, native ETH ve ERC-20 tarama yükseklikleri
- SQLite idempotent whale event kayıtları
- 24 saatlik muhafazakâr cross-chain evidence analyzer
- Bağımsız kaynak hata izolasyonu ve 5 dakikalık cycle
- Canlı ürün döngüsüne sekizinci adım olarak entegrasyon

Doğrulama:

- İlk Bitcoin taramasında 962.051 numaralı blokta 8, sonraki blokta 1 eşik üstü çıktı kaydedildi.
- Son 24 saatlik Bitcoin kanıtı: 9 olay, 9 işlem, yaklaşık 308,2 milyon dolar brüt çıktı; en büyük tekil çıktı yaklaşık 83,0 milyon dolar.
- Ethereum native collector gerçek testte 32 safe blok taradı ve kaynak hatası vermedi.
- ERC-20 collector 7 kontratın decimals değerini zincir üzerinde doğruladı; ilk safe blok taramasında hata veya eşik üstü olay oluşmadı.
- Bütünleşik Whale Tracker cycle: SUCCESS, source_errors=0, alert_eligible=0, MONITOR_ONLY.

Not:

Brüt transfer/çıktı toplamı net akış değildir. Change, self-transfer, mint, burn veya sözleşme içi hareket olabilir. Sahiplik ile borsa giriş/çıkış yönü doğrulanmayan olaylar alarm ya da işlem sinyali değildir.

---

## v0.8.0

### Macro Agent - İlk Çalışan Sürüm

Tamamlandı:

- Resmî FRED CSV üzerinden DFF, DGS10, DTWEXBGS, VIXCLS ve CPIAUCSL toplama
- 800 günlük geçmiş ve SQLite idempotent makro gözlemleri
- Fed Funds 90 günlük baz puan değişimi
- ABD 10 yıllık tahvil faizi 30 günlük baz puan değişimi
- Geniş dolar endeksi ve VIX 30 günlük değişimleri
- CPI aylık, yıllık ve yıllık eğilim değişimi
- Beş şeffaf bileşenden PROVISIONAL RISK_ON / MIXED / RISK_OFF sınıflandırması
- Makro rejim snapshot kaydı
- 6 saatlik güvenli cadence ve hata izolasyonu
- Canlı ürün döngüsüne yedinci adım olarak entegrasyon

Doğrulama:

- Genişletilmiş collector testinde 2.481 gözlem, 4 yeni kayıt ve sıfır seri hatası.
- Analyzer kapsamı 5/5.
- Son geçici rejim RISK_ON, skor=-3.
- Entegrasyon testinde Macro adımı 6 saat dolmadan doğru biçimde SKIPPED verdi.
- Tam canlı ürün cycle'ı 7/7 adımla başarıyla tamamlandı.

Not:

Makro rejim yalnızca bağlam göstergesidir. Tarihsel ve forward doğrulama olmadan alarm, işlem sinyali veya Decision Engine girdisi değildir.

---

## v0.7.0

### On-chain Agent - İlk Çalışan Sürüm

Tamamlandı:

- Bitcoin mempool işlem sayısı, vsize ve toplam ücret metrikleri
- Bitcoin fastest/half-hour/hour/economy fee verileri
- Ethereum, Solana, BSC, Arbitrum, Base, Avalanche ve Sui güncel DeFi TVL verileri
- SQLite kalıcı ve idempotent on-chain gözlemleri
- 15 dakikalık rate-safe on-chain cycle
- Kaynak hata izolasyonu
- Sekiz zincir için son 7 günlük tarihsel TVL bootstrap
- Yaklaşık 24 saatlik (18-30h) değişim analizi
- WARMING_UP ve NO_DATA veri olgunluğu korumaları
- Collector + analyzer'ın canlı ürün döngüsüne entegrasyonu

Doğrulama:

- İlk collector testinde 14 metrik, sıfır kaynak hatası.
- Tarihsel bootstrap testinde 64 gözlem, sıfır hata.
- Yanlış 43.87 saatlik pencere tespit edilip en yakın ~24 saatlik baseline seçimiyle düzeltildi.
- Sıfır Celestia TVL verisinin hazır sayılması engellendi.
- Son analyzer testinde yedi zincir STABLE, Bitcoin ağ metrikleri WARMING_UP.
- Otomatik On-chain cycle: SUCCESS, metrics=14, analyzed=15, source_errors=0.

Not:

İlk sürüm zincir seviyesi likidite ve Bitcoin ağ sağlığını kapsar. Token transferleri, borsa giriş/çıkışları ve büyük cüzdan hareketleri ileri on-chain/Whale Tracker kapsamındadır. Olgunlaşmamış metrikler işlem veya alarm sinyali değildir.

---

## v0.6.0

### Social Sentiment Agent - İlk Çalışan Sürüm

Tamamlandı:

- Geniş Reddit kripto topluluk akışından tek istekte 100 güncel paylaşım toplama
- SQLite kalıcı kayıt ve global external_id tekilleştirme
- Başlık + gönderi içeriği sentiment analizi
- Takip listesi aliasları ve dinamik Binance Spot USDT ticker keşfi
- Belirsiz sosyal ticker yanlış eşleşmelerine karşı koruma
- Coin bazlı 24 saatlik sentiment özeti
- Pozitif / negatif / nötr yön sınıflandırması
- Örneklem, yönlü kanıt ve fikir birliğine dayalı muhafazakâr güven puanı
- 15 dakikalık rate-safe çalışma aralığı
- 5 dakikalık canlı ürün döngüsüne hata izolasyonlu entegrasyon

Doğrulama:

- Collector gerçek veride 100 paylaşım aldı.
- Tekilleştirme testi aynı akışta yeni kayıt=0 üretti.
- Yanlış LINK eşleşmesi tespit edilip düzeltildi.
- Analyzer gerçek coin bazlı sinyaller ve muhafazakâr güven puanları üretti.
- Cadence testi 15 dakika dolmadan ikinci isteği doğru biçimde atladı.
- NEWS -> FORWARD SHADOW -> ALERT BRIDGE -> SOCIAL SENTIMENT tam cycle testi geçti.

Not:

Özel takip listesindeki düşük hacimli coinler için sosyal örneklem henüz yetersizdir. Düşük güvenli sosyal sonuçlar üretim alarmı veya işlem sinyali sayılmaz; validasyon paralelde devam eder.

---

## Unreleased - v0.5.x Research / Validation Layer

### News Intelligence doğrulama ve ürünleştirme

Eklendi:

- GDELT tarihsel haber backfill ile yaklaşık 360 günlük veri seti
- Historical News Analyzer
- Historical Market Data eşleştirme
- Leakage-safe Walk-Forward Backtest
- Locked Out-of-Sample Validation
- Rolling Research Laboratory
- Policy Robustness Analyzer
- Frozen Policy Monthly Stress Test
- Market Regime Analyzer
- Regime-Aware Rolling Validation
- High-Confidence Signal Calibration
- News + Technical Confluence Lab
- Weighted Multi-Factor Champion/Challenger Lab
- Multifactor Robustness & Weight Stability Analyzer
- Adaptive Dual-Champion Lab

Araştırma sonucu:

- 1h NEWS BASE policy güçlü benchmark olarak belirlendi.
- Weighted REGIME_AWARE multifactor provisional Champion adayı oldu.
- Multifactor robustness statüsü ROBUST_B.
- Adaptive selector reddedildi.
- Overfitting riskini azaltmak için aynı tarihsel veri üzerindeki optimizasyon durduruldu ve model forward doğrulamaya geçirildi.

### Forward Shadow Validation

Eklendi:

- REGIME_AWARE_FORWARD_V2
- 1h horizon
- Frozen cutoff 0.249
- Gerçek forward-only tahmin kaydı
- 1h sonra Binance minute data ile otomatik settle
- NEWS BASE vs MULTIFACTOR scoreboard
- V1 pre-start seed hatası düzeltmesi; 16 eski kayıt geçersiz sayıldı
- Tek komutluk live cycle: collect -> analyze -> settle/record
- macOS LaunchAgent ile 5 dakikada bir otomatik çalışma

### Coin kapsamı

- Takip listesi coin aliasları eklendi: ARKM, FIDA, WLD, TIA, ETHFI, BIO, LQTY, ILV, SLP, UMA, IO
- Binance Spot USDT ticker keşfi dinamik hale getirildi
- Yeni coin/fırsatların sabit liste dışında da tespit edilebilmesi hedeflendi

### Bildirim / rapor

- ChatGPT Günlük Kripto Brifingi yeniden aktif edildi: 11:00
- ChatGPT Kritik Gelişme Takibi yeniden aktif edildi: saatlik condition watch
- Kritik olay yoksa bildirim göndermeme prensibi korundu
- Telegram notifier yardımcı/opsiyonel altyapı olarak eklendi; ana kullanıcı bildirim kanalı değildir

---

## v0.5.0

### News Intelligence Agent - İlk Çalışan Sürüm

Tamamlandı:

- CoinDesk RSS haber toplama
- Cointelegraph RSS haber toplama
- The Block RSS haber toplama
- SQLite kalıcı haber kayıt sistemi
- Haber tekrarlarının link bazında engellenmesi
- Haber analiz kayıt tablosu
- Haberlerden coin tespiti
- Olay türü sınıflandırması
- Pozitif / negatif / nötr yön sınıflandırması
- Etki skoru
- Güven skoru
- Analiz sonuçlarının SQLite'a kaydedilmesi

Not:

Bu sürüm kural tabanlı ilk News Intelligence katmanıdır. Nihai tahmin başarısı iddiasında bulunmaz. Tahmin değeri tarihsel ve forward validasyon katmanlarıyla ölçülmektedir.

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

# Sonraki Faz

Ana ürün yol haritası tamamlandı. Yeni sürüm numarası gerektiren geliştirmeler yalnız ölçülmüş ürün ihtiyacıyla açılacaktır; yakın öncelik operasyonel doğrulama, veri birikimi ve güvenilirliktir.

## 2026-08-28 — Private Supabase runtime RLS hardening

- Enabled RLS on the 10 remaining `agca_runtime` tables; all 43 runtime tables
  are now RLS-enabled.
- Revoked schema/table/default privileges from `anon` and `authenticated`;
  no public grant or schema usage existed before or after the migration.
- Added `PRIVATE_RLS_V1` to mirror table provisioning so future remote tables
  are fail-closed by construction.
- Full suite passes 124/124.

## 2026-08-28 — Advanced technical feature synthesis

- Added 24 closed-candle technical features covering trend, momentum,
  volatility, volume/flow, support/resistance, Fibonacci range position,
  candle structure and breakouts.
- The rolling 1h classifier now synthesizes a nonlinear technical direction
  composite from these features plus the existing market inputs.
- Six-block walk-forward accuracy improved from 42.426% to 42.727%; Brier
  improved from 0.630754 to 0.628099 and in all six blocks.
- 4h/24h and every strategy, evidence, risk and order-authority gate are unchanged.

## 2026-08-29 — Top-down multi-timeframe technical development result

- Built a causal monthly, weekly, daily, 4h and 1h feature layer using only
  fully closed higher-timeframe bars. Tests prove future and incomplete bars
  cannot change an earlier feature vector.
- Compared the fixed top-down layer with the same advanced technical classifier
  across six sequential 30-day blocks, using non-overlapping labels for 4h/24h.
  It failed the predeclared gate at every horizon: 1h accuracy/Brier moved from
  42.738%/0.628966 to 42.579%/0.631307; 4h from 44.757%/0.604854 to
  45.324%/0.605884; and 24h from 47.619%/0.611188 to 48.371%/0.603935, but
  accuracy block wins were only 2/6 at 24h and Brier wins only 2/6 at 4h.
- The layer remains reproducible rejected research and is not connected to live
  forecasts, PAPER policy, thresholds or authority.
- A second fixed experiment implemented the intended sequential workflow rather
  than feeding every metric to one model: BTC/risk market ground, monthly-
  weekly-daily big picture, 4h setup, 1h timing and 24h spot-flow confirmation.
  Missing evidence could not add confidence and any higher/lower-timeframe
  conflict forced abstention.
- The hierarchy also failed. Confirmed direction accuracy was 41.24% vs 42.33%
  at 1h, 39.59% vs 44.86% at 4h, and 45.06% vs 47.62% at 24h. Net-after-cost
  utility stayed negative at all horizons and the fixed gate rejected it.
- The hierarchy remains auditable rejected research with zero production,
  policy-mutation, action or real-order authority.
- Replaced the overly conservative all-or-nothing agreement rule with a fixed
  regime-aware soft confidence system. Only invalid data and enforced risk are
  hard blocks; ordinary disagreements change confidence according to TREND,
  RANGE, TRANSITION or SHOCK state. The audit now measures coverage, accuracy,
  net-after-cost utility and captured positive utility together.
- Soft selection retained 14.5%/15.7%/17.3% of directional opportunities at
  1h/4h/24h. It improved average accuracy and utility at 1h and 24h, but failed
  the stability gate: accuracy improved in only 3/6 blocks at both horizons;
  4h accuracy declined and improved in only 2/6 blocks. Positive-utility capture
  was only about 14%/14%/18%.
- Moving the six-block boundary by one hour also changed the raw 4h feature
  decision, demonstrating boundary sensitivity. No live or forward promotion
  was made from this unstable development result.

## 2026-08-28 — Fixed rolling 1h direction ensemble

- Added a 50/50 incumbent + rolling HistGradientBoosting probability ensemble
  for 1h direction only after six-block walk-forward improvement.
- Kept 4h/24h, price targets, action/cost thresholds, risk veto, strategy policy,
  evidence gates and real-order authority unchanged.
- Made the sklearn model cache-stable with once-serialized, SHA-256-verified
  model bytes; full suite passes 122/122.


---

## Forecast Core V2.1 Historical Result — 2026-08-16

- Binance USD-M türev arşivi 12/13 coin için hazırlandı; 400 günlük ortak pencere kuruldu.
- Seyrek ve modelde zorunlu olmayan hesap-oranı alanlarının geçerli OI bağlamlarını silmesi düzeltildi.
- Eksik tek OI saatinin 24 saatlik pencereyi topluca geçersiz saymasına yol açan positional-index hatası kaldırıldı; yalnız exact 1h/4h/24h anchor eksikleri atlanıyor.
- Son veri kalitesi: 52 bağlam atlandı, en kötü hafta %2.4; önceden kayıtlı %2 global ve %10 haftalık sınırlar geçildi.
- Dondurulmuş Futures/Microstructure Forecast Core V2.1 gerçek VALIDATION'da reddedildi:
  - 4h PRIMARY: n=5,748, Brier skill +%0.2, MAE skill %0.0, aksiyon 0.
  - 24h SECONDARY: n=948, Brier skill +%3.8, MAE skill -%2.8, class ECE %11.2, aksiyon 0.
  - 1h EXPLORATORY: n=23,028, Brier skill -%1.0, MAE skill -%0.2, aksiyon 0.
- Eşik düşürülmedi; OBSERVED_DIAGNOSTIC açılmadı; canlı Decision Engine, Risk Manager ve alarm politikası değiştirilmedi.
- Additive-stump V2.1 ailesi kapatıldı. Sonraki tek model araştırması aynı Forecast Contract ve veri katmanı üzerinde interaction-capable Forecast Core V3 ile sınırlandı.


### Forecast Core V3 Development Gate

- Aynı Forecast Contract, derivatives feature provider, Risk veto ve validator korunarak interaction-capable model katmanı eklendi.
- Model: scikit-learn HistGradientBoosting, yalnız 4h PRIMARY; depth=4, 15 leaf, 180 iteration, learning-rate=0.05, min-leaf=80, L2=1.0, seed=20260816.
- Hyperparameter/model/threshold grid açılmadı. V1 ve dondurulmuş V2.1 baselinelarını geçmek zorunlu hale getirildi.
- Geliştirme sorguları 2026-05-28'de fiziksel olarak kesiliyor; 2026-05-28..2026-08-16 sealed dönemine erişen evaluator henüz oluşturulmadı.
- Runtime scikit-learn 1.8.0 olarak sabitlendi. Sentetik model, probability, artifact round-trip ve V2 regression testleri geçti.


---

## Forecast Core V3 Development Result — 2026-08-16

- Interaction-capable 4h PRIMARY V3 development gate çalıştırıldı.
- Sealed 2026-05-28..2026-08-16 dönemi okunmadı veya değerlendirilmedi.
- Sonuç REJECT: n=5.688, Brier skill -%3,3, MAE skill %0,0, class ECE %9,1.
- 34 ham aksiyon (%0,6 coverage), %47,1 claimed hit ve -%0,295 ortalama utility üretildi.
- V3 hem piyasa/V1 baselinelarını hem frozen V2.1'i geçemedi.
- Önceden kayıtlı stop kuralı uygulandı: aynı fiyat+türev veri üzerinde yeni model, threshold veya V4 araması açılmayacak.
- Sonraki araştırma yalnız exact-time catalyst verisi veya yeni forward kanıtla aynı Forecast Contract'a feature/event provider ekleyebilir.

## 2026-08-16 — Exact-time catalyst checkpoint

- Added `data.exact_time_catalyst_feasibility` v1.4 with recent-window exact-time coverage, conservative safety buffer and pinned-announcement chronological-backbone handling.
- Added `data.exact_time_catalyst_market_data` v1.1 for targeted checksum-verified Binance Vision spot archives and causal 1h/4h/24h price-readiness checks.
- Collected 25,005 hourly rows across 1,079 ready archives with zero archive errors in the first real run.
- Added `analysis.exact_time_catalyst_event_study` v1.0 with fixed listing/delisting actions, article-clustered chronology, weekly cluster bootstrap and fail-closed validation gates.
- Real event-study returns remain unopened at this checkpoint.


## 2026-08-17 — Crypto Profit Agent v1.0.0

- Added autonomous PAPER_ONLY portfolio agent consuming Unified Forecast rows.
- Added exact timestamp fills, 1x LONG/SHORT ledger, explicit two-sided fees,
  cash/reserved capital, realized/unrealized P&L and persistent trade audit.
- Added fixed opportunity policy with Risk Manager veto and portfolio limits.
- Integrated paper execution into the existing hourly Forecast LaunchAgent.
- Added Europe/Istanbul 03:00 detailed Markdown report and Telegram summary.
- Added deterministic policy/P&L/time-window self-test.
# 2026-08-26 — Autonomous PAPER research challengers

- Added a continuous improvement loop that creates trader-balanced research candidates from quarantined public evidence.
- Added immutable forward-start timestamps and forward-only evaluations; retrospective results cannot validate a candidate.
- Added one-trader/one-vote open-position consensus snapshots with conflict exclusion.
- Research candidates cannot mutate policy, promote themselves, execute orders, or bypass PAPER_ONLY.
- Integrated the loop into both daily OKX collection and the PAPER trade-learning review.
- Added manipulation, lookahead, concentration, and execution-authority tests; full suite passes (98/98).
# 2026-08-26 — Learned strategies now trade virtual capital

- Added PAPER-only strategy sleeves sharing the existing 10,000 USDT portfolio.
- Learned LONG/SHORT challengers now apply their filters to eligible AGCA forward forecasts instead of remaining report-only hypotheses.
- Allocation is based only on closed, evidence-eligible, net-after-cost results; fewer than 10 closed trades cannot change a sleeve's weight.
- Bounded exploration prevents immediate winner-take-all allocation. Real-order authority remains structurally zero.
- Full test suite passes (102/102).
- Extended only the hash-verified PAPER exchange-rule cache window from 2h to 24h so temporary Binance 451/Supabase transport failures do not halt virtual learning; unknown symbols still fail closed.

## 2026-08-29 — Piyasa türüne göre uzman model araştırması

- Yükseliş/düşüş eğilimi, yatay piyasa, geçiş ve sert hareket dönemleri için ayrı
  yön modelleri; veri yetersizse ortak modele dönme kuralıyla tarihsel olarak sınandı.
- Sabit kurallı altı ayrı 30 günlük dönemde 4 saatlik yön isabeti altı dönemin
  altısında arttı (%44,96'dan %46,01'e); 24 saatte dört, 1 saatte iki dönem kazanıldı.
- Buna karşılık tahmin olasılıklarının güvenilirliği hiçbir zaman diliminde
  iyileşmedi. Masraflar sonrası ortalama sonuç da üç zaman diliminde ekside kaldı.
- Bu nedenle uzman modeller canlı veya ileriye dönük gölge tahmine alınmadı.
  PAPER_ONLY, karar eşikleri, strateji politikası ve gerçek emir yetkisi değişmedi.

## 2026-08-29 — Geçmiş sonuçla ekonomik işlem seçme sınaması

- Her sınav ayından önceki ayrı 30 günlük dönemde, piyasa türü, yön ve model
  güven aralığına göre masraf sonrası açıkça olumlu kalan koşullar belirlendi.
  Sınav ayının sonuçları seçim yapılırken görülmedi.
- 4 saatlik model 1.822 işlem (%13,2 kapsam) seçti; yön isabeti %48,52 ve işlem
  başına masraf sonrası ortalama sonuç -%0,049 oldu. Altı dönemin yalnız ikisi
  artıda kaldı; son dönemde güvenilir koşul bulunamadığı için işlem seçilmedi.
- 1 saatlik küçük ortalama artı sonuç yalnız tek dönemde oluştu; 24 saatlik sonuç
  belirgin biçimde ekside kaldı. Hiçbir zaman dilimi kabul şartlarını geçmedi.
- Sonuç, geçmiş ayda işe yarayan dar koşulların sonraki aya güvenilir biçimde
  taşınmadığını gösterdi. Yöntem canlı/gölge tahmine alınmadı; güvenlik ve emir
  yetkileri değiştirilmedi.

## 2026-08-29 — Vadeli piyasa + çoklu zaman dilimi 4 saatlik sınaması

- Fiyat ve çoklu zaman dilimi modeline, önceden tanımlı 23 vadeli piyasa ölçüsü
  eklendi: fonlama, açık pozisyon değişimi, spot-vadeli fiyat farkı ve vadeli
  alıcı/satıcı baskısının coin ve BTC görünümleri.
- 12 coin ve 82.664 uygun 4 saatlik örnek kullanıldı. Tek bir sıfır açık-pozisyon
  arşiv saati doldurulmadı; yalnız ona bağlı örnekler dışlandı. Toplam eksik bağlam
  oranı %0,046 ile mevcut güvenlik sınırlarının içinde kaldı.
- Altı görülmemiş 30 günlük dönemde yön isabeti ortalama %45,38'den %45,69'a
  çıktı; ancak yalnız üç dönem kazanıldı. Olasılık kalitesi 0,6048'den 0,6116'ya
  kötüleşti (düşük daha iyi), masraf sonrası sonuç -%0,094'te kaldı.
- Aday reddedildi; canlı veya ileri PAPER tahmine alınmadı. PAPER_ONLY, kanıt
  eşikleri, strateji politikası ve gerçek emir yetkisi değişmedi.
# 2026-08-31

- Ana worker'ı çevrim sonunda uzun SQLite/remote I/O beklemesine sokan yinelenen
  senkron attestation çağrısı kaldırıldı. Hash-korumalı attestation mevcut ayrı
  beş dakikalık systemd timer'ında devam eder; gözlem ve güvenlik kapsamı azalmaz.
  Odaklı testler 14/14 ve tam suite 232/232 geçti.
- PAPER ajanına ikinci ajan veya portföy oluşturmadan sürümlü tecrübe belleği
  eklendi. Yeni işlemlerin karar tezi sonuçtan önce dondurulur; kapanan işlemler
  net maliyet sonrası, idempotent ve nedensellik iddiası olmadan değerlendirilir.
- Eski tezsiz PAPER geçmişi geriye dönük gerekçe uydurulmadan “ekonomik sonuç
  korunur, beceri kanıtlanmamıştır” özetiyle saklanır. Tek işlem politika
  değiştiremez veya beceri kanıtlayamaz; P&L yoğunlaşması öğrenme kanıtına eklendi.
- Lookahead-karantina işlemleri retrospektif tecrübe özetinden çıkarıldı; temiz
  V2 özet yalnız `evidence_eligible` işlemleri kullanır ve ilk V1'i supersede eder.
- Model, strateji, PAPER seçim eşikleri, tek kasa ve PAPER_ONLY kapıları
  değiştirilmedi. Nihai tam test suite 233/233 geçti.

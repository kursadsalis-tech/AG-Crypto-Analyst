# AG Crypto Analyst — Session Handoff 2026-08-16

## Current product state

- Product remains `SHADOW_ONLY`.
- Live Decision Engine, Risk Manager and alert policy were not changed.
- V1 price-only, V2 futures/microstructure and V3 interaction challengers were rejected.
- The stop rule remains active: do not reopen indicator, threshold or model-grid search on the same price/derivatives history.

## Exact-time catalyst result

The frozen Binance listing/delisting event study completed on 82 independent
articles and 172 usable event rows. The fixed rule was listing=`AL`,
delisting=`SAT/REDUCE/AVOID`, with 10 bps cost and a chronological 70/30 split.

Validation results:

- 1h: 56.0% article hit, +1.510% average utility, weekly clustered lower bound -0.895% — rejected.
- 4h primary: 60.0% article hit, +3.609% average utility, weekly clustered lower bound -0.594% — rejected.
- 24h: 44.0% article hit, +1.430% average utility, weekly clustered lower bound -5.478% — rejected.

Interpretation: 4h is an interesting historical observation, not validated edge.
The average is positive but not stable across time clusters. No threshold,
event subtype or model search will be opened on these same outcomes.

## Next bounded research decision

The next single research path is a market-implied option distribution for BTC
and ETH. It is intentionally narrow:

- source: Deribit public option instruments and current book summaries;
- first supported horizon: 24h primary;
- outputs, if later built: median target, 80% range and cost-aware up/down probabilities;
- history: forward-only unless a separate point-in-time historical surface is proven;
- no claim that an implied distribution is forecasting edge before forward calibration;
- no expansion to altcoins, 4h or 1h before BTC/ETH 24h earns evidence.

`data.options_implied_feasibility` was added as a source/liquidity audit only.
It reads no future outcome and computes no probability, target, return or
backtest. Passing it authorizes only a forward collector design; failing it
ends this option-source path without architecture churn.

The first Mac run reached neither BTC nor ETH on Deribit and timed out. This
was a provider connectivity failure, not a liquidity/data verdict. Version
1.1 therefore keeps Deribit as the first source and automatically falls back
to Bybit's unauthenticated V5 option instruments/tickers endpoints. Both
providers are audited under the same frozen BTC/ETH liquidity gates; a partial
surface cannot pass.

The Bybit fallback was reachable only up to TLS verification on the first Mac
run; Python's framework trust store lacked the issuer chain. Version 1.1.1
uses the installed `certifi` CA bundle when available and otherwise retains
the standard verified SSL context. Certificate verification is never disabled.

The next Mac run reached Bybit successfully but its regional public endpoint
returned an empty BTC/ETH option universe. Version 1.2 adds one final official
fallback: OKX public option instruments, tickers, implied-volatility summary
and open interest. If OKX also provides no complete BTC/ETH surface, the
options-implied research path ends rather than adding more providers.

## Next command

```bash
git pull origin main && python3 -m data.options_implied_feasibility
```

Send the complete terminal output. The next decision will be made from the
reported BTC/ETH active contracts, expiries, two-sided quotes and liquid
strikes near 24 hours.


## Unified Forecast Engine — first live runtime

The earlier options-provider search is closed as a delivery priority. Commit
`e7cc0cf` adds the missing product-facing forecast runtime. Its first Mac run
sealed artifact `56c040aaaca4...` and persisted 39/39 exact forward forecasts
for 13 symbols across 1h, 4h and 24h with no missing row.

All first-bundle opinions are BEKLE because no direction reached the frozen 70%
action threshold. This is not presented as model success. It is the live
baseline from which target error, interval coverage, Brier, direction hit and
action utility will now be settled automatically. The next work item is to
observe the first settlements and connect this forecast table to the daily
investment committee report; no new provider or model lab is opened.


## 2026-08-17 — Crypto Profit Agent handoff

- Added `paper_trading.crypto_profit_agent` as PAPER_ONLY economic validation.
- Default virtual capital: 10,000 USDT; 1x LONG/SHORT; 10 bps per side.
- Exact next-open entry and exact due-close exit; no gap bridging.
- Risk limits: 10% per position, 60% total, six open, three new/cycle.
- Candidate policy: 4h/24h, >=52% directional probability, >=5pp gap,
  q50 direction alignment, current Risk Manager permission.
- Integrated into `decision_engine.hourly_forecast_job`; existing LaunchAgent
  does not need reinstall after git pull.
- Europe/Istanbul 03:00 report:
  `reports/daily_crypto_profit_agent_report.md`; Telegram summary when configured.
- Next verification: pull main, run the agent self-test once, then let the next
  natural hourly job create/fill the first paper positions.

## Oturum Sonu Yönetişim Notu — Crypto Profit Agent

Kullanıcının kesin talebi:

- AG Crypto Analyst'ın nihai patronu/orkestratörü ana asistandır; yetkiler Crypto Profit Agent ile paylaşılmayacaktır.
- Ajan öğrenen, hesap veren ve yeterli kanıtla tartışabilen bir yardımcıdır; kendi kendine üretime geçemez veya gerçek emir gönderemez.
- Ajanın eğitimi, denetimi ve performansından ana orkestratör sorumludur.
- Paper simülasyon gerçek borsa işlemlerine çok yakın olmalıdır: spread, maker/taker ücretleri, slippage, latency, exchange filtreleri, kısmi/reddedilen dolumlar, likidite, funding/borrow, veri ve bağlantı hataları dahil edilmelidir.
- Bilinmeyen koşullar iyimser doldurulmaz; fail-closed veya açık tahmin etiketi kullanılır.
- Gerçek paraya geçiş; aynı execution contract, dry-run/reconciliation, kanıt kapıları, kullanıcının açık onayı ve ayrı orkestratör terfi kararı olmadan yapılamaz.

Sonraki oturum başlangıç cümlesi: “Dostum, AG Crypto Analyst’a kaldığımız yerden devam edelim.”

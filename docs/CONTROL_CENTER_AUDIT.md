# AGCA panel audit — 28 September 2026

Canonical source: `a2616017844bfb7699634feebda969b8c9510202` (PR #100).
Audit branch: `codex/panel-observability-audit`. No merge or live deployment.

## Proven access failure

Read-only GCE/SSH observations on 2026-09-28:

- VM `agca-runtime`, project `project-6a717580-e37d-48f6-832`, zone `us-east1-b`
  is RUNNING. External IP remains `35.207.11.112`; internal IP `10.142.0.2`.
- VM checkout `/home/kursadsalis/AG-Crypto-Analyst-cloud` is at the canonical SHA.
- `agca-control-center.service` and `agca-control-broker.service` are **masked**.
- Neither 8765 nor 8787 has a listener. GET `127.0.0.1:8787/healthz` is refused.
- Tailscaled is active. Tailscale Serve already proxies
  `https://agca-runtime.tail3dd9cd.ts.net/` to `http://127.0.0.1:8787`.
- Caddy is active but its old public hostname
  `https://agca.35.207.11.112.sslip.io/` proxies to **8765**, which has no listener.
- Tailscale inventory showed the Android device offline at observation time.
  Access requires the viewing device to be connected to the same tailnet.

The absent backend is a proven present cause. Old Caddy points to a different
application/port. Who masked the services, when the panel stopped, and whether
an ad-hoc panel previously depended on a shell session cannot be established
from these observations. Do not claim a week-long timeline or an IP change.

The dirty primary local checkout contains an older, uncommitted
`runtime/control_center` FastAPI/broker implementation. It is not the current
`control_center` package on main and was neither copied nor changed.

## Current architecture and recommended access

`python -m control_center.server` serves a standard-library HTTP application on
loopback **127.0.0.1:8787**; `/` is the UI, `/api/v1/overview` projects local data,
and `/healthz` checks the panel process only (not trading health). SQLite is
opened with `mode=ro` and `query_only`. Existing JSON files supply market,
realtime/brain, technical radar, observer and learning health. The UI refreshes
completion-to-completion every two seconds. No market connection, scheduler,
model call, service-control API or trading loop is introduced.

Use **https://agca-runtime.tail3dd9cd.ts.net/** from a connected, authorized
Tailscale device. Keep the backend on loopback and use Tailscale Serve, not
Funnel. Tailscale Personal is currently free for eligible personal use; this
route adds no VM, domain or public load balancer. Existing VM charges are
unchanged. Serve `--bg` survives shell exit and resumes after reboot; systemd
keeps the Python backend running. Restrict tailnet access to the user's devices.

Sources: https://tailscale.com/docs/reference/tailscale-cli/serve and
https://tailscale.com/docs/reference/free-plans-discounts .

Cloud Shell forwarding is only a temporary fallback: in Cloud Shell run
`gcloud compute ssh kursadsalis@agca-runtime --project=project-6a717580-e37d-48f6-832 --zone=us-east1-b -- -N -L 8080:127.0.0.1:8787 -o ExitOnForwardFailure=yes`,
then use Cloud Shell Web Preview port 8080. That tunnel lives with its session
and still requires the VM panel backend. A browser's localhost never means
VM localhost. Do not open 8787 to the public internet or unmask the legacy broker.

## Code audit and changes

| Requested field | At canonical baseline | This PR |
| --- | --- | --- |
| Live price and quote age | Read model/presentation present; broken DOM prevents reliable rendering | Restored screen; existing per-symbol age retained |
| Receive lag | Health emits quote_ingest, UI drops it | Displays per-market receive/dispatch counters, receive age/lag |
| Broad radar count/freshness | Absent | Technical worker count, original freshness declaration, recomputed radar age and health age/status |
| Deep rotation/outside-deep | Absent | Observer current rotation, deep/outside counts and shortlist-is-universe flag, timestamps |
| Current candidates/rejections | Brain/API has them; missing radar DOM, reason cell omitted | Candidate table, reason cell and full aggregate rejection reasons |
| Orchestrator planned/deferred | Runtime computes capacity but never publishes it | One additional brain telemetry field; null on earlier processes, never inferred as zero |
| Brain phase/message/decision/symbol/price/times | API present; DOM targets missing | All rendered plus existing 30-event timeline |
| Open/pending/closed positions | Open/last 100 closed present; pending absent | Adds last 50 pending entries; actual DB status is PENDING_ENTRY |
| Entry/stop/TP1/TP2/trailing | Entry/stop present; TP recalculated from generic R even for frozen technical plan; no trailing label | Stored TP targets preferred; legacy R fallback retained; TP1-based trailing activation label |
| Paper equity/cash/exposure | Adds gross entry notional to cash; full original quantity for partial positions; stale PnL treated as current | Remaining margin + cash + remaining-quantity executable PnL; pure canonical quote validator; missing/stale/crossed/future quotes produce unknown equity; separate marked gross exposure |
| Position management actions | Only latest activity time | Last 50 canonical events including reason and stop-before/after |
| Latest selection/core status | API present, UI DOM absent | Restored selection summary; detailed scan errors shown separately |
| Scan duration/errors/SQLite errors | Health present, omitted in panel | Exposes existing scan_runtime including timings and SQLite name/code |
| Learning/challengers/reviewable | Old review only, unconditional ACTIVE adaptation claim | Current learning health/counts/statuses/reviewability and age; unsupported ACTIVE claim removed |
| PAPER/real-order/source SHA/dirty | Hardcoded PAPER banner; no source/event evidence | Explicit observed health flags, process source SHA/dirty and DB real-order event count; missing evidence stays unknown |
| Single authority | Repository contract only | Contract labelled as such; no claim of live process census |
| Mismatch feedback | Endpoint/function existed, UI button absent; incident columns omitted | Working diagnostic-only button and full incident details; no policy/order mutation |

Other concrete UI repairs: activity tab now hides correctly, literal backslash-n
removed, overlapping polling removed, missing DOM restored and failed refresh
labels previous values as not current. JSON feedback rejects cross-origin
requests, non-object bodies and non-finite prices. Database read failures return
503 instead of silently terminating the response.

The new `deploy/agca-live-panel.service` is a loopback-only panel process with
restart/autostart, a read-only runtime directory, and a separate writable
`/var/lib/agca-panel` for diagnostic feedback. It does not load trading secrets
or expose pause/restart/promotion. Legacy masked units stay masked. Existing
feedback at the old path is not automatically migrated; copy it only if desired
as a separate reviewed file operation.

## Limits that remain explicit

- This is the agent's recorded observations/decisions and evidence, not access
  to hidden thoughts. It does not fetch independent exchange prices; the only
  operator intervention records a suspected agent-vs-real-data mismatch.
- Separate file/DB timestamps are not an atomic whole-system snapshot. Technical
  radar and observer counts may differ between cycles. A producer's old `fresh`
  boolean does not override the displayed current age/STALE label.
- Brain candidate arrays are existing bounded scan telemetry, not every market
  symbol or a reconstruction of every rejected historical candidate. Zero/missing
  candidate telemetry is not proof the whole broad universe was deeply analysed.
- New orchestrator capacity remains unknown until the updated realtime process
  naturally writes its new brain field. Only telemetry publication changes.
- Trailing activation follows the current policy's TP1 condition; individual
  trailing updates are evidenced by the event list. No synthetic exit is computed.
- UI quote display uses its existing five-second freshness label; **valuation**
  uses the agent's stricter two-second executable quote validation. Therefore a
  displayed recent quote can still have unknown equity with an explicit reason.
- Equity is a current mark before future exit costs. It is not proof of economic
  performance or sufficient evidence for real-money operation.
- Old PAPER banners express the code's mode; the explicit authority/source section
  is the observed runtime evidence and includes unknown/stale state. An absent
  health file is not proof of real orders being disabled on a running process.

## Minimal reviewed VM installation / acceptance

Run only after this PR is reviewed and merged; do not merge as part of the audit.
Use the resulting reviewed main/merge commit as PANEL_COMMIT (a squash merge
creates a different SHA from the PR head) and require it to be on canonical main.
No database migration or trading configuration change is needed.

```bash
set -euo pipefail
cd /home/kursadsalis/AG-Crypto-Analyst-cloud
PANEL_COMMIT=REPLACE_WITH_REVIEWED_MAIN_MERGE_SHA
[ -z "$(git status --porcelain)" ]
git fetch origin main
git merge-base --is-ancestor "$PANEL_COMMIT" origin/main
git checkout main
git pull --ff-only origin main
sudo install -m 0644 deploy/agca-live-panel.service /etc/systemd/system/agca-live-panel.service
sudo systemctl daemon-reload
sudo systemctl enable --now agca-live-panel.service
sudo tailscale serve --bg http://127.0.0.1:8787
curl --fail --max-time 20 http://127.0.0.1:8787/healthz
curl --fail --max-time 20 http://127.0.0.1:8787/api/v1/overview >/dev/null
```

This restores durable access without restarting trading. To accept the new
orchestrator field and matching process SHA as well, perform one normal,
reviewed `sudo systemctl restart agca-paper-realtime-worker.service` and wait
for a fresh natural brain/health cycle. Do not restart the observer/technical
workers for this panel-only change. On the connected phone/Mac open the Tailscale
URL and verify fresh observations, planned/deferred counts, source SHA/dirty,
PAPER flags and zero real-order events, then submit one clearly labelled test
mismatch. It writes only diagnostic feedback. Never create a trade for acceptance.

Rollback: `sudo systemctl disable --now agca-live-panel.service`. This does not
stop trading. No public firewall, old mask, Caddy or production data was changed
by this audit. This session prepared commands only; live acceptance is pending.

## Verification

- Focused changed-behaviour suite: 16 tests passed.
- Complete required offline suite: 561 tests passed; no production DB used.
- Headless Chrome: full render, candidate reason, tab switching, mismatch button,
  390px layout, zero JavaScript exceptions. Synthetic UI data was used.
- `git diff --check` passed. CI/PR links are recorded in the task delivery.

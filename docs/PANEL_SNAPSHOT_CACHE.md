# Panel cache: başarısız canary sonrası revizyon — PR #102

PR açık kalacak, **merge edilmeyecek**. Bu runbook yalnız panel içindir.
Realtime/observer/technical worker, onların unit/priority değerleri, PAPER_ONLY,
tek trading authority ve diagnostic-only mismatch feedback değişmez.
Eski dirty checkout ve tarihsel PR temizliği ayrı TODO olarak kalır.

## Kanıt ve teşhis

Kullanıcının production ölçümü: doğru
`AGCA_DATABASE_PATH=/var/lib/agca/crypto_data.db` ile doğrudan shell snapshot'ı
normal scheduling altında **1.932 s**. Eski panel %25 kotada overview için 20 s
timeout: `nr_throttled 6753 → 6951` (**+198**),
`throttled_usec 752200967 → 784407619` (**+32.206652 s**).
İki istemcinin 2 s polling'i ayrı snapshot hesaplıyordu; health yaklaşık 0.97 s.

Başarısız izole canary tam `7c77d4f90c08c6cb56c1a00fb6ebc7a0ad076f4e` idi:
%50 kota, CPUWeight=20, Nice=10, MemoryMax=256M. İlk 503 tek refresh başlattı;
69.512 s sonra generation=0, refresh devam ediyor, failure yoktu.
10 s örneğinde usage **+606552 µs** (tek CPU'nun yaklaşık %6.07'si),
nr_throttled **+4**, throttled_usec **+361544 µs**. Leaf quota throttling azalmış;
bu örnek 69 s beklemeyi yalnız quota ile açıklamıyor. Ölçüm tüm 69 s'yi kapsamıyor.

Production kullanıcı tarafından eski unit'e geri alındı: cloud checkout,
%25, Nice=0, açık CPUWeight ayarı yok, 127.0.0.1:8787, READ_ONLY health başarılı.
Bu revizyon production'a burada uygulanmadı.

| Hipotez | Bulgu / kalan belirsizlik |
| --- | --- |
| Cache kilidi / duplicate compute | Compute ve tek JSON encoding cache kilidinin dışında. Eşzamanlı istekler tek compute kullanıyor; bloklanan compute sırasında HTTP/health cevap veriyor. |
| Background thread | SQLite bağlantısı hesaplamanın kendi thread'inde açılıyor. Aynı gerçek read-model/projection fixture'ı doğrudan ve thread'de aynı sonucu üretti, DB byte'ları değişmedi. Production I/O/lock beklemesi henüz dışlanmış değil. |
| Nice / CPUWeight | Yerel kontrollü ölçüm Nice=10 altında belirgin yavaşlama üretti. Weight etkisi ayrı cgroup A/B ile burada ölçülmedi. Bu nedenle bunlar güçlü scheduler hipotezi; VM kök nedeni kesinleşmiş sayılmıyor. |
| CPUQuota | %50 aynen korunuyor. Quota üst sınırdır, ayrılmış CPU garantisi değildir; düşük leaf throttling ancestor quota veya CPU rekabetini dışlamaz. |

Tek yerel deney: 50.000 kapalı pozisyonlu geçici SQLite fixture, satır başına
12 gerçek snapshot, aynı CPU'ya affinity, rakip varsa tek normal öncelikli CPU
süreci. Dört payload digest'i aynı; veritabanı değişmedi.

| Yol | Nice | CPU rakibi | Wall s | CPU s |
| --- | ---: | --- | ---: | ---: |
| Doğrudan | 0 | Yok | 0.9260 | 0.9259 |
| Cache thread | 0 | Yok | 1.0055 | 1.0053 |
| Cache thread | 0 | Var | 2.2432 | 1.1235 |
| Cache thread | 10 | Var | 11.0067 | 1.0736 |

Bu üretim DB benchmark'ı veya minimum kaynak garantisi değildir. Nice=10 aynı
CPU işini yük altında yaklaşık 4.9 kat wall süresinde yaptı. Yeni aday
**CPUQuota=50%, CPUWeight=100, Nice=0, MemoryMax=256M**: panel normal önceliğe
döner; hiçbir worker önceliği değişmez ve panelin hard cap'i kalır. %50 canlı
kabul bekleyen sınırlı adaydır; daha yüksek kota ya da kotayı kaldırma önerilmez.

Referanslar: [kernel cgroup CPU controller](https://docs.kernel.org/admin-guide/cgroup-v2.html),
[kernel scheduler statistics](https://docs.kernel.org/scheduler/sched-stats.html),
[systemd execution scheduling](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#Nice=).

## Cache ve ilerleme semantiği

- İstekle başlayan tek daemon refresh; timer, decision loop veya trading/learning
  yazımı yok. Son başarılı JSON tüm istemcilerce paylaşılır. TTL tamamlanmasından
  itibaren 2 s; UI polling 2 s kalır.
- Başarı yoksa 503/UNAVAILABLE. TTL geçmiş veya refresh başarısızsa önceki başarı
  yalnız STALE. Hata sonrası 2 s retry backoff; başarısız encoding de eskiyi korur.
  UI, fetch gecikse bile CURRENT etiketi için yerel TTL uygular.
- Mevcut age/hit/miss/generation/failure/duration/CPU alanlarına sabit allowlist
  `refresh_phase`, phase elapsed, native `refresh_thread_id` ve son checkpoint'in
  thread CPU süresi eklendi. Health compute başlatmaz. Error metni/SQL/path sızmaz.
- QUEUED thread'in henüz başlamadığını; diğer aşamalar son girilen okuma/işleme
  adımını gösterir. Son aşama hata sonrası korunur; canlı TID ve phase elapsed
  bitince null olur. Checkpoint CPU alanı sürekli ölçüm **değildir**.
- Bloklanan thread'i öldürüp ikinci compute başlatmıyoruz. Cache tek uçuşu korur;
  aşağıdaki dış kabul sınırı paneli geri alır. Kodda zorla thread iptali yok.

## Kısa canlı kabul: iki refresh, otomatik rollback

Mac/telefon panel sekmelerini kapat. PR #102 gövdesindeki **tam yeni head SHA** ile
`export AGCA_PANEL_SHA=...` çalıştır; başarısız 7c77 head'ini kullanma.
Aşağıdaki blok mevcut checkout'u değiştirmeden yeni detached worktree oluşturur.
Yalnız kendi geçici panel drop-in'ini kaldırır; eski unit/drop-in'ler korunur.
**Başarılı denemede de rollback yapılır.** Deployment veya merge değildir.

```bash
bash <<'SH'
set -euo pipefail
: "${AGCA_PANEL_SHA:?PR #102 gövdesindeki tam yeni SHA değerini export edin}"
[[ "$AGCA_PANEL_SHA" =~ ^[0-9a-f]{40}$ ]]
AGCA_SOURCE=/home/kursadsalis/AG-Crypto-Analyst-cloud
git -C "$AGCA_SOURCE" fetch --no-tags origin pull/102/head
test "$(git -C "$AGCA_SOURCE" rev-parse FETCH_HEAD)" = "$AGCA_PANEL_SHA"
AGCA_RELEASE=$(mktemp -d "/home/kursadsalis/agca-panel-canary-${AGCA_PANEL_SHA:0:12}.XXXXXX")
git -C "$AGCA_SOURCE" worktree add --detach "$AGCA_RELEASE" "$AGCA_PANEL_SHA"
AGCA_PROBE=$(mktemp -d /tmp/agca-panel-acceptance.XXXXXX)
AGCA_DROPIN=/run/systemd/system/agca-live-panel.service.d/90-agca-cache-canary.conf
test ! -e "$AGCA_DROPIN"
AGCA_WORKERS=(agca-paper-realtime-worker.service agca-market-behavior-observer.service agca-technical-kline-worker.service)
systemctl show "${AGCA_WORKERS[@]}" -p Id -p MainPID -p ExecMainStartTimestamp -p NRestarts > "$AGCA_PROBE/workers.before"
systemctl show agca-live-panel.service -p WorkingDirectory -p CPUQuotaPerSecUSec -p CPUWeight -p Nice -p MemoryMax > "$AGCA_PROBE/panel.before"
rollback() {
  agca_exit=$?
  trap - EXIT INT TERM
  set +e
  sudo rm -f "$AGCA_DROPIN"
  sudo systemctl daemon-reload
  sudo systemctl restart agca-live-panel.service
  agca_rollback=$?
  for agca_try in 1 2 3; do
    if curl --fail --silent --show-error --max-time 1 http://127.0.0.1:8787/healthz > "$AGCA_PROBE/rollback.health.json"; then break; fi
    sleep 1
  done
  python3 - "$AGCA_PROBE/rollback.health.json" <<'PY'
import json, sys
h = json.load(open(sys.argv[1]))
assert h['status'] == 'ok' and h['authority'] == 'READ_ONLY'
PY
  if [ "$?" -ne 0 ]; then agca_rollback=1; fi
  systemctl show agca-live-panel.service -p WorkingDirectory -p CPUQuotaPerSecUSec -p CPUWeight -p Nice -p MemoryMax > "$AGCA_PROBE/panel.after"
  diff -u "$AGCA_PROBE/panel.before" "$AGCA_PROBE/panel.after" || agca_rollback=1
  systemctl show "${AGCA_WORKERS[@]}" -p Id -p MainPID -p ExecMainStartTimestamp -p NRestarts > "$AGCA_PROBE/workers.after"
  diff -u "$AGCA_PROBE/workers.before" "$AGCA_PROBE/workers.after" || agca_rollback=1
  echo "Canary exit=$agca_exit rollback exit=$agca_rollback report=$AGCA_PROBE"
  if [ "$agca_rollback" -ne 0 ]; then exit 1; fi
  exit "$agca_exit"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
cat > "$AGCA_PROBE/canary.conf" <<UNIT
[Service]
WorkingDirectory=$AGCA_RELEASE
ExecStart=
ExecStart=$AGCA_SOURCE/.venv/bin/python -m control_center.server
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=AGCA_DATABASE_PATH=/var/lib/agca/crypto_data.db
Environment=AGCA_EXECUTION_MODE=PAPER_ONLY
CPUQuota=50%
CPUWeight=100
Nice=0
MemoryMax=256M
UNIT
sudo install -D -m 0644 "$AGCA_PROBE/canary.conf" "$AGCA_DROPIN"
sudo systemctl daemon-reload
sudo systemctl restart agca-live-panel.service
systemctl show agca-live-panel.service -p ActiveState -p WorkingDirectory -p CPUQuotaPerSecUSec -p CPUWeight -p Nice -p MemoryMax > "$AGCA_PROBE/panel.canary"
python3 - "$AGCA_PROBE/panel.canary" "$AGCA_RELEASE" <<'PY'
import pathlib, sys
p = dict(line.split('=', 1) for line in pathlib.Path(sys.argv[1]).read_text().splitlines())
assert p == dict(ActiveState='active', WorkingDirectory=sys.argv[2], CPUQuotaPerSecUSec='500ms', CPUWeight='100', Nice='0', MemoryMax='268435456'), p
PY
for agca_try in 1 2 3; do
  if curl --fail --silent --show-error --max-time 1 http://127.0.0.1:8787/healthz > "$AGCA_PROBE/start.health.json"; then break; fi
  sleep 1
done
timeout --signal=INT --kill-after=2s 25s "$AGCA_SOURCE/.venv/bin/python" \
  "$AGCA_RELEASE/scripts/panel_cache_acceptance.py" --output "$AGCA_PROBE/acceptance.json"
SH
```

Probe: ilk 503, en fazla 8 s ilk generation bekleme; iki eşzamanlı cache hit;
2.05 s TTL sonrası iki istemciyle tek yeni refresh; en fazla 8 s ikinci generation;
son iki hızlı hit. Toplam iki compute, altı 200 isteği. Her compute ≤5 s,
HTTP/health <500 ms, hata yok, ikinci generation var. BTC/ETH quote age ≤1.5 s,
receive lag ≤1 s, PAPER_ONLY ve real_order_allowed=false; eksik kanıt başarısızdır.
Worker process identity/restart sayısı önce/sonra aynı kalmalı. Probe dış sınırı
25 s; takılan ilk refresh için 69 s beklenmez. Bu kısa smoke test uzun yükte
p95 veya minimum kaynak ispatı değildir; daha uzun canlı deneme ayrı değerlendirilir.

Rapor health/cache aşamalarını, aktif TID'nin `schedstat` CPU/runqueue wait,
state/wchan bilgilerini ve leaf + ancestor cgroup cpu.stat/cpu.max/cpu.weight
örneklerini saklar. Aynı TID ve cgroup için ardışık örnekleri karşılaştır:

- CPU az, runqueue wait hızlı artıyorsa runnable iş CPU bekliyor.
- S/D ve ilerlemeyen CPU, az runqueue wait: phase/wchan ile I/O/lock beklemesini
  araştır. Tek örnek veya erişilemeyen/kapalı schedstats kesin kanıt değildir.
- Leaf veya ancestor nr_throttled/throttled_usec artışı: quota etkisini birlikte
  değerlendir. Düşük leaf değerinden ancestor throttling yok sonucu çıkarma.
- QUEUED'da TID yoksa thread başlamamış; diğer sabit phase hangi adımda kaldığını
  ayırır. Kaybolan TID unknown'dır; CPU=0 diye yorumlanmaz.

Başarısızsa raporu koru, kotayı tekrar artırma. Unit/priority öncesi ayarlara
rollback doğrulanmalı. Worktree/report ve tarihsel checkout/PR'ları bu görevde
silme. SQL failure injection veya mismatch POST canlıda yapılmaz; STALE/error,
read-only, feedback ve single authority sözleşmeleri offline testlerde korunur.

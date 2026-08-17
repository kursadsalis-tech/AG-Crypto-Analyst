# AG Crypto Analyst — Mac-Bağımsız Bulut Runtime

Durum: hazırlık. Bu katman model veya işlem politikasını değiştirmez; mevcut
Python ürününü kalıcı bir Ubuntu VM üzerinde çalıştırır.

## Neden Supabase tek başına yeterli değildi?

Supabase uzaktaki PostgreSQL hafızasıdır. AGCA v1.2.1, 28 operasyonel/audit
tablosunu SQLite'tan Supabase'e güvenli biçimde aynalayabilir. Ancak Supabase
Python modüllerini, saatlik Forecast Engine'i veya beş dakikalık collector
cycle'ını kendi başına çalıştırmaz. Mac bağımlılığını kaldıran eksik parça
kalıcı bir compute worker'dır.

Hedef yapı:

- Ubuntu VM: Python süreçleri ve kalıcı yerel SQLite source-of-truth
- Supabase: operasyonel tabloların uzak aynası ve denetim hafızası
- GitHub: sürümlenmiş kod
- systemd: 5 dakikalık ürün cycle'ı ve saatlik forecast/agent retry'ları
- Mac: geliştirme/erişim cihazı; nihai cutover sonrası çalışma zorunluluğu yok

## Ücretsiz-öncelikli hedef

Birinci tercih Oracle Cloud Always Free üzerinde Ubuntu VM'dir. Kaynak
kapasitesi açılamazsa ikinci tercih, uygun ücretsiz bölgede Google Compute
Engine e2-micro ve kalıcı standart disktir. Kurulum paketi sağlayıcıdan
bağımsızdır; aynı systemd sözleşmesi ücretli küçük bir Ubuntu VM'e de taşınır.

Ücretsiz kota aşımı sessizce kabul edilmez. Hesapta bütçe alarmı ve mümkünse
hard quota kurulur. AGCA için dışarıya açık HTTP portu gerekmez; yalnız SSH
yönetimi ve outbound HTTPS kullanılır.

## Runtime sözleşmesi

systemd iki timer kurar:

1. agca-live-cycle.timer
   - her 5 dakikada analysis.forward_shadow_cycle
2. agca-hourly-forecast.timer
   - her saatin :02, :07, :12 dakikalarında
     decision_engine.hourly_forecast_job

İki iş aynı SQLite dosyasına yazdığı için scripts.cloud_job_runner ortak
non-blocking dosya kilidi kullanır. Çakışan ikinci çalışma hata sayılmadan
atlanır. Timer'larda retrospective catch-up kapalıdır; sunucu kapalıyken geçmiş
saate tahmin uydurulmaz. Mevcut Forecast Engine stale-candle ve exact
settlement korumaları değişmez.

## VM dosya düzeni

Önerilen yollar:

- repo: /opt/agca/app
- virtualenv: /opt/agca/venv
- local state: /opt/agca/app/crypto_data.db
- secrets: /etc/agca/agca.env (0600, root)
- shared lock: /var/lock/agca-runtime.lock
- logs: systemd journal
- Markdown reports: /opt/agca/app/reports

Gerçek Supabase URL'si, Telegram anahtarı veya başka secret GitHub'a yazılmaz.

## Kurulum öncesi doğrulama

    python3 -m scripts.cloud_job_runner --self-test
    python3 -m scripts.install_ubuntu_cloud_runtime --self-test
    python3 -m decision_engine.forecast_engine --self-test
    python3 -m paper_trading.crypto_profit_agent --self-test

## Geçiş planı

### A. VM hazırlığı

1. Ubuntu 24.04 LTS VM oluştur.
2. Yalnız SSH erişimini aç; web portu açma.
3. /opt/agca/app içine private GitHub reposunu çek.
4. /opt/agca/venv oluştur ve requirements-cloud.txt kur.
5. Mac'teki crypto_data.db dosyasının bütünlüğü korunmuş bir kopyasını VM'e
   aktar. Secret veya DB dosyasını Git'e koyma.

### B. 24 saat parity modu

- Mac çalışma görevleri açık kalır.
- Bulutta /etc/agca/agca.env içine henüz AGCA_REMOTE_DATABASE_URL yazılmaz;
  böylece iki makine aynı Supabase tablolarına yarışarak yazmaz.
- Telegram secret buluta verilmez; çift bildirim üretilmez.
- VM systemd timer'ları etkinleştirilir.
- Mac ve VM aynı başlangıç DB snapshot'ından 24 saat paralel çalışır.

Parite geçiş koşulları:

- aynı kapanış için 13x3 = 39 forecast veya aynı açık NO_FORECAST nedeni,
- q50/aralık/olasılıkların aynı model-artifact altında deterministik eşleşmesi,
- exact entry/exit settlement ve paper-agent muhasebesinde sessiz gap yok,
- duplicate bundle/pozisyon yok,
- 03:00 ve 11:00 rapor cycle'ları hata vermiyor,
- timer ve kaynak hataları görünür, fail-closed.

### C. Nihai cutover

1. Mac ve VM timer'larını kısa süre durdur.
2. Mac'teki son authoritative SQLite dosyasını bir kez daha VM'e kopyala.
3. VM'de Supabase URL'sini /etc/agca/agca.env içine gizli olarak tanımla.
4. python3 -m scripts.bootstrap_remote_runtime ile operasyonel tabloları
   Supabase'e eşitle.
5. VM timer'larını aç ve bir tam saatlik cycle'ı doğrula.
6. Mac LaunchAgent'lerini kapalı bırak.

Bu noktadan sonra Mac kapalıyken de sistem çalışır. Geri dönüş gerekirse VM
timer'ları durdurulur ve Mac görevleri tekrar açılır; aynı anda iki
authoritative writer çalıştırılmaz.

## Ubuntu systemd kurulumu

VM üzerinde repo ve virtualenv hazırlandıktan sonra:

    sudo /opt/agca/venv/bin/python -m scripts.install_ubuntu_cloud_runtime \
      --project-dir /opt/agca/app \
      --service-user ubuntu

Durum kontrolü:

    systemctl list-timers 'agca-*'
    journalctl -u agca-live-cycle.service -n 100 --no-pager
    journalctl -u agca-hourly-forecast.service -n 100 --no-pager

Bu kurulum gerçek emir yetkisi vermez. Crypto Profit Agent PAPER_ONLY,
Forecast Engine FORWARD_SHADOW ve Risk Manager veto yetkisi değişmeden kalır.

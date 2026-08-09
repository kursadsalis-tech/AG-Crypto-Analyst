import sqlite3
from statistics import median
from datetime import datetime

DB_NAME = "crypto_data.db"
REPORT_FILE = "reports/market_analysis.txt"

connection = sqlite3.connect(DB_NAME)
cursor = connection.cursor()

cursor.execute("""
    SELECT
        coin,
        price,
        change_24h,
        volume,
        high_24h,
        low_24h
    FROM crypto_prices
    ORDER BY timestamp DESC
    LIMIT 13
""")

rows = cursor.fetchall()

connection.close()

volumes = [row[3] for row in rows]
median_volume = median(volumes)

report_lines = []

report_lines.append("AG CRYPTO ANALYST - MARKET ANALYSIS")
report_lines.append("=" * 60)
report_lines.append(
    f"Analiz zamanı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
)
report_lines.append("")


for row in rows:
    (
        coin,
        price,
        change_24h,
        volume,
        high_24h,
        low_24h
    ) = row

    # Trend skoru
    if change_24h > 2:
        trend_score = 2
        trend = "GÜÇLÜ POZİTİF"
    elif change_24h > 0:
        trend_score = 1
        trend = "POZİTİF"
    elif change_24h < -2:
        trend_score = -2
        trend = "GÜÇLÜ NEGATİF"
    elif change_24h < 0:
        trend_score = -1
        trend = "NEGATİF"
    else:
        trend_score = 0
        trend = "NÖTR"

    # Hacim skoru
    if volume >= median_volume * 2:
        volume_score = 1
        volume_status = "ÇOK YÜKSEK"
    elif volume >= median_volume:
        volume_score = 0
        volume_status = "YÜKSEK"
    else:
        volume_score = -1
        volume_status = "DÜŞÜK"

    # Fiyat konumu skoru
    price_range = high_24h - low_24h

    if price_range == 0:
        position_score = 0
        price_position = "BELİRSİZ"
    else:
        position = (price - low_24h) / price_range * 100

        if position >= 70:
            position_score = 1
            price_position = "ÜST BÖLGE"
        elif position >= 30:
            position_score = 0
            price_position = "ORTA BÖLGE"
        else:
            position_score = -1
            price_position = "ALT BÖLGE"

    # Genel skor
    total_score = trend_score + volume_score + position_score

    if total_score >= 3:
        overall_signal = "GÜÇLÜ POZİTİF"
    elif total_score >= 1:
        overall_signal = "POZİTİF"
    elif total_score <= -3:
        overall_signal = "GÜÇLÜ NEGATİF"
    elif total_score <= -1:
        overall_signal = "NEGATİF"
    else:
        overall_signal = "NÖTR"

    report_lines.append(
        f"{coin:<12} | "
        f"Fiyat: {price:<12} | "
        f"24h: {change_24h:>7.2f}% | "
        f"Trend: {trend:<16} | "
        f"Hacim: {volume_status:<11} | "
        f"Konum: {price_position:<12} | "
        f"Skor: {total_score:>2} | "
        f"Sinyal: {overall_signal}"
    )


with open(REPORT_FILE, "w", encoding="utf-8") as file:
    file.write("\n".join(report_lines))

print("✅ Market analizi tamamlandı.")
print(f"✅ Rapor oluşturuldu: {REPORT_FILE}")
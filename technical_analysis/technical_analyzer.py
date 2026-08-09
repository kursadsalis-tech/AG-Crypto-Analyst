import requests
from datetime import datetime

BASE_URL = "https://api.binance.com/api/v3/klines"
REPORT_FILE = "reports/technical_analysis.txt"

coins = [
    "BTCUSDT",
    "ETHUSDT",
    "ARKMUSDT",
    "WLDUSDT",
    "TIAUSDT",
    "ETHFIUSDT",
    "UMAUSDT",
    "FIDAUSDT",
    "SLPUSDT",
    "LQTYUSDT",
    "ILVUSDT",
    "IOUSDT",
    "BIOUSDT"
]


def calculate_ema_series(values, period):
    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    result = [ema]

    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
        result.append(ema)

    return result


def calculate_rsi(closes, period=14):
    if len(closes) <= period:
        return None

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        average_gain = (
            (average_gain * (period - 1)) + gains[i]
        ) / period

        average_loss = (
            (average_loss * (period - 1)) + losses[i]
        ) / period

    if average_loss == 0:
        return 100

    rs = average_gain / average_loss

    return 100 - (100 / (1 + rs))


def calculate_macd(closes):
    ema_12 = calculate_ema_series(closes, 12)
    ema_26 = calculate_ema_series(closes, 26)

    if not ema_12 or not ema_26:
        return None, None, None

    offset = len(ema_12) - len(ema_26)

    macd_values = []

    for i in range(len(ema_26)):
        macd_values.append(
            ema_12[i + offset] - ema_26[i]
        )

    macd_line = macd_values[-1]

    signal_values = calculate_ema_series(macd_values, 9)

    if not signal_values:
        return macd_line, None, None

    signal_line = signal_values[-1]
    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram


report_lines = []

report_lines.append("AG CRYPTO ANALYST - TECHNICAL ANALYSIS")
report_lines.append("=" * 80)
report_lines.append(
    f"Analiz zamanı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
)
report_lines.append("Zaman dilimi: 1H")
report_lines.append("")


for coin in coins:
    try:
        response = requests.get(
            BASE_URL,
            params={
                "symbol": coin,
                "interval": "1h",
                "limit": 100
            },
            timeout=10
        )

        response.raise_for_status()

        candles = response.json()
        closes = [float(candle[4]) for candle in candles]

        # RSI
        rsi = calculate_rsi(closes)

        if rsi >= 70:
            rsi_score = -1
            rsi_signal = "AŞIRI ALIM"
        elif rsi <= 30:
            rsi_score = 1
            rsi_signal = "AŞIRI SATIM"
        elif rsi >= 50:
            rsi_score = 1
            rsi_signal = "POZİTİF"
        else:
            rsi_score = -1
            rsi_signal = "NEGATİF"

        # MACD
        macd, signal, histogram = calculate_macd(closes)

        if histogram > 0:
            macd_score = 1
            macd_signal = "POZİTİF"
        elif histogram < 0:
            macd_score = -1
            macd_signal = "NEGATİF"
        else:
            macd_score = 0
            macd_signal = "NÖTR"

        # EMA20 / EMA50
        ema20 = calculate_ema_series(closes, 20)[-1]
        ema50 = calculate_ema_series(closes, 50)[-1]

        if ema20 > ema50:
            ema_score = 1
            ema_signal = "POZİTİF"
        elif ema20 < ema50:
            ema_score = -1
            ema_signal = "NEGATİF"
        else:
            ema_score = 0
            ema_signal = "NÖTR"

        # Birleşik teknik skor
        technical_score = (
            rsi_score +
            macd_score +
            ema_score
        )

        if technical_score >= 3:
            technical_signal = "GÜÇLÜ POZİTİF"
        elif technical_score >= 1:
            technical_signal = "POZİTİF"
        elif technical_score <= -3:
            technical_signal = "GÜÇLÜ NEGATİF"
        elif technical_score <= -1:
            technical_signal = "NEGATİF"
        else:
            technical_signal = "NÖTR"

        report_lines.append(
            f"{coin:<12} | "
            f"RSI: {rsi:>6.2f} | "
            f"MACD: {macd_signal:<8} | "
            f"EMA: {ema_signal:<8} | "
            f"Skor: {technical_score:>2} | "
            f"Teknik: {technical_signal}"
        )

    except Exception as e:
        report_lines.append(
            f"{coin:<12} | HATA: {e}"
        )


with open(REPORT_FILE, "w", encoding="utf-8") as file:
    file.write("\n".join(report_lines))

print("✅ Teknik analiz tamamlandı.")
print(f"✅ Rapor oluşturuldu: {REPORT_FILE}")
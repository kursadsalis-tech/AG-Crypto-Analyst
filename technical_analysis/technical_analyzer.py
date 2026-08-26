import os
import requests
from datetime import datetime

BASE_URL = os.getenv(
    "AGCA_BINANCE_SPOT_BASE_URL", "https://data-api.binance.vision"
) + "/api/v3/klines"
REPORT_FILE = "reports/technical_analysis.txt"

coins = [
    "BTCUSDT", "ETHUSDT", "ARKMUSDT", "WLDUSDT", "TIAUSDT", "ETHFIUSDT",
    "UMAUSDT", "FIDAUSDT", "SLPUSDT", "LQTYUSDT", "ILVUSDT", "IOUSDT", "BIOUSDT"
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
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        average_gain = ((average_gain * (period - 1)) + gains[i]) / period
        average_loss = ((average_loss * (period - 1)) + losses[i]) / period
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
    macd_values = [ema_12[i + offset] - ema_26[i] for i in range(len(ema_26))]
    macd_line = macd_values[-1]
    signal_values = calculate_ema_series(macd_values, 9)
    if not signal_values:
        return macd_line, None, None
    signal_line = signal_values[-1]
    return macd_line, signal_line, macd_line - signal_line


def analyze_symbol(coin):
    response = requests.get(
        BASE_URL,
        params={"symbol": coin, "interval": "1h", "limit": 100},
        timeout=10,
    )
    response.raise_for_status()
    candles = response.json()
    closes = [float(candle[4]) for candle in candles]

    rsi = calculate_rsi(closes)
    if rsi >= 70:
        rsi_score, rsi_signal = -1, "AŞIRI ALIM"
    elif rsi <= 30:
        rsi_score, rsi_signal = 1, "AŞIRI SATIM"
    elif rsi >= 50:
        rsi_score, rsi_signal = 1, "POZİTİF"
    else:
        rsi_score, rsi_signal = -1, "NEGATİF"

    macd, signal, histogram = calculate_macd(closes)
    if histogram > 0:
        macd_score, macd_signal = 1, "POZİTİF"
    elif histogram < 0:
        macd_score, macd_signal = -1, "NEGATİF"
    else:
        macd_score, macd_signal = 0, "NÖTR"

    ema20 = calculate_ema_series(closes, 20)[-1]
    ema50 = calculate_ema_series(closes, 50)[-1]
    if ema20 > ema50:
        ema_score, ema_signal = 1, "POZİTİF"
    elif ema20 < ema50:
        ema_score, ema_signal = -1, "NEGATİF"
    else:
        ema_score, ema_signal = 0, "NÖTR"

    technical_score = rsi_score + macd_score + ema_score
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

    return {
        "coin": coin,
        "rsi": rsi,
        "rsi_signal": rsi_signal,
        "macd": macd,
        "macd_signal": macd_signal,
        "ema_signal": ema_signal,
        "technical_score": technical_score,
        "technical_signal": technical_signal,
    }


def main():
    report_lines = [
        "AG CRYPTO ANALYST - TECHNICAL ANALYSIS",
        "=" * 80,
        f"Analiz zamanı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "Zaman dilimi: 1H",
        "",
    ]

    for coin in coins:
        try:
            result = analyze_symbol(coin)
            report_lines.append(
                f"{coin:<12} | RSI: {result['rsi']:>6.2f} | "
                f"MACD: {result['macd_signal']:<8} | EMA: {result['ema_signal']:<8} | "
                f"Skor: {result['technical_score']:>2} | Teknik: {result['technical_signal']}"
            )
        except Exception as exc:
            report_lines.append(f"{coin:<12} | HATA: {exc}")

    with open(REPORT_FILE, "w", encoding="utf-8") as file:
        file.write("\n".join(report_lines))

    print("✅ Teknik analiz tamamlandı.")
    print(f"✅ Rapor oluşturuldu: {REPORT_FILE}")


if __name__ == "__main__":
    main()

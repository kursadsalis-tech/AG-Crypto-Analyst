import requests
from database.repository import save_price

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

BASE_URL = "https://api.binance.com/api/v3/ticker/24hr"

for coin in coins:
    try:
        response = requests.get(
            BASE_URL,
            params={"symbol": coin},
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        print(f"{coin:<12} -> {data['lastPrice']}")

        save_price(
            coin,
            data["lastPrice"],
            data["priceChangePercent"],
            data["quoteVolume"],
            data["highPrice"],
            data["lowPrice"],
            data["openPrice"],
            data["weightedAvgPrice"],
            data["count"]
        )

    except Exception as e:
        print(f"{coin:<12} -> HATA: {e}")

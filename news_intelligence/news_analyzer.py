import re

import requests

from news_intelligence.news_repository import (
    create_news_analysis_table,
    get_all_news,
    save_news_analysis_batch,
)


BINANCE_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"

# Project-name aliases stay explicit for precision. The user's tracked universe is
# included here, while a separate dynamic ticker detector below lets newly listed
# Binance spot assets be discovered when their ticker appears explicitly in news.
COIN_KEYWORDS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "ether", "eth"],
    "XRP": ["xrp", "ripple"],
    "HYPE": ["hyperliquid", "hype"],
    "CRO": ["crypto.com", "cro"],
    "ABTC": ["american bitcoin", "abtc"],
    "SOL": ["solana", "sol"],
    "BNB": ["bnb", "binance coin"],
    "ADA": ["cardano", "ada"],
    "DOGE": ["dogecoin", "doge"],
    "TRX": ["tron", "trx"],
    "LINK": ["chainlink", "link"],
    "AVAX": ["avalanche", "avax"],
    "SUI": ["sui"],
    "ARKM": ["arkham", "arkm"],
    "FIDA": ["bonfida", "fida"],
    "WLD": ["worldcoin", "world coin", "wld"],
    "TIA": ["celestia", "tia"],
    "ETHFI": ["ether.fi", "etherfi", "ethfi"],
    "BIO": ["bio protocol", "bio"],
    "LQTY": ["liquity", "lqty"],
    "ILV": ["illuvium", "ilv"],
    "SLP": ["smooth love potion", "slp"],
    "UMA": ["uma protocol", "uma"],
    "IO": ["io.net", "ionet", "io token"],
}

# Common all-caps words that can look like crypto tickers in headlines.
DYNAMIC_TICKER_BLACKLIST = {
    "A", "I", "AI", "CEO", "CFO", "SEC", "ETF", "FED", "GDP", "IPO", "API",
    "USD", "USDT", "USDC", "EUR", "UK", "US", "EU", "TV", "ATH", "ATL", "NFT",
    "DEX", "CEX", "DAO", "KYC", "AML", "FBI", "DOJ", "IRS", "CPI", "PPI", "FOMC",
}

EVENT_KEYWORDS = {
    "regulation": ["regulation", "regulatory", "law", "act", "senate", "sanction", "rules", "compliance"],
    "security": ["hack", "exploit", "attack", "stolen", "drain", "vulnerability", "freeze"],
    "institutional": ["etf", "institutional", "asset manager", "treasury", "investment", "fund"],
    "network": ["fork", "upgrade", "amendment", "network", "block", "validator"],
    "market": ["price", "revenue", "volume", "trading", "market", "investors", "inflows", "outflows"],
    "business": ["deal", "partnership", "sale", "acquisition", "revenue", "launch"],
}

POSITIVE_KEYWORDS = [
    "approval", "approved", "inflows", "buy", "buys", "growth", "surge",
    "bullish", "adoption", "investment", "institutional", "launch", "partnership",
    "record", "rises", "rally", "support"
]

NEGATIVE_KEYWORDS = [
    "hack", "exploit", "attack", "sanction", "ban", "delay", "collapse", "risk",
    "drain", "stolen", "lawsuit", "lawsuits", "crackdown", "falls", "drop",
    "warning", "controversial", "loss", "selling"
]

HIGH_IMPACT_KEYWORDS = [
    "hack", "exploit", "sanction", "etf", "senate", "law", "act", "fork",
    "approval", "lawsuit", "crackdown", "treasury", "billion", "million"
]


def normalize(text):
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def find_matches(text, mapping):
    matches = {}
    for name, keywords in mapping.items():
        found = [keyword for keyword in keywords if re.search(r"\b" + re.escape(keyword) + r"\b", text)]
        if found:
            matches[name] = found
    return matches


def get_dynamic_binance_spot_assets():
    """Return Binance spot-USDT base assets; fail safely if Binance is unavailable."""
    try:
        response = requests.get(BINANCE_EXCHANGE_INFO, timeout=15)
        response.raise_for_status()
        data = response.json()
        return {
            item.get("baseAsset", "").upper()
            for item in data.get("symbols", [])
            if item.get("status") == "TRADING"
            and item.get("isSpotTradingAllowed", True)
            and item.get("quoteAsset") == "USDT"
            and item.get("baseAsset")
        }
    except Exception:
        return set()


def find_dynamic_tickers(raw_text, available_assets):
    """Detect explicit ticker mentions such as WLD, ARKM or a future new ticker.

    Only uppercase ticker-like tokens are accepted and they must already be a
    live Binance spot-USDT base asset. This intentionally avoids treating normal
    lowercase words as coins.
    """
    if not available_assets:
        return {}
    tokens = set(re.findall(r"(?<![A-Za-z0-9])\$?([A-Z][A-Z0-9]{1,9})(?![A-Za-z0-9])", raw_text or ""))
    found = {}
    for token in tokens:
        token = token.upper()
        if token in DYNAMIC_TICKER_BLACKLIST:
            continue
        if token in available_assets:
            found[token] = [token.lower()]
    return found


def classify_news(article, dynamic_assets=None):
    raw_text = f"{article.get('title', '')} {article.get('description', '')}"
    text = normalize(raw_text)

    coin_matches = find_matches(text, COIN_KEYWORDS)
    dynamic_matches = find_dynamic_tickers(raw_text, dynamic_assets or set())
    for coin, keywords in dynamic_matches.items():
        coin_matches.setdefault(coin, []).extend(keywords)

    event_matches = find_matches(text, EVENT_KEYWORDS)

    positive_hits = [keyword for keyword in POSITIVE_KEYWORDS if re.search(r"\b" + re.escape(keyword) + r"\b", text)]
    negative_hits = [keyword for keyword in NEGATIVE_KEYWORDS if re.search(r"\b" + re.escape(keyword) + r"\b", text)]
    impact_hits = [keyword for keyword in HIGH_IMPACT_KEYWORDS if re.search(r"\b" + re.escape(keyword) + r"\b", text)]

    if negative_hits and len(negative_hits) > len(positive_hits):
        direction = "negative"
    elif positive_hits and len(positive_hits) > len(negative_hits):
        direction = "positive"
    else:
        direction = "neutral"

    if event_matches:
        event_type = max(event_matches, key=lambda key: len(event_matches[key]))
    else:
        event_type = "general"

    impact_score = min(100, 25 + len(impact_hits) * 15 + len(coin_matches) * 10)
    confidence_score = min(100, 40 + len(coin_matches) * 15 + len(event_matches) * 10 + abs(len(positive_hits) - len(negative_hits)) * 5)

    coins = ",".join(sorted(coin_matches.keys())) if coin_matches else "MARKET"
    matched_keywords = ",".join(sorted(set(positive_hits + negative_hits + impact_hits)))

    return {
        "news_id": article["id"],
        "coins": coins,
        "event_type": event_type,
        "direction": direction,
        "impact_score": impact_score,
        "confidence_score": confidence_score,
        "matched_keywords": matched_keywords,
    }


def analyze_all_news():
    create_news_analysis_table()
    articles = get_all_news()
    dynamic_assets = get_dynamic_binance_spot_assets()
    analyses = [classify_news(article, dynamic_assets) for article in articles]

    if analyses:
        save_news_analysis_batch(analyses)

    return analyses


def main():
    analyses = analyze_all_news()

    print("AG Crypto Analyst - News Intelligence Analyzer")
    print(f"Analiz edilen haber : {len(analyses)}")
    print("Coin tanıma: takip listesi aliasları + dinamik Binance spot-USDT ticker keşfi")

    for analysis in analyses[:20]:
        print(
            f"#{analysis['news_id']} | "
            f"{analysis['coins']} | "
            f"{analysis['event_type']} | "
            f"{analysis['direction']} | "
            f"impact={analysis['impact_score']} | "
            f"confidence={analysis['confidence_score']}"
        )


if __name__ == "__main__":
    main()

import re

from news_intelligence.news_repository import (
    create_news_analysis_table,
    get_all_news,
    save_news_analysis_batch,
)


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
    "HYPE": ["hyperliquid", "hype"],
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


def classify_news(article):
    text = normalize(f"{article.get('title', '')} {article.get('description', '')}")

    coin_matches = find_matches(text, COIN_KEYWORDS)
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
    analyses = [classify_news(article) for article in articles]

    if analyses:
        save_news_analysis_batch(analyses)

    return analyses


def main():
    analyses = analyze_all_news()

    print("AG Crypto Analyst - News Intelligence Analyzer")
    print(f"Analiz edilen haber : {len(analyses)}")

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

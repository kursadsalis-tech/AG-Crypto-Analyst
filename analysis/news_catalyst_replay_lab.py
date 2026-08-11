"""Conservative historical replay for independently sourced news catalysts.

This lab deliberately does not reuse the rejected price/technical rule family.
It audits the GDELT archive contract first, rebuilds a precision-first news
classification from titles, removes repeated stories, delays availability by
one hour, enters at the next hourly open and enforces a 24-hour symbol
cooldown. Outcomes include a fixed round-trip cost.

Candidate rules are fixed below. Selection is chronological LEARN ->
VALIDATION -> mechanically sealed EXAM with seven-day block confidence bounds.
The lab never changes a live policy; even a historical pass remains
SHADOW_ONLY and must earn fresh forward evidence behind the Risk Manager.
"""
from __future__ import annotations

import bisect
import csv
import html
import math
import os
import re
import sqlite3
import statistics
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse


DB_NAME = os.getenv("AGCA_DB", "crypto_data.db")
LAB_VERSION = "NEWS_CATALYST_REPLAY_V1"
HORIZONS = {"1h": 1, "4h": 4, "24h": 24}
ROUND_TRIP_COST_BPS = float(os.getenv("AGCA_CATALYST_COST_BPS", "10"))
AVAILABILITY_DELAY_MINUTES = int(
    os.getenv("AGCA_CATALYST_DELAY_MINUTES", "60")
)
COOLDOWN_HOURS = 24
BLOCK_DAYS = 7
DETAIL_PATH = Path("reports/news_catalyst_replay_lab.csv")
SELECTION_PATH = Path("reports/news_catalyst_selection.csv")
AUDIT_PATH = Path("reports/news_catalyst_data_audit.csv")

MIN_RAW_ARTICLES = 300
MIN_SPAN_DAYS = 270.0
MIN_VALID_TIME_RATIO = 0.98
MIN_EXACT_TIME_RATIO = 0.90
MIN_TITLE_QUALITY_RATIO = 0.90
MIN_UNIQUE_STORY_RATIO = 0.75
MIN_SOURCES = 2
MIN_EVALUATED_EVENTS = 150
MIN_EVALUATED_COINS = 3
MIN_PRICE_MATCH_RATIO = 0.60

MIN_LEARN_EVENTS = 80
MIN_VALIDATION_EVENTS = 30
MIN_EXAM_EVENTS = 30
MIN_LEARN_BLOCKS = 16
MIN_VALIDATION_BLOCKS = 6
MIN_EXAM_BLOCKS = 6
SHORTLIST_SIZE = 3
LEARN_Z = 3.20
VALIDATION_Z = 2.40
EXAM_Z = 1.96


# Phrase aliases are intentionally precision-first. Ambiguous short names such
# as link, bio, ada, sol, sui and uma are accepted only as uppercase tickers.
COIN_PHRASES = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "ether", "eth"),
    "XRP": ("ripple", "xrp"),
    "SOL": ("solana",),
    "BNB": ("binance coin", "bnb"),
    "ADA": ("cardano",),
    "DOGE": ("dogecoin", "doge"),
    "TRX": ("tron", "trx"),
    "LINK": ("chainlink",),
    "AVAX": ("avalanche", "avax"),
    "SUI": ("sui blockchain", "sui network"),
    "HYPE": ("hyperliquid",),
    "CRO": ("crypto.com", "cronos"),
    "ABTC": ("american bitcoin",),
    "ARKM": ("arkham",),
    "FIDA": ("bonfida",),
    "WLD": ("worldcoin", "world coin"),
    "TIA": ("celestia",),
    "ETHFI": ("ether.fi", "etherfi"),
    "BIO": ("bio protocol",),
    "LQTY": ("liquity",),
    "ILV": ("illuvium",),
    "SLP": ("smooth love potion",),
    "UMA": ("uma protocol",),
    "IO": ("io.net", "ionet", "io token"),
}

EVENT_KEYWORDS = {
    "regulation": (
        "regulation", "regulatory", "law", "act", "senate", "sanction",
        "rules", "compliance",
    ),
    "security": (
        "hack", "exploit", "attack", "stolen", "drain",
        "vulnerability", "freeze",
    ),
    "institutional": (
        "etf", "institutional", "asset manager", "treasury",
        "investment", "fund",
    ),
    "network": (
        "fork", "upgrade", "amendment", "network", "block", "validator",
    ),
    "market": (
        "price", "revenue", "volume", "trading", "market", "investors",
        "inflows", "outflows",
    ),
    "business": (
        "deal", "partnership", "sale", "acquisition", "revenue", "launch",
    ),
}

POSITIVE_KEYWORDS = (
    "approval", "approved", "inflows", "buy", "buys", "growth", "surge",
    "bullish", "adoption", "investment", "institutional", "launch",
    "partnership", "record", "rises", "rally", "support",
)
NEGATIVE_KEYWORDS = (
    "hack", "exploit", "attack", "sanction", "ban", "delay", "collapse",
    "risk", "drain", "stolen", "lawsuit", "lawsuits", "crackdown",
    "falls", "drop", "warning", "controversial", "loss", "selling",
)
HIGH_IMPACT_KEYWORDS = (
    "hack", "exploit", "sanction", "etf", "senate", "law", "act", "fork",
    "approval", "lawsuit", "crackdown", "treasury", "billion", "million",
)


FILTER_FAMILIES = (
    ("BROAD", 55, 25, "ALL", ()),
    ("HIGH_CONF", 70, 25, "ALL", ()),
    ("HIGH_IMPACT", 55, 50, "ALL", ()),
    ("STRONG", 70, 50, "ALL", ()),
    ("VERY_STRONG", 85, 50, "ALL", ()),
    ("NEGATIVE_HIGH_CONF", 70, 25, "negative", ()),
    ("POSITIVE_HIGH_CONF", 70, 25, "positive", ()),
    (
        "SYSTEMIC_NEGATIVE",
        70,
        50,
        "negative",
        ("security", "regulation"),
    ),
)


def fixed_configs():
    configs = []
    for orientation in ("FOLLOW", "CONTRARIAN"):
        for name, confidence, impact, direction, event_types in FILTER_FAMILIES:
            configs.append({
                "name": f"{orientation}|{name}",
                "orientation": orientation,
                "family": name,
                "confidence_min": confidence,
                "impact_min": impact,
                "direction": direction,
                "event_types": event_types,
            })
    return tuple(configs)


CONFIGS = fixed_configs()
BASE_CONFIG_NAME = "FOLLOW|NEGATIVE_HIGH_CONF"


def parse_time(value):
    text = str(value or "").strip()
    if re.fullmatch(r"\d{14}", text):
        result = datetime.strptime(text, "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc
        )
    else:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def phrase_match(text, phrase):
    return bool(re.search(
        rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])",
        text,
    ))


def strict_coins(title):
    raw = html.unescape(title or "")
    lowered = unicodedata.normalize("NFKC", raw).lower()
    coins = set()
    for coin, phrases in COIN_PHRASES.items():
        if any(phrase_match(lowered, phrase) for phrase in phrases):
            coins.add(coin)
            continue
        if re.search(
            rf"(?<![A-Za-z0-9])\$?{re.escape(coin)}(?![A-Za-z0-9])",
            raw,
        ):
            coins.add(coin)
    return tuple(sorted(coins))


def keyword_hits(text, keywords):
    return [word for word in keywords if phrase_match(text, word)]


def classify_title(title):
    coins = strict_coins(title)
    text = unicodedata.normalize("NFKC", html.unescape(title or "")).lower()
    positive = keyword_hits(text, POSITIVE_KEYWORDS)
    negative = keyword_hits(text, NEGATIVE_KEYWORDS)
    high_impact = keyword_hits(text, HIGH_IMPACT_KEYWORDS)
    event_matches = {
        event: keyword_hits(text, words)
        for event, words in EVENT_KEYWORDS.items()
    }
    event_matches = {
        event: matches
        for event, matches in event_matches.items()
        if matches
    }
    if negative and len(negative) > len(positive):
        direction = "negative"
    elif positive and len(positive) > len(negative):
        direction = "positive"
    else:
        direction = "neutral"
    event_type = (
        max(event_matches, key=lambda key: len(event_matches[key]))
        if event_matches
        else "general"
    )
    impact = min(100, 25 + len(high_impact) * 15 + len(coins) * 10)
    confidence = min(
        100,
        40
        + len(coins) * 15
        + len(event_matches) * 10
        + abs(len(positive) - len(negative)) * 5,
    )
    return {
        "coins": coins,
        "direction": direction,
        "event_type": event_type,
        "impact": impact,
        "confidence": confidence,
    }


def archive_day(source_file):
    match = re.search(r"(\d{8})\.gkg\.csv", source_file or "", re.I)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def is_approximate_noon(timestamp, source_file):
    day = archive_day(source_file)
    return bool(
        day == timestamp.date()
        and timestamp.hour == 12
        and timestamp.minute == 0
        and timestamp.second == 0
        and timestamp.microsecond == 0
    )


def normalize_title(value):
    text = unicodedata.normalize("NFKC", html.unescape(value or "")).lower()
    text = re.sub(r"\b(?:coindesk|cointelegraph|the block)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def canonical_url(value):
    parsed = urlparse(value or "")
    host = parsed.netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    path = unquote(parsed.path or "").lower().rstrip("/")
    path = re.sub(r"/(?:amp)$", "", path)
    path = re.sub(r"\.(?:html?|amp)$", "", path)
    return f"{host}{path}" if host and path else (value or "").strip()


def title_is_usable(title):
    normalized = normalize_title(title)
    return len(normalized) >= 20 and len(normalized.split()) >= 4


def validate_schema(con):
    required = {
        "id", "published_at", "source", "title", "url", "source_file",
    }
    columns = {
        row[1] for row in con.execute(
            "PRAGMA table_info(historical_news_raw)"
        )
    }
    missing = required - columns
    if missing:
        raise RuntimeError(
            "historical_news_raw schema missing: " + ", ".join(sorted(missing))
        )


def load_articles(con):
    validate_schema(con)
    rows = con.execute("""
        SELECT id, published_at, source, title, url, source_file
        FROM historical_news_raw
        ORDER BY published_at ASC, id ASC
    """).fetchall()
    articles = []
    invalid_time = 0
    approximate_time = 0
    usable_titles = 0
    for row in rows:
        try:
            timestamp = parse_time(row[1])
        except (TypeError, ValueError):
            invalid_time += 1
            continue
        approximate = is_approximate_noon(timestamp, row[5])
        approximate_time += int(approximate)
        usable_titles += int(title_is_usable(row[3]))
        classification = classify_title(row[3])
        articles.append({
            "news_id": int(row[0]),
            "event_time": timestamp,
            "source": str(row[2] or "").lower(),
            "title": str(row[3] or ""),
            "url": str(row[4] or ""),
            "source_file": str(row[5] or ""),
            "approximate_time": approximate,
            **classification,
        })
    return {
        "raw_count": len(rows),
        "articles": articles,
        "invalid_time": invalid_time,
        "approximate_time": approximate_time,
        "usable_titles": usable_titles,
    }


def deduplicate_directional_articles(articles):
    eligible = [
        row for row in articles
        if not row["approximate_time"]
        and title_is_usable(row["title"])
        and row["coins"]
        and row["direction"] in {"positive", "negative"}
    ]
    eligible.sort(key=lambda row: (row["event_time"], row["news_id"]))
    urls = set()
    recent_titles = {}
    kept = []
    duplicate_urls = 0
    duplicate_titles = 0
    for row in eligible:
        url_key = canonical_url(row["url"])
        title_key = normalize_title(row["title"])
        last_title_time = recent_titles.get(title_key)
        if url_key and url_key in urls:
            duplicate_urls += 1
            continue
        if (
            last_title_time is not None
            and row["event_time"] - last_title_time <= timedelta(hours=72)
        ):
            duplicate_titles += 1
            continue
        if url_key:
            urls.add(url_key)
        recent_titles[title_key] = row["event_time"]
        kept.append(row)
    return {
        "eligible": eligible,
        "kept": kept,
        "duplicate_urls": duplicate_urls,
        "duplicate_titles": duplicate_titles,
    }


def stored_symbol_map(con):
    symbols = {
        str(row[0]).upper()
        for row in con.execute("""
            SELECT DISTINCT symbol
            FROM market_klines
            WHERE interval='1h'
        """)
    }
    return {
        coin: f"{coin}USDT"
        for coin in COIN_PHRASES
        if f"{coin}USDT" in symbols
    }


def load_candle_series(con, symbols):
    output = {}
    for symbol in sorted(set(symbols)):
        rows = con.execute("""
            SELECT open_time, close_time, open, close
            FROM market_klines
            WHERE symbol=? AND interval='1h'
            ORDER BY open_time ASC
        """, (symbol,)).fetchall()
        candles = []
        for row in rows:
            try:
                candle = {
                    "open_time": parse_time(row[0]),
                    "close_time": parse_time(row[1]),
                    "open": float(row[2]),
                    "close": float(row[3]),
                }
            except (TypeError, ValueError):
                continue
            if candle["open"] > 0 and candle["close"] > 0:
                candles.append(candle)
        output[symbol] = {
            "candles": candles,
            "open_times": [row["open_time"] for row in candles],
        }
    return output


def require_hourly_continuity(candles, start, end):
    if start < 0 or end >= len(candles):
        raise ValueError("candle window outside stored history")
    for index in range(start + 1, end + 1):
        gap = (
            candles[index]["open_time"] - candles[index - 1]["open_time"]
        ).total_seconds()
        if not 3599.0 <= gap <= 3601.0:
            raise ValueError("non-contiguous hourly candles")


def evaluate_article_coin(article, coin, symbol, series_item):
    availability = article["event_time"] + timedelta(
        minutes=AVAILABILITY_DELAY_MINUTES
    )
    entry_index = bisect.bisect_left(
        series_item["open_times"], availability
    )
    candles = series_item["candles"]
    final_index = entry_index + max(HORIZONS.values()) - 1
    if entry_index >= len(candles) or final_index >= len(candles):
        return None
    require_hourly_continuity(candles, entry_index, final_index)
    entry = candles[entry_index]
    output = {
        "news_id": article["news_id"],
        "event_time": article["event_time"],
        "availability_time": availability,
        "entry_time": entry["open_time"],
        "source": article["source"],
        "title": article["title"],
        "url": article["url"],
        "coin": coin,
        "symbol": symbol,
        "direction": article["direction"],
        "event_type": article["event_type"],
        "impact": article["impact"],
        "confidence": article["confidence"],
        "entry_price": entry["open"],
    }
    for horizon, hours in HORIZONS.items():
        exit_candle = candles[entry_index + hours - 1]
        market_return = (exit_candle["close"] / entry["open"] - 1.0) * 100.0
        output[horizon] = {
            "exit_time": exit_candle["close_time"],
            "exit_price": exit_candle["close"],
            "market_return_pct": market_return,
        }
    return output


def build_observations(con, articles):
    symbol_map = stored_symbol_map(con)
    needed = {
        symbol_map[coin]
        for article in articles
        for coin in article["coins"]
        if coin in symbol_map
    }
    series = load_candle_series(con, needed)
    observations = []
    attempted = 0
    continuity_gaps = 0
    missing_symbol = 0
    for article in articles:
        for coin in article["coins"]:
            attempted += 1
            symbol = symbol_map.get(coin)
            if not symbol:
                missing_symbol += 1
                continue
            try:
                observation = evaluate_article_coin(
                    article, coin, symbol, series[symbol]
                )
            except ValueError:
                continuity_gaps += 1
                continue
            if observation:
                observations.append(observation)
    observations.sort(key=lambda row: (row["entry_time"], row["symbol"]))
    return {
        "observations": observations,
        "attempted": attempted,
        "missing_symbol": missing_symbol,
        "continuity_gaps": continuity_gaps,
        "stored_symbols": len(symbol_map),
    }


def assign_splits(observations):
    if not observations:
        return None
    start = min(row["entry_time"] for row in observations)
    end = max(row["entry_time"] for row in observations)
    span = end - start
    boundaries = {
        "start": start,
        "learn_end": start + span * 0.60,
        "validation_end": start + span * 0.80,
        "end": end,
    }
    for row in observations:
        if row["entry_time"] < boundaries["learn_end"]:
            row["split"] = "LEARN"
        elif row["entry_time"] < boundaries["validation_end"]:
            row["split"] = "VALIDATION"
        else:
            row["split"] = "EXAM"
    return boundaries


def config_matches(observation, config):
    if observation["confidence"] < config["confidence_min"]:
        return False
    if observation["impact"] < config["impact_min"]:
        return False
    if (
        config["direction"] != "ALL"
        and observation["direction"] != config["direction"]
    ):
        return False
    if (
        config["event_types"]
        and observation["event_type"] not in config["event_types"]
    ):
        return False
    return True


def action_for(observation, orientation):
    action = "AL" if observation["direction"] == "positive" else "SAT"
    if orientation == "CONTRARIAN":
        return "SAT" if action == "AL" else "AL"
    return action


def event_from_observation(observation, config):
    action = action_for(observation, config["orientation"])
    cost = ROUND_TRIP_COST_BPS / 100.0
    event = {
        "lab_version": LAB_VERSION,
        "config": config["name"],
        "family": config["family"],
        "orientation": config["orientation"],
        "split": observation["split"],
        "news_id": observation["news_id"],
        "event_time": iso(observation["event_time"]),
        "entry_time": iso(observation["entry_time"]),
        "entry_timestamp": observation["entry_time"].timestamp(),
        "source": observation["source"],
        "coin": observation["coin"],
        "symbol": observation["symbol"],
        "news_direction": observation["direction"],
        "action": action,
        "event_type": observation["event_type"],
        "impact": observation["impact"],
        "confidence": observation["confidence"],
    }
    for horizon in HORIZONS:
        market_return = observation[horizon]["market_return_pct"]
        gross = market_return if action == "AL" else -market_return
        event[horizon] = {
            "gross": gross,
            "net": gross - cost,
            "cost": cost,
        }
    return event


def events_for_config(observations, config):
    events = []
    next_allowed = {}
    for observation in observations:
        if not config_matches(observation, config):
            continue
        symbol = observation["symbol"]
        if observation["entry_time"] < next_allowed.get(
            symbol, datetime.min.replace(tzinfo=timezone.utc)
        ):
            continue
        event = event_from_observation(observation, config)
        events.append(event)
        next_allowed[symbol] = observation["24h"]["exit_time"]
    return events


def block_key(event):
    return int(
        event["entry_timestamp"] // (BLOCK_DAYS * 24 * 60 * 60)
    )


def wilson_lower(hits, count, z=1.96):
    if count <= 0:
        return 0.0
    proportion = hits / count
    denominator = 1.0 + z * z / count
    centre = proportion + z * z / (2.0 * count)
    margin = z * math.sqrt(
        (
            proportion * (1.0 - proportion)
            + z * z / (4.0 * count)
        ) / count
    )
    return max(0.0, (centre - margin) / denominator) * 100.0


def metric(events, config, split, horizon, z):
    selected = [row for row in events if row["split"] == split]
    values = [row[horizon]["net"] for row in selected]
    by_block = {}
    for event, value in zip(selected, values):
        by_block.setdefault(block_key(event), []).append(value)
    block_means = [
        statistics.fmean(by_block[key]) for key in sorted(by_block)
    ]
    block_average = statistics.fmean(block_means) if block_means else 0.0
    block_deviation = (
        statistics.stdev(block_means) if len(block_means) > 1 else 0.0
    )
    standard_error = (
        block_deviation / math.sqrt(len(block_means))
        if block_means else 0.0
    )
    lower_bound = (
        block_average - z * standard_error if block_means else 0.0
    )
    hits = sum(value > 0 for value in values)
    positives = sum(value for value in values if value > 0)
    negatives = abs(sum(value for value in values if value < 0))
    running = peak = 0.0
    sequence_drawdown = 0.0
    for value in values:
        running += value
        peak = max(peak, running)
        sequence_drawdown = min(sequence_drawdown, running - peak)
    return {
        "lab_version": LAB_VERSION,
        "config": config["name"],
        "family": config["family"],
        "orientation": config["orientation"],
        "split": split,
        "horizon": horizon,
        "events": len(values),
        "time_blocks": len(block_means),
        "hits": hits,
        "hit_rate_pct": hits / len(values) * 100.0 if values else 0.0,
        "wilson_lower_pct": wilson_lower(hits, len(values), z),
        "average_net_return_pct": (
            statistics.fmean(values) if values else 0.0
        ),
        "median_net_return_pct": (
            statistics.median(values) if values else 0.0
        ),
        "block_mean_return_pct": block_average,
        "block_mean_lower_bound_pct": lower_bound,
        "profit_factor": (
            positives / negatives if negatives else
            (float("inf") if positives else 0.0)
        ),
        "sequence_drawdown_pct_points": sequence_drawdown,
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "availability_delay_minutes": AVAILABILITY_DELAY_MINUTES,
        "cooldown_hours": COOLDOWN_HOURS,
    }


def eligible(row, minimum_events, minimum_blocks):
    return (
        row["events"] >= minimum_events
        and row["time_blocks"] >= minimum_blocks
        and row["block_mean_lower_bound_pct"] > 0.0
    )


def select_horizon(events_by_config, horizon, configs=CONFIGS):
    by_name = {config["name"]: config for config in configs}
    detail = []
    learn_rows = []
    for config in configs:
        row = metric(
            events_by_config[config["name"]],
            config,
            "LEARN",
            horizon,
            LEARN_Z,
        )
        detail.append(row)
        if eligible(row, MIN_LEARN_EVENTS, MIN_LEARN_BLOCKS):
            learn_rows.append(row)
    learn_rows.sort(
        key=lambda row: (
            row["block_mean_lower_bound_pct"], row["events"]
        ),
        reverse=True,
    )

    validation_pairs = []
    for learn in learn_rows[:SHORTLIST_SIZE]:
        config = by_name[learn["config"]]
        row = metric(
            events_by_config[config["name"]],
            config,
            "VALIDATION",
            horizon,
            VALIDATION_Z,
        )
        detail.append(row)
        if eligible(row, MIN_VALIDATION_EVENTS, MIN_VALIDATION_BLOCKS):
            validation_pairs.append((learn, row))

    if not learn_rows:
        return {
            "horizon": horizon,
            "status": "NO_LEARN_EDGE",
            "learn_qualified": 0,
            "validation_qualified": 0,
            "selected_config": None,
            "learn": None,
            "validation": None,
            "exam": None,
            "detail": detail,
        }
    if not validation_pairs:
        return {
            "horizon": horizon,
            "status": "NO_VALIDATED_CATALYST",
            "learn_qualified": len(learn_rows),
            "validation_qualified": 0,
            "selected_config": None,
            "learn": None,
            "validation": None,
            "exam": None,
            "detail": detail,
        }
    learn, validation = max(
        validation_pairs,
        key=lambda pair: (
            min(
                pair[0]["block_mean_lower_bound_pct"],
                pair[1]["block_mean_lower_bound_pct"],
            ),
            pair[1]["block_mean_lower_bound_pct"],
            pair[1]["events"],
        ),
    )
    config = by_name[learn["config"]]

    # EXAM is evaluated only after the LEARN/VALIDATION winner is frozen.
    exam = metric(
        events_by_config[config["name"]],
        config,
        "EXAM",
        horizon,
        EXAM_Z,
    )
    detail.append(exam)
    if exam["events"] < MIN_EXAM_EVENTS or exam["time_blocks"] < MIN_EXAM_BLOCKS:
        status = "EXAM_INSUFFICIENT"
    elif exam["block_mean_lower_bound_pct"] > 0.0:
        status = "HISTORICAL_CATALYST_PASS"
    elif exam["average_net_return_pct"] > 0.0:
        status = "EXAM_INCONCLUSIVE"
    else:
        status = "EXAM_FAIL"
    return {
        "horizon": horizon,
        "status": status,
        "learn_qualified": len(learn_rows),
        "validation_qualified": len(validation_pairs),
        "selected_config": config,
        "learn": learn,
        "validation": validation,
        "exam": exam,
        "detail": detail,
    }


def audit_row(check, value, threshold, passed, detail=""):
    return {
        "lab_version": LAB_VERSION,
        "check": check,
        "value": value,
        "threshold": threshold,
        "passed": int(bool(passed)),
        "detail": detail,
    }


def build_audit(load_result, dedup_result, observation_result):
    raw = load_result["raw_count"]
    valid = len(load_result["articles"])
    exact = valid - load_result["approximate_time"]
    eligible = len(dedup_result["eligible"])
    kept = len(dedup_result["kept"])
    observations = observation_result["observations"]
    valid_ratio = valid / raw if raw else 0.0
    exact_ratio = exact / valid if valid else 0.0
    title_ratio = load_result["usable_titles"] / valid if valid else 0.0
    unique_ratio = kept / eligible if eligible else 0.0
    price_ratio = (
        len(observations) / observation_result["attempted"]
        if observation_result["attempted"] else 0.0
    )
    sources = {row["source"] for row in dedup_result["kept"]}
    coins = {row["coin"] for row in observations}
    if dedup_result["kept"]:
        span_days = (
            dedup_result["kept"][-1]["event_time"]
            - dedup_result["kept"][0]["event_time"]
        ).total_seconds() / 86400.0
    else:
        span_days = 0.0
    rows = [
        audit_row("raw_articles", raw, f">={MIN_RAW_ARTICLES}", raw >= MIN_RAW_ARTICLES),
        audit_row("valid_timestamp_ratio", valid_ratio, f">={MIN_VALID_TIME_RATIO:.2f}", valid_ratio >= MIN_VALID_TIME_RATIO),
        audit_row("exact_timestamp_ratio", exact_ratio, f">={MIN_EXACT_TIME_RATIO:.2f}", exact_ratio >= MIN_EXACT_TIME_RATIO, "day-noon fallback timestamps are excluded"),
        audit_row("usable_title_ratio", title_ratio, f">={MIN_TITLE_QUALITY_RATIO:.2f}", title_ratio >= MIN_TITLE_QUALITY_RATIO),
        audit_row("directional_strict_articles", eligible, f">={MIN_EVALUATED_EVENTS}", eligible >= MIN_EVALUATED_EVENTS, "strict coin + directional title before deduplication"),
        audit_row("unique_story_ratio", unique_ratio, f">={MIN_UNIQUE_STORY_RATIO:.2f}", unique_ratio >= MIN_UNIQUE_STORY_RATIO, f"url_duplicates={dedup_result['duplicate_urls']}; title_duplicates={dedup_result['duplicate_titles']}"),
        audit_row("usable_span_days", span_days, f">={MIN_SPAN_DAYS:.0f}", span_days >= MIN_SPAN_DAYS),
        audit_row("source_count", len(sources), f">={MIN_SOURCES}", len(sources) >= MIN_SOURCES, ",".join(sorted(sources))),
        audit_row("price_match_ratio", price_ratio, f">={MIN_PRICE_MATCH_RATIO:.2f}", price_ratio >= MIN_PRICE_MATCH_RATIO, f"attempted={observation_result['attempted']}; missing_symbol={observation_result['missing_symbol']}; gaps={observation_result['continuity_gaps']}"),
        audit_row("evaluated_events", len(observations), f">={MIN_EVALUATED_EVENTS}", len(observations) >= MIN_EVALUATED_EVENTS),
        audit_row("evaluated_coins", len(coins), f">={MIN_EVALUATED_COINS}", len(coins) >= MIN_EVALUATED_COINS, ",".join(sorted(coins))),
    ]
    return rows


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def selection_row(result):
    row = {
        "lab_version": LAB_VERSION,
        "horizon": result["horizon"],
        "status": result["status"],
        "learn_qualified": result["learn_qualified"],
        "validation_qualified": result["validation_qualified"],
        "selected_config": (
            result["selected_config"]["name"]
            if result["selected_config"] else ""
        ),
    }
    for split in ("learn", "validation", "exam"):
        value = result[split]
        row[f"{split}_events"] = value["events"] if value else 0
        row[f"{split}_time_blocks"] = value["time_blocks"] if value else 0
        row[f"{split}_average_net_return_pct"] = (
            value["average_net_return_pct"] if value else 0.0
        )
        row[f"{split}_block_mean_lower_bound_pct"] = (
            value["block_mean_lower_bound_pct"] if value else 0.0
        )
    return row


def short_metric(row):
    return (
        f"n={row['events']} | blocks={row['time_blocks']} | "
        f"hit={row['hit_rate_pct']:.1f}% | "
        f"net={row['average_net_return_pct']:+.3f}% | "
        f"block_LB={row['block_mean_lower_bound_pct']:+.3f}%"
    )


def render_audit(audit):
    print("DATA QUALITY GATE")
    for row in audit:
        state = "PASS" if row["passed"] else "BLOCK"
        value = row["value"]
        if isinstance(value, float):
            value = f"{value:.3f}"
        print(
            f"{state:<5} | {row['check']:<28} | "
            f"value={value} | {row['threshold']}"
        )


def render_results(results, events_by_config):
    counts = [len(rows) for rows in events_by_config.values()]
    print()
    print("AG Crypto Analyst - News Catalyst Replay Lab v1.0.0")
    print(
        f"Policy family: fixed={len(CONFIGS)}/horizon | "
        f"delay={AVAILABILITY_DELAY_MINUTES}m | cooldown={COOLDOWN_HOURS}h | "
        f"cost={ROUND_TRIP_COST_BPS:.1f}bps"
    )
    print(
        f"Events/config after cooldown: {min(counts)}..{max(counts)} | "
        "strict aliases + story deduplication"
    )
    print(
        "Discipline: LEARN z=3.20 -> top 3 -> VALIDATION z=2.40 "
        "-> frozen winner -> EXAM z=1.96"
    )
    print(
        "Technical direction is not used. Any future product use remains "
        "behind the live Risk Manager."
    )
    for result in results:
        print(
            f"\n{result['horizon'].upper()} | "
            f"qualified learn={result['learn_qualified']} "
            f"validation={result['validation_qualified']}"
        )
        if result["selected_config"] is None:
            print(f"result: {result['status']}")
            continue
        print(f"winner: {result['selected_config']['name']}")
        print(f"LEARN      {short_metric(result['learn'])}")
        print(f"VALIDATION {short_metric(result['validation'])}")
        print(f"EXAM       {short_metric(result['exam'])}")
        print(f"result: {result['status']}")
    passes = sum(
        result["status"] == "HISTORICAL_CATALYST_PASS"
        for result in results
    )
    conclusion = (
        f"HISTORICAL_NEWS_CATALYST_CANDIDATE ({passes}/{len(results)})"
        if passes else "NO_CONFIRMED_NEWS_CATALYST_EDGE"
    )
    print(f"\nCONCLUSION: {conclusion} | product_state=SHADOW_ONLY")
    print(
        "No live threshold or policy changed. Historical success would be "
        "corroboration, not pristine proof, and still requires fresh forward evidence."
    )
    print(f"Audit: {AUDIT_PATH}")
    print(f"Details: {DETAIL_PATH}")
    print(f"Selection: {SELECTION_PATH}")


def synthetic_event(split, block, value, config_name, horizon="1h"):
    timestamp = block * BLOCK_DAYS * 24 * 60 * 60 + 3600
    event = {
        "split": split,
        "entry_timestamp": float(timestamp),
        "entry_time": iso(datetime.fromtimestamp(timestamp, timezone.utc)),
    }
    for label in HORIZONS:
        event[label] = {"net": value if label == horizon else value}
    return event


def run_self_tests():
    assert "LINK" not in strict_coins("Read the link in this market bio")
    assert "BIO" not in strict_coins("Read the link in this market bio")
    assert strict_coins("Chainlink and $BIO rally after Bio Protocol launch") == (
        "BIO", "LINK",
    )

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    candles = []
    for hour in range(30):
        opened = start + timedelta(hours=hour)
        candles.append({
            "open_time": opened,
            "close_time": opened + timedelta(hours=1) - timedelta(milliseconds=1),
            "open": 100.0,
            "close": 101.0,
        })
    article = {
        "news_id": 1,
        "event_time": start + timedelta(minutes=30),
        "source": "coindesk.com",
        "title": "Bitcoin rally gains institutional support",
        "url": "https://coindesk.com/a",
        "direction": "positive",
        "event_type": "institutional",
        "impact": 50,
        "confidence": 70,
    }
    observation = evaluate_article_coin(
        article,
        "BTC",
        "BTCUSDT",
        {"candles": candles, "open_times": [x["open_time"] for x in candles]},
    )
    assert observation["entry_time"] == start + timedelta(hours=2)
    observation["split"] = "LEARN"
    follow = next(config for config in CONFIGS if config["name"] == "FOLLOW|STRONG")
    contrary = next(config for config in CONFIGS if config["name"] == "CONTRARIAN|STRONG")
    follow_event = event_from_observation(observation, follow)
    contrary_event = event_from_observation(observation, contrary)
    assert abs(follow_event["1h"]["net"] - 0.9) < 1e-9
    assert abs(contrary_event["1h"]["net"] + 1.1) < 1e-9

    repeated = [dict(observation), dict(observation)]
    repeated[1]["entry_time"] += timedelta(hours=1)
    repeated[1]["24h"] = dict(repeated[1]["24h"])
    repeated[1]["24h"]["exit_time"] += timedelta(hours=1)
    assert len(events_for_config(repeated, follow)) == 1

    configs = CONFIGS[:2]
    events = {}
    for index, config in enumerate(configs):
        rows = []
        learn_value = 0.40 if index == 0 else -0.10
        validation_value = 0.30 if index == 0 else -0.10
        for block in range(20):
            for _ in range(5):
                rows.append(synthetic_event("LEARN", block, learn_value, config["name"]))
        for block in range(20, 28):
            for _ in range(5):
                rows.append(synthetic_event("VALIDATION", block, validation_value, config["name"]))
        for block in range(28, 36):
            for _ in range(5):
                rows.append(synthetic_event("EXAM", block, 0.20, config["name"]))
        events[config["name"]] = rows
    first = select_horizon(events, "1h", configs=configs)
    mutated = {
        name: [dict(row) for row in rows]
        for name, rows in events.items()
    }
    for rows in mutated.values():
        for row in rows:
            if row["split"] == "EXAM":
                row["1h"] = {"net": -999.0}
    second = select_horizon(mutated, "1h", configs=configs)
    assert first["selected_config"]["name"] == second["selected_config"]["name"]


def main():
    con = sqlite3.connect(DB_NAME)
    try:
        load_result = load_articles(con)
        dedup_result = deduplicate_directional_articles(
            load_result["articles"]
        )
        observation_result = build_observations(
            con, dedup_result["kept"]
        )
        audit = build_audit(load_result, dedup_result, observation_result)
        write_csv(AUDIT_PATH, audit)
        render_audit(audit)
        blockers = [row for row in audit if not row["passed"]]
        if blockers:
            print()
            print(
                "CONCLUSION: DATA_QUALITY_BLOCKED | product_state=SHADOW_ONLY"
            )
            print(
                "No candidate selection or EXAM evaluation was performed. "
                "The archive must pass every pre-registered quality gate first."
            )
            print(f"Audit: {AUDIT_PATH}")
            write_csv(SELECTION_PATH, [{
                "lab_version": LAB_VERSION,
                "horizon": "ALL",
                "status": "DATA_QUALITY_BLOCKED",
                "failed_checks": ",".join(row["check"] for row in blockers),
            }])
            write_csv(DETAIL_PATH, [])
            return

        observations = observation_result["observations"]
        boundaries = assign_splits(observations)
        if not boundaries:
            raise RuntimeError("no price-matched catalyst observations")
        events_by_config = {
            config["name"]: events_for_config(observations, config)
            for config in CONFIGS
        }
        results = [
            select_horizon(events_by_config, horizon)
            for horizon in HORIZONS
        ]

        detail = [row for result in results for row in result["detail"]]
        # The frozen historical NEWS BASE is reported for context only. It is
        # never injected into candidate selection after the fact.
        base = next(config for config in CONFIGS if config["name"] == BASE_CONFIG_NAME)
        for horizon in HORIZONS:
            for split, z in (
                ("LEARN", LEARN_Z),
                ("VALIDATION", VALIDATION_Z),
                ("EXAM", EXAM_Z),
            ):
                row = metric(
                    events_by_config[base["name"]], base, split, horizon, z
                )
                row["report_only_baseline"] = 1
                detail.append(row)
        write_csv(DETAIL_PATH, detail)
        write_csv(SELECTION_PATH, [selection_row(row) for row in results])
        render_results(results, events_by_config)
    finally:
        con.close()


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        run_self_tests()
        print("News Catalyst Replay self-test: SUCCESS")
    else:
        main()

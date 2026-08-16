"""Audit exact-time Binance listing/delisting announcement history.

This is a source feasibility tool, not a backtest and not a forecast model. It
never reads future price outcomes. It accepts only first-party Binance article
metadata with an explicit releaseDate and preserves every raw timestamp/code
needed for a later point-in-time availability contract.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "EXACT_TIME_CATALYST_FEASIBILITY_V1_3"
BASE_URL = "https://www.binance.com"
LIST_URL = BASE_URL + "/bapi/apex/v1/public/apex/cms/article/list/query"
DETAIL_URL = BASE_URL + "/bapi/composite/v1/public/cms/article/detail/query"
CATEGORIES = {
    48: "NEW_CRYPTOCURRENCY_LISTING",
    161: "DELISTING",
}
# Binance rejects oversized announcement queries with HTTP 400.  The current
# web client uses small pages; keep this fixed and paginate deterministically.
PAGE_SIZE = 20
MAX_PAGES = 100
TIMEOUT = 25
RECENT_AUDIT_DAYS = 1095
ORDER_SAFETY_DAYS = 30
OUTPUT_PATH = Path("reports/exact_time_catalyst_feasibility.csv")
AUDIT_PATH = Path("reports/exact_time_catalyst_audit.json")

STRICT_LISTING = re.compile(r"\bbinance\s+will\s+list\b", re.I)
STRICT_DELISTING = re.compile(r"\bbinance\s+will\s+delist\b", re.I)
PAIR_REMOVAL = re.compile(r"\bremoval\s+of\s+spot\s+trading\s+pairs\b", re.I)
PAIR_PATTERN = re.compile(
    r"\b([A-Z0-9]{2,15})/(?:USDT|USDC|FDUSD|BUSD|BTC|ETH|BNB|TRY|EUR|BRL)\b"
)
PAREN_PATTERN = re.compile(r"\(([A-Z0-9]{2,15})\)")
SYMBOL_PATTERN = re.compile(
    r"\b([A-Z0-9]{2,15})(?:USDT|USDC|FDUSD|BUSD)\b"
)
TICKER_STOPWORDS = {
    "API", "APR", "BEP2", "BEP20", "BTC", "BUSD", "CEO", "DEX", "ETF",
    "ETH", "EUR", "FDUSD", "GBP", "KYC", "NFT", "OTC", "P2P", "TRY",
    "USD", "USDC", "USDT", "VIP",
}


def utcnow():
    return datetime.now(timezone.utc)


def parse_release_date(value):
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        result = datetime.fromtimestamp(number, timezone.utc)
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        if text.isdigit():
            return parse_release_date(int(text))
        result = datetime.fromisoformat(text)
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        result = result.astimezone(timezone.utc)
    else:
        raise ValueError("missing releaseDate")
    if not datetime(2017, 1, 1, tzinfo=timezone.utc) <= result <= utcnow():
        raise ValueError("releaseDate outside plausible Binance history")
    return result


def article_list(payload, catalog_id):
    if payload.get("code") not in {None, "000000"}:
        raise ValueError(f"Binance response code={payload.get('code')}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("Binance response has no data object")
    direct = data.get("articles")
    if isinstance(direct, list):
        return direct, data.get("total")
    catalogs = data.get("catalogs")
    if not isinstance(catalogs, list):
        raise ValueError("Binance response has no article collection")
    for catalog in catalogs:
        if int(catalog.get("catalogId", -1)) == int(catalog_id):
            articles = catalog.get("articles")
            if not isinstance(articles, list):
                raise ValueError("catalog articles missing")
            return articles, catalog.get("total") or data.get("total")
    return [], data.get("total")


def classify(title, category):
    if STRICT_LISTING.search(title):
        return "STRICT_LISTING"
    if STRICT_DELISTING.search(title):
        return "STRICT_DELISTING"
    if PAIR_REMOVAL.search(title):
        return "PAIR_REMOVAL"
    return "CATEGORY_OTHER_" + category


def extract_tickers(title):
    values = set(PAREN_PATTERN.findall(title.upper()))
    values.update(PAIR_PATTERN.findall(title.upper()))
    values.update(SYMBOL_PATTERN.findall(title.upper()))
    return sorted(value for value in values if value not in TICKER_STOPWORDS)


def normalize_article(
    article, catalog_id, category, release_value=None,
    timestamp_source="LIST_RELEASE_DATE", raw_rank=None,
):
    code = str(article.get("code") or "").strip()
    title = str(article.get("title") or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{16,64}", code):
        raise ValueError("invalid article code")
    if len(title) < 8:
        raise ValueError("invalid article title")
    released = parse_release_date(
        article.get("releaseDate") if release_value is None else release_value
    )
    tickers = extract_tickers(title)
    return {
        "source": "BINANCE_OFFICIAL_ANNOUNCEMENT",
        "catalog_id": catalog_id,
        "category": category,
        "raw_rank": raw_rank,
        "article_code": code.lower(),
        "title": title,
        "release_time": released.isoformat(),
        "available_at": released.isoformat(),
        "timestamp_source": timestamp_source,
        "event_type": classify(title, category),
        "tickers": ",".join(tickers),
        "ticker_count": len(tickers),
        "url": f"{BASE_URL}/en/support/announcement/detail/{code}",
    }


def fetch_category(session, catalog_id, category):
    output = []
    seen = set()
    for page in range(1, MAX_PAGES + 1):
        response = session.get(
            LIST_URL,
            params={
                "type": 1, "catalogId": catalog_id,
                "pageNo": page, "pageSize": PAGE_SIZE,
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        articles, total = article_list(payload, catalog_id)
        if not articles:
            break
        page_new = 0
        for article in articles:
            code = str(article.get("code") or "").lower()
            if code in seen:
                continue
            seen.add(code)
            output.append((article, catalog_id, category, len(output) + 1))
            page_new += 1
        if page_new == 0 or len(articles) < PAGE_SIZE:
            break
        if total is not None and len(output) >= int(total):
            break
    return output


def detail_release_date(session, article_code):
    response = session.get(
        DETAIL_URL,
        params={"articleCode": article_code},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") not in {None, "000000"}:
        raise ValueError(f"Binance detail response code={payload.get('code')}")

    def candidates(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.lower() in {
                    "releasedate", "publishdate", "publishtime", "publishedat"
                } and child is not None and child != "":
                    yield key, child
                yield from candidates(child)
        elif isinstance(value, list):
            for child in value:
                yield from candidates(child)

    valid = []
    for key, value in candidates(payload.get("data")):
        try:
            valid.append((parse_release_date(value), key, value))
        except (TypeError, ValueError, OverflowError):
            continue
    if not valid:
        raise ValueError("detail metadata has no valid release/publish timestamp")
    valid.sort(key=lambda item: item[0])
    released, key, raw_value = valid[-1]
    return raw_value, f"DETAIL_{key.upper()}", released


def audit(rows, raw_count, invalid):
    times = sorted(datetime.fromisoformat(row["release_time"]) for row in rows)
    strict = [
        row for row in rows
        if row["event_type"] in {"STRICT_LISTING", "STRICT_DELISTING"}
    ]
    codes = [row["article_code"] for row in rows]
    counts = Counter(row["category"] for row in rows)
    audit_end = times[-1] if times else utcnow()
    recent_cutoff = audit_end - timedelta(days=RECENT_AUDIT_DAYS)
    protected_cutoff = recent_cutoff - timedelta(days=ORDER_SAFETY_DAYS)
    recent_boundaries = {}
    for category in CATEGORIES.values():
        ranks = [
            row["raw_rank"] for row in rows
            if row["category"] == category
            and datetime.fromisoformat(row["release_time"]) >= protected_cutoff
        ]
        recent_boundaries[category] = max(ranks, default=0)
    recent_raw = sum(recent_boundaries.values())
    recent_valid = sum(
        row["raw_rank"] <= recent_boundaries[row["category"]]
        for row in rows
    )
    recent_exact_ratio = recent_valid / recent_raw if recent_raw else 0.0
    recent_invalid = sum(
        item.get("raw_rank", 10**9) <= recent_boundaries.get(item["category"], 0)
        for item in invalid
    )
    order_violations = 0
    max_order_inversion_seconds = 0.0
    for category in CATEGORIES.values():
        ordered = sorted(
            (row for row in rows if row["category"] == category),
            key=lambda row: row["raw_rank"],
        )
        previous = None
        for row in ordered:
            current = datetime.fromisoformat(row["release_time"])
            if previous is not None and current > previous:
                order_violations += 1
                max_order_inversion_seconds = max(
                    max_order_inversion_seconds,
                    (current - previous).total_seconds(),
                )
            previous = current
    span_days = (times[-1] - times[0]).total_seconds() / 86400.0 if len(times) >= 2 else 0.0
    result = {
        "version": VERSION,
        "generated_at": utcnow().isoformat(),
        "source_endpoint": LIST_URL,
        "raw_articles": raw_count,
        "valid_articles": len(rows),
        "invalid_articles": len(invalid),
        "detail_recovered_articles": sum(
            row["timestamp_source"].startswith("DETAIL_") for row in rows
        ),
        "exact_timestamp_ratio": len(rows) / raw_count if raw_count else 0.0,
        "recent_audit_days": RECENT_AUDIT_DAYS,
        "order_safety_days": ORDER_SAFETY_DAYS,
        "recent_cutoff": recent_cutoff.isoformat(),
        "protected_cutoff": protected_cutoff.isoformat(),
        "recent_raw_articles": recent_raw,
        "recent_valid_articles": recent_valid,
        "recent_invalid_articles": recent_invalid,
        "recent_exact_timestamp_ratio": recent_exact_ratio,
        "recent_rank_boundaries": recent_boundaries,
        "recent_accounting_complete": recent_valid + recent_invalid == recent_raw,
        "catalog_time_order_violations": order_violations,
        "max_order_inversion_days": max_order_inversion_seconds / 86400.0,
        "unique_story_ratio": len(set(codes)) / len(codes) if codes else 0.0,
        "usable_span_days": span_days,
        "strict_directional_articles": len(strict),
        "articles_with_ticker": sum(row["ticker_count"] > 0 for row in rows),
        "category_counts": dict(counts),
        "invalid_examples": invalid[:20],
    }
    gates = {
        "raw_articles_gte_300": raw_count >= 300,
        "recent_exact_timestamp_ratio_gte_99pct": recent_exact_ratio >= 0.99,
        "recent_rank_accounting_complete": result["recent_accounting_complete"],
        "max_order_inversion_within_safety_buffer": (
            max_order_inversion_seconds <= ORDER_SAFETY_DAYS * 86400.0
        ),
        "unique_story_ratio_gte_99pct": result["unique_story_ratio"] >= 0.99,
        "usable_span_gte_730d": span_days >= 730.0,
        "strict_directional_articles_gte_150": len(strict) >= 150,
        "ticker_articles_gte_150": result["articles_with_ticker"] >= 150,
        "listing_and_delisting_present": all(counts[name] > 0 for name in CATEGORIES.values()),
    }
    result["gates"] = gates
    result["status"] = "SOURCE_FEASIBLE" if all(gates.values()) else "SOURCE_BLOCKED"
    return result


def run():
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError(
            "requests paketi gerekli; proje bağımlılıklarını kurun"
        ) from exc
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 AG-Crypto-Analyst/1.0",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en",
        "Referer": f"{BASE_URL}/en/messages/v2/group/announcement",
        "clienttype": "web",
        "lang": "en",
    })
    raw = []
    for catalog_id, category in CATEGORIES.items():
        raw.extend(fetch_category(session, catalog_id, category))
    rows, invalid = [], []
    for article, catalog_id, category, raw_rank in raw:
        try:
            rows.append(normalize_article(
                article, catalog_id, category, raw_rank=raw_rank
            ))
        except Exception as list_exc:
            try:
                release_value, timestamp_source, _ = detail_release_date(
                    session, str(article.get("code") or "").strip()
                )
                rows.append(normalize_article(
                    article, catalog_id, category,
                    release_value=release_value,
                    timestamp_source=timestamp_source,
                    raw_rank=raw_rank,
                ))
            except Exception as detail_exc:
                invalid.append({
                    "catalog_id": catalog_id,
                    "category": category,
                    "raw_rank": raw_rank,
                    "code": article.get("code"),
                    "title": article.get("title"),
                    "list_error": str(list_exc),
                    "detail_error": str(detail_exc),
                })
    rows.sort(key=lambda row: (row["release_time"], row["article_code"]))
    result = audit(rows, len(raw), invalid)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["status"])
        writer.writeheader()
        if rows:
            writer.writerows(rows)
    AUDIT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def render(result):
    print("AG Crypto Analyst - Exact-Time Catalyst Source Feasibility v1.3.0")
    print(f"Source: Binance official announcement metadata | status={result['status']}")
    print(
        f"Raw={result['raw_articles']} | valid={result['valid_articles']} | "
        f"exact_time={100.0 * result['exact_timestamp_ratio']:.1f}% | "
        f"unique={100.0 * result['unique_story_ratio']:.1f}% | "
        f"span={result['usable_span_days']:.1f}d"
    )
    print(
        f"Recent {result['recent_audit_days']}d "
        f"(+{result['order_safety_days']}d safety): "
        f"raw={result['recent_raw_articles']} | "
        f"valid={result['recent_valid_articles']} | "
        f"invalid={result['recent_invalid_articles']} | "
        f"exact_time={100.0 * result['recent_exact_timestamp_ratio']:.1f}% | "
        f"order_violations={result['catalog_time_order_violations']} | "
        f"max_inversion={result['max_order_inversion_days']:.2f}d"
    )
    print(
        f"Strict directional={result['strict_directional_articles']} | "
        f"with_ticker={result['articles_with_ticker']} | "
        f"detail_recovered={result['detail_recovered_articles']} | "
        f"categories={result['category_counts']}"
    )
    failed = [name for name, value in result["gates"].items() if not value]
    if failed:
        print("failed: " + ",".join(failed))
    print("No price outcome was read and no model/backtest was run.")
    print(f"Events: {OUTPUT_PATH}")
    print(f"Audit: {AUDIT_PATH}")


def self_test():
    payload = {
        "code": "000000",
        "data": {"catalogs": [{
            "catalogId": 48,
            "articles": [{
                "code": "a" * 32,
                "title": "Binance Will List Example Token (EXT)",
                "releaseDate": 1_735_689_600_000,
            }],
        }]},
    }
    articles, total = article_list(payload, 48)
    assert total is None and len(articles) == 1
    row = normalize_article(articles[0], 48, CATEGORIES[48], raw_rank=1)
    assert row["event_type"] == "STRICT_LISTING"
    assert row["tickers"] == "EXT"
    assert row["release_time"].startswith("2025-01-01")
    duplicate = dict(row)
    duplicate["raw_rank"] = 2
    result = audit([row, duplicate], 2, [])
    assert result["unique_story_ratio"] == 0.5
    assert result["status"] == "SOURCE_BLOCKED"

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class FakeSession:
        def get(self, url, params, timeout):
            assert url == LIST_URL
            assert params["pageSize"] == 20
            assert params["catalogId"] == 48
            assert timeout == TIMEOUT
            return FakeResponse()

    fetched = fetch_category(FakeSession(), 48, CATEGORIES[48])
    assert len(fetched) == 1

    class FakeDetailResponse(FakeResponse):
        def json(self):
            return {
                "code": "000000",
                "data": {"article": {"releaseDate": 1_735_689_600_000}},
            }

    class FakeDetailSession:
        def get(self, url, params, timeout):
            assert url == DETAIL_URL
            assert params["articleCode"] == "a" * 32
            return FakeDetailResponse()

    detail_value, detail_source, detail_time = detail_release_date(
        FakeDetailSession(), "a" * 32
    )
    assert detail_value == 1_735_689_600_000
    assert detail_source == "DETAIL_RELEASEDATE"
    assert detail_time.isoformat().startswith("2025-01-01")
    print("Exact-time catalyst feasibility self-test: PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        render(run())


if __name__ == "__main__":
    main()

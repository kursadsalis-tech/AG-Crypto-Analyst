import requests
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from news_intelligence.news_repository import (
    create_news_table,
    save_news_batch,
    get_news_count,
)


RSS_SOURCES = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
}


def fetch_rss(source_name, url):
    response = requests.get(
        url,
        timeout=15,
        headers={
            "User-Agent": "AG-Crypto-Analyst/0.5.0"
        }
    )

    response.raise_for_status()

    root = ET.fromstring(response.content)

    articles = []

    for item in root.findall(".//item"):
        title = item.findtext("title")
        link = item.findtext("link")
        description = item.findtext("description")
        pub_date = item.findtext("pubDate")

        published_at = None

        if pub_date:
            try:
                published_at = parsedate_to_datetime(
                    pub_date
                ).isoformat()
            except Exception:
                published_at = pub_date

        articles.append({
            "source": source_name,
            "title": title,
            "link": link,
            "description": description,
            "published_at": published_at
        })

    return articles


def main():
    create_news_table()

    all_articles = []

    for source_name, url in RSS_SOURCES.items():
        try:
            articles = fetch_rss(source_name, url)
            all_articles.extend(articles)

            print(
                f"{source_name}: "
                f"{len(articles)} haber alındı."
            )

        except Exception as e:
            print(
                f"{source_name}: HATA -> {e}"
            )

    if all_articles:
        save_news_batch(all_articles)

    print()
    print("=" * 70)
    print("AG Crypto Analyst - News Intelligence Agent v0.5.0")
    print("=" * 70)
    print(f"Toplam çekilen haber : {len(all_articles)}")
    print(f"Veritabanındaki haber : {get_news_count()}")
    print("=" * 70)

    for article in all_articles[:20]:
        print()
        print(f"Kaynak   : {article['source']}")
        print(f"Başlık   : {article['title']}")
        print(f"Tarih    : {article['published_at']}")
        print(f"Link     : {article['link']}")


if __name__ == "__main__":
    main()

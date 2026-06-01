from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from confluent_kafka import Producer

from listing_feature_scraper import ScrapeConfig, iter_listing_records


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def delivery_report(err, msg) -> None:
    if err is not None:
        logger.error("Kafka delivery failed: %s", err)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape property listings at scale and publish model features to Kafka.")
    parser.add_argument("--limit", type=int, default=100000, help="Maximum number of listings to scrape.")
    parser.add_argument("--max-pages", type=int, default=1000, help="Maximum number of list pages to crawl.")
    parser.add_argument("--start-page", type=int, default=1, help="First page to crawl.")
    parser.add_argument("--state-file", type=Path, default=Path("runtime") / "scrape_state" / "producer_state.json")
    parser.add_argument("--request-delay", type=float, default=0.25)
    parser.add_argument("--detail-delay", type=float, default=0.1)
    parser.add_argument("--include-unverified", action="store_true")
    parser.add_argument("--fresh-start", action="store_true", help="Remove saved crawl state before scraping.")
    parser.add_argument("--topic", default=os.environ.get("KAFKA_RAW_TOPIC", "real_estate_raw"))
    parser.add_argument("--bootstrap-servers", default=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
    args = parser.parse_args()

    if args.fresh_start and args.state_file.exists():
        args.state_file.unlink()
        logger.info("Removed previous scrape state: %s", args.state_file)

    producer = Producer(
        {
            "bootstrap.servers": args.bootstrap_servers,
            "client.id": "real-estate-verified-producer",
            "linger.ms": 50,
            "batch.num.messages": 100,
        }
    )

    config = ScrapeConfig(
        max_pages=args.max_pages,
        start_page=args.start_page,
        max_items=args.limit,
        request_delay_seconds=args.request_delay,
        detail_delay_seconds=args.detail_delay,
        use_verified_filter=not args.include_unverified,
        state_file=args.state_file,
    )
    published_count = 0
    for record in iter_listing_records(config):
        payload = json.dumps(record, ensure_ascii=False).encode("utf-8")
        producer.produce(args.topic, key=record["url"], value=payload, callback=delivery_report)
        producer.poll(0)
        published_count += 1
        if published_count % 20 == 0:
            producer.flush()
            logger.info("Published %s messages to topic %s so far", published_count, args.topic)

    producer.flush()
    logger.info("Published %s messages to topic %s", published_count, args.topic)


if __name__ == "__main__":
    main()

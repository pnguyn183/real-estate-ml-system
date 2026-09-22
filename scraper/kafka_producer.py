from __future__ import annotations

"""
Module: scraper/kafka_producer.py
Purpose: Entrypoint for the scraper Kafka producer. Iterates listing records from
`iter_source_records` and publishes canonical v2 JSON to the existing Kafka topic.
Behavior: acknowledges delivery before advancing resume state, with bounded HTTP and Kafka waits.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from confluent_kafka import KafkaException, Producer

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scraper.listing_feature_scraper import ScrapeConfig
from scraper.http_policy import ScraperFetchError, ScraperPolicyError
from scraper.multi_source import iter_source_records, selected_sources


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class KafkaDeliveryError(RuntimeError):
    """Delivery is unconfirmed; leave the current URL replayable on the next run."""


def publish_records(producer, records, topic: str, delivery_timeout: float) -> int:
    """At-least-once handoff: never checkpoint an unacknowledged message.

    A crash between acknowledgement and checkpoint may replay a URL; the
    downstream URL upsert remains the idempotency boundary.
    """
    published_count = 0
    iterator = iter(records)
    try:
        for record in iterator:
            delivered = []

            def on_delivery(error, message):
                delivered.append(error)

            producer.produce(
                topic, key=record["url"],
                value=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                callback=on_delivery,
            )
            pending = producer.flush(delivery_timeout)
            if pending or not delivered or delivered[0] is not None:
                raise KafkaDeliveryError("Kafka delivery not acknowledged; scrape checkpoint was not advanced")
            published_count += 1
            if published_count % 20 == 0:
                logger.info("Acknowledged %s messages to topic %s so far", published_count, topic)
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()
    return published_count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bounded source-adapter crawling; publish canonical raw listings to Kafka.")
    parser.add_argument(
        "--source",
        choices=("alonhadat", "guland", "homedy"),
        default=None,
        help="One source override; no legacy Batdongsan crawling is registered.",
    )
    parser.add_argument("--sources", default=os.environ.get("ENABLED_SOURCES", "alonhadat,homedy"))
    parser.add_argument("--crawl-enabled", action="store_true", default=os.environ.get("CRAWL_ENABLED", "false").lower() in {"1", "true", "yes"})
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of listings per enabled source.")
    parser.add_argument("--max-pages", type=int, default=1, help="Per-source listing-page budget.")
    parser.add_argument("--start-page", type=int, default=1, help="First page to crawl.")
    parser.add_argument("--state-file", type=Path, default=Path("runtime") / "scrape_state" / "producer_state.json")
    parser.add_argument("--request-delay", type=float, default=2.0)
    parser.add_argument("--detail-delay", type=float, default=2.0)
    parser.add_argument("--delay-min", type=float, default=float(os.environ.get("SCRAPE_DELAY_MIN", "2")))
    parser.add_argument("--delay-max", type=float, default=float(os.environ.get("SCRAPE_DELAY_MAX", "5")))
    parser.add_argument("--http-timeout", type=int, default=int(os.environ.get("SCRAPE_HTTP_TIMEOUT", "30")))
    parser.add_argument("--http-attempts", type=int, default=int(os.environ.get("SCRAPE_HTTP_ATTEMPTS", "4")))
    parser.add_argument("--delivery-timeout", type=int, default=int(os.environ.get("SCRAPE_KAFKA_DELIVERY_TIMEOUT", "30")))
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("CRAWLER_USER_AGENT", "RealEstatePipelineCrawler/1.0"),
        help="Truthful crawler identity; do not use this to bypass access controls.",
    )
    parser.add_argument("--include-unverified", action="store_true")
    parser.add_argument("--fresh-start", action="store_true", help="Ignore saved state for this run; replace checkpoint only after Kafka acknowledgment.")
    parser.add_argument("--revisit-seconds", type=float, default=float(os.environ.get("SCRAPE_REVISIT_SECONDS", "86400")),
                        help="Refresh acknowledged URLs after this interval; unseen URLs take priority.")
    parser.add_argument("--max-consecutive-failures", type=int, default=int(os.environ.get("SCRAPE_MAX_CONSECUTIVE_FAILURES", "3")),
                        help="Stop a source after this many consecutive failed or invalid details.")
    parser.add_argument("--topic", default=os.environ.get("KAFKA_RAW_TOPIC", "real_estate_raw"))
    parser.add_argument("--bootstrap-servers", default=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
    args = parser.parse_args(argv)
    sources = selected_sources(args.source or args.sources)
    if not args.crawl_enabled:
        logger.info("Crawling disabled; no HTTP requests or Kafka producer created")
        return 0
    if not 1 <= args.delivery_timeout <= 300:
        parser.error("--delivery-timeout must be between 1 and 300 seconds")

    config = ScrapeConfig(
        max_pages=args.max_pages,
        start_page=args.start_page,
        max_items=args.limit,
        request_delay_seconds=args.request_delay,
        detail_delay_seconds=args.detail_delay,
        delay_min_seconds=args.delay_min,
        delay_max_seconds=args.delay_max,
        user_agent=args.user_agent,
        timeout_seconds=args.http_timeout,
        max_retries=args.http_attempts,
        use_verified_filter=not args.include_unverified,
        state_file=args.state_file,
    )
    producer = Producer({
        "bootstrap.servers": args.bootstrap_servers,
        "client.id": "real-estate-multi-source-producer",
        "enable.idempotence": True,
        "acks": "all",
        "delivery.timeout.ms": args.delivery_timeout * 1000,
        "request.timeout.ms": min(args.delivery_timeout * 1000, 30000),
        "linger.ms": 0,
    })
    status = 0
    for source in sources:
        try:
            published_count = publish_records(producer, iter_source_records(source, config, fresh_start=args.fresh_start,
                                                                           revisit_seconds=args.revisit_seconds,
                                                                           max_consecutive_failures=args.max_consecutive_failures),
                                              args.topic, args.delivery_timeout)
            logger.info("source=%s acknowledged=%s topic=%s", source, published_count, args.topic)
        except ScraperPolicyError as error:
            logger.error("source=%s policy_stop=%s", source, error)
            status = 20
        except (ScraperFetchError, KafkaDeliveryError, KafkaException, BufferError, OSError, ValueError) as error:
            logger.error("source=%s run_failed=%s: %s", source, type(error).__name__, error)
            status = status or 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())

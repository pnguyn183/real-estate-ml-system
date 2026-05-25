#!/usr/bin/env python
import os
import time
import subprocess
import logging
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCRAPE_INTERVAL = int(os.environ.get("SCRAPE_INTERVAL", 1800))
SCRAPE_LIMIT = int(os.environ.get("SCRAPE_LIMIT", 100000))
SCRAPE_MAX_PAGES = int(os.environ.get("SCRAPE_MAX_PAGES", 1000))
SCRAPE_START_PAGE = int(os.environ.get("SCRAPE_START_PAGE", 1))
SCRAPE_REQUEST_DELAY = float(os.environ.get("SCRAPE_REQUEST_DELAY", 0.1))
SCRAPE_DETAIL_DELAY = float(os.environ.get("SCRAPE_DETAIL_DELAY", 0.05))
SCRAPE_TIMEOUT = int(os.environ.get("SCRAPE_TIMEOUT", max(60, SCRAPE_INTERVAL - 30)))
INCLUDE_UNVERIFIED = os.environ.get("SCRAPE_INCLUDE_UNVERIFIED", "false").lower() in {"1", "true", "yes"}
FRESH_START_EACH_RUN = os.environ.get("SCRAPE_FRESH_START", "true").lower() in {"1", "true", "yes"}
SCRAPE_STATE_FILE = Path(os.environ.get("SCRAPE_STATE_FILE", "runtime/scrape_state/producer_state.json"))

def run_scraper():
    try:
        logger.info(
            "Starting scraper: limit=%s max_pages=%s start_page=%s timeout=%ss",
            SCRAPE_LIMIT,
            SCRAPE_MAX_PAGES,
            SCRAPE_START_PAGE,
            SCRAPE_TIMEOUT,
        )
        cmd = [
            "python",
            "scraper/kafka_producer.py",
            "--limit",
            str(SCRAPE_LIMIT),
            "--max-pages",
            str(SCRAPE_MAX_PAGES),
            "--start-page",
            str(SCRAPE_START_PAGE),
            "--request-delay",
            str(SCRAPE_REQUEST_DELAY),
            "--detail-delay",
            str(SCRAPE_DETAIL_DELAY),
            "--state-file",
            str(SCRAPE_STATE_FILE),
        ]
        if INCLUDE_UNVERIFIED:
            cmd.append("--include-unverified")
        if FRESH_START_EACH_RUN:
            cmd.append("--fresh-start")
        result = subprocess.run(cmd, timeout=SCRAPE_TIMEOUT)
        if result.returncode == 0:
            logger.info("Scraper completed successfully")
        else:
            logger.error(f"Scraper failed with code {result.returncode}")
    except subprocess.TimeoutExpired:
        logger.warning("Scraper timeout")
    except Exception as exc:
        logger.error(f"Scraper error: {exc}")

def main():
    logger.info(
        "Auto-scraper started, interval=%ss, fresh_start_each_run=%s, state_file=%s",
        SCRAPE_INTERVAL,
        FRESH_START_EACH_RUN,
        SCRAPE_STATE_FILE,
    )
    logger.info("Running first scrape immediately")
    while True:
        try:
            run_scraper()
            logger.info(f"Next scrape in {SCRAPE_INTERVAL}s (at {datetime.fromtimestamp(time.time() + SCRAPE_INTERVAL)})")
            time.sleep(SCRAPE_INTERVAL)
        except KeyboardInterrupt:
            logger.info("Auto-scraper stopped")
            break
        except Exception as exc:
            logger.error(f"Unexpected error: {exc}")
            time.sleep(30)

if __name__ == "__main__":
    main()

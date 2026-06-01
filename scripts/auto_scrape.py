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

INITIAL_SCRAPE_LIMIT = int(os.environ.get("SCRAPE_INITIAL_LIMIT", SCRAPE_LIMIT))
INITIAL_SCRAPE_MAX_PAGES = int(os.environ.get("SCRAPE_INITIAL_MAX_PAGES", SCRAPE_MAX_PAGES))
INITIAL_SCRAPE_START_PAGE = int(os.environ.get("SCRAPE_INITIAL_START_PAGE", SCRAPE_START_PAGE))
INITIAL_SCRAPE_TIMEOUT = int(os.environ.get("SCRAPE_INITIAL_TIMEOUT", SCRAPE_TIMEOUT))
INITIAL_FRESH_START = os.environ.get("SCRAPE_INITIAL_FRESH_START", "true").lower() in {"1", "true", "yes"}
INITIAL_SCRAPE_STATE_FILE = Path(
    os.environ.get("SCRAPE_INITIAL_STATE_FILE", "runtime/scrape_state/producer_initial_state.json")
)


def run_scraper(
    *,
    limit: int,
    max_pages: int,
    start_page: int,
    timeout: int,
    fresh_start: bool,
    state_file: Path,
    run_label: str,
):
    try:
        logger.info(
            "Starting %s scraper: limit=%s max_pages=%s start_page=%s timeout=%ss fresh_start=%s",
            run_label,
            limit,
            max_pages,
            start_page,
            timeout,
            fresh_start,
        )
        cmd = [
            "python",
            "scraper/kafka_producer.py",
            "--limit",
            str(limit),
            "--max-pages",
            str(max_pages),
            "--start-page",
            str(start_page),
            "--request-delay",
            str(SCRAPE_REQUEST_DELAY),
            "--detail-delay",
            str(SCRAPE_DETAIL_DELAY),
            "--state-file",
            str(state_file),
        ]
        if INCLUDE_UNVERIFIED:
            cmd.append("--include-unverified")
        if fresh_start:
            cmd.append("--fresh-start")
        result = subprocess.run(cmd, timeout=timeout)
        if result.returncode == 0:
            logger.info("%s scraper completed successfully", run_label.capitalize())
        else:
            logger.error("%s scraper failed with code %s", run_label.capitalize(), result.returncode)
    except subprocess.TimeoutExpired:
        logger.warning("%s scraper timeout", run_label.capitalize())
    except Exception as exc:
        logger.error("%s scraper error: %s", run_label.capitalize(), exc)

def main():
    logger.info(
        "Auto-scraper started, interval=%ss, periodic_limit=%s, periodic_max_pages=%s, "
        "initial_limit=%s, initial_max_pages=%s, periodic_fresh_start=%s, state_file=%s",
        SCRAPE_INTERVAL,
        SCRAPE_LIMIT,
        SCRAPE_MAX_PAGES,
        INITIAL_SCRAPE_LIMIT,
        INITIAL_SCRAPE_MAX_PAGES,
        FRESH_START_EACH_RUN,
        SCRAPE_STATE_FILE,
    )
    logger.info("Running first scrape immediately")
    run_scraper(
        limit=INITIAL_SCRAPE_LIMIT,
        max_pages=INITIAL_SCRAPE_MAX_PAGES,
        start_page=INITIAL_SCRAPE_START_PAGE,
        timeout=INITIAL_SCRAPE_TIMEOUT,
        fresh_start=INITIAL_FRESH_START,
        state_file=INITIAL_SCRAPE_STATE_FILE,
        run_label="initial",
    )
    logger.info(f"Next scrape in {SCRAPE_INTERVAL}s (at {datetime.fromtimestamp(time.time() + SCRAPE_INTERVAL)})")
    time.sleep(SCRAPE_INTERVAL)

    while True:
        try:
            run_scraper(
                limit=SCRAPE_LIMIT,
                max_pages=SCRAPE_MAX_PAGES,
                start_page=SCRAPE_START_PAGE,
                timeout=SCRAPE_TIMEOUT,
                fresh_start=FRESH_START_EACH_RUN,
                state_file=SCRAPE_STATE_FILE,
                run_label="periodic",
            )
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

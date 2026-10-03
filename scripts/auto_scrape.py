#!/usr/bin/env python
import os
import time
import subprocess
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCRAPE_INTERVAL = int(os.environ.get("SCRAPE_INTERVAL", 1800))
SCRAPE_LIMIT = int(os.environ.get("SCRAPE_LIMIT", 10))
SCRAPE_MAX_PAGES = int(os.environ.get("SCRAPE_MAX_PAGES", 1))
SCRAPE_START_PAGE = int(os.environ.get("SCRAPE_START_PAGE", 1))
SCRAPE_REQUEST_DELAY = float(os.environ.get("SCRAPE_REQUEST_DELAY", 2.0))
SCRAPE_DETAIL_DELAY = float(os.environ.get("SCRAPE_DETAIL_DELAY", 2.0))
SCRAPE_DELAY_MIN = float(os.environ.get("SCRAPE_DELAY_MIN", 2.0))
SCRAPE_DELAY_MAX = float(os.environ.get("SCRAPE_DELAY_MAX", 5.0))
SCRAPE_USER_AGENT = os.environ.get("CRAWLER_USER_AGENT", "RealEstatePipelineCrawler/1.0")
ENABLED_SOURCES = os.environ.get("ENABLED_SOURCES", "alonhadat,homedy")
SCRAPE_TIMEOUT = int(os.environ.get("SCRAPE_TIMEOUT", max(60, SCRAPE_INTERVAL - 30)))
SCRAPE_MAX_RESPONSE_BYTES = int(os.environ.get("SCRAPE_MAX_RESPONSE_BYTES", "32000000"))
INCLUDE_UNVERIFIED = os.environ.get("SCRAPE_INCLUDE_UNVERIFIED", "false").lower() in {"1", "true", "yes"}
FRESH_START_EACH_RUN = os.environ.get("SCRAPE_FRESH_START", "false").lower() in {"1", "true", "yes"}
SCRAPE_STATE_FILE = Path(os.environ.get("SCRAPE_STATE_FILE", "runtime/scrape_state/producer_state.json"))

INITIAL_SCRAPE_LIMIT = int(os.environ.get("SCRAPE_INITIAL_LIMIT", SCRAPE_LIMIT))
INITIAL_SCRAPE_MAX_PAGES = int(os.environ.get("SCRAPE_INITIAL_MAX_PAGES", SCRAPE_MAX_PAGES))
INITIAL_SCRAPE_START_PAGE = int(os.environ.get("SCRAPE_INITIAL_START_PAGE", SCRAPE_START_PAGE))
INITIAL_SCRAPE_TIMEOUT = int(os.environ.get("SCRAPE_INITIAL_TIMEOUT", SCRAPE_TIMEOUT))
INITIAL_FRESH_START = os.environ.get("SCRAPE_INITIAL_FRESH_START", "false").lower() in {"1", "true", "yes"}
INITIAL_SCRAPE_STATE_FILE = Path(
    os.environ.get("SCRAPE_INITIAL_STATE_FILE", str(SCRAPE_STATE_FILE))
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
    from scraper.multi_source import selected_sources
    from scraper.source_metrics import RunMetrics

    failures = []
    for source in selected_sources(ENABLED_SOURCES):
        logger.info(
            "Starting %s scraper source=%s: limit=%s max_pages=%s start_page=%s timeout=%ss fresh_start=%s",
            run_label,
            source,
            limit,
            max_pages,
            start_page,
            timeout,
            fresh_start,
        )
        cmd = [
            sys.executable,
            "scraper/kafka_producer.py",
            "--limit",
            str(limit),
            "--source",
            source,
            "--max-pages",
            str(max_pages),
            "--start-page",
            str(start_page),
            "--request-delay",
            str(SCRAPE_REQUEST_DELAY),
            "--detail-delay",
            str(SCRAPE_DETAIL_DELAY),
            "--delay-min",
            str(SCRAPE_DELAY_MIN),
            "--delay-max",
            str(SCRAPE_DELAY_MAX),
            "--max-response-bytes",
            str(SCRAPE_MAX_RESPONSE_BYTES),
            "--user-agent",
            SCRAPE_USER_AGENT,
            "--state-file",
            str(state_file),
        ]
        if INCLUDE_UNVERIFIED:
            cmd.append("--include-unverified")
        if fresh_start:
            cmd.append("--fresh-start")
        try:
            # The timeout belongs to this source, so a slow/unavailable site
            # cannot consume the next source's request budget.
            result = subprocess.run(cmd, timeout=timeout, cwd=ROOT)
            if result.returncode == 0:
                logger.info("%s source=%s scraper completed successfully", run_label.capitalize(), source)
            else:
                failures.append(f"{source}: exit {result.returncode}")
                logger.error("%s source=%s scraper failed with code %s", run_label.capitalize(), source, result.returncode)
        except subprocess.TimeoutExpired:
            failures.append(f"{source}: timeout")
            logger.warning("%s source=%s scraper timeout; continuing other sources", run_label.capitalize(), source)
            try:
                metrics = RunMetrics(source)
                metrics.inc("scheduler_timeouts")
                metrics.values["last_failure"] = time.time()
                metrics.save()
            except OSError as exc:
                logger.error("source=%s timeout metrics could not be saved: %s", source, exc)
        except OSError as exc:
            failures.append(f"{source}: {type(exc).__name__}")
            logger.error("%s source=%s scraper process error: %s", run_label.capitalize(), source, exc)
    if failures:
        raise RuntimeError(f"{run_label} scraper had source failures: {', '.join(failures)}")

def main():
    from scraper.source_metrics import start_metrics
    start_metrics()
    if os.environ.get("CRAWL_ENABLED", "false").lower() not in {"true", "1", "yes"}:
        logger.info("Crawler disabled by configuration; metrics remain available, no website requests")
        while True:
            try:
                time.sleep(60)
            except KeyboardInterrupt:
                return
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
    initial = True
    while True:
        try:
            run_scraper(
                limit=INITIAL_SCRAPE_LIMIT if initial else SCRAPE_LIMIT,
                max_pages=INITIAL_SCRAPE_MAX_PAGES if initial else SCRAPE_MAX_PAGES,
                start_page=INITIAL_SCRAPE_START_PAGE if initial else SCRAPE_START_PAGE,
                timeout=INITIAL_SCRAPE_TIMEOUT if initial else SCRAPE_TIMEOUT,
                fresh_start=INITIAL_FRESH_START if initial else FRESH_START_EACH_RUN,
                state_file=INITIAL_SCRAPE_STATE_FILE if initial else SCRAPE_STATE_FILE,
                run_label="initial" if initial else "periodic",
            )
        except KeyboardInterrupt:
            logger.info("Auto-scraper stopped")
            break
        except Exception as exc:
            # A failed first run must not cause a Compose restart storm. Both
            # failed and successful runs respect the configured crawl interval.
            logger.error("Scrape attempt failed; waiting for next scheduled run: %s", exc)
        initial = False
        logger.info("Next scrape in %ss (at %s)", SCRAPE_INTERVAL,
                    datetime.fromtimestamp(time.time() + SCRAPE_INTERVAL))
        try:
            time.sleep(max(60, SCRAPE_INTERVAL))
        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()

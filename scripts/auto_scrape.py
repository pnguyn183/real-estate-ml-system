#!/usr/bin/env python
import os
import time
import subprocess
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCRAPE_INTERVAL = int(os.environ.get("SCRAPE_INTERVAL", 3600))  # 1 hour default

def run_scraper():
    try:
        logger.info("Starting scraper...")
        cmd = ["python", "scraper/kafka_producer.py"]
        result = subprocess.run(cmd, timeout=SCRAPE_INTERVAL - 60)
        if result.returncode == 0:
            logger.info("Scraper completed successfully")
        else:
            logger.error(f"Scraper failed with code {result.returncode}")
    except subprocess.TimeoutExpired:
        logger.warning("Scraper timeout")
    except Exception as exc:
        logger.error(f"Scraper error: {exc}")

def main():
    logger.info(f"Auto-scraper started, interval={SCRAPE_INTERVAL}s")
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

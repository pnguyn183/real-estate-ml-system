from __future__ import annotations

import csv
import json
from pathlib import Path

from listing_feature_scraper import ScrapeConfig, scrape_listing_records


MAX_ITEMS = 10
OUTPUT_JSON = Path(__file__).with_name("sample_raw_listings.json")
OUTPUT_CSV = Path(__file__).with_name("sample_raw_listings.csv")


def write_json(records, path: Path) -> None:
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(records, path: Path) -> None:
    fieldnames = [
        "url",
        "listing_id",
        "title",
        "price_text",
        "area_text",
        "bedroom_text",
        "bathroom_text",
        "floor_text",
        "front_width_text",
        "road_width_text",
        "legal_text",
        "direction_text",
        "property_type",
        "listing_type",
        "posted_date_text",
        "furniture_text",
        "project_hint",
        "description",
        "verified",
        "location_slug",
        "province_slug",
        "district_slug",
        "ward_slug",
        "scraped_at",
    ]

    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def main() -> None:
    records = scrape_listing_records(ScrapeConfig(max_pages=2, max_items=MAX_ITEMS, state_file=None))
    write_json(records, OUTPUT_JSON)
    write_csv(records, OUTPUT_CSV)
    print(f"Exported {len(records)} listings")
    print(f"JSON: {OUTPUT_JSON}")
    print(f"CSV:  {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

# Three-source migration: audit and evidence

## Audit before migration — 2026-09-17

The working tree already contains uncommitted Agent, Docker, monitoring and
Airflow changes. They are retained, not reset. This document separates observed
behavior from implementation and future verification.

The following findings describe the pre-migration state on 2026-09-17, not
current operating instructions. At that checkpoint, ingestion entrypoints were
`scripts/auto_scrape.py`, the optional Airflow DAG and `scraper/kafka_producer.py`.
The producer accepted a Batdongsan fallback; an intermediate Homedy-only adapter
was also present. Neither provided a three-source contract. Batdongsan selectors,
URL-derived location and verified filtering must not be reused for other websites.
The current registry excludes Batdongsan; see
[crawl stability and current configuration (2026-09-22)](CRAWL_STABILITY.md).

Downstream audit:

- Three existing processors consume raw, stress and AI-result topics in one
  group. The seven topics, three brokers and isolation databases stay unchanged.
- Normalization currently consumes `price_text`, `area_text`, optional attribute
  text and location slugs. Its generic first-number parser can misread ranges,
  dimensions, hectares, negative text and frontage prices. New-schema messages
  require strict parsing; historical messages must remain readable.
- Raw Mongo preserves arbitrary fields; normalized features currently drop most
  source provenance. URL upserts distinguish domains, but do not identify the
  same property across websites. A source listing ID alone is not globally unique.
- Invalid/AI-pending updates can leave a previously trainable feature behind;
  migration needs explicit feature invalidation rather than stale training data.
- Training uses numeric/categorical imputation, text hashing and grouped price
  review. Source changes are not evidence of improved prediction accuracy.
- AI is optional, evidence/confidence validated, cached, rate-limited and
  asynchronous. Preserve its request/result envelopes and synthetic boundaries.
- Stress data already has a separate topic/database and multi-layer training
  exclusion. New canonical fixture messages must use that test boundary.
- API and frontend consume model feature names, not website selectors; no new
  API or frontend architecture is needed.

## Access checks before implementation

Small direct-HTTP probes on 2026-09-17 used an honest project User-Agent, at
least three seconds between requests, timeouts, a 2 MB response cap, no redirect
following, no credentials/proxies/browser impersonation and no Kafka writes.

| Source | Current evidence | Decision for automated crawling |
| --- | --- | --- |
| AloNhaDat | robots/category/detail HTTP 200; 20 links; detail 18007584: 23.5 billion VND, 73 m², hotel/commercial category | Adapter can be tested; collection/reuse approval remains operator responsibility; no bulk-readiness claim |
| Guland | robots HTTP 200; published terms restrict unauthorized automated extraction; no new listing/detail request after reviewing this restriction | Disabled pending permission and live revalidation; offline adapter fixtures only |
| Homedy | robots/category/detail HTTP 200; 18 links; detail 3220677: 10.45 billion VND, 180.1 m², house, Hà Nội | Small-sample access verified; earlier sequential-response failures remain unresolved; conservative opt-in only |

The browser/search fetch of AloNhaDat returned 429 while the local project
HTTP client returned 200. This difference is recorded, not treated as sustained
reliability. Historical September 11 Guland samples were accessible and showed
separate total/m²/frontage prices and old/new addresses; those are not a fresh
live verification or collection permission.

Policies: [AloNhaDat robots](https://alonhadat.com.vn/robots.txt),
[Guland terms](https://guland.vn/dieu-khoan-thoa-thuan),
[Guland robots](https://guland.vn/robots.txt),
[Homedy rules](https://homedy.com/quy-che-hoat-dong),
[Homedy robots](https://homedy.com/robots.txt).
Robots permission is not a bulk-reuse/ML licence. No seller contacts are needed
for price training; do not copy phone numbers into fixtures, metrics or logs.

Implementation and verification results below will be recorded only after they
are performed. No source is currently certified production-ready by this audit.

# Project status and verification evidence

Agent-pipeline verification: 2026-09-09. Source-trial follow-up: 2026-09-10.
Scope: the existing single-host Docker Compose project;
this is not a production-HA certification. Verification continues from the
previous implementation, not a redesign. Architecture contracts are in
[ARCHITECTURE.md](ARCHITECTURE.md); friendly and technical visuals are in
[flow_diagram.md](../flow_diagram.md).

## Homedy source trial — 2026-09-10

An opt-in source trial was added without changing the scheduled Batdongsan
scraper, Compose, Kafka, Processors, agents or training pipeline. It is **partially
runtime-verified, not ready for bulk ingestion**. No old source was removed.

- Docker Engine 29.6.2 was available after the operator opened Docker Desktop.
  `docker compose config --quiet` and `docker compose build scraper` passed.
- The actual command `python -m scraper.homedy_scraper --limit 5 --delay 3`
  ran in a disposable container from the existing scraper image, with ignored
  `runtime/` bind-mounted for the report. No dependency services were recreated.
- Run `homedy-20260910T055204Z-1c79624c` found 18 sale URLs, requested five
  details (seven HTTP requests including robots/index), and finished with
  **`needs_review`, exit 1: one usable real record, four explicitly rejected
  responses exceeding the 8 MB decompressed-body cap**. It was not a five-record
  success. Price, area, property type and province of the saved record passed
  the existing Processor normalizer/validator and the trial's required-field
  checks. This is source-format compatibility, not independent verification of
  the advertised property or price.
- Earlier attempts stopped after one record at 2 MB and then 8 MB. The final
  implementation reports oversized details individually and checks the rest
  of the requested URLs, without increasing the cap again. An isolated
  diagnostic fetched an affected URL at approximately 605 KB; changing
  connection reuse or requesting uncompressed responses did not resolve the
  sequential-run issue; one diagnostic also reached the response deadline.
  Its root cause
  remains unconfirmed. Diagnostics and partial runs must not be counted as
  additional unique listings.
- HTTP/robots access succeeded in this snapshot. No CAPTCHA, browser session,
  credential, proxy rotation or external LLM was used. This neither fixes nor
  re-verifies live Batdongsan crawling, whose latest established failure is 403.
- Raw records, checks and reports stay in ignored `runtime/source_trials/`.
  Kafka publications = 0; Mongo writes = 0; primary-training additions = 0.
  Tests explicitly forbid Kafka/Mongo constructors during a trial. Real trial
  records are not synthetic; `verified=0` and the report's
  `training_approved=false` must not be mistaken for trainer exclusion filters.
  Do not manually feed these files to primary ingestion/training.
- `python -m pytest -q`: **271 passed**, 373 existing NumPy/joblib deprecation
  warnings (12.01 s initially, 8.54 s on the final repeat). The 73 source tests
  also passed inside the rebuilt Docker image in 1.20 s. They cover URL/sale
  checks, fixture parsing, correct units, missing data, unrelated JSON-LD,
  robots, access errors, response bounds and local-only/partial-result behavior.
  The fixtures contain fabricated descriptions, not copied seller data.

Commands and storage boundaries are in
[README](../README.md#alternative-source-trial-not-scheduled-ingestion).
The trial is intentionally capped at ten details with no pagination, automatic
scheduling or Kafka integration. Before bulk use, resolve sequential retrieval,
evaluate a representative sample, and establish appropriate collection/reuse
permission; the [source terms](https://homedy.com/quy-che-hoat-dong) and robots
availability are not a verified bulk/ML licence.

The rest of this document records the **2026-09-09 full-stack verification**,
not a new full-stack certification. During this source-only follow-up, the
three brokers and Processors were running/healthy; `ai-agent` and `stress-agent`
were observed exited and were not started or tested. No fresh LLM, prediction,
training or end-to-end Kafka/Mongo test is claimed by this source trial.

## Completion verdict (2026-09-09 full-stack verification)

**NOT YET READY for the full requested demonstration, including a real LLM.**
Stored-real replay, distributed processing, mock AI integration and bounded
synthetic generation have passed. Live scraping is blocked by HTTP 403.
The repository-root `.env` was empty (0 bytes) when checked; the configured
model and API key were therefore unavailable. No real external API request
was made, and mock success is not evidence of external-provider compatibility.
A replay/mock-only demonstration is available with those limitations stated.

| Component | Status | Evidence / boundary |
| --- | --- | --- |
| Kafka / three Processors / MongoDB | IMPLEMENTED AND VERIFIED | Seven replicated topics, stable three-member group, real replay and synthetic writes |
| AI request/result handling, cache, retries, validation | IMPLEMENTED AND VERIFIED | Actual Kafka/Mongo integration with explicitly simulated provider transport |
| AI container, disabled mode | IMPLEMENTED AND VERIFIED | Healthy, enabled=false, no key required, no API calls or Kafka consumption |
| Real external LLM extraction | BLOCKED BY EXTERNAL LIMITATION | Local `.env` empty; no selected model/key available to the worker |
| Stress Agent | IMPLEMENTED AND VERIFIED | Bounded 12-message run, all ACKed, isolated Mongo persistence |
| Synthetic training exclusion | IMPLEMENTED AND VERIFIED | Unit tests, shared filters, zero synthetic primary features, stress candidates excluded |
| Trainer and model artifact | IMPLEMENTED AND VERIFIED | Startup training on 4,948 eligible real candidates; artifact loaded and legacy prediction returned a finite positive price |
| FastAPI and frontend serving | IMPLEMENTED AND VERIFIED | API health/model readiness and frontend HTTP 200; auth/model tests pass |
| Authenticated browser prediction journey | IMPLEMENTED BUT NOT RUNTIME-VERIFIED | No new account or browser session created in this verification |
| Prometheus / Grafana | IMPLEMENTED AND VERIFIED | Seven scrape targets up, config and six alert rules valid, Grafana health OK |
| Live website scraping | BLOCKED BY EXTERNAL LIMITATION | batdongsan.com.vn returns HTTP 403; scheduler running does not mean crawl success |

## Final architecture

Normal: website → scraper → `real_estate_raw` → the same three deterministic
Processors → MongoDB `real_estate_db` → periodic trainer → joblib price model
→ authenticated FastAPI → React/Nginx. Stored-record replay verified ingestion
without pretending the blocked website was reachable.

AI fallback: difficult record → Processor archive and acknowledged enqueue
→ `real_estate_ai_input` → `ai-agent` → configured external HTTPS provider
→ schema/evidence/business checks → `real_estate_ai_results` → result handler
inside the same Processors → validation and origin-specific Mongo persistence.
The worker stores extraction/cache state, not training features directly.
Failed results are preserved for review and sent to `real_estate_ai_dlq`.

Synthetic: bounded deterministic `stress-agent` → `real_estate_stress_raw`
→ existing Processors → `real_estate_stress_db`. Synthetic AI requires its own
explicit opt-in and returns to that same isolated database. There is no path
from the stress database to the primary trainer.

## Kafka topic inventory

Broker IDs 1/2/3 are Compose services `kafka`/`kafka2`/`kafka3`, using
Confluent 7.5.3 and ZooKeeper. Host bootstrap ports: 9092/9093/9094; internal
listeners: 29092/29093/29094. Every topic below was observed with **3 partitions,
RF=3, min.insync.replicas=2, and all three replicas in sync**.

| Topic | Purpose | Producer | Consumer / group |
| --- | --- | --- | --- |
| `real_estate_raw` | Real raw input | Scraper; controlled unchanged-real replay | Three Processors / `real_estate_training_pipeline` |
| `real_estate_features` | Best-effort real normalized audit | Processors | No in-repository consumer; trainer reads MongoDB |
| `real_estate_stress_raw` | Tagged synthetic workload | Stress Agent; labeled integration fixtures | Same three Processors / `real_estate_training_pipeline` |
| `real_estate_stress_features` | Best-effort synthetic audit | Processors' isolated stress branch | No in-repository consumer |
| `real_estate_ai_input` | Original record, origin and stable event ID for extraction | Processor fallback branch | AI worker / `real_estate_ai_extraction` |
| `real_estate_ai_results` | Validated success or terminal failure envelope | AI worker | Same Processors' result handler / `real_estate_training_pipeline` |
| `real_estate_ai_dlq` | Malformed AI requests and failed result review | AI worker / Processor result handler | Manual recovery; no automatic consumer |

The Processor group owns nine partitions across three subscribed topics, not
one three-partition topic only. Assignments are dynamic, not permanently tied
to worker names. The mock worker joined the existing AI group and was assigned
request partitions 0/1/2. With the deployed AI service disabled, the AI group
has no active member; this is expected, not a health failure.
The final post-build check observed zero Processor lag across its nine
assignments and zero AI-request lag, with all 21 application-topic partitions
fully in sync.

## Tests and measured evidence

### Complete suite and build

```text
python -m pytest -q                         PASS: 198 tests, 26.85 s
docker compose config --quiet              PASS
docker compose build --quiet               PASS: all 10 buildable service images
docker compose up -d                       PASS
promtool check config /etc/prometheus/prometheus.yml
                                           PASS: configuration + six alert rules
```

The 373 pytest warnings are NumPy/joblib array-shape deprecations, not failed
tests. The suite includes structured/partial/malformed JSON, provider status
and timeout handling, rate/concurrency/circuit limits, bounded retries, result
publish failures leaving offsets uncommitted, cache reuse, stale/forged results,
Mongo/DLQ failures, and synthetic exclusion before model training/evaluation.
The direct-script Processor import regression test is included in the 198.

The prior runtime fix retained here aliases the Docker `__main__` Processor
module to its canonical import name. Otherwise the lazy AI result import
registered metrics twice and incorrectly treated valid result envelopes as
malformed. Three affected earlier mock envelopes were recovered by exact
republication; their old DLQ documents were marked recovered, not deleted.
Fresh integration after the fix passed without that failure.

### Unchanged stored-real replay

Three previously stored real records were republished without changing listing
values, URLs or scrape timestamps. No listing contents appear in this report.

| Raw partition | Broker-acknowledged offset | Observed committed next offset |
| --- | --- | --- |
| 0 | 13479 | 13480 |
| 1 | 2170 | 2171 |
| 2 | 2053 | 2054 |

All three Mongo records matched normalized price, area and event ID and stayed
deterministic/nonsynthetic. Counts stayed at 6,564 raw and 6,530 features: URL
upserts did not create extra records. AI input high watermarks stayed
`{0:1, 1:1, 2:5}` during this exclusive replay window. Elapsed time was 1.203 s;
this small smoke test is not a throughput benchmark.

### Mock AI integration — not an external API call

`scripts/verify_agent_pipeline.py --timeout 90`, run
`smoke-c6e534d5b6b64a37`, passed in 5.266 s. Kafka, extraction validation,
Processor result consumption and MongoDB were real; only provider transport
was replaced by the script's explicit test provider.

- 17 publications across stress partitions 0/1/2 (5/6/6 messages).
- Four AI requests handled, including a duplicate reusing cached extraction.
- Exactly one successful extraction transport call, one invalid-JSON call and
  two timeout attempts (one bounded retry), all simulated.
- 15 unique raw records, 13 synthetic features and two invalid review records.
- Three terminal receipts: success, failed, failed; failures recoverable in
  MongoDB and the existing Kafka DLQ path.
- Zero writes to primary raw/features/invalid collections for this run;
  all synthetic features marked non-candidates.

An initial preflight immediately after worker recreation refused to start
because Kafka was still rebalancing; it published zero messages. After three
members and nine assignments were present, the above run passed. No code or
timeout workaround was needed.

### Actual bounded Stress Agent run

Run `final-verification-20260909`, mixed scenario, seed 42, two messages/second,
maximum 12 messages: generated=12, ACKed=12, failed=0; partitions=3/6/3;
one duplicate and five unstructured messages. Generation took 5.514 s.
The stress database held 11 unique raw/features records, all synthetic and
non-candidates, with zero primary writes. AI routing was off, so no external
costs were possible. Optional missing-field behavior with AI disabled remains
unchanged; not every incomplete record becomes an invalid-record entry.
The main training filter selected zero of these stress features.
Run reports remain under ignored `runtime/stress/` as evidence.

## Training safety and ML evidence

Safety is enforced independently at topic/origin boundaries, separate Mongo
databases, feature candidate flags, automatic/manual Mongo queries, dataset
export, and `RealEstatePriceModel.train` / `evaluate_feature_variants` in-memory
filters. JSON/manual training therefore cannot bypass the synthetic marker
filter simply by bypassing a Mongo query. Primary raw/features collections
contained zero records matching supported synthetic markers at verification.

A fresh trainer startup fitted 4,948 candidates in 26.25 s (3,958/990 split).
The observed artifact version was `20260909_052019`, holdout R² approximately
0.5911 and median absolute percentage error approximately 26.98%. These results
do not meet all aspirational PRD accuracy targets. The API reported model-ready;
a stored-record POST to the separate legacy predictor returned a finite,
positive price. This does not substitute for an authenticated frontend session.
After the rebuilt images were started, the trainer again completed on 4,948
candidates in 18.40 s; the earlier artifact/version above is an observed
verification snapshot, not a promise that the periodic trainer stops updating it.

## Runtime and logs

Compose defines 19 services: 18 long-running containers plus `kafka-init`,
which completes with exit code 0. AI and stress containers remain alive in
disabled mode after tests; Processor AI routing gates are restored to false.
After starting the rebuilt images, all 15 configured long-running healthchecks
passed; the other three running services have no Compose healthcheck.

| Services | Verified behavior |
| --- | --- |
| `kafka`, `kafka2`, `kafka3` | Healthy, IDs 1/2/3, all application replicas in sync |
| `processor`, `processor-2`, `processor-3` | Healthy, shared group with three active members |
| `mongodb`, `trainer`, `api`, `frontend`, `predictor` | Healthy; data/model/HTTP checks above |
| `ai-agent`, `stress-agent` | Healthy while disabled; zero deployed AI provider calls |
| `prometheus`, `grafana` | Healthy; seven targets up and Grafana database health OK |
| `zookeeper`, `mongo-express` | Running; no Compose healthcheck defined |
| `scraper` | Running scheduler, but live fetch fails with HTTP 403 |
| `kafka-init` | Exited 0, expected one-shot completion |

Prometheus targets: processors 8003/8004/8005, trainer 8001, AI 8006, stress
8007 and Prometheus itself. AI/stress ports are not host-published. Agent
counters are queryable; custom Agent Grafana panels have not been added.

Docker Desktop initially needed starting. Broker restarts briefly encountered
ZooKeeper ephemeral-node ownership errors and topic metadata warnings; they
recovered automatically. Prometheus briefly could not resolve `ai-agent`
during container recreation, then all seven targets became UP. The scraper's
HTTP 403 remains a real external failure. Historical recovered mock DLQ/log
entries remain. Logs have not been suppressed or deleted to appear clean.

## Remaining limitations and future verification

- Save configured provider/model/key locally using the existing `LLM_*`
  names, then perform exactly one bounded real-API request through Kafka and
  verify its result/receipt in the synthetic database. Never paste the key into
  chat, print resolved Compose config, or commit `.env`. Provider/model
  compatibility cannot be concluded until this succeeds.
- Verify authenticated FastAPI prediction and a complete browser journey with
  an operator-controlled account; no new account was created by this audit.
- Restore legitimate website access before claiming live crawling success.
- Sustained load, quota sharing across scaled AI workers and a new performance
  baseline were not tested here. Limits are per process, not global.
- Exactly-once Kafka/Mongo/API transactions, atomic cross-collection stale
  writes, cross-URL fuzzy merging, automatic DLQ replay and dedicated Agent
  Grafana panels are NOT IMPLEMENTED, not promised by the current flow.
- Brokers share a host and have no explicit persistent volumes. MongoDB and
  legacy predictor exposure are development-only security limitations.

## Scope of this completion pass

No architecture was redesigned, no service/topic was added and no application
code was changed in this final verification pass. The previously implemented
Processor import fix was retained and tested. Documentation corrections cover
this evidence, safe `config --quiet` commands, the seven-topic/nine-assignment
flow, standalone-versus-Compose environment settings and actual host exposure
of the legacy predictor. Pre-existing local changes and document deletions were
preserved; no existing project files were deleted. Temporary Mermaid render
inputs/output were removed after both diagrams rendered successfully; the
editable Markdown and full-size project SVG remain in the repository.

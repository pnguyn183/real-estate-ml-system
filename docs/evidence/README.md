# Validation evidence

These dated results support the [Project Report](../PROJECT_REPORT.md).
Each document identifies the tested configuration, measurements and limits of
the conclusion. A saved configuration or test plan is not a completed experiment.

## Pipeline and control

| Validation | Evidence | Scope |
| --- | --- | --- |
| Pipeline and source ingestion, 2026-09-09–10 | [Pipeline validation](PIPELINE_VALIDATION_20260910.md) | Kafka replay, synthetic isolation, mock extraction, serving and bounded Homedy trial |
| Docker resource control, 2026-10-06 | [Resource validation](LLM_FEEDBACK_VALIDATION_20261006.md) | Apply/rollback and safety guards; no overload-effectiveness claim |
| LLM feedback and fallback, 2026-10-07 | [Feedback validation](LLM_FEEDBACK_VALIDATION_20261007.md), [measurements](llm-feedback-20261007.json) | Real low-load decisions and provider failover |
| Overload attempts, 2026-10-13 | [Validation](LLM_OVERLOAD_VALIDATION_20261013.md), [measurements](llm-overload-20261013.json) | Interrupted trials and policy-write failure diagnosis |
| Overload calibration, 2026-10-14 | [Validation](LLM_OVERLOAD_VALIDATION_20261014.md), [measurements](llm-overload-20261014.json) | Measured saturation and incomplete paired comparison |
| Memory-limited trial, 2026-10-19 | [Validation](LLM_OVERLOAD_VALIDATION_20261019.md), [measurements](llm-overload-20261019.json) | Host-memory stop before the LLM/overload phases |
| Memory-limited calibration, 2026-11-14 | [Measurements](llm-overload-20261114.json) | 9,747 committed records at up to 300 rps; host-memory guard stopped before overload/LLM phases |
| Memory-limited calibration, 2026-11-18 | [Measurements](llm-overload-20261118.json) | Core-only calibration stopped at the 10 rps stage when host RAM crossed below 10%; no LLM decision or A/B pair |
| Container lifecycle, 2026-11-04 | [Checks](container-lifecycle-20261104.json) | Unit tests, Docker observation and provider timeout |
| Container lifecycle, 2026-11-05 | [Live decisions and restoration](container-lifecycle-live-20261105.json) | Two LLM-selected stops; restoration verified by script on 2026-11-11; LLM restart and active-load stability were unproven in this run |
| Container lifecycle recovery, 2026-11-14 | [Live stop/start recovery](container-lifecycle-recovery-20261114.json) | Same-ID LLM stop/start of mongo-express, then three healthy small-load windows; not an overload-capacity result |
| Ops revalidation, 2026-11-18 | [Promtool, drain and lifecycle scope](ops-validation-20261118.json) | Promtool passed; one same-ID predictor stop/start and isolated trainer drain ACKs observed; no repeated-cycle or causal-RAM claim |
| Overload calibration retry, 2026-11-20 | [Guard-stopped baselines](llm-overload-20261120.json) | Stronger core-only retry reached 300 rps and committed 8,154/8,154 records, but RAM crossed below 10% before measurable overload/600 rps; no profile, LLM decision or A/B trial exists |
| Memory-headroom calibration, 2026-11-22 | [Guard-stopped attempt](llm-overload-20261122.json) | Baseline stopped at 10 rps when host RAM reached 5.38%; 42/42 records committed and all seven isolated services were restored. No LLM call/A-B claim; a separate 25% startup headroom gate was added and tested afterward |
| Lean-topology overload A/B, 2026-11-23 | [Calibration, paired result and hashes](llm-lean-overload-20261123.json) | One-broker/one-processor calibration reached 300 rps; three eligible pairs contained real LLM rate decisions, but 0/3 demonstrated improvement because served fraction regressed/trade-offs remained. Final provider-unavailable attempt is inconclusive, not a RAM failure |

## Telemetry and forecasting

| Validation | Evidence | Scope |
| --- | --- | --- |
| History inventory, 2026-10-23 | [Assessment](TELEMETRY_VALIDATION_20261023.md), [manifest](telemetry-status-20261023.json) | Existing sessions, file hashes and usable chronological splits |
| Collection and forecasts, 2026-10-24 | [Validation](TELEMETRY_VALIDATION_20261024.md), [measurements](telemetry-status-20261024.json) | Memory-limited collection, five-minute forecast result and ten-minute forecast limitations |
| Forecast plots, 2026-10-24 | [Five minutes](telemetry-forecast-300s-20261024.png), [ten minutes](telemetry-forecast-600s-20261024.png) | Held-out predictions from the documented benchmark |
| Two-hour telemetry session, 2026-11-14 | [Measurements](telemetry-session-20261114.json) | 1,446 healthy synthetic-traffic observations, 219,434 ACKs and clean restoration |
| Two-hour offline forecast evaluation, 2026-11-17 | [Measurements and hashes](telemetry-forecast-2h-20261117.json) | Chronological 5/10-minute forecasts beat persistence on one scripted synthetic run; no production/predictive-control claim |
| Predictive-control readiness, 2026-11-18–21 | [Historical fail-closed result](predictive-control-readiness-20261118.json), [bound re-export result](predictive-control-readiness-20261121.json), [protocol](../PREDICTIVE_CONTROL_EXPERIMENT.md) | Re-export resolved support/split/fingerprint provenance blockers; trial remains blocked because overload calibration/rate are missing, and forecast remains outside production control |

## Model and extraction evaluation

| Validation | Evidence | Scope |
| --- | --- | --- |
| Legacy price MdAPE derivation, 2026-11-17 | [Measurements and prediction hash](price-benchmark-mdape-20261117.json) | Derived from 996 saved holdout predictions without refitting; leakage and local-only source artifact remain |
| Price dataset preparation, 2026-11-18 | [Leakage/dedup/group-split evidence](price-dataset-preparation-20261118.json) | 4,972 rows after 8 exact duplicates; conservative 4,683-group split has zero key overlap; no new model score/generalization claim |
| Clean price diagnostic, 2026-11-18 | [Grouped holdout metrics and hashes](price-benchmark-grouped-20261118.json) | 3,491/750/731 rows, seed 42; XGBoost numerically best in this single diagnostic, not a deployment/generalization claim |
| Price selection, 2026-11-21 | [Group-CV selection metrics and hashes](price-selection-group-cv-20261121.json) | Four candidates selected by 3-fold property-group CV on 4,241 development rows; only XGBoost opened the 731-row outer test; no deployment/generalization claim |
| Extraction annotation panel, 2026-11-18 | [Protocol](../EXTRACTION_EVALUATION.md), [panel and manifest](extraction-panel-20261118/manifest.json), [label schema](../templates/extraction-ground-truth.schema.json) | 34 explicitly-real unique records and blinded packets for two reviewers plus adjudication; human reviews remain pending, so no accuracy result exists |
| Project validation, 2026-11-18 | [Command/results summary](project-validation-20261118.json) | 1,419 tests plus frontend/build/config/preview, benchmark, privacy/hash and promtool checks |
| Final validation, 2026-11-20 | [Command/results summary](project-validation-20261120.json) | 1,422 tests and final provenance/safety checks; unchanged frontend and promtool results are referenced, not rerun |
| Project validation, 2026-11-21 | [Code, benchmark, browser and safety checks](project-validation-20261121.json) | 1,470 tests; full price group-CV, browser E2E and production dependency audit passed; predictive gate remained blocked and Docker live work was not run |
| Project validation, 2026-11-22 | [Code, live guard and Promtool checks](project-validation-20261122.json) | 1,473 tests; control subset, compile, JSON, report links, Compose and Promtool passed. Calibration was safely guard-stopped, so no LLM overload claim |

Raw records and experiment outputs referenced under `runtime/` remain local and
are excluded from Git. JSON summaries retain relevant measurements, action
receipts and hashes where available. Historical observations describe their test
dates; current operating instructions are in the [runbook](../RUNBOOK.md).

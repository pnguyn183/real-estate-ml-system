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
| Container lifecycle, 2026-11-04 | [Checks](container-lifecycle-20261104.json) | Unit tests, Docker observation and provider timeout |
| Container lifecycle, 2026-11-05 | [Live decisions and restoration](container-lifecycle-live-20261105.json) | Two LLM-selected stops; restoration verified on 2026-11-11; automatic restart and active-load stability unproven |

## Telemetry and forecasting

| Validation | Evidence | Scope |
| --- | --- | --- |
| History inventory, 2026-10-23 | [Assessment](TELEMETRY_VALIDATION_20261023.md), [manifest](telemetry-status-20261023.json) | Existing sessions, file hashes and usable chronological splits |
| Collection and forecasts, 2026-10-24 | [Validation](TELEMETRY_VALIDATION_20261024.md), [measurements](telemetry-status-20261024.json) | Memory-limited collection, five-minute forecast result and ten-minute forecast limitations |
| Forecast plots, 2026-10-24 | [Five minutes](telemetry-forecast-300s-20261024.png), [ten minutes](telemetry-forecast-600s-20261024.png) | Held-out predictions from the documented benchmark |

Raw records and experiment outputs referenced under `runtime/` remain local and
are excluded from Git. JSON summaries retain relevant measurements, action
receipts and hashes where available. Historical observations describe their test
dates; current operating instructions are in the [runbook](../RUNBOOK.md).

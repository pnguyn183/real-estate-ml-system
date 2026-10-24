# Telemetry follow-up — 2026-10-24

**The additional two-hour collection is still incomplete.** A separate session
started on October 23 local time recorded about 27 minutes before its Docker VM
memory guard stopped admission. This session was discovered after the
[October 23 inventory](TELEMETRY_STATUS_20261023.md); it does not replace that
historical assessment or complete the saved collection plan.

The [evidence file](evidence/telemetry-status-20261024.json) records raw artifact
hashes, accounting, memory samples and the deployment-verification limitation.
The raw session is
`runtime/research/traffic-sessions/session-20261022T233647.626631Z-8f23060b`.

| Measurement | Recorded result |
| --- | --- |
| Requested / actual load duration | 7,200 / 1,602.453 seconds |
| Observation span, including drain | 1,619.140 seconds |
| Observations / healthy target observations | 327 / 322 |
| Longest uninterrupted healthy segment | 1,254.215 seconds, about 20m54s |
| Collection-error rows | 3 |
| Offered / admitted and acknowledged | 49,064 / 48,094 |
| Rejected / delivery failures / final Kafka lag | 970 / 0 / 0 |
| Stop condition | Docker VM available memory 8.23%, below the 10% guard |
| Optional-service restoration | Completed; no recorded restoration errors |

This session used seed `1378811262` and fixed baseline admission, with synthetic
variable demand. The saved, unstarted plan used seed `20261023`; they are separate
runs. The observation audit has no timestamp gaps or duplicate timestamps, but
unavailable/recovery observations split the usable history. Sample counts alone
cannot establish natural traffic patterns, forecast accuracy or LLM control
effectiveness. The offline forecast evaluation below adds a measured result for
the existing controlled history.

## Completed offline forecast evaluation

With Docker off, seven finalized sessions were exported into 1,732 observations
and evaluated using the existing chronological benchmark. The stale October 1
session was excluded. The target is synthetic offered demand (`requested_rate`),
not natural incoming traffic. The export preserves original timestamps and run
IDs; features and future labels do not cross runs, gaps or unavailable intervals.

The benchmark uses 60- and 120-second historical features, 100 trees and one job.
It selects a candidate by the lowest validation RMSE, then evaluates that same
choice on the later test set. Training labels strictly precede the next split's
observations. Export requires at least 5% RMSE improvement over persistence and
positive R² on both validation and test. Persistence means predicting that the
current requested rate will remain unchanged.

| Horizon | Selected model | Validation RMSE: model / persistence | Test RMSE: model / persistence | Test R² | Result |
| --- | --- | --- | --- | --- | --- |
| +5 minutes | XGBoost | 4.554 / 27.587 | 8.588 / 27.697 | 0.699 | Passed for experimental inference only |
| +10 minutes | Gradient Boosting | 15.175 / 28.314 | 16.813 / 27.894 | -0.084 | Failed export criterion |

Recorded comparison charts: [+5 minutes](evidence/telemetry-forecast-300s-20261024.png)
and [+10 minutes](evidence/telemetry-forecast-600s-20261024.png).

RMSE units are records/second. The five-minute model reduces test RMSE by 68.99%
relative to persistence. Its outer train/test sizes are 676/185, with 531/136
rows in the selection-training/validation split. The ten-minute sizes are
397/130 and 283/80 respectively. Its negative test R² prevents export even though
it beats persistence. The five-minute XGBoost choice is retained even though
Gradient Boosting has a lower test RMSE; the test set does not choose a replacement.

The five-minute artifact is saved at
`runtime/research/telemetry-evaluation-20261024/forecast/horizon_300s/forecast.joblib`.
No ten-minute artifact was exported. The evidence JSON records the input,
selection, metrics, split and artifact hashes. A separate check recomputed
selected-model and persistence metrics from saved predictions and confirmed the
temporal split boundaries and validation-based selection.

This completes an offline evaluation of existing measurements. The result does
not complete the requested two-hour collection, establish production or seasonal
forecasting accuracy, or prove that an LLM stabilizes an overloaded pipeline.

## Memory and deployed code

VM available memory fell from 56.64% initially to 8.23% at the stop observation;
one subsequent drain sample reached 6.95%. VM swap use grew from 0.07% to 64.84%.
MongoDB grew from about 685 MiB to 2,147 MiB, and the three Kafka brokers together
grew from about 1,306 MiB to 2,796 MiB. Processor memory also spiked together:
roughly 586–609 MiB each at 320 seconds, and 530–541 MiB each at the stop.
After draining, processor memory fell to about 116–121 MiB each.

The early processor spikes near 12, 320 and 622 seconds are consistent with the
configured 300-second anomaly-history refresh. This is a hypothesis supported by
timing and memory correlation, not a measurement proving which code path caused
the pressure. Lowering new input alone does not remove existing history scans or
the existing database and broker memory footprint.

The manifest records workspace commit `acb8803`, which contains the streaming
history fix. However, its processor container IDs and image hashes match those
recorded before that fix in the October 19 experiment. Its worker source hashes
cover only `kafka_to_mongo.py`, not `price_anomaly.py`. Thus the manifest does not
verify that the running processors used the streaming fix. A new image tag alone
does not establish which image a running container used. Verify the deployed
dependency hash before the next run, and recreate processors from the updated
image if necessary.

## Current continuation

During the October 24 continuation, the coordinating agent observed host RAM
fall from about 3.25 GiB available before Docker startup to about 0.01 GiB
(0.09%) afterward. No new workload collection was started. Docker Desktop was
stopped successfully to return to its initial off state; available host RAM then
recovered to 4.25 GiB (27.06%). The checks are saved in
`runtime/research/telemetry-preparation-20261024/readiness.json`.
This host-memory observation is
distinct from the historical session's Docker VM memory guard.

A later read-only check at 18:36 local time found 4.959 GiB (31.56%) of
available host memory, but the Docker Linux engine was still unavailable.
That check did not start Docker or a new collection; the earlier startup
pressure remains an observation from the previous attempt, not the current
host-memory reading.

Once the runtime can remain available with sufficient memory and the deployed
source is verified, a smaller varied profile is ready in
`runtime/research/telemetry-preparation-20261024/plan.json`, with its launch
metadata explicitly marked `prepared_not_started`:

```powershell
python -m research.collect_session start --hours 2 --lean --min-rate 5 --max-rate 20 --seed 20261024 --name telemetry-20261024
```

This profile has 120 one-minute stages, projects 85,961 input records and targets
about 1,440 five-second observations. Its generated rates range from 5.2182 to
18.9268 records/second. It preserves the existing memory and telemetry guards.
It reduces incoming work but does not guarantee that the existing runtime fits
in available memory. It has **not** been started, and this document does not
count planned samples as collected data.

The matched baseline-versus-LLM overload experiment remains separately pending;
the user's current instruction excludes that RAM-intensive work.

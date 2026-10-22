# Telemetry history assessment — 2026-10-23

**Readiness is not established.** Recorded history can support some chronological
forecast evaluations, but no recent forecast validation or completed comparison
showing LLM overload control is available. Collecting for two hours alone does not
establish either result.

The [verified inventory](evidence/telemetry-status-20261023.json) records raw-file
SHA-256 hashes, session status and structural split counts. This assessment read
the existing files; it did not start Docker, publish traffic, call an LLM or fit
models.

## Available history

| History | Observations | Usable target observations | Finding |
| --- | ---: | ---: | --- |
| Seven controlled traffic sessions | 1,623 | 1,609 | Synthetic offered demand with real pipeline measurements; separate sessions, not a continuous trace |
| Latest session, October 9 | 708 | 702 | Planned two hours; recorded 58m43s, then stopped for VM memory pressure |
| Two older passive histories | 5,209 | 2,318 | Every healthy ingress value is zero; 2,889 rows contain collection errors |

The latest session has 60 distinct healthy requested-rate values, but its longest
uninterrupted healthy segment is only 25 minutes. Three collection-error rows and
three recovery observations split the history. Its recorded stop condition was
Docker VM swap usage of 98.54% and free memory of 4.96%.

The October 1 session contains 218 healthy observations, spanning about 18m05s.
Its persisted metadata still says `running` and lacks final audit/closure. Those
rows are preserved and listed separately; that status does not prove a collector
is still running. All existing audit hashes for the controlled session files
match the raw files.

## What can be evaluated

These are lightweight structural counts following the benchmark's chronological
split rules, with 120 seconds of past history and a purge of labels crossing each
split. No feature matrix or model score was computed. The canonical benchmark
must still be run before claiming forecast skill.

| Dataset and horizon | Outer train / test | Selection train / validation | Count requirement |
| --- | ---: | ---: | --- |
| October 9 only, +5 minutes | 247 / 77 | 161 / 50 | Met |
| October 9 only, +10 minutes | 101 / 52 | 0 / 21 | Selection split insufficient |
| All finalized controlled sessions, +5 minutes | 543 / 151 | 374 / 109 | Met |
| All finalized controlled sessions, +10 minutes | 384 / 108 | 283 / 77 | Met |

The requirement is at least 100 training rows and 30 evaluation rows at each
stage, with varying targets. Passing these counts is permission to evaluate,
not a successful forecast. The existing benchmark selects a model on validation
data and requires it to beat persistence on validation and untouched test data.
Do not join gaps, error intervals, topics or different runs into artificial
continuous history. The combined rows remain synthetic workload evidence and
cannot establish daily/weekly patterns or production generalization.

## Additional two-hour collection

At this assessment Docker is off, and the coordinating agent measured only about
0.57 GiB of available host RAM. **No additional collection was started.** Starting
the pipeline is deferred under the user's instruction to exclude RAM-dependent
work. A collector pointed at stopped Docker services would only record failures.

The saved dry-run plan is
`runtime/research/telemetry-preparation-20261023/plan.json`; its accompanying
`launch.json` explicitly records `prepared_not_started`. The plan covers 7,200
seconds and 220,036 projected input records. No collector or waiting process was
started. Before the actual run, rebuild the processor image to include the newly
tested streaming fix; Docker image rebuilding was also deferred.

Once the existing pipeline is available with sufficient memory, the bounded
variable-workload command is:

```powershell
python -m research.collect_session start --hours 2 --lean --min-rate 5 --max-rate 60 --seed 20261023 --name telemetry-20261023
```

This retains the existing resource guards, creates a new session and targets
roughly 1,440 samples at five-second intervals. Startup, queue drain and audit add
wall time. Lean mode temporarily stops optional running services and restores
their recorded container IDs afterward. The workload is synthetic, and the
session makes no LLM calls or resource-allocation changes. Inspect its final
`session.json` and `history_audit.json`; a requested duration is not proof that
the whole duration completed.

For passive measurements of an already-running crawl pipeline, use:

```powershell
python -m research.telemetry --output runtime/research/traffic-history/passive-20261023/observations.jsonl --samples 1440 --interval 5 --duration-seconds 7200 --topic real_estate_raw --group-id real_estate_training_pipeline --run-id passive-20261023
```

Passive collection creates no traffic and may produce another constant idle
trace. The two-hour history task remains pending until measurements are actually
recorded. Forecast history also remains separate from the unfinished matched
baseline-versus-LLM overload experiment.

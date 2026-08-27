# Battery Health Benchmark

A behavior-driven, reproducible benchmark for battery state-of-health prediction.

## Status

The project has completed the first three behavior-driven requirements:

- FR-01: import and normalize configured cycle CSV files
- FR-02: reject duplicate cycles and non-positive capacities with a quality report
- FR-03: create deterministic cell-level splits and reject cell leakage

MVP decisions:

- Target: SOH prediction
- First real dataset: MATR
- Interface: command line only
- Current executable data: synthetic test fixtures

## Development method

Behavior is specified in `features/` using Given/When/Then scenarios. Each implemented
scenario is bound to an executable acceptance test under `tests/bdd/`.

## Safety and data policy

Only public data and synthetic fixtures may be used. Raw battery data, credentials,
company material, unpublished patent details, and machine-specific absolute paths must
not be committed.

## Planned quick start

```powershell
uv sync
uv run pytest -q
uv run battery-health --help
```

The CLI and end-to-end demo will be enabled incrementally as their scenarios are implemented.
The next behavior is FR-04, explicit SOH label generation from measured and reference capacity.

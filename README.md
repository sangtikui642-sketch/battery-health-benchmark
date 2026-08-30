# Battery Health AutoBench

A behavior-driven, reproducible benchmark for battery state-of-health prediction.

> Local release status: `v0.2.0rc1` open-source release candidate under the MIT License. The
> repository contains local verification evidence; GitHub-hosted CI, a Git tag, a GitHub Release,
> and PyPI publication are not claimed until separately authorized and observed.

Release governance is documented in [CHANGELOG.md](CHANGELOG.md),
[CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md),
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), [CITATION.cff](CITATION.cff), and
[docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md).

Local release verification currently records 158 passing tests, including 47 executable BDD
tests, zero skipped or xfailed tests, and 87.70% package coverage. The built wheel and source
archive both carry version `0.2.0rc1`; the wheel includes `battery_health/py.typed`, MIT package
metadata, and the license file. These figures are software-verification evidence, not battery
accuracy or production BMS claims.

## Status

The project has completed the eight behavior-driven MVP requirements:

- FR-01: import and normalize configured cycle CSV files
- FR-02: reject duplicate cycles and non-positive capacities with a quality report
- FR-03: create deterministic cell-level splits and reject cell leakage
- FR-04: generate traceable SOH labels without guessing reference capacity
- FR-05: train three registered baselines with train-only preprocessing
- FR-06: evaluate held-out predictions with traceable metrics, predictions, and plots
- FR-07: retain portable run provenance and verify deterministic predictions
- FR-08: reject credential patterns and disallowed raw-data locations

The next AutoBench milestones are also implemented:

- FR-09: create an immutable, fingerprinted experiment plan without training candidates
- FR-10: execute every locked candidate on validation data with bounded failure isolation
- FR-11: apply mandatory validation gates, lock one candidate without test access, and run one
  held-out test after locking
- FR-12: build a deterministic, self-verifying evidence bundle with machine-resolvable claims
- FR-13: approve, reject, or quarantine complete model-plugin declarations before planning

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

## Reproduce the offline demo

```powershell
uv sync --frozen
uv run battery-health benchmark --config configs/demo.yaml --output results/demo
```

The command creates an anonymous deterministic dataset and runs import, data-quality checks,
cell-level splitting, SOH labelling, all three registered baselines, evaluation, repository
safety checks, and provenance capture without network access during the benchmark.

The evidence directory contains:

- effective and source configurations, the dependency lock, and the shared split manifest;
- data-quality and SOH-label provenance;
- per-model training metadata, predictions, metrics, and actual-versus-predicted PNG plots;
- a repository-safety report, SHA-256 reproducibility manifest, final evidence index, and
  limitations.

The final report deliberately does not select a model from held-out test metrics.

## Govern model plugins before planning

FR-13 validates complete plugin declarations against a reviewed compatibility and license policy
before any plan or training artifact exists:

```powershell
uv run battery-health agent govern-plugins --catalog configs/plugin_catalog.quarantine-example.json --policy configs/plugin_governance_policy.example.json --output governance/demo
uv run battery-health agent verify-plugins --bundle governance/demo
```

Each declaration must identify its implementation version, target, feature names and units,
training/prediction capabilities, deterministic seed behavior, dependencies, accelerator and
memory requirements, code and weight licenses, upstream source and citation, bounded search space,
serialization/loading contract, and execution adapter.

The command produces deterministic per-plugin decisions plus `approved_registry.json` and
`external_references.json`. Compatible and policy-approved plugins become `APPROVED`; incompatible
plugins become `REJECTED`; missing, unknown, or policy-incompatible licenses become `QUARANTINED`.
Quarantined plugins are reference-only: execution, default enablement, bundling, and redistribution
approval are all false. The governance bundle contains JSON declarations only and never downloads
or copies plugin code or weights.

The supplied catalog is intentionally safe by default and produces one quarantined external
reference with an empty approved registry. Replace it only with declarations and license decisions
that have been independently reviewed. A non-empty governed registry can be passed directly to
FR-09 planning.

## Create an auditable experiment plan

FR-09 consumes a completed benchmark evidence bundle, an explicitly approved model-plugin
registry, and a resource/selection policy. It validates evidence hashes, target metadata,
feature units, leakage-safe partitions, candidate compatibility, licenses, and budgets before
creating a plan:

```powershell
uv run battery-health agent plan --evidence results/demo --registry governance/reviewed/approved_registry.json --policy configs/agent_policy.yaml --output plans/demo
```

The command creates `plan.json` plus immutable registry, policy, and split snapshots. The plan records
the target, data and split fingerprints, feature units, compatible candidate versions and
licenses, train-only preprocessing, selection rules, gates, seeds, uncertainty method, and
resource limits. It never trains a candidate or reads held-out test metrics.

The repository intentionally does not ship an executable default registry. The repository's MIT
license does not approve or relicense third-party plugin code or weights. Users must provide an
explicitly reviewed registry; unknown or undeclared code and weight licenses are rejected instead
of being guessed.

## Execute the locked candidates

FR-10 verifies the plan and evidence fingerprints again, then executes every candidate and
predeclared seed in an isolated child process:

```powershell
uv run battery-health agent execute --plan plans/demo --evidence results/demo --output executions/demo
```

Candidate preprocessing is fitted on train cells and candidate metrics are calculated on
validation cells only. Held-out test predictions are not created. Each candidate receives a
portable record containing its plan, split, implementation and license fingerprints, seed,
artifacts, resource usage, and structured failure status. A dependency failure or timeout does
not terminate unrelated candidates.

Wall-clock limits are enforced by terminating the candidate child process. Python allocation
peaks are measured with `tracemalloc` and checked after each run; native-library RSS is not yet
hard-limited and is recorded as a current measurement limitation.

## Validate and lock a candidate

FR-11 reads only the execution bundle. Its public validation API does not accept the original
labelled evidence directory, so held-out test labels cannot be supplied to model selection:

```powershell
uv run battery-health agent validate --execution executions/demo --output validations/demo
```

The validator rechecks the immutable plan and split snapshot, execution input hash, cell-level
partition isolation, train-only preprocessing metadata, prediction coverage and finiteness,
configured SOH bounds, required metrics, resource limits, and licenses. It recomputes metrics
from validation predictions and calculates the predeclared confidence interval by bootstrapping
cell identities. Failed and ineligible candidates remain visible in `candidate_validations/` and
`leaderboard.json`.

If at least one candidate passes every gate, deterministic metric and tie-break rules create a
fingerprinted `selection_lock.json`. Otherwise the run becomes `REJECTED` and no lock is written.

## Finalize the held-out test once

Only a verified selection lock authorizes access to the original evidence containing test labels:

```powershell
uv run battery-health agent finalize --validation validations/demo --evidence results/demo --output finalizations/demo
```

Before reading the labelled evidence, finalization creates an exclusive claim for the run identity.
The locked plugin, feature contract, bounded parameters, preprocessing partition, and predeclared
finalization seed are then evaluated on the test cells. A receipt links test predictions and metrics
to the selection fingerprint. Reusing the same validation bundle is rejected even when a different
output directory is requested.

## Build and verify the deterministic evidence bundle

FR-12 consolidates the immutable plan, execution, validation, and optional finalization evidence
into one portable reporting bundle. It does not call an LLM and does not require network access:

```powershell
uv run battery-health agent report --evidence results/demo --execution executions/demo --validation validations/demo --finalization finalizations/demo --output reports/demo
uv run battery-health agent verify --bundle reports/demo
```

Omit `--finalization` for a `LOCKED` or `REJECTED` validation run. The report command verifies the
full upstream identity and hash chain before copying artifacts. It writes a strict
`evidence_manifest.json`, a deterministic `report.md`, and portable evidence files whose SHA-256
hashes are recorded in the manifest. Every quantitative statement records both an artifact ID and
a JSON Pointer to the exact numeric value that supports it.

The offline verifier recomputes the manifest fingerprint, every bundled artifact hash, every
claim-to-JSON value, and the human-readable report. Changing a metric, artifact, claim, or report
therefore makes verification fail. `LOCKED` and `REJECTED` bundles retain validation and resource
evidence but expose no held-out test claim; only a verified `FINALIZED` bundle may contain final
test metrics.

## Current limitations

- The executable demo uses synthetic data; the MATR real-data adapter is not implemented.
- Trained model serialization and loading are not implemented.
- Finalization deterministically retrains the locked built-in model and seed because exact model
  serialization is not yet available; it does not claim byte-identical model-object reuse.
- The one-test rule is enforced by an exclusive claim in the authoritative validation bundle; this
  release does not provide a remote run registry or cryptographic signature service.
- The evidence bundle retains the labelled-dataset SHA-256 digest, not the complete SOH-labelled
  source file, to avoid redistributing held-out labels through reporting.
- The current code fingerprint covers the locked planner/plugin descriptors and reporting code; it
  is not yet a full source-tree or signed-build attestation.
- Trained-model size and a dedicated inference-latency benchmark are not available until model
  serialization is implemented. Execution records wall time and Python allocation peaks; native
  RSS is not a hard limit.
- Governance verifies declarations and a local review policy; it does not replace legal review,
  retrieve license texts, resolve dependency environments, or certify upstream claims.
- The current executor supports only the three built-in scikit-learn adapters and rejects non-empty
  parameter overrides. Governing an external adapter or serialization contract does not implement
  that adapter or serializer.
- Synthetic benchmark metrics do not establish real-world battery accuracy or BMS readiness.

## License and citation

Project source code and documentation are available under the [MIT License](LICENSE), copyright
2026 赵一冰. Cite release candidate `0.2.0rc1` using [CITATION.cff](CITATION.cff).

The project license does not relicense external datasets, papers, model implementations, model
weights, or other third-party material. The repository does not bundle MATR data or quarantined
plugin code and weights.

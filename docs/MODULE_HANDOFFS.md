# AutoBench Module Handoffs

This document records what each completed AutoBench module does, how it works, what proves it,
and what it does not justify claiming. A section is added only after its focused BDD path is green.

## FR-11 - Leakage-safe selection and one-shot final validation

### Problem and product role

FR-10 can run several candidates, but a collection of validation metrics is not yet a governed
decision. FR-11 turns that evidence into one defensible selection without allowing test results to
influence the decision. It then authorizes one held-out test evaluation for the locked identity.

The module answers two separate questions:

1. Which candidate is supported by validation evidence and the predeclared policy?
2. After that decision is immutable, how does the selected candidate perform on held-out test cells?

Keeping these questions in separate APIs is the main leakage-control boundary.

### Inputs, outputs, and state transitions

Validation input:

- an FR-10 execution directory;
- its copied immutable plan, registry, policy, and split snapshots;
- fingerprinted train-validation input;
- candidate records, validation predictions, metrics, training metadata, and resource records.

Validation deliberately has no `evidence_dir` parameter. It cannot be given the original labelled
file containing test rows. Its outputs are:

- `candidate_validations/<plugin>.json`;
- `leaderboard.json` in `VALIDATED` state;
- `selection_lock.json` when at least one candidate passes;
- `validation_manifest.json` in `LOCKED` or `REJECTED` state;
- a copied immutable `plan/` bundle.

Finalization input:

- the authoritative `LOCKED` validation directory;
- the original fingerprinted evidence directory;
- a new output directory.

Finalization writes an exclusive `finalization_claim.json` before labelled evidence is loaded. A
successful run adds `finalization_receipt.json` to the validation bundle and writes a new final
bundle containing test predictions, metrics, plot, training metadata, plan, selection lock, and
`finalization_manifest.json`.

The recorded state sequence is:

```text
RUNNING -> VALIDATED -> LOCKED -> FINALIZED
                    |
                    -> REJECTED
```

### Core code and schemas

- `validate_and_lock_execution`: validates an execution bundle and optionally locks a winner.
- `finalize_locked_selection`: claims and performs the one held-out test evaluation.
- `verify_selection_lock`: recomputes the canonical decision fingerprint.
- `ValidationPlan`: strict complete view of the immutable plan.
- `ExecutionManifest` and `CandidateRecord`: strict FR-10 evidence inputs.
- `GateDecision`: one machine-readable mandatory decision.
- `SelectionLock`: the selected plugin, implementation, configuration, seed, policy, evidence
  hashes, winner metric, uncertainty, and canonical fingerprint.
- `ValidationResult` and `FinalizationResult`: typed public return values.

Unknown top-level schema fields are rejected. Portable evidence paths are rejected if absolute or
if they escape their bundle root.

### Algorithms, statistics, and engineering techniques

Selection uses the primary validation metric and direction already stored in `plan.json`. Ties are
resolved only by the ordered, predeclared tie-break list. The current built-in profile supports
metric tie-breaks and `plugin_id_lexicographic`. Unsupported rules fail instead of being guessed.

The point metrics are recomputed from the retained validation predictions rather than trusted from
candidate JSON. Recorded metrics must match the recomputation within the deterministic tolerance.
MAE, RMSE, MAPE, and R-squared are aggregated over declared seeds.

The confidence interval resamples battery-cell identities with replacement. All cycles belonging
to a sampled cell remain together, so the interval does not pretend that correlated cycles are
independent observations. Confidence level, resample count, and RNG seed come from the immutable
plan. No wall-clock time participates in the selection fingerprint.

Seven candidate gates are applied:

1. split and candidate identity consistency;
2. train-only preprocessing;
3. finite, complete validation predictions with no test access;
4. configured SOH bounds without clipping or correction;
5. required metrics and cell-bootstrap uncertainty;
6. runtime and measured-memory policy;
7. known code and weight license declarations.

Run-level gates additionally verify the plan, execution-input hash, execution contract, and test
isolation. A failing candidate remains on the leaderboard. A run-level failure or zero eligible
candidates produces `REJECTED` and no selection lock.

The selection fingerprint uses SHA-256 over canonical, sorted JSON. The selected candidate,
implementation version, feature contract, bounded parameters, finalization seed, selection policy,
leaderboard hash, candidate-validation hash, and execution-manifest hash are included.

### BDD scenarios and failure paths

Six executable scenarios cover:

- validation-only ranking and an immutable lock;
- a physically implausible candidate that cannot win;
- one held-out test after locking;
- all candidates failing mandatory gates;
- execution evidence claiming test access;
- CLI validation, finalization, identity output, and repeat rejection.

Ten focused tests add missing predictions, non-finite predictions, overlapping cells, input-hash
tampering, candidate-record tampering, deterministic bootstrap, deterministic tie-breaking,
non-overwriting output, lock tampering, and claim ordering.

### Quantitative evidence

FR-11 focused evidence:

```text
BDD: 6 passed
Additional unit/integration: 10 passed
Full repository: 82 passed
Executable BDD total: 30
Skipped: 0
Xfailed: 0
Package coverage: 86.12%
validation.py coverage: 88%
Strict mypy: 18 source files, 0 issues
```

These are software-verification metrics. They are not battery-accuracy claims.

### Known limitations and unsupported claims

- Current executable acceptance data is synthetic. It does not demonstrate MATR accuracy or BMS
  production readiness.
- Models are not serialized yet. Finalization retrains the locked built-in algorithm with the
  locked features, preprocessing contract, bounded parameters, and seed. It does not reuse a
  byte-identical fitted object.
- The exclusive claim prevents a second evaluation through the authoritative validation bundle.
  There is no remote transaction service; copying evidence outside the governed run directory is
  not a cryptographic access-control mechanism and must be treated as a new, untrusted run.
- Python allocation peaks are checked after execution; native-library RSS is not a hard limit.
- The repository license is not selected. This blocks a formal open-source release.
- FR-12 consolidated evidence reporting and FR-13 complete plugin governance remain separate work.

### Interview explanation question

> Why is selecting the lowest test MAE data leakage even if the test set was never used to fit the
> model, and how does AutoBench prove that this did not happen?

A strong answer should distinguish parameter fitting, model selection, and final generalization
estimation; explain the validation-only API boundary; and cite the lock, evidence hashes, state
machine, and one-time test claim.

### Live modification question

> Add a predeclared tie-break that prefers lower measured prediction latency after equal MAE and
> RMSE. Which plan field, schema, gate, fingerprint field, test fixture, and BDD scenario must change?

The candidate should preserve validation-only ranking, reject missing latency rather than inventing
it, and demonstrate that the new value participates in the selection fingerprint.

## FR-12 - Evidence bundle and deterministic reporting

### Problem and product role

FR-09 through FR-11 produce a chain of separate planning, execution, validation, selection, and
finalization artifacts. FR-12 turns that chain into one portable release artifact without trusting
an LLM to summarize the result. The bundle answers three audit questions:

1. Which exact inputs, plan, code/dependencies, plugins, and seeds produced the run?
2. Which candidates succeeded, failed, timed out, or failed a mandatory validation gate?
3. Can every reported number be resolved to a protected machine-readable value and verified
   offline?

The report is a deterministic view of retained evidence, not a new source of truth. It cannot
change selection, run a candidate, authorize finalization, or infer missing metrics.

### Inputs, outputs, and state rules

`generate_evidence_bundle` accepts the original benchmark evidence, FR-10 execution bundle,
FR-11 validation bundle, an optional finalization bundle, and a new output directory. Before it
writes output, it verifies the plan fingerprint, benchmark reproducibility manifest, execution
identity and candidate-record hashes, validation identity and candidate-validation hashes,
selection fingerprint, finalization fingerprint, and every referenced upstream artifact hash.

The output contains:

- `evidence_manifest.json`, the strict machine-readable audit index;
- `report.md`, an exactly reproducible human-readable projection of the manifest;
- portable copies of plan, registry, policy, split, configuration, dependency, execution,
  validation, candidate, selection, and permitted finalization evidence.

The report state is inherited and constrained:

```text
validation REJECTED                  -> report REJECTED, no winner or test claim
validation LOCKED, no finalization   -> report LOCKED, test explicitly NOT FINALIZED
validation LOCKED + verified receipt -> report FINALIZED, final-test claims permitted
```

A rejected or unfinalized run cannot attach finalization evidence. The complete SOH-labelled
source file is not copied; its digest is retained so reporting does not redistribute held-out
labels.

### Core code and schemas

- `generate_evidence_bundle`: verifies the source chain, copies portable artifacts, creates claims,
  fingerprints the manifest, and renders the report.
- `verify_evidence_bundle`: independently checks the schema, manifest fingerprint, artifact hashes,
  claim pointers and values, and exact Markdown derivation without network access.
- `EvidenceManifest`: strict top-level contract for identity, state, policies, fingerprints,
  artifacts, outcomes, claims, final-test status, limitations, and unsupported claims.
- `ArtifactReference`: stable ID, semantic role, portable path, media type, and SHA-256 digest.
- `CandidateOutcome`: execution/validation status, failure details, gate decisions, metrics,
  resources, and explicit outcome tags.
- `QuantitativeClaim`: name, partition, metric, numeric value, units, artifact IDs, and JSON Pointer.
- `FinalTestEvidence`, `SourceFingerprints`, and `GenerationPolicy`: strict supporting schemas.
- `ReportingResult`, `VerificationResult`, and `ReportError`: typed public results and failure API.

Unknown fields, duplicate IDs or paths, absolute/escaping paths, non-finite values, incoherent
states, an unknown claim artifact, or a claim that does not resolve to the recorded JSON number are
rejected.

### Algorithms and engineering techniques

The evidence fingerprint is SHA-256 over canonical sorted JSON after excluding the fingerprint
field itself. Output directories and timestamps are absent from the payload, so two bundles made
from the same source run are byte-for-byte equivalent at the manifest and report layers. This is
tamper evidence, not a cryptographic signature or identity service.

Artifact paths are normalized and copied under stable roles. SHA-256 is recomputed from bytes, not
accepted from filenames. Candidate record and validation hashes are cross-checked against their
parent manifests before bundling, which prevents internally inconsistent evidence from being
repackaged as valid.

Each quantitative statement points to one or more artifact IDs and a JSON Pointer such as
`/metrics/mae`. Verification loads that protected JSON, resolves the pointer, requires a finite
numeric value, and compares it with the claim. Validation metrics, cell-bootstrap interval bounds,
execution/resource numbers, and permitted final-test metrics use the same mechanism.

The Markdown report is rendered only from the strict manifest. Verification renders it again and
requires exact text equality. No timestamp, provider, prompt, network response, or hidden advisor
state participates because the generation policy is explicitly `language_model=disabled` and
`network_access=false`.

### BDD scenarios and failure paths

Six executable scenarios cover:

- a complete finalized bundle without a language model;
- mixed successful, failed, timed-out, and gate-rejected outcomes;
- a locked run with no held-out test claims;
- API and CLI rejection of an altered protected artifact;
- resolution of every quantitative claim to hashed numeric JSON;
- identical fingerprints across two output directories with no timestamp input.

Eleven focused tests add output non-overwrite behavior, execution-identity and finalization
tampering, strict schema rejection, unsafe-path rejection, unknown and false claims, human-report
tampering, unfinalized CLI behavior, rejected-run finalization exclusion, and missing-report
rejection.

### Quantitative evidence

FR-12 focused evidence:

```text
BDD: 6 passed
Additional unit/integration: 11 passed
Focused total: 17 passed in 120.68s
Full repository: 99 passed in 359.01s
Executable BDD total: 36
Skipped: 0
Xfailed: 0
Package coverage: 86.86%
reporting.py: 590 statements, 88.98% coverage
Strict mypy: 19 source files, 0 issues
Ruff lint/format and git diff --check: passed
```

These measurements establish software behavior and verification depth. They do not establish
battery-model accuracy, physical validity on MATR, or production BMS readiness.

### Known limitations and unsupported claims

- LLM advice is deliberately absent; FR-14 remains out of scope.
- Current executable acceptance data is synthetic. No real MATR generalization claim is made.
- The labelled dataset is represented by its digest but is not copied into the report bundle.
- The current code fingerprint is narrower than a signed full-source build attestation.
- Models are not serialized, so model-object size and a dedicated inference-latency benchmark are
  not available.
- Python allocation peaks and wall time are retained, but native-library RSS is not hard-limited.
- SHA-256 detects changes but does not establish author identity, trusted time, or non-repudiation.
- The repository has no selected `LICENSE`, so a formal open-source release remains blocked.
- FR-13 complete plugin governance remains separate work.

### Interview explanation question

> Why is a Markdown report alone insufficient audit evidence, and how can a reviewer prove that an
> AutoBench number was neither invented nor changed after the run?

A strong answer should explain canonical fingerprints, byte-level artifact hashes, JSON Pointer
claim resolution, exact deterministic rendering, source-chain verification, and the difference
between tamper evidence and digital signatures.

### Live modification question

> Add a validation-cell count claim to the report. Which strict schema, source artifact, JSON
> Pointer, rendering rule, verifier rule, and BDD assertion must change?

The candidate should derive the number from an existing protected artifact, never count test rows
for an unfinalized report, preserve deterministic ordering, and demonstrate tamper rejection.

## FR-13 - Model-plugin governance

### Problem and product role

FR-09 originally consumed a manually reviewed minimal registry. FR-13 makes the review result
executable and auditable before planning: a complete plugin catalog is evaluated against one locked
compatibility and license policy, and every plugin receives an explicit `APPROVED`, `REJECTED`, or
`QUARANTINED` decision.

This layer answers four questions without downloading or running a model:

1. Is the plugin declaration structurally complete and internally coherent?
2. Does its target, feature, capability, environment, resource, search, and serialization contract
   satisfy the local experiment policy?
3. Are both code and weight licenses known, allowed, and explicitly approved for redistribution?
4. Which declarations may enter FR-09 planning, and which may remain only as research references?

### Inputs, outputs, and decision states

`govern_model_plugins` accepts a plugin-catalog JSON file, a governance-policy JSON file, and a new
output directory. Each plugin must declare stable identity/version, target, feature units,
preprocessing, training/prediction capabilities, deterministic seed behavior, dependencies,
accelerator and memory, code and weight licenses, upstream source/citation, bounded search space,
serialization/loading, and an execution adapter.

The deterministic output contains:

- normalized `catalog_snapshot.json` and `policy_snapshot.json`;
- `decisions/<plugin_id>.json` for every catalog entry;
- `approved_registry.json`, directly consumable by FR-09 planning;
- `external_references.json`, containing only quarantined provenance and reason codes;
- `governance_manifest.json`, with counts, portable paths, SHA-256 values, and a canonical bundle
  fingerprint.

Decision semantics are:

```text
all non-license gates pass + both licenses approved -> APPROVED -> executable registry
non-license incompatibility with approved licenses  -> REJECTED -> no plan candidate
missing/unknown/disallowed license                  -> QUARANTINED -> reference only
```

Quarantine takes precedence over execution compatibility. A plugin with unknown licensing can
never acquire execution, default-enable, code/weight bundling, or redistribution permissions.

### Core code and schemas

- `govern_model_plugins`: validates inputs, evaluates every plugin, and writes a non-overwriting
  declarations-only governance bundle.
- `verify_governance_bundle`: checks the manifest and file hashes, then independently reevaluates
  the catalog against the retained policy and requires exact registry, reference, decision, and
  count equality.
- `PluginDeclaration`: complete identity, feature, capability, environment, license, provenance,
  search, serialization, and adapter contract.
- `GovernancePolicy`: exact target/features plus dependency, accelerator, memory, license, adapter,
  search, and serialization allow lists or bounds.
- `ParameterDomain`: finite categorical choices or closed integer/float intervals.
- `GovernanceDecision` and `GovernancePermissions`: strict three-state result and operations that
  the result permits.
- `ApprovedRegistry`: FR-09-compatible projection that contains only approved plugins.
- `GovernanceManifest`: protected declarations-only file index and deterministic fingerprint.

Plugin IDs use a path-safe lowercase identifier pattern. Unknown schema fields, duplicate IDs,
duplicate features/dependencies, incoherent license claims, unbounded parameters, invalid
serialization contracts, ambiguous allow lists, non-portable evidence paths, and non-JSON protected
files are rejected.

### Policy gates and engineering techniques

The evaluator applies explicit gates for target, exact feature name/unit pairs, training,
prediction, determinism and seed handling, dependencies, accelerator, minimum memory, bounded trial
count, serialization, execution adapter, known licenses, and allowed licenses. Every negative gate
has a stable machine-readable reason code.

An approved plugin is projected into the existing FR-09 registry shape. Its executable-manifest
fingerprint is SHA-256 over canonical JSON, matching the fingerprint that FR-09 records in the
immutable plan. This proves that the planned candidate is the exact approved projection rather than
another declaration with the same display name.

The bundle contains JSON declarations only; input schemas expose no code or weight artifact path.
`artifact_inventory` is required to remain empty, and offline verification rejects any protected
non-JSON path. This is a structural safeguard in addition to the quarantine permissions.

Verification is semantic, not only hash-based. Even if a modified decision or registry is rehashed
and the top-level manifest is refingerprinted, the verifier rebuilds the expected outcome from the
retained catalog and policy and rejects the mismatch. Timestamps and output paths are excluded, so
the same inputs produce identical governance manifests across directories.

### BDD scenarios and failure paths

Seven executable scenarios cover:

- complete compatible approval and declaration fingerprints;
- incompatible-target rejection before plan or training output;
- unknown-license quarantine and reference-only retention;
- incomplete serialization/loading declaration failure before output;
- API and CLI detection of a modified decision;
- deterministic fingerprints plus real FR-09 planning from the approved registry;
- CLI governance and offline verification of a mixed three-outcome catalog.

Thirty-seven additional cases cover schema cross-fields, safe file identities, all main policy
gates, policy-incompatible licenses, semantic tampering after rehashing, missing evidence,
non-overwriting behavior, CLI failure behavior, and the repository's quarantine-by-default example.

### Quantitative evidence

FR-13 focused evidence:

```text
BDD: 7 passed
Additional unit/integration cases: 37 passed
Focused total: 44 passed in 5.25s
Full repository: 143 passed in 356.51s
Executable BDD total: 43
Skipped: 0
Xfailed: 0
Package coverage: 87.70%
governance.py: 438 statements, 92.47% coverage
Strict mypy: 20 source files, 0 issues
Ruff lint/format and git diff --check: passed
```

These are governance-software verification metrics. They do not certify that an upstream license,
citation, dependency, or model claim is legally or scientifically correct.

### Known limitations and unsupported claims

- The governance policy records a local review decision; the software does not retrieve license
  texts, perform legal analysis, contact upstream authors, or establish legal ownership.
- Dependency version specifiers are retained and dependency names are allow-listed, but FR-13 does
  not create an isolated environment or solve/install those constraints.
- The current FR-10 executor still supports only three built-in scikit-learn model IDs and its
  built-in adapter rejects non-empty parameter overrides.
- A declared external adapter or serialization format is not automatically implemented by
  governance. Serialization remains unavailable for the current built-in models.
- SHA-256 provides tamper evidence, not author identity, trusted time, or a digital signature.
- The safe example intentionally yields an empty approved registry and one quarantined reference.
- The repository still has no selected `LICENSE`, which blocks a formal open-source release.

### Interview explanation question

> Why is checking `code_license != "unknown"` inside the training loop weaker than producing a
> governed registry before planning, and what evidence prevents a quarantined plugin from entering
> AutoBench by accident?

A strong answer should cite fail-before-training architecture, explicit permission fields,
approved-registry projection, exact manifest fingerprints, empty artifact inventory, deterministic
reason codes, and semantic offline verification.

### Live modification question

> Add a policy rule that permits CUDA only when a plugin also declares a bounded GPU-memory limit.
> Which declaration schema, policy schema, gate, reason code, decision artifact, fingerprint, BDD
> scenario, and unit boundary tests must change?

The candidate should reject missing limits rather than inventing them, preserve quarantine
precedence for unknown licenses, and keep the approved registry deterministic.

## v0.2.0rc1 - Open-source release readiness

### Problem and product role

FR-01 through FR-13 were executable and well tested but not yet distributable as a governed
open-source release: the repository had no license, release identity, CI, contribution/security
policies, citation, release checklist, or built-package contract. This module turns that technical
baseline into a locally auditable release candidate without changing battery-model behavior.

### Inputs, outputs, and release boundary

Inputs are the verified FR-13 worktree, user-confirmed MIT identity, existing `pyproject.toml` and
`uv.lock`, Python 3.12, and the established local quality gates. Outputs are:

- root MIT LICENSE and consistent PEP 621/639 package metadata;
- a source-level `__version__` aligned with package and citation metadata;
- CFF 1.2 citation metadata with public author and repository identity;
- read-only GitHub CI for Windows and Ubuntu plus bounded Dependabot updates;
- changelog, contribution, security, conduct, and release-checklist governance;
- four release-readiness BDD scenarios and eleven focused contracts;
- one typed wheel and one source distribution in ignored `dist/`.

The local module itself does not mutate GitHub state. Subsequent explicitly authorized release
orchestration committed and pushed the candidate, made the repository public, enabled Private
Vulnerability Reporting, and observed the hosted matrix. Tag, GitHub Release, and PyPI remain
outside this evidence checkpoint.

### Core contracts and engineering techniques

- `release_readiness.feature` defines exactly four observable release outcomes.
- `test_release_readiness.py` binds every scenario and performs real temporary archive builds,
  Typer help checks, metadata consistency checks, and CI/governance inspection.
- `test_release_readiness_unit.py` adds eleven independent release contracts using standard-library
  TOML parsing, precise text patterns, full-SHA validation, and negative publish/secret checks.
- PEP 621/639 metadata declares version, SPDX license expression, license file, public author,
  project URLs, classifiers, and typed-package status.
- The source-level version in `src/battery_health/__init__.py` is checked against `pyproject.toml`,
  and the real `battery-health version` command must return the same release candidate value.
- GitHub Actions permissions are `contents: read`; checkout, setup-python, and setup-uv are pinned
  to reviewed 40-character commits and annotated with release versions.
- Dependabot monitors only the native `uv` and `github-actions` ecosystems.
- Wheel inspection verifies version, MIT metadata, LICENSE, and `battery_health/py.typed`; sdist
  inspection verifies the legal and typed source files.
- The project lock remains unchanged except for its root editable package version.

### BDD Red and Green

The first focused run collected all contracts and recorded exit code 1 with `15 failed in 2.70s`.
Failures were limited to intentionally absent release files, `0.1.0` metadata, and the unsupported
requested build flag. After the minimal release implementation, an explicit correction from
`uv build --frozen` to frozen sync plus `uv build --no-sources`, and the user-approved source
version correction, the version-fix run recorded `15 passed in 4.93s`. A final rerun after the
evidence documents were synchronized recorded `15 passed in 7.20s`. The final CI portability
correction recorded `15 passed in 4.61s` and an exact-workflow full run of
`158 passed in 395.30s`.

### Quantitative evidence

```text
Release BDD: 4 passed
Release contract tests: 11 passed
Focused total: 15 passed in 4.61s
Full repository: 158 passed in 395.30s
Executable BDD total: 47
Skipped: 0
Xfailed: 0
Package coverage: 87.76%
Strict mypy: 20 source files, 0 issues
Ruff: passed, 60 files formatted
Safety scan: 87 files, 0 issues
Build: 1 wheel + 1 sdist, version 0.2.0rc1
Tracked dist artifacts: 0
```

These measurements establish local software and packaging behavior. They are not GitHub runner,
PyPI, real-battery accuracy, embedded BMS, functional-safety, or cybersecurity-certification
claims.

### Known limitations and unsupported claims

- The first hosted CI run (`33339051515`) exposed that a cache-nested basetemp was not creatable
  from a fresh checkout. A root-level attempt then proved temporary test output must also remain
  outside repository safety discovery. The final BDD/unit contract uses the ignored, guaranteed
  `.venv/release_readiness_ci` parent; corrective run `33339984774` passed both matrix jobs.
- GitHub Private Vulnerability Reporting is enabled and API-verified for the public repository.
- Installed dependency metadata and the Hatchling upstream declaration show permissive licenses;
  this mechanical review is not legal advice and does not relicense datasets, models, or weights.
- Python 3.12 is the only declared and tested interpreter line for this candidate.
- Synthetic data remains the only executable acceptance dataset.
- Distribution hashes identify these local bytes only and are not signatures or attestations.

### Interview explanation question

> Why does a passing local test suite not prove that a repository is ready for a public release?

A strong answer should connect code behavior to legal permission, package metadata, citation,
contribution/security governance, least-privilege CI, pinned supply-chain inputs, archive-content
inspection, cross-platform hosted evidence, and explicit external authorization boundaries.

### Live modification question

> Add Python 3.13 to the release matrix without weakening Python 3.12 support. Which metadata,
> CI matrix, lock assumptions, BDD assertions, hosted evidence, and release checklist entries must
> change before the compatibility claim is valid?

The candidate should distinguish declaring compatibility from observing it, preserve frozen
resolution, require both operating systems, and avoid marking external CI complete from local
evidence.

## FR-14 - MATR real-data adapter

### Problem and product role

The earlier benchmark could validate tabular data and run an auditable synthetic workflow, but it
had no executable boundary for the first declared real dataset. FR-14 converts one user-supplied
official MATR MATLAB v7.3/HDF5 batch into the existing minimal cycle schema while preserving
source identity and refusing ambiguous structures. It is an ingestion module, not a model or an
LLM advisor; the optional advisor is now FR-15.

### Inputs, outputs, and safety boundary

Inputs are an exact local HDF5 file plus explicit batch ID, HTTPS source URL, acquisition date, and
reviewed usage-status text. The adapter reads only `batch/summary`, the referenced summary groups,
and their direct or singly referenced `cycle` and `QDischarge` numeric vectors. Outputs are a new directory with
`cycles.csv`, `quality_report.json`, and `source_manifest.json`.

Raw data and generated data remain ignored. The manifest stores the portable basename, byte size,
SHA-256, source metadata, observed structure, exact type/unit mapping, counts, output hashes,
canonical fingerprint, and unsupported claims. It never stores a machine-specific absolute path
or claims that public access grants redistribution permission.

### Core contracts and engineering techniques

- `matr_data_import.feature` defines eight observable import, rejection, reproducibility, and CLI
  outcomes; anonymous HDF5 fixtures cover the acquired direct-vector layout, while unit fixtures
  cover the older author-parser layout with referenced vectors.
- `h5py` is isolated at the scientific-file boundary; its missing upstream type marker receives a
  narrow import-only mypy suppression while repository-wide strict typing remains enabled.
- Cycle vectors must contain finite positive integers; capacities must contain finite positive Ah
  values; vector lengths must match; `(cell_id, cycle_index)` identities must be unique.
- Cell IDs derive only from reviewed batch identity and stable source-array position. Source cycle
  values are preserved and rows are sorted only for deterministic output.
- SHA-256 is streamed in 1 MiB chunks so a multi-gigabyte source file is not loaded into memory.
- JSON is key-sorted and the import fingerprint uses canonical compact JSON. CSV line endings and
  float serialization are explicit.
- All validation and input hashing precede publication. Files are written to a random sibling
  staging directory and renamed only when complete; failure removes staging, and existing output
  is never overwritten.

### BDD Red, Green, and quantitative evidence

Initial Red: two collection errors, both caused by the intentionally absent production module.
Initial Green: 28 tests passed. Official-parser review then strengthened the fixture to two-level
references; the resulting Red recorded 17 failures and 12 passes. The corrected focused suite
recorded 29 passes. The acquired corrected batch then exposed its direct-vector variant: a third
Red recorded 8 failures and 21 passes before the dual-layout Green recorded 29 passes in 2.61
seconds. The suite consists of eight executable BDD scenarios and 21 boundary cases.

The 2,007,331,155-byte official batch had SHA-256
`63ab200d09ecb237fee5ef3a5c5db76e3212e3206a0bd92f769e1427fed338b8`. The CLI imported 48 cells
and 24,920 rows in 4.153 seconds with fingerprint
`93e8dac89c310bd329de0957222ac802d542713476637d8b222e0f976bb3df05`. A second real import made all
three output files byte-identical. Raw and normalized data remained ignored and untracked.

Final release-wide evidence recorded 187 tests passed in 368.61 seconds, including 55 executable
BDD tests, zero skipped, zero xfailed, and 87.40% package coverage against the unchanged 85% gate.
The final adapter reached 81% statement coverage and the CLI reached 89%. Ruff lint and the
64-file format check passed; strict mypy found no issues in 21 source files; the final wheel and
sdist built successfully; and the tracked diff contained no machine absolute path or binary data.

### Known limitations and unsupported claims

- FR-14 imports capacity summaries only; voltage, current, temperature, and time-series cycle data
  are intentionally outside this first adapter.
- Import success proves structural and value integrity, not dataset correctness, label quality,
  battery accuracy, generalization, BMS suitability, functional safety, or cybersecurity.
- The repository does not redistribute the MATR bytes and does not infer a dataset license.
- Output is not yet wired into an end-to-end real-data training profile or batch-holdout study.
- SHA-256 detects byte changes but is not a signature, trusted timestamp, or proof of authorship.

### Interview explanation question

> Why is a passing CSV parser insufficient evidence that the project supports the official MATR
> dataset, and what makes this adapter's output independently auditable?

A strong answer should explain MATLAB v7.3/HDF5 object references, explicit discrimination of
direct versus referenced vectors, source-position identity, explicit units, fail-closed validation, streamed input hashing,
deterministic serialization, portable provenance, atomic publication, and scientific/legal claim
boundaries.

### Live modification question

> Add one optional voltage summary field without silently changing the current three-column
> contract. Which atomic requirement, Gherkin scenario, HDF5 reference validator, unit declaration,
> schema/version rule, manifest mapping, determinism assertion, and backward-compatibility test
> must change?

The candidate should make absence versus invalid presence explicit, reject ambiguous units, retain
the current required columns, and avoid claiming support for raw per-cycle time series.

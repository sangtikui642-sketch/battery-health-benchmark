# Battery Health AutoBench Agent - Requirements v0.2

Status: draft for review on 2026-08-28.

## 1. Product objective

Extend Battery Health Benchmark with a command-line intelligent agent that can turn a
validated, labelled battery-cycle dataset into a reproducible modelling plan, execute a bounded
set of compatible models, validate every result through mandatory gates, and publish an evidence
bundle that another researcher can reproduce.

The agent is an experiment orchestrator, not an unrestricted shell agent. Deterministic code owns
data access, splitting, training, metric calculation, model selection, and artifact writing. An
optional language model may explain evidence or recommend a next experiment, but its output is
advisory and cannot change a locked plan or recorded result.

## 2. Relationship to the v0.1 MVP

The v0.1 requirements remain the immediate implementation dependency:

- FR-04 produces explicit SOH labels.
- FR-05 supplies the first registered model implementations.
- FR-06 supplies traceable evaluation.
- FR-07 supplies run provenance and deterministic reproduction.
- FR-08 supplies repository and data-safety checks.

AutoBench v0.2 remains SOH-only, CLI-only, and restricted to public redistributable data or
synthetic fixtures. RUL, a web interface, autonomous data download, arbitrary command execution,
and embedded deployment remain out of scope.

## 3. Users and primary workflow

The primary user is a battery algorithm researcher who wants a defensible answer to:

> Given this dataset, target definition, resource budget, and validation policy, which registered
> model is the strongest supported candidate, and what evidence justifies that conclusion?

The intended CLI workflow is:

```text
battery-health agent plan      -> immutable plan.json
battery-health agent run       -> candidate run records and validation predictions
battery-health agent validate  -> gate results and locked selection
battery-health agent finalize  -> one final held-out test and evidence bundle
battery-health agent explain   -> optional evidence-grounded narrative
```

Planning and execution may later be combined by a convenience command, but the immutable plan and
individual stages must remain available for audit and reproduction.

## 4. Trust architecture

```text
validated cycles + SOH labels + split manifest + policy + model registry
                              |
                              v
                       deterministic planner
                              |
                         immutable plan
                              |
                              v
                    bounded experiment executor
                              |
                 candidate artifacts and failures
                              |
                              v
                 validation and governance gates
                              |
                    locked model selection
                              |
                              v
                    one final held-out test
                              |
                              v
                     signed evidence manifest
                              |
                    optional LLM explanation
```

The language-model boundary is after evidence generation. Moving it earlier requires a separate
approved requirement and threat model.

## 5. Functional requirements

### FR-09 Auditable experiment planning

The system shall generate an immutable experiment plan from validated data, an existing
leakage-safe split manifest, a model registry, and a resource policy. The plan shall contain:

- target name and definition;
- dataset and split-manifest fingerprints;
- feature contract and units;
- candidate model identifiers and versions;
- preprocessing policy;
- model-selection metric and tie-break rules;
- mandatory validation gates;
- random seeds and deterministic settings;
- candidate, runtime, and memory budgets;
- software revision and dependency-lock fingerprint.

The planner shall reject missing prerequisites or an empty compatible candidate set. It shall not
invent fields, capacities, units, model licenses, or validation thresholds.

### FR-10 Bounded candidate execution

The system shall execute only candidates and parameter ranges registered in the locked plan. Every
candidate shall use the same cell-level split. Preprocessing and hyperparameter selection shall use
training and validation data only.

A failed candidate shall receive a structured failure record without terminating unrelated
candidates. Timeouts, memory limits, invalid predictions, and dependency failures shall be reported
as failures rather than silently removed from the comparison.

### FR-11 Leakage-safe selection and final validation

Candidate ranking and selection shall use validation evidence only. Test labels and test metrics
shall be unavailable to the selection component. After the selected candidate and configuration
are locked, the system may evaluate that candidate on the held-out test partition once for a final
run.

Mandatory gates shall include:

- cell identities remain disjoint across partitions;
- preprocessing is fitted on training data only;
- predictions are finite and cover the expected rows;
- predicted SOH remains within a configured plausible range;
- required accuracy metrics and uncertainty intervals are present;
- candidate execution stayed within the declared resource policy;
- all evaluated code, data, and model artifacts have traceable license status.

Failure of a mandatory gate shall prevent the system from declaring a winner. No gate may be
weakened automatically in response to a failure.

### FR-12 Evidence bundle and explanation

Every completed agent run shall retain:

- immutable plan and plan fingerprint;
- effective configurations and seeds;
- input, split, code, dependency, and model fingerprints;
- records for successful, failed, timed-out, and rejected candidates;
- validation leaderboard with uncertainty intervals;
- gate decisions and machine-readable reasons;
- final test predictions and metrics when finalization is permitted;
- runtime, peak memory, model size, and prediction latency where measurable;
- limitations and unsupported claims;
- a machine-readable evidence manifest linking every report claim to an artifact.

The deterministic evidence bundle shall be complete without a language model. If an optional LLM
is used, its provider, model identifier, prompt version, and referenced evidence identifiers shall
be recorded. An LLM explanation shall not overwrite metrics or gate decisions.

### FR-13 Model-plugin governance

Each model plugin shall declare a manifest containing:

- stable plugin and implementation version;
- supported target and required feature contract;
- training and prediction capabilities;
- deterministic support and seed handling;
- dependency and accelerator requirements;
- code and weight license identifiers;
- upstream source and citation;
- default bounded search space;
- serialization and loading contract.

An incompatible plugin shall be rejected before training. A plugin with unknown or incompatible
license status may be documented as an external research reference, but its code or weights shall
not be bundled, executed by default, or represented as approved for redistribution.

### FR-14 Optional evidence-grounded advisor

The optional advisor may summarize results, explain failed gates, and propose a new draft plan. It
shall receive sanitized manifests and aggregate evidence rather than unrestricted filesystem
access. It shall not execute shell commands, access test labels during selection, alter an existing
plan, modify metric files, or approve its own recommendation.

The complete plan-run-validate-finalize workflow shall work with the advisor disabled.

## 6. Quantitative acceptance gates

The initial gates are contracts, not fabricated performance promises. Dataset-specific thresholds
must be declared before the run and stored in the locked plan.

| Dimension | Required v0.2 evidence |
|---|---|
| Accuracy | MAE, RMSE, MAPE, R-squared by cycle and by cell |
| Uncertainty | Bootstrap confidence interval for primary metrics |
| Generalization | Cell-level holdout; later profiles may add batch/protocol holdout |
| Physical plausibility | Finite predictions and configured SOH bounds; no forced cycle-wise monotonicity |
| Efficiency | Fit duration, prediction duration, serialized size, peak memory when available |
| Stability | Results across declared seeds, with mean and dispersion |
| Reproducibility | Fingerprinted inputs, plan, environment, code, split, and outputs |
| Governance | Known license status for every bundled data, code, and weight artifact |

Cycle-wise monotonic SOH is deliberately not a universal gate because capacity measurements may
contain recovery effects and measurement noise. A smoothing or monotonic model must be an explicit
model assumption, not an invisible validator correction.

## 7. Run state machine

```text
DRAFT -> PLANNED -> RUNNING -> VALIDATED -> LOCKED -> FINALIZED
                    |            |            |
                    v            v            v
                  FAILED      REJECTED      REJECTED
```

- `PLANNED`: all prerequisites and plugin manifests are valid; plan fingerprint is fixed.
- `VALIDATED`: candidate validation evidence exists, but no winner is implied.
- `LOCKED`: selection is recorded from validation evidence and cannot be changed in place.
- `FINALIZED`: the locked candidate has one recorded held-out test evaluation.
- `REJECTED`: a mandatory gate failed; the evidence is retained.

A new decision after `LOCKED` requires a new plan and run identity.

## 8. Initial registered model families

The first implementation shall reuse FR-05 models:

- linear regression;
- random forest;
- histogram gradient boosting.

Later adapters may add NARX-Transformer, SambaMixer, PBT, and physics-informed models only after
their feature, license, environment, and evaluation contracts are explicitly reviewed. PyBaMM and
PyBOP belong to a later physics-consistency profile rather than the first AutoBench execution
milestone.

## 9. Requirement-to-feature map

| Requirement | Gherkin feature |
|---|---|
| FR-09 | `features/agent_planning.feature` |
| FR-10 | `features/agent_execution.feature` |
| FR-11 | `features/agent_validation.feature` |
| FR-12, FR-14 | `features/agent_reporting.feature` |
| FR-13 | `features/model_plugin_governance.feature` |

## 10. Delivery sequence

1. Complete FR-04 through FR-08 and the v0.1 CLI path.
2. Implement FR-09 with a deterministic planner and immutable JSON plan.
3. Implement FR-10 by orchestrating the three registered v0.1 baselines.
4. Implement FR-11 validation-only selection followed by one locked final test.
5. Implement FR-12 evidence manifests and comparison reports.
6. Implement FR-13 plugin manifests and license policy.
7. Add FR-14 only after the deterministic agent passes all acceptance checks.

## 11. Definition of done for AutoBench v0.2

1. One synthetic fixture completes the entire CLI workflow without network access or an LLM.
2. At least three registered candidates run from one immutable plan and the same split manifest.
3. An intentionally leaking split prevents execution.
4. A failed candidate remains visible while unrelated candidates complete.
5. Model selection cannot read test labels or test metrics.
6. A mandatory-gate failure produces `REJECTED`, not a winner.
7. Final test evaluation is linked to one locked selection and cannot be repeated in place.
8. Every report statement is traceable to the evidence manifest.
9. Unknown-license plugins are quarantined by an executable acceptance test.
10. Tests, Ruff, formatting, mypy, coverage, and repository safety checks pass with no skipped or
    xfailed checks.

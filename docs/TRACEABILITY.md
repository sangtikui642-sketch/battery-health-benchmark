# AutoBench v0.2 Traceability

Status snapshot after FR-13. `complete` means the cited executable evidence exists. `not
implemented` is deliberately not treated as completion.

## FR-11 leakage-safe selection and final validation

| ID | Atomic requirement | Gherkin scenario | Executable test | Production entry | Primary artifact | Status |
|---|---|---|---|---|---|---|
| FR11-01 | Rank with validation evidence only | Lock a candidate using validation evidence only | `test_lock_a_candidate_using_validation_evidence_only` | `validate_and_lock_execution` | `leaderboard.json` | complete |
| FR11-02 | Make test labels and metrics unavailable during selection | Lock a candidate using validation evidence only; Reject execution evidence that exposes the test partition | `test_lock_a_candidate_using_validation_evidence_only`; `test_reject_execution_evidence_that_exposes_the_test_partition` | validation API accepts no evidence directory | `validation_manifest.json/run_gates` | complete |
| FR11-03 | Evaluate the locked candidate on held-out test once | Finalize one held-out test after selection is locked | `test_finalize_one_heldout_test_after_selection_is_locked` | `finalize_locked_selection` | `finalization_claim.json`; `finalization_manifest.json` | complete |
| FR11-04 | Keep cell identities disjoint | Do not declare a winner after a mandatory gate failure | `test_self_consistent_overlapping_split_is_rejected_before_output` | `_load_split` | `plan/split_snapshot.json` | complete |
| FR11-05 | Fit preprocessing on train only | Lock a candidate using validation evidence only | `test_lock_a_candidate_using_validation_evidence_only` | `_candidate_validation` | candidate gate `train_only_preprocessing` | complete |
| FR11-06 | Require finite and complete predictions | Do not declare a winner after a mandatory gate failure | `test_missing_predictions_reject_every_candidate_without_a_lock`; `test_nonfinite_prediction_is_ineligible_and_never_corrected` | `_candidate_validation` | candidate gate `finite_complete_predictions` | complete |
| FR11-07 | Enforce configured SOH bounds without correction | Reject a candidate with physically implausible predictions | `test_reject_a_candidate_with_physically_implausible_predictions` | `_candidate_validation` | candidate gate `configured_soh_bounds` | complete |
| FR11-08 | Require accuracy metrics and uncertainty | Lock a candidate using validation evidence only | `test_cell_bootstrap_and_selection_fingerprint_are_deterministic` | `_metrics`; `_bootstrap_by_cell` | candidate metrics and uncertainty | complete |
| FR11-09 | Enforce declared resources | Do not declare a winner after a mandatory gate failure | FR-10 timeout and memory tests plus FR-11 gate assertions | `_candidate_validation` | candidate gate `resource_budget_respected` | complete |
| FR11-10 | Require traceable licenses | Lock a candidate using validation evidence only | FR-09 incompatible-registry tests plus FR-11 gate assertions | `_known_license`; `_candidate_validation` | candidate gate `known_licenses` | complete |
| FR11-11 | Prevent a winner after mandatory gate failure | Do not declare a winner after a mandatory gate failure | `test_do_not_declare_a_winner_after_a_mandatory_gate_failure` | `validate_and_lock_execution` | `validation_manifest.json` in `REJECTED` | complete |
| FR11-12 | Never weaken a gate automatically | Do not declare a winner after a mandatory gate failure | `test_do_not_declare_a_winner_after_a_mandatory_gate_failure` | immutable plan copy and gate evaluation | retained plan and negative evidence | complete |

## FR-12 evidence bundle and explanation

| ID | Atomic requirement | Gherkin scenario | Executable test | Production entry | Primary artifact | Status |
|---|---|---|---|---|---|---|
| FR12-01 | Retain the immutable plan and its fingerprint | Generate a finalized evidence bundle without a language model | `test_generate_a_finalized_evidence_bundle_without_a_language_model` | `generate_evidence_bundle` | `artifacts/plan/plan.json`; `plan_fingerprint` | complete |
| FR12-02 | Retain effective configuration and declared seeds | Generate a finalized evidence bundle without a language model | `test_generate_a_finalized_evidence_bundle_without_a_language_model` | `generate_evidence_bundle` | effective-configuration and plan artifacts | complete |
| FR12-03 | Retain input, split, code, dependency, and model fingerprints | Generate a finalized evidence bundle without a language model; Keep evidence fingerprints deterministic across output directories | `test_generate_a_finalized_evidence_bundle_without_a_language_model`; `test_keep_evidence_fingerprints_deterministic_across_output_directories` | `_source_fingerprints` | `source_fingerprints` | complete |
| FR12-04 | Retain successful, failed, timed-out, and gate-rejected candidates | Retain every outcome in a rejected run report | `test_retain_every_outcome_in_a_rejected_run_report` | `_candidate_outcomes` | `candidate_outcomes`; candidate records | complete |
| FR12-05 | Retain leaderboard metrics and cell-bootstrap uncertainty | Generate a finalized evidence bundle without a language model; Resolve every quantitative report claim to evidence | `test_generate_a_finalized_evidence_bundle_without_a_language_model`; `test_resolve_every_quantitative_report_claim_to_evidence` | `_quantitative_claims` | leaderboard and candidate-validation artifacts | complete |
| FR12-06 | Retain mandatory gate decisions and reasons | Generate a finalized evidence bundle without a language model; Retain every outcome in a rejected run report | reporting BDD finalized and rejected-run tests | `_candidate_outcomes`; `_copy_artifacts` | validation manifest and candidate validations | complete |
| FR12-07 | Include held-out test evidence only after verified finalization | Generate a finalized evidence bundle without a language model; Report a locked run that has not been finalized | finalized, locked, and rejected reporting BDD tests | `generate_evidence_bundle` | `final_test`; optional finalization artifacts | complete |
| FR12-08 | Retain measured resource evidence as traceable claims | Resolve every quantitative report claim to evidence | `test_resolve_every_quantitative_report_claim_to_evidence` | `_quantitative_claims` | execution manifest and candidate records | complete |
| FR12-09 | State limitations and unsupported claims for every state | Retain every outcome in a rejected run report; Report a locked run that has not been finalized | rejected and locked reporting BDD tests | `_limitations`; `_unsupported_claims`; `_render_report` | `limitations`; `unsupported_claims`; `report.md` | complete |
| FR12-10 | Resolve every quantitative claim to hashed numeric JSON | Resolve every quantitative report claim to evidence; Detect a tampered evidence artifact through the API and CLI | claim-resolution and tamper BDD tests | `verify_evidence_bundle` | `quantitative_claims`; artifact SHA-256 table | complete |
| FR12-11 | Complete a deterministic, offline bundle without an LLM | Generate a finalized evidence bundle without a language model; Keep evidence fingerprints deterministic across output directories | finalized and determinism reporting BDD tests | `generate_evidence_bundle`; `verify_evidence_bundle` | `generation_policy`; `evidence_fingerprint`; `report.md` | complete |
| FR12-12 | Record provider/model/prompt/evidence when an LLM is enabled | Explain recorded evidence without changing it | deferred to FR-14 | not in current scope | advisor provenance | deferred FR-14 |
| FR12-13 | Prevent an explanation from overwriting evidence | Explain recorded evidence without changing it | deferred to FR-14 | not in current scope | immutable deterministic evidence | deferred FR-14 |

## FR-13 model-plugin governance

| ID | Atomic requirement | Gherkin scenario | Executable test | Production entry | Primary artifact | Status |
|---|---|---|---|---|---|---|
| FR13-01 | Declare stable plugin identity and implementation version | Approve a complete compatible model plugin | `test_approve_a_complete_compatible_model_plugin` | `PluginDeclaration`; `govern_model_plugins` | catalog snapshot and per-plugin decision | complete |
| FR13-02 | Declare target and feature names with units | Approve a complete compatible model plugin; Reject an incompatible plugin before planning or training | approval and incompatible-target BDD tests | `_evaluate_plugin` target/feature gates | gate results and approved registry | complete |
| FR13-03 | Declare training and prediction capabilities | Approve a complete compatible model plugin | approval BDD plus `test_incompatible_contract_is_rejected_with_a_reason` capability cases | `CapabilityDeclaration`; `_evaluate_plugin` | capability gate results | complete |
| FR13-04 | Declare deterministic support and seed handling | Approve a complete compatible model plugin | approval BDD; deterministic compatibility and invalid seed-contract unit cases | `DeterminismDeclaration`; `_evaluate_plugin` | determinism gate result | complete |
| FR13-05 | Declare dependencies, accelerator, and resource requirements | Reject an incompatible plugin before planning or training | dependency, accelerator, and memory unit cases | `RequirementDeclaration`; `_evaluate_plugin` | requirement declarations and gate results | complete |
| FR13-06 | Declare code and weight license identifiers and status | Approve a complete compatible model plugin; Quarantine a plugin with unknown license status | approval/quarantine BDD and disallowed-license unit test | `LicenseDeclaration`; `_license_allowed` | license gates and permissions | complete |
| FR13-07 | Retain upstream source, repository, and citation | Approve a complete compatible model plugin | `test_approve_a_complete_compatible_model_plugin` | `UpstreamDeclaration`; `_catalog_payload` | `catalog_snapshot.json` | complete |
| FR13-08 | Require a finite, bounded search space | Approve a complete compatible model plugin | bounded, unbounded, and over-policy search unit cases | `ParameterDomain`; `SearchSpaceDeclaration` | search declaration and gate result | complete |
| FR13-09 | Declare serialization and loading support honestly | Reject an incomplete plugin declaration before output | incomplete-manifest BDD and required-serialization unit cases | `SerializationDeclaration`; `_evaluate_plugin` | serialization declaration and gate result | complete |
| FR13-10 | Reject incompatible plugins before planning or training | Reject an incompatible plugin before planning or training; Keep governance deterministic and feed only approved plugins to planning | incompatible and approved-registry planning BDD tests | `_evaluate`; `govern_model_plugins` | rejected decision; `approved_registry.json` | complete |
| FR13-11 | Never bundle code or weights for unknown-license plugins | Quarantine a plugin with unknown license status | `test_quarantine_a_plugin_with_unknown_license_status` | declarations-only writer and strict manifest | empty `artifact_inventory`; JSON-only bundle | complete |
| FR13-12 | Never execute or default-enable unknown-license plugins | Quarantine a plugin with unknown license status | `test_quarantine_a_plugin_with_unknown_license_status` | `GovernancePermissions` | execution/default permissions false | complete |
| FR13-13 | Never claim redistribution approval for unknown-license plugins | Quarantine a plugin with unknown license status | quarantine BDD and false-redistribution unit test | `GovernanceDecision.permissions_match_status` | redistribution and bundling permissions false | complete |

## v0.2.0rc1 release readiness

| ID | Atomic requirement | Gherkin scenario | Executable test | Release entry | Evidence | Status |
|---|---|---|---|---|---|---|
| REL-01 | Run CI on push and pull requests | Enforce release gates on Windows and Ubuntu | `test_enforce_release_gates_on_windows_and_ubuntu` | `.github/workflows/ci.yml` | trigger contract | complete locally |
| REL-02 | Cover Windows, Ubuntu, and Python 3.12 | Enforce release gates on Windows and Ubuntu | CI matrix BDD and unit assertions | CI matrix | `windows-latest`, `ubuntu-latest`, `3.12` | complete locally |
| REL-03 | Enforce frozen sync, tests, quality, typing, and build | Enforce release gates on Windows and Ubuntu | command contract assertions | CI steps | local command summaries | complete locally |
| REL-04 | Keep source/package version, license, author, URL, and citation consistent | Keep release metadata consistent | metadata BDD and three unit contracts | `src/battery_health/__init__.py`; `pyproject.toml`; `LICENSE`; `CITATION.cff`; `uv.lock` | CLI and wheel metadata | complete |
| REL-05 | Publish governance documents without placeholders | Publish complete open-source governance files | governance BDD and placeholder contract | five governance documents | document inventory | complete |
| REL-06 | Use confidential security reporting | Publish complete open-source governance files | private-reporting assertions | `SECURITY.md` | advisory URL and policy | configured locally; GitHub setting pending |
| REL-07 | Keep external release actions unauthorized | Publish complete open-source governance files | safe-checklist assertions | `docs/RELEASE_CHECKLIST.md` | unchecked external-action list | complete |
| REL-08 | Build one typed wheel and one sdist | Build artifacts and expose the CLI release surface | archive-inspection BDD | `uv build --no-sources` | versioned archives and content audit | complete locally |
| REL-09 | Preserve CLI version, root help, and agent help | Build artifacts and expose the CLI release surface | Typer version/help assertions | `battery-health`; `battery-health agent` | `0.2.0rc1` and exit 0 help output | complete locally |
| REL-10 | Keep dependency and Action updates bounded | Enforce release gates on Windows and Ubuntu | pinned-action and Dependabot contracts | `.github/dependabot.yml`; CI action refs | exact ecosystems and SHAs | complete locally |

`complete locally` does not mean GitHub-hosted CI has run. Commit, push, tag, GitHub Release, and
PyPI state remain outside this evidence and require explicit authorization.

# Changelog

All notable changes to Battery Health AutoBench are documented in this file. The project follows
the Keep a Changelog structure and uses Python packaging version identifiers.

## Unreleased

No changes have been recorded after the local `0.2.0rc1` release candidate.

## 0.2.0rc1 - 2026-08-31

### Added

- FR-01 through FR-08: deterministic SOH import, validation, cell-level splitting, labelling,
  three baseline models, evaluation, reproducibility evidence, and repository safety checks.
- FR-09 through FR-13: immutable planning, bounded candidate execution, leakage-safe selection,
  one-shot finalization, deterministic evidence reporting, and model-plugin governance.
- MIT license, citation metadata, contribution and conduct guidance, security reporting policy,
  release checklist, Dependabot configuration, and cross-platform GitHub Actions CI.
- Release-readiness BDD and contract tests for metadata, governance, CI, artifacts, and CLI help.

### Changed

- Advanced the package version from `0.1.0` to `0.2.0rc1`.
- Declared the public project identity, repository URLs, typed-package classifier, and license
  files in Python package metadata.
- Corrected the CI basetemp to use the ignored uv environment created on fresh Windows and Ubuntu
  runners, with a regression contract preserving creation and repository-safety requirements.

### Security

- Limited CI permissions to read-only repository contents and prohibited publishing credentials.
- Selected GitHub Private Vulnerability Reporting as the confidential reporting mechanism.
- Pinned every GitHub Action reference to a reviewed full commit SHA.

### Limitations

- The executable acceptance workflow uses synthetic battery data and does not establish real-world
  accuracy, BMS readiness, or safety certification.
- The first GitHub-hosted run exposed and retained evidence for a fresh-runner basetemp defect; a
  green corrective matrix run, Git tag, GitHub Release, and optional PyPI publication remain
  required before broader release claims.

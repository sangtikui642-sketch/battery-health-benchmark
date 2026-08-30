# v0.2.0rc1 Release Checklist

Status: public release-candidate verification completed on 2026-08-31; tag and GitHub Release
remain pending at this checkpoint.

Release identity:

- version: `0.2.0rc1`;
- SPDX license: `MIT`;
- copyright: `Copyright (c) 2026 赵一冰`;
- author: `赵一冰 (sangtikui642-sketch)`;
- repository: `https://github.com/sangtikui642-sketch/battery-health-benchmark`;
- security channel: GitHub Private Vulnerability Reporting.

## Local verification

These checks may be marked complete only after their exact command exits successfully in the local
working tree. GitHub-hosted status is not inferred from local execution.

- [x] Four release-readiness BDD scenarios and eleven release contract tests pass.
- [x] Full pytest suite passes with at least 85% package coverage, zero skipped, and zero xfailed.
- [x] Ruff lint and Ruff format checks pass.
- [x] Strict mypy passes for `src/battery_health`.
- [x] Frozen dependency synchronization passes without changing dependency resolution.
- [x] One wheel and one source archive build with version `0.2.0rc1`.
- [x] The wheel contains `battery_health/py.typed` and MIT package metadata.
- [x] Root and `agent` CLI help commands exit successfully.
- [x] Repository safety scanning and `git diff --check` pass.
- [x] Build products remain ignored and untracked.

Approved local commands:

```powershell
uv sync --frozen
uv run --frozen pytest -q --cov=battery_health --cov-report=term-missing --cov-fail-under=85 --basetemp .pytest_cache\release_readiness_20260831
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src\battery_health
uv build --no-sources
uv run --frozen battery-health --help
uv run --frozen battery-health agent --help
git diff --check
```

`uv build --frozen` is not a valid command in uv 0.12.7. The executable release gate uses
`uv sync --frozen` to enforce the lock, followed by `uv build --no-sources` to build against
standards-compliant publishable metadata without workspace source overrides.

Recorded local evidence:

- focused release suite after the CI regression correction: `15 passed in 4.61s`;
- exact corrected CI test command: `158 passed in 395.30s`, 47 executable BDD tests, no skipped
  or xfailed tests;
- package coverage: `87.76%` against the unchanged 85% gate;
- Ruff: all checks passed and 60 files formatted;
- strict mypy: no issues in 20 source files;
- CLI: version returned `0.2.0rc1`; root and agent help both exited 0 and exposed all eight agent commands;
- repository safety: 87 files scanned, zero issues;
- lock audit: only the root package version changed from `0.1.0` to `0.2.0rc1`;
- Git audit: `dist/` is ignored and contains zero tracked files.

Final artifact SHA-256 values are reported after the last source edit and rebuild rather than
embedded here, because this checklist is itself included in the source archive.

Reviewed action references:

- `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1` (`v7.0.1`);
- `actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97` (`v7.0.0`);
- `astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9` (`v9.0.0`).

These references were checked against the action maintainers' GitHub repositories and the official
uv GitHub Actions integration documentation. Dependabot is limited to the `uv` and
`github-actions` ecosystems.

## External actions requiring authorization

External release state after the first authorized push:

- [x] git commit: reviewed candidate committed as `619a6b2`.
- [x] git push: `619a6b2` pushed to `origin/main`.
- [x] hosted CI: corrective run `33339984774` passed Ubuntu in 4m14s and Windows in 8m24s; failed
  run `33339051515` remains retained as regression evidence.
- [x] repository visibility: verified `PUBLIC` through the GitHub API.
- [ ] git tag: create annotated tag `v0.2.0rc1` only after both CI matrix jobs pass.
- [ ] GitHub Release: publish the tag, changelog excerpt, wheel, and source archive.
- [ ] PyPI: publish distributions only after a separate credential and publication decision.
- [x] Enable GitHub Private Vulnerability Reporting; API verification returned `enabled: true`.

Remaining candidate commands for an authorized maintainer are:

```powershell
git tag -a v0.2.0rc1 -m "Battery Health AutoBench v0.2.0rc1"
git push origin v0.2.0rc1
gh release create v0.2.0rc1 dist\*.whl dist\*.tar.gz --title "Battery Health AutoBench v0.2.0rc1" --notes-file CHANGELOG.md
uv publish dist\*
```

The remaining commands above are documentation, not an execution record. The failed hosted run is
retained as evidence and the corrective matrix is green. Tag and release creation remain pending
until this final evidence update itself passes CI.

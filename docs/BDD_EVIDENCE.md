# FR-06 to FR-08 BDD evidence

All commands below were run from the repository root on 2026-08-28. The red phase was
recorded before any of the new production modules or the `benchmark` command existed.

## Red phase

```powershell
.venv\Scripts\python.exe -m pytest -q tests\bdd\test_evaluation.py tests\bdd\test_reproducibility.py tests\bdd\test_data_protection.py tests\bdd\test_cli_benchmark.py --basetemp .pytest_cache\goal_red
```

Result: exit code 1, `6 failed`.

| Scenario | Expected failure evidence |
|---|---|
| Generate a traceable evaluation report | `battery_health.evaluation` did not exist |
| Reject an empty test partition | no `EvaluationError` behavior existed |
| Repeat a deterministic experiment | `battery_health.reproducibility` did not exist |
| Reject disallowed repository content | `battery_health.safety` did not exist |
| Use reproducible anonymous fixtures | `battery_health.demo` did not exist |
| Complete the three-model benchmark from one leakage-safe split | CLI had no `benchmark` command |

## Green phase

The same scenarios passed after their minimal implementations:

| Requirement | Command scope | Recorded result |
|---|---|---|
| FR-06 | evaluation BDD plus unit tests | `5 passed` |
| FR-07 | reproducibility BDD plus unit tests | `5 passed` |
| FR-08 | data-protection BDD plus safety/demo unit tests | `8 passed` |
| Offline CLI | CLI BDD plus benchmark unit tests | `5 passed` |
| Full-run determinism | two complete three-model runs | `1 passed` |

## Release-wide evidence

```powershell
.venv\Scripts\python.exe -m pytest -q --cov=battery_health --cov-fail-under=80 --basetemp .pytest_cache\goal_release
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\mypy.exe
git diff --check
```

Recorded release result:

- 42 tests passed, including 17 executable BDD scenarios;
- zero skipped and zero xfailed tests;
- total package coverage was 88.18%;
- Ruff lint, Ruff formatting, strict mypy, and `git diff --check` all exited successfully.

## FR-09 planning evolution

The planning behavior was bound before production code existed:

```powershell
.venv\Scripts\python.exe -m pytest -q tests\bdd\test_agent_planning.py --basetemp .pytest_cache\fr09_red
```

Recorded red result: exit code 1, `3 failed`. The planner module, `PlanningError`, and
`battery-health agent plan` command did not yet exist.

After implementing the immutable planner, the three BDD scenarios and eight focused unit and
integration tests passed. They cover successful planning, an empty compatible-candidate set,
CLI creation, stale evidence hashes, missing feature units, non-overwriting output, tampered
plan and registry fingerprints, isolation from held-out test metrics, and consumption of a
real benchmark evidence bundle.

## FR-09 release validation

```powershell
.venv\Scripts\python.exe -m pytest -q --cov=battery_health --cov-fail-under=80 --basetemp .pytest_cache\fr09_release
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\mypy.exe
git diff --check
```

Recorded result:

- 53 tests passed, including 20 executable BDD scenarios;
- zero skipped and zero xfailed tests;
- total package coverage was 87.78%;
- Ruff lint, Ruff formatting, strict mypy, and `git diff --check` all exited successfully.

A direct CLI acceptance run against a benchmark-generated evidence bundle produced state
`PLANNED` with execution status `not_started`, three compatible candidates, four required
metrics, and seven validation gates. The output contained only `plan.json`,
`policy_snapshot.yaml`, and `registry_snapshot.json`; it created zero training directories.

## FR-10 bounded execution evolution

Four executable scenarios were bound before adding an execution module or CLI command:

```powershell
.venv\Scripts\python.exe -m pytest -q tests\bdd\test_agent_execution.py --basetemp .pytest_cache\fr10_red
```

Recorded red result: exit code 1, `4 failed`. The execution module did not exist and the CLI
reported no `agent execute` command.

After implementation, all four BDD scenarios and nine focused unit and integration tests passed.
They cover
all-candidate validation execution, one-candidate dependency failure isolation, locked runtime
rejection, CLI execution, plan and evidence tampering, non-overwriting output, child-process
timeout termination, measured-memory failure, candidate-set mismatch, and non-zero CLI policy
rejection. The integration test runs an immutable plan created from a complete benchmark evidence
bundle and verifies a fingerprinted train-validation input physically excludes test cells.

## FR-10 release validation

```powershell
.venv\Scripts\python.exe -m pytest -q --cov=battery_health --cov-fail-under=80 --basetemp .pytest_cache\fr10_release_final
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\mypy.exe
git diff --check
```

Recorded result:

- 66 tests passed, including 24 executable BDD scenarios;
- zero skipped and zero xfailed tests;
- total package coverage was 85.59%;
- Ruff lint, Ruff formatting, strict mypy, and `git diff --check` all exited successfully.

A direct `battery-health agent execute` acceptance run against benchmark-generated evidence
completed three of three candidates. Its fingerprinted execution input contained nine
train-validation cells, zero held-out test cells, and every saved prediction was explicitly
marked `validation`. Wall-clock bounds used child-process termination. Python allocation peaks
were measured and checked after each run; native-library RSS remains a documented non-hard limit.

## FR-11 leakage-safe validation evolution

Six scenarios were bound before the validation module or CLI commands existed:

```powershell
.venv\Scripts\uv.exe run --frozen pytest -q tests\bdd\test_agent_validation.py --basetemp .pytest_cache\fr11_red_final
```

Recorded red result: exit code 1, `6 failed`. Five scenarios failed because
`battery_health.agent.validation` did not exist; the CLI scenario reported no `agent validate`
command. The collection contained no skip, xfail, or infrastructure error.

After implementing validation-only ranking, seven mandatory candidate gates, cell bootstrap,
selection locking, one-time finalization, and CLI commands, the focused BDD result was:

```text
6 passed in 69.59s
```

Ten additional unit and integration tests passed in 114.58 seconds. They cover missing and
non-finite predictions, a self-consistent but overlapping split, tampered execution input,
candidate-record tampering, deterministic bootstrap and fingerprints, declared tie-breaking,
non-overwriting outputs, selection-lock tampering, and test-access claim behavior.

## FR-11 release validation

```powershell
.venv\Scripts\uv.exe run --frozen pytest -q --cov=battery_health --cov-report=term-missing --cov-fail-under=85 --basetemp .pytest_cache\fr11_release
.venv\Scripts\uv.exe run --frozen ruff check .
.venv\Scripts\uv.exe run --frozen ruff format --check .
.venv\Scripts\uv.exe run --frozen mypy src\battery_health
git diff --check
```

Recorded pytest result:

- 82 tests passed, including 30 executable BDD scenarios;
- zero skipped and zero xfailed tests;
- total package coverage was 86.12%;
- the new `agent/validation.py` module had 88% statement coverage;
- strict mypy reported no issues in 18 source files;
- Ruff lint, Ruff format check, and `git diff --check` exited successfully;
- user-level uv 0.12.7 executed the release command directly from the repository root.

## FR-12 deterministic reporting evolution

Six reporting scenarios were bound before the reporting module and its CLI commands existed:

```powershell
uv run --frozen pytest -q tests\bdd\test_agent_reporting.py --basetemp .pytest_cache\fr12_red_20260831
```

Recorded red result: exit code 1, `6 failed in 68.27s`. Failures were limited to the missing
`battery_health.agent.reporting` module and missing `agent report` command. There were no skips,
xfails, or infrastructure errors.

After implementing strict evidence schemas, source-chain verification, portable artifact hashing,
machine-resolvable quantitative claims, deterministic Markdown generation, and offline API/CLI
verification, the same BDD suite recorded:

```text
6 passed in 70.79s
```

Eleven additional unit and integration tests cover non-overwriting output, source identity and
finalization-fingerprint tampering, strict unknown-field rejection, absolute-path rejection,
unknown claim artifacts, wrong claim values after refingerprinting, report tampering, unfinalized
CLI reporting and verification, rejected-run finalization exclusion, and a missing human report.

The combined focused gate was:

```powershell
uv run --frozen pytest -q tests\bdd\test_agent_reporting.py tests\test_agent_reporting_unit.py --cov=battery_health.agent.reporting --cov-report=term-missing --cov-fail-under=80 --basetemp .pytest_cache\fr12_focused_final_20260831
```

Recorded result:

- 17 tests passed in 120.68 seconds;
- zero skipped and zero xfailed tests;
- `agent/reporting.py` contains 590 statements and reached 88.98% statement coverage;
- focused Ruff checks passed and strict mypy reported no issues in 19 source files.

## FR-12 release validation

Because the Windows user temporary directory had a pre-existing pytest ACL problem, the approved
project-local base directory was used without changing test behavior or assertions:

```powershell
uv run --frozen pytest -q --cov=battery_health --cov-report=term-missing --cov-fail-under=85 --basetemp .pytest_cache\fr12_release_20260831
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src\battery_health
git diff --check
```

Recorded result:

- 99 tests passed in 359.01 seconds, including 36 collected executable BDD scenarios;
- zero skipped and zero xfailed tests;
- total package coverage was 86.86%, above the unchanged 85% gate;
- `agent/reporting.py` reached 88.98% coverage in the focused measurement and 89% in the rounded
  release table;
- Ruff lint passed and all 50 files were already formatted;
- strict mypy reported no issues in 19 source files;
- `git diff --check` passed with only Windows line-ending conversion warnings;
- the offline agent CLI exposed `plan`, `execute`, `validate`, `finalize`, `report`, and `verify`.

## FR-13 model-plugin governance evolution

Seven governance scenarios were bound before the governance module and CLI commands existed:

```powershell
uv run --frozen pytest -q tests\bdd\test_model_plugin_governance.py --basetemp .pytest_cache\fr13_red_20260831
```

Recorded red result: exit code 1, `7 failed in 2.66s`. Six scenarios failed because
`battery_health.agent.governance` did not exist; the CLI scenario reported no
`govern-plugins` command. There were no skips, xfails, or infrastructure errors.

After implementing complete declaration schemas, policy gates, deterministic APPROVED/REJECTED/
QUARANTINED decisions, declarations-only evidence, an FR-09-compatible approved registry, and
offline semantic verification, the same BDD suite recorded:

```text
7 passed in 2.69s
```

Thirty-seven additional unit and integration cases cover non-overwriting output, duplicate and
path-injecting identities, capability, determinism, dependency, accelerator, memory, bounded-search,
serialization and adapter incompatibility, unknown and policy-incompatible licenses, invalid schema
cross-fields, safe repository examples, missing evidence, CLI failure behavior, and semantic
tampering even after an attacker recomputes file and manifest hashes.

The combined focused gate was:

```powershell
uv run --frozen pytest -q tests\bdd\test_model_plugin_governance.py tests\test_agent_governance_unit.py --cov=battery_health.agent.governance --cov-report=term-missing --cov-fail-under=90 --basetemp .pytest_cache\fr13_focused_final_20260831
```

Recorded result:

- 44 tests passed in 5.25 seconds;
- zero skipped and zero xfailed tests;
- `agent/governance.py` contains 438 statements and reached 92.47% statement coverage;
- the repository example produced zero approved plugins and one quarantined reference, then
  verified successfully offline.

## FR-13 release validation

```powershell
uv run --frozen pytest -q --cov=battery_health --cov-report=term-missing --cov-fail-under=85 --basetemp .pytest_cache\fr13_release_20260831
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src\battery_health
git diff --check
```

Recorded result:

- 143 tests passed in 356.51 seconds, including 43 collected executable BDD scenarios;
- zero skipped and zero xfailed tests;
- total package coverage was 87.70%, above the unchanged 85% gate;
- `agent/governance.py` reached 92.47% in the focused measurement and 92% in the rounded release
  table;
- Ruff lint passed and all 53 files were already formatted;
- strict mypy reported no issues in 20 source files;
- `git diff --check` passed with only Windows line-ending conversion warnings;
- the CLI exposed eight agent commands, including `govern-plugins` and `verify-plugins`.

## v0.2.0rc1 release-readiness evolution

Four release scenarios and eleven contract tests were added before any release file or package
metadata was implemented:

```powershell
uv run --frozen pytest -q tests\bdd\test_release_readiness.py tests\test_release_readiness_unit.py --basetemp .pytest_cache\release_readiness_red_20260831
```

Recorded Red result: exit code 1, `15 failed in 2.70s`. All four BDD scenarios were collected and
failed for expected release gaps: CI, LICENSE, CITATION, governance documents, release metadata,
and versioned distributions did not exist or still identified `0.1.0`. There were no collection,
fixture-registration, skip, or xfail failures.

The Red run also proved that the requested `uv build --frozen` command is unsupported by uv 0.12.7
and exits 2. The executable contract was corrected to `uv sync --frozen` followed by
`uv build --no-sources`: the former verifies the dependency lock, while the latter builds
standards-compliant publishable metadata without uv workspace source overrides.

After adding the user-confirmed MIT identity, PEP 621/639 metadata, CFF citation, pinned read-only
CI, uv/Actions Dependabot, governance documents, and a safe release checklist, a direct CLI probe
found that `src/battery_health/__init__.py` still exposed the former `0.1.0` version. The user
explicitly approved that file's inclusion in release scope. The hard-coded value was corrected,
and the BDD and unit contracts now assert source, package, citation, and CLI version consistency.
The focused version-fix command recorded:

```text
15 passed in 4.93s
```

The 15 tests comprise exactly four executable BDD scenarios and eleven release contract tests.
They inspect CI triggers, OS/Python matrix, full-SHA action pins, commands and permissions;
cross-check version, license, author, URLs and citation; reject governance placeholders; build a
temporary wheel and sdist; inspect archive metadata and `py.typed`; and exercise both CLI help
surfaces. After the evidence documents were synchronized with the final full-suite result, the
same release suite was rerun and recorded `15 passed in 7.20s`.

## v0.2.0rc1 local release validation

```powershell
uv sync --frozen
uv run --frozen pytest -q --cov=battery_health --cov-report=term-missing --cov-fail-under=85 --basetemp .pytest_cache\release_readiness_20260831
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src\battery_health
uv build --no-sources
uv run --frozen battery-health version
uv run --frozen battery-health --help
uv run --frozen battery-health agent --help
git diff --check
```

Recorded local results:

- 158 tests passed in 395.30 seconds with the final CI basetemp, including 47 executable BDD tests;
- zero skipped and zero xfailed tests;
- package coverage was 87.76%, above the unchanged 85% gate;
- Ruff lint passed and all 60 files were formatted;
- strict mypy reported no issues in 20 source files;
- CLI version returned `0.2.0rc1`; root and agent help exited 0 and exposed all eight agent commands;
- exactly one `0.2.0rc1` wheel and one sdist were built;
- wheel metadata contained `License-Expression: MIT`, the LICENSE file, and
  `battery_health/py.typed`; the sdist contained LICENSE and the typed marker;
- repository safety scanned 87 files with zero issues;
- `dist/` contained zero tracked files and `uv.lock` changed only the root package version;
- `git diff --check` exited 0 with Windows line-ending conversion warnings only.

## First hosted CI execution and basetemp regression

The reviewed candidate was committed as `619a6b2` and pushed to `main`. GitHub Actions run
`33339051515` executed both matrix jobs, and both failed in the test step before meaningful test
execution. The fresh runners did not contain the parent `.pytest_cache` directory required by the
configured `.pytest_cache/release_readiness_ci` basetemp. The local worktree had contained that
cache directory, so the earlier local gate did not expose the portability defect.

The existing release-gate scenario and unit contract were strengthened before the CI change to
reject the missing cache parent. Recorded regression Red result: exit code 1,
`2 failed, 13 passed in 7.71s`. A first root-level correction passed the focused suite but polluted
repository safety discovery during the exact full CI command; that full run retained a second Red
result of `5 failed, 153 passed in 370.51s`. Three failures proved the root temp tree was visible to
the safety scanner, while two proved the release-state contract still described commit and push as
unexecuted. The final workflow uses `--basetemp .venv/release_readiness_ci`: `uv sync --frozen`
guarantees the parent and `.gitignore` excludes the environment from repository safety discovery.
The release-state contracts now record commit/push as completed while keeping tag, GitHub Release,
PyPI, and Private Vulnerability Reporting pending.

Final local corrective evidence: the focused release suite recorded `15 passed in 4.61s`; the
exact test command from the corrected workflow recorded `158 passed in 395.30s` with 87.76%
coverage, zero skipped tests, and zero xfailed tests.

The failed hosted run is retained as evidence. A hosted rerun on the corrective commit remains
required before a tag or GitHub Release can be created. No tag, GitHub Release, Private
Vulnerability Reporting setting change, or PyPI publication has been performed or claimed.

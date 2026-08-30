# Contributing to Battery Health AutoBench

Thank you for helping improve an auditable battery state-of-health benchmark. Contributions should
preserve the project's deterministic, leakage-safe, CLI-only product boundary.

## Development setup

Use Python 3.12 and the locked uv environment:

```powershell
uv sync --frozen
uv run --frozen battery-health --help
```

Do not commit virtual environments, caches, build products, raw battery files, credentials,
personal data, company data, or unpublished research material.

## Behavior-driven workflow

Changes to observable behavior follow this sequence:

1. Add or update a Gherkin scenario under `features/`.
2. Bind it to a pytest-bdd acceptance test and record the expected failing result.
3. Implement the smallest production change that satisfies the scenario.
4. Run the focused tests and the full repository gates.
5. Update traceability and module handoff evidence with commands actually executed.

Never hide a regression with `skip`, `xfail`, weaker assertions, fabricated metrics, or changed
expected values. Split battery observations by cell identity and fit preprocessing on training
data only.

## Local quality gates

Run these commands before requesting review:

```powershell
uv run --frozen pytest -q --cov=battery_health --cov-report=term-missing --cov-fail-under=85 --basetemp .pytest_cache\contribution_review
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src\battery_health
uv build --no-sources
git diff --check
```

The build command uses `--no-sources` so distribution metadata is evaluated without uv-specific
workspace or local source overrides. Dependency immutability is enforced separately by
`uv sync --frozen`.

## Pull request expectations

- Explain the problem, intended behavior, and product boundary.
- Link every new behavior to its feature, executable test, code, and artifact where applicable.
- Include exact local command summaries; do not claim GitHub-hosted checks that have not run.
- Add no new runtime or development dependency without an explicit design and license review.
- Keep public functions typed and documented.
- Preserve compatibility with Windows and Ubuntu on Python 3.12.

## Data, model, and license review

Only synthetic or public, redistributable data may enter the repository. A repository-level MIT
license does not relicense an external dataset, model implementation, weight file, or copied code.
Every external component must retain its own source, citation, license identifier, and local
governance decision. Unknown-license model plugins remain quarantined and reference-only.

By submitting a contribution, you agree that your contribution may be distributed under this
repository's MIT License and confirm that you have the right to provide it on those terms.

## Security reports

Do not disclose suspected vulnerabilities in a public issue. Follow `SECURITY.md` and use the
repository's private vulnerability reporting channel.

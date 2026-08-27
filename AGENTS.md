# Repository rules

## Product boundary

- The MVP predicts state of health (SOH), not RUL.
- MATR is the first real-data target, but the initial executable scenario uses synthetic fixtures.
- The first release is CLI-only. Do not add a web UI or service layer.

## BDD workflow

1. Add or update a scenario in `features/` before changing behavior.
2. Bind the scenario to a pytest-bdd acceptance test and demonstrate the expected failure.
3. Implement the smallest change that makes the scenario pass.
4. Refactor only while the full test suite remains green.
5. Never use `skip`, `xfail`, weakened assertions, or edited expected values to hide a regression.

## Data and research integrity

- Use only public, redistributable data or synthetic fixtures.
- Never commit company data, unpublished patent details, credentials, personal data, or local absolute paths.
- Never fabricate metrics. Results not produced by a recorded command must be marked unverified.
- Split by cell identity. A cell must never occur in more than one of train, validation, and test.
- Fit preprocessing operations on training data only.

## Engineering rules

- Production code belongs in `src/battery_health/`; notebooks are exploratory only.
- Dependencies are declared in `pyproject.toml` and locked in `uv.lock`.
- Public functions use type annotations and concise docstrings.
- Run pytest, Ruff, formatting checks, and mypy before declaring a change complete.
- Do not create a remote repository or push without explicit user approval.

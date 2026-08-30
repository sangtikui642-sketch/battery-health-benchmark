import re
import tomllib
from pathlib import Path
from typing import Any

from battery_health import __version__

REPOSITORY = Path(__file__).resolve().parents[1]
REPOSITORY_URL = "https://github.com/sangtikui642-sketch/battery-health-benchmark"
PLACEHOLDER_PATTERN = re.compile(
    r"(?:\bTODO\b|\bTBD\b|your-email|example\.com|\[year\]|\[fullname\])",
    re.IGNORECASE,
)


def _read(path: str) -> str:
    return (REPOSITORY / path).read_text(encoding="utf-8")


def _project() -> dict[str, Any]:
    with (REPOSITORY / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]


def test_project_version_license_author_urls_and_classifiers_are_release_ready() -> None:
    project = _project()
    assert project["version"] == "0.2.0rc1"
    assert __version__ == project["version"]
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["authors"] == [{"name": "赵一冰"}]
    assert project["urls"] == {
        "Homepage": REPOSITORY_URL,
        "Issues": f"{REPOSITORY_URL}/issues",
        "Repository": REPOSITORY_URL,
    }
    assert {
        "Development Status :: 4 - Beta",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3.12",
        "Typing :: Typed",
    }.issubset(project["classifiers"])


def test_mit_license_contains_the_confirmed_copyright_identity() -> None:
    license_text = _read("LICENSE")
    assert license_text.startswith("MIT License\n")
    assert "Copyright (c) 2026 赵一冰" in license_text
    assert "Permission is hereby granted, free of charge" in license_text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in license_text
    assert PLACEHOLDER_PATTERN.search(license_text) is None


def test_citation_matches_release_identity_and_contains_no_placeholder() -> None:
    citation = _read("CITATION.cff")
    assert re.search(r'(?m)^cff-version:\s*["\']1\.2\.0["\']\s*$', citation)
    assert re.search(r'(?m)^version:\s*["\']0\.2\.0rc1["\']\s*$', citation)
    assert re.search(r'(?m)^license:\s*["\']MIT["\']\s*$', citation)
    assert 'family-names: "赵"' in citation
    assert 'given-names: "一冰"' in citation
    assert 'alias: "sangtikui642-sketch"' in citation
    assert f'repository-code: "{REPOSITORY_URL}"' in citation
    assert PLACEHOLDER_PATTERN.search(citation) is None


def test_ci_triggers_matrix_and_actions_are_explicit_and_pinned() -> None:
    ci = _read(".github/workflows/ci.yml")
    assert re.search(r"(?m)^on:\s*$", ci)
    assert re.search(r"(?m)^\s{2}push:\s*$", ci)
    assert re.search(r"(?m)^\s{2}pull_request:\s*$", ci)
    assert "ubuntu-latest" in ci
    assert "windows-latest" in ci
    assert re.search(r"python-version:\s*\[?[\"']?3\.12", ci)
    action_lines = [line.strip() for line in ci.splitlines() if line.strip().startswith("uses:")]
    assert len(action_lines) == 3
    assert all(
        re.fullmatch(r"uses: [^@]+@[0-9a-f]{40} # v\d+(?:\.\d+){1,2}", line)
        for line in action_lines
    )


def test_ci_enforces_every_local_quality_and_build_command() -> None:
    ci = _read(".github/workflows/ci.yml")
    for command in (
        "uv sync --frozen",
        "uv run --frozen pytest -q",
        "--cov=battery_health",
        "--cov-report=term-missing",
        "--cov-fail-under=85",
        "--basetemp .venv/release_readiness_ci",
        "uv run --frozen ruff check .",
        "uv run --frozen ruff format --check .",
        "uv run --frozen mypy src/battery_health",
        "uv build --no-sources",
    ):
        assert command in ci
    assert ".pytest_cache/release_readiness_ci" not in ci


def test_ci_has_read_only_permissions_and_no_publish_or_secret_path() -> None:
    ci = _read(".github/workflows/ci.yml")
    lowered = ci.lower()
    assert re.search(r"(?m)^permissions:\s*\n\s{2}contents:\s*read\s*$", ci)
    for forbidden in (
        "${{ secrets.",
        "id-token: write",
        "contents: write",
        "packages: write",
        "uv publish",
        "twine",
        "gh release",
        "pypi",
    ):
        assert forbidden not in lowered


def test_dependabot_monitors_only_uv_and_github_actions() -> None:
    dependabot = _read(".github/dependabot.yml")
    ecosystems = re.findall(r'package-ecosystem:\s*["\']([^"\']+)["\']', dependabot)
    assert ecosystems == ["uv", "github-actions"]
    assert dependabot.count('directory: "/"') == 2
    assert dependabot.count('interval: "weekly"') == 2


def test_governance_documents_exist_and_contain_no_placeholders() -> None:
    paths = (
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "docs/RELEASE_CHECKLIST.md",
    )
    for path in paths:
        content = _read(path)
        assert content.strip(), path
        assert PLACEHOLDER_PATTERN.search(content) is None, path


def test_security_policy_uses_private_vulnerability_reporting() -> None:
    security = _read("SECURITY.md")
    assert f"{REPOSITORY_URL}/security/advisories/new" in security
    assert "Do not report vulnerabilities in public issues" in security
    assert "No security email address is published for this release" in security


def test_release_checklist_records_completed_and_pending_external_actions() -> None:
    checklist = _read("docs/RELEASE_CHECKLIST.md")
    assert "## Local verification" in checklist
    assert "## External actions requiring authorization" in checklist
    external = checklist.split("## External actions requiring authorization", maxsplit=1)[1]
    for action in ("git commit", "git push"):
        assert re.search(rf"(?mi)^- \[x\] {re.escape(action)}", external)
        assert re.search(rf"(?m)^- \[ \] {re.escape(action)}", external) is None
    for action in ("git tag", "GitHub Release", "PyPI"):
        assert re.search(rf"(?m)^- \[ \] {re.escape(action)}", external)
        assert re.search(rf"(?m)^- \[x\] {re.escape(action)}", external, re.IGNORECASE) is None


def test_readme_exposes_version_license_and_release_limitations() -> None:
    readme = _read("README.md")
    assert "v0.2.0rc1" in readme
    assert "MIT License" in readme
    assert "synthetic data" in readme.lower()
    assert "not" in readme.lower() and "BMS" in readme

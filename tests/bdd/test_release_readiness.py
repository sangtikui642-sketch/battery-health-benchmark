import re
import shutil
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any

from pytest_bdd import given, scenarios, then, when
from typer.testing import CliRunner

from battery_health.cli import app

scenarios("../../features/release_readiness.feature")

REPOSITORY = Path(__file__).resolve().parents[2]
REPOSITORY_URL = "https://github.com/sangtikui642-sketch/battery-health-benchmark"
GOVERNANCE_FILES = (
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "docs/RELEASE_CHECKLIST.md",
)
PLACEHOLDER_PATTERN = re.compile(
    r"(?:\bTODO\b|\bTBD\b|your-email|example\.com|\[year\]|\[fullname\])",
    re.IGNORECASE,
)


def _read(path: str) -> str:
    return (REPOSITORY / path).read_text(encoding="utf-8")


def _project() -> dict[str, Any]:
    with (REPOSITORY / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]


@given(
    "the repository is prepared as a v0.2.0rc1 release candidate",
    target_fixture="release_context",
)
def release_candidate(tmp_path: Path) -> dict[str, Any]:
    return {"dist": tmp_path / "dist"}


@when("the release CI policy is inspected")
def inspect_ci(release_context: dict[str, Any]) -> None:
    release_context["ci"] = _read(".github/workflows/ci.yml")


@then("push and pull requests run on Windows and Ubuntu with Python 3.12")
def ci_has_required_matrix(release_context: dict[str, Any]) -> None:
    ci = release_context["ci"]
    assert re.search(r"(?m)^on:\s*$", ci)
    assert re.search(r"(?m)^\s{2}push:\s*$", ci)
    assert re.search(r"(?m)^\s{2}pull_request:\s*$", ci)
    assert "ubuntu-latest" in ci
    assert "windows-latest" in ci
    assert re.search(r"python-version:\s*\[?[\"']?3\.12", ci)


@then("frozen dependencies tests quality checks typing and package build are enforced")
def ci_has_required_commands(release_context: dict[str, Any]) -> None:
    ci = release_context["ci"]
    required = (
        "uv sync --frozen",
        "--cov-fail-under=85",
        "--basetemp .venv/release_readiness_ci",
        "ruff check .",
        "ruff format --check .",
        "mypy src/battery_health",
        "uv build --no-sources",
    )
    assert all(command in ci for command in required)
    assert ".pytest_cache/release_readiness_ci" not in ci
    action_lines = [line.strip() for line in ci.splitlines() if line.strip().startswith("uses:")]
    assert action_lines
    assert all(
        re.fullmatch(r"uses: [^@]+@[0-9a-f]{40} # v\d+(?:\.\d+){1,2}", line)
        for line in action_lines
    )


@when("the package license and citation metadata are inspected")
def inspect_release_metadata(release_context: dict[str, Any]) -> None:
    release_context["project"] = _project()
    release_context["license"] = _read("LICENSE")
    release_context["citation"] = _read("CITATION.cff")


@then("the version license author and repository identity agree")
def metadata_is_consistent(release_context: dict[str, Any]) -> None:
    project = release_context["project"]
    license_text = release_context["license"]
    citation = release_context["citation"]
    assert project["version"] == "0.2.0rc1"
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["authors"] == [{"name": "赵一冰"}]
    assert project["urls"]["Repository"] == REPOSITORY_URL
    assert "Copyright (c) 2026 赵一冰" in license_text
    assert re.search(r'(?m)^version:\s*["\']?0\.2\.0rc1["\']?\s*$', citation)
    assert re.search(r'(?m)^license:\s*["\']?MIT["\']?\s*$', citation)
    assert REPOSITORY_URL in citation
    assert "sangtikui642-sketch" in citation


@then("release metadata contains no placeholder identity")
def metadata_has_no_placeholders(release_context: dict[str, Any]) -> None:
    combined = release_context["license"] + release_context["citation"]
    assert PLACEHOLDER_PATTERN.search(combined) is None


@when("the open-source governance documents are inspected")
def inspect_governance(release_context: dict[str, Any]) -> None:
    release_context["governance"] = {path: _read(path) for path in GOVERNANCE_FILES}


@then("every required governance document exists without placeholders")
def governance_is_complete(release_context: dict[str, Any]) -> None:
    governance = release_context["governance"]
    assert set(governance) == set(GOVERNANCE_FILES)
    assert all(content.strip() for content in governance.values())
    assert all(PLACEHOLDER_PATTERN.search(content) is None for content in governance.values())


@then("private vulnerability reporting and external release authorization are explicit")
def security_and_release_authorization_are_explicit(
    release_context: dict[str, Any],
) -> None:
    security = release_context["governance"]["SECURITY.md"]
    checklist = release_context["governance"]["docs/RELEASE_CHECKLIST.md"]
    assert f"{REPOSITORY_URL}/security/advisories/new" in security
    assert "Do not report vulnerabilities in public issues" in security
    assert "Local verification" in checklist
    assert "External actions requiring authorization" in checklist
    assert re.search(r"(?mi)^- \[x\] git commit", checklist)
    assert re.search(r"(?mi)^- \[x\] git push", checklist)
    assert re.search(r"(?mi)^- \[x\] git tag", checklist)
    assert re.search(r"(?mi)^- \[x\] GitHub Release", checklist)
    assert re.search(r"(?m)^- \[ \] PyPI", checklist)


@when("the researcher builds the package and inspects both CLI help surfaces")
def build_and_inspect_cli(release_context: dict[str, Any]) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    build = subprocess.run(
        [uv, "build", "--no-sources", "--out-dir", str(release_context["dist"])],
        cwd=REPOSITORY,
        capture_output=True,
        check=False,
        text=True,
    )
    release_context["build"] = build
    runner = CliRunner()
    release_context["root_help"] = runner.invoke(app, ["--help"])
    release_context["agent_help"] = runner.invoke(app, ["agent", "--help"])
    release_context["version"] = runner.invoke(app, ["version"])


@then("one wheel and one source archive carry version 0.2.0rc1")
def artifacts_have_release_version(release_context: dict[str, Any]) -> None:
    build = release_context["build"]
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = list(release_context["dist"].glob("*.whl"))
    sdists = list(release_context["dist"].glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1
    assert "0.2.0rc1" in wheels[0].name
    assert "0.2.0rc1" in sdists[0].name
    with zipfile.ZipFile(wheels[0]) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith("/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
    assert "Version: 0.2.0rc1" in metadata
    assert "License-Expression: MIT" in metadata


@then("the typed package marker and agent commands are distributed")
def typed_cli_surface_is_distributed(release_context: dict[str, Any]) -> None:
    wheel = next(release_context["dist"].glob("*.whl"))
    sdist = next(release_context["dist"].glob("*.tar.gz"))
    with zipfile.ZipFile(wheel) as archive:
        assert "battery_health/py.typed" in archive.namelist()
    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
        assert any(name.endswith("/LICENSE") for name in names)
        assert any(name.endswith("/src/battery_health/py.typed") for name in names)
    root_help = release_context["root_help"]
    agent_help = release_context["agent_help"]
    version = release_context["version"]
    assert root_help.exit_code == 0, root_help.output
    assert agent_help.exit_code == 0, agent_help.output
    assert version.exit_code == 0, version.output
    assert version.output.strip() == "0.2.0rc1"
    assert "agent" in root_help.output
    for command in (
        "plan",
        "execute",
        "validate",
        "finalize",
        "report",
        "verify",
        "govern-plugins",
        "verify-plugins",
    ):
        assert command in agent_help.output

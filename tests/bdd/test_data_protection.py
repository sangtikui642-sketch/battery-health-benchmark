import importlib
import json
from pathlib import Path
from typing import Any

from pytest_bdd import given, scenarios, then, when

scenarios("../../features/data_protection.feature")


@given(
    "raw battery data or a credential is present in a tracked location",
    target_fixture="safety_context",
)
def disallowed_tracked_content(tmp_path: Path) -> dict[str, Any]:
    repository_root = tmp_path / "repository"
    credential_path = repository_root / "src" / "local.env"
    credential_path.parent.mkdir(parents=True)
    credential_value = "bdd" + "-synthetic-secret-" + "9f41d7c2"
    credential_path.write_text(f"API_KEY={credential_value}\n", encoding="utf-8")
    raw_data_path = repository_root / "data" / "raw" / "battery_cycles.csv"
    raw_data_path.parent.mkdir(parents=True)
    raw_data_path.write_text("cell_id,capacity_ah\ncell-001,2.0\n", encoding="utf-8")
    return {
        "repository_root": repository_root,
        "tracked_files": [Path("src/local.env"), Path("data/raw/battery_cycles.csv")],
        "report_path": tmp_path / "safety_report.json",
        "credential_value": credential_value,
        "error": None,
    }


@given(
    "an acceptance test requires representative battery data",
    target_fixture="fixture_context",
)
def representative_fixture_request(tmp_path: Path) -> dict[str, Any]:
    return {
        "first_path": tmp_path / "first.csv",
        "second_path": tmp_path / "second.csv",
        "error": None,
    }


@when("the researcher runs the repository safety check")
def scan_tracked_content(safety_context: dict[str, Any]) -> None:
    try:
        module = importlib.import_module("battery_health.safety")
        module.check_repository_safety(
            repository_root=safety_context["repository_root"],
            report_path=safety_context["report_path"],
            tracked_files=safety_context["tracked_files"],
        )
    except Exception as error:  # the red phase intentionally captures the missing behavior
        safety_context["error"] = error


@when("the test suite runs")
def generate_representative_fixtures(fixture_context: dict[str, Any]) -> None:
    try:
        module = importlib.import_module("battery_health.demo")
        for path in (fixture_context["first_path"], fixture_context["second_path"]):
            module.generate_anonymous_cycle_csv(
                output_path=path,
                random_seed=2026,
                cell_count=6,
                cycles_per_cell=4,
                reference_capacity_ah=2.0,
            )
    except Exception as error:  # the red phase intentionally captures the missing behavior
        fixture_context["error"] = error


@then("the check fails without displaying the credential value")
def check_fails_safely(safety_context: dict[str, Any]) -> None:
    error = safety_context["error"]
    assert error is not None
    assert error.__class__.__name__ == "RepositorySafetyError"
    assert safety_context["credential_value"] not in str(error)
    report_text = safety_context["report_path"].read_text(encoding="utf-8")
    assert safety_context["credential_value"] not in report_text


@then("the report identifies the disallowed file type")
def report_names_issue_types(safety_context: dict[str, Any]) -> None:
    report = json.loads(safety_context["report_path"].read_text(encoding="utf-8"))
    issue_types = {issue["issue_type"] for issue in report["issues"]}
    assert issue_types == {"credential", "raw_battery_data"}
    assert {issue["path"] for issue in report["issues"]} == {
        "src/local.env",
        "data/raw/battery_cycles.csv",
    }


@then("it uses an anonymous reproducible fixture")
def fixture_is_anonymous_and_repeatable(fixture_context: dict[str, Any]) -> None:
    assert fixture_context["error"] is None, repr(fixture_context["error"])
    first = fixture_context["first_path"].read_bytes()
    second = fixture_context["second_path"].read_bytes()
    assert first == second
    text = first.decode("utf-8")
    assert "cell-000" in text
    assert "company" not in text.lower()


@then("it does not depend on company data or a personal directory")
def fixture_has_no_external_dependency(fixture_context: dict[str, Any]) -> None:
    text = fixture_context["first_path"].read_text(encoding="utf-8")
    assert "Users/" not in text
    assert "Users\\" not in text
    assert "http://" not in text
    assert "https://" not in text

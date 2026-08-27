import csv
import json
from pathlib import Path
from typing import Any

from pytest_bdd import given, scenarios, then, when

from battery_health.data.importer import DataImportError, ImportResult, import_cycle_csv

scenarios("../../features/data_import.feature")


@given(
    "a CSV file containing cell identity, cycle index, and capacity",
    target_fixture="import_context",
)
def valid_cycle_csv(tmp_path: Path) -> dict[str, Any]:
    input_path = tmp_path / "cycles.csv"
    with input_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["cell", "cycle", "capacity"])
        writer.writeheader()
        writer.writerows(
            [
                {"cell": "cell-a", "cycle": 1, "capacity": 1.98},
                {"cell": "cell-a", "cycle": 2, "capacity": 1.95},
                {"cell": "cell-b", "cycle": 1, "capacity": 2.02},
            ]
        )
    return {
        "input_path": input_path,
        "output_dir": tmp_path / "normalized",
        "mapping": {},
        "result": None,
        "error": None,
    }


@given("a CSV file without a cell identity field", target_fixture="import_context")
def invalid_cycle_csv(tmp_path: Path) -> dict[str, Any]:
    input_path = tmp_path / "cycles.csv"
    with input_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["cycle", "capacity"])
        writer.writeheader()
        writer.writerow({"cycle": 1, "capacity": 1.98})
    return {
        "input_path": input_path,
        "output_dir": tmp_path / "normalized",
        "mapping": {},
        "result": None,
        "error": None,
    }


@given("a field mapping with capacity measured in ampere-hours")
def configured_field_mapping(import_context: dict[str, Any]) -> None:
    import_context["mapping"] = {
        "cell_id": "cell",
        "cycle_index": "cycle",
        "capacity_ah": "capacity",
    }


@when("the researcher imports the cycle data")
def run_import(import_context: dict[str, Any]) -> None:
    try:
        import_context["result"] = import_cycle_csv(
            input_path=import_context["input_path"],
            output_dir=import_context["output_dir"],
            field_mapping=import_context["mapping"],
        )
    except DataImportError as error:
        import_context["error"] = error


@then("a normalized cycle file is created")
def normalized_file_exists(import_context: dict[str, Any]) -> None:
    result = import_context["result"]
    assert isinstance(result, ImportResult)
    assert result.normalized_path.is_file()


@then("a quality report records the cell count and cycle count")
def report_has_counts(import_context: dict[str, Any]) -> None:
    result = import_context["result"]
    assert isinstance(result, ImportResult)
    report = json.loads(result.quality_report_path.read_text(encoding="utf-8"))
    assert report["cell_count"] == 2
    assert report["cycle_count"] == 3


@then("the report contains no machine-specific absolute input path")
def report_has_portable_path(import_context: dict[str, Any]) -> None:
    result = import_context["result"]
    assert isinstance(result, ImportResult)
    report_text = result.quality_report_path.read_text(encoding="utf-8")
    assert str(import_context["input_path"].resolve()) not in report_text
    assert json.loads(report_text)["input_file"] == "cycles.csv"


@then("the import fails with an error naming the missing field")
def error_names_missing_field(import_context: dict[str, Any]) -> None:
    error = import_context["error"]
    assert isinstance(error, DataImportError)
    assert "cell_id" in str(error)


@then("no partial normalized output is left behind")
def no_partial_output(import_context: dict[str, Any]) -> None:
    output_dir = import_context["output_dir"]
    assert not (output_dir / "cycles.csv").exists()
    assert not (output_dir / "quality_report.json").exists()

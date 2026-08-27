import json
from pathlib import Path
from typing import Any

import pandas as pd
from pytest_bdd import given, scenarios, then, when

from battery_health.data.quality import DataQualityError, validate_cycle_data

scenarios("../../features/data_quality.feature")


@given("one cell has the same cycle index twice", target_fixture="quality_context")
def duplicate_cycle_data(tmp_path: Path) -> dict[str, Any]:
    data_path = tmp_path / "cycles.csv"
    pd.DataFrame(
        [
            {"cell_id": "cell-a", "cycle_index": 1, "capacity_ah": 2.0},
            {"cell_id": "cell-a", "cycle_index": 1, "capacity_ah": 1.99},
        ]
    ).to_csv(data_path, index=False)
    return {
        "data_path": data_path,
        "report_path": tmp_path / "quality_report.json",
        "error": None,
    }


@given("a cycle has a negative capacity", target_fixture="quality_context")
def negative_capacity_data(tmp_path: Path) -> dict[str, Any]:
    data_path = tmp_path / "cycles.csv"
    pd.DataFrame(
        [
            {"cell_id": "cell-a", "cycle_index": 1, "capacity_ah": 2.0},
            {"cell_id": "cell-b", "cycle_index": 7, "capacity_ah": -0.1},
        ]
    ).to_csv(data_path, index=False)
    return {
        "data_path": data_path,
        "report_path": tmp_path / "quality_report.json",
        "error": None,
    }


@when("the researcher validates the cycle data")
def validate_data(quality_context: dict[str, Any]) -> None:
    try:
        validate_cycle_data(
            data_path=quality_context["data_path"],
            report_path=quality_context["report_path"],
        )
    except DataQualityError as error:
        quality_context["error"] = error


@then("validation fails")
def validation_failed(quality_context: dict[str, Any]) -> None:
    assert isinstance(quality_context["error"], DataQualityError)


@then("the quality report records the duplicate count")
def report_records_duplicates(quality_context: dict[str, Any]) -> None:
    report = json.loads(quality_context["report_path"].read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["duplicate_cycle_count"] == 2


@then("the quality report identifies the cell and cycle")
def report_identifies_invalid_cycle(quality_context: dict[str, Any]) -> None:
    report = json.loads(quality_context["report_path"].read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["invalid_capacity_count"] == 1
    assert {
        "code": "non_positive_capacity",
        "cell_id": "cell-b",
        "cycle_index": 7,
    } in report["issues"]

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pytest_bdd import given, scenarios, then, when

from battery_health.data.labels import (
    SOHLabelError,
    SOHLabelResult,
    generate_soh_labels,
)

scenarios("../../features/soh_labels.feature")


@given("cycles have valid measured capacities", target_fixture="label_context")
def valid_measured_capacities(tmp_path: Path) -> dict[str, Any]:
    data_path = tmp_path / "cycles.csv"
    pd.DataFrame(
        [
            {"cell_id": "cell-a", "cycle_index": 1, "capacity_ah": 2.0},
            {"cell_id": "cell-a", "cycle_index": 2, "capacity_ah": 1.8},
            {"cell_id": "cell-b", "cycle_index": 1, "capacity_ah": 1.6},
        ]
    ).to_csv(data_path, index=False)
    return {
        "data_path": data_path,
        "output_dir": tmp_path / "labelled",
        "reference_capacity_ah": None,
        "reference_capacity_source": None,
        "result": None,
        "error": None,
    }


@given("configuration declares the reference capacity")
def configured_reference_capacity(label_context: dict[str, Any]) -> None:
    label_context["reference_capacity_ah"] = 2.0
    label_context["reference_capacity_source"] = "configuration:nominal_capacity_ah"


@given(
    "neither configuration nor source data provides reference capacity",
    target_fixture="label_context",
)
def missing_reference_capacity(tmp_path: Path) -> dict[str, Any]:
    data_path = tmp_path / "cycles.csv"
    pd.DataFrame(
        [
            {"cell_id": "cell-a", "cycle_index": 1, "capacity_ah": 2.0},
            {"cell_id": "cell-a", "cycle_index": 2, "capacity_ah": 1.8},
        ]
    ).to_csv(data_path, index=False)
    return {
        "data_path": data_path,
        "output_dir": tmp_path / "labelled",
        "reference_capacity_ah": None,
        "reference_capacity_source": None,
        "result": None,
        "error": None,
    }


@when("the researcher generates SOH labels")
def generate_labels(label_context: dict[str, Any]) -> None:
    try:
        label_context["result"] = generate_soh_labels(
            data_path=label_context["data_path"],
            output_dir=label_context["output_dir"],
            reference_capacity_ah=label_context["reference_capacity_ah"],
            reference_capacity_source=label_context["reference_capacity_source"],
        )
    except SOHLabelError as error:
        label_context["error"] = error


@then("each valid cycle receives an SOH value")
def every_cycle_has_soh(label_context: dict[str, Any]) -> None:
    result = label_context["result"]
    assert isinstance(result, SOHLabelResult)
    labelled = pd.read_csv(result.labelled_cycles_path)
    assert labelled["soh"].tolist() == pytest.approx([1.0, 0.9, 0.8])
    assert labelled["reference_capacity_ah"].tolist() == pytest.approx([2.0, 2.0, 2.0])


@then("the output records the reference-capacity source")
def output_records_reference_source(label_context: dict[str, Any]) -> None:
    result = label_context["result"]
    assert isinstance(result, SOHLabelResult)
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["reference_capacity_source"] == "configuration:nominal_capacity_ah"
    assert metadata["target_definition"] == "soh = capacity_ah / reference_capacity_ah"


@then("label generation fails")
def label_generation_failed(label_context: dict[str, Any]) -> None:
    assert isinstance(label_context["error"], SOHLabelError)


@then("the system does not guess a reference capacity")
def no_reference_capacity_is_guessed(label_context: dict[str, Any]) -> None:
    assert "reference capacity" in str(label_context["error"]).lower()
    assert not (label_context["output_dir"] / "cycles_with_soh.csv").exists()
    assert not (label_context["output_dir"] / "soh_metadata.json").exists()

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from battery_health.data.split import SplitManifest
from battery_health.modeling.baselines import (
    BaselineTrainingResult,
    train_baseline,
)

scenarios("../../features/baseline_training.feature")


@given(
    "validated data and a leakage-free split manifest",
    target_fixture="training_context",
)
def validated_labelled_data(tmp_path: Path) -> dict[str, Any]:
    rows: list[dict[str, object]] = []
    cell_offsets = {
        "cell-a": 0.000,
        "cell-b": 0.004,
        "cell-c": -0.003,
        "cell-d": 0.002,
        "cell-e": -0.002,
        "cell-f": 0.003,
        "cell-g": -0.001,
    }
    for cell_id, offset in cell_offsets.items():
        for cycle_index in range(1, 5):
            temperature_c = 24.0 + cycle_index
            rows.append(
                {
                    "cell_id": cell_id,
                    "cycle_index": cycle_index,
                    "temperature_c": temperature_c,
                    "soh": 1.0 - 0.02 * cycle_index + 0.001 * temperature_c + offset,
                }
            )

    data_path = tmp_path / "cycles_with_soh.csv"
    pd.DataFrame(rows).to_csv(data_path, index=False)
    manifest = SplitManifest(
        strategy="group_by_cell",
        random_seed=2026,
        train_cells=("cell-a", "cell-b", "cell-c"),
        validation_cells=("cell-d", "cell-e"),
        test_cells=("cell-f", "cell-g"),
    )
    return {
        "data_path": data_path,
        "manifest": manifest,
        "output_dir": tmp_path / "run",
        "model_name": None,
        "feature_columns": ("cycle_index", "temperature_c"),
        "random_seed": 73,
        "result": None,
    }


@given(parsers.parse("the selected model is {model}"))
def selected_model(training_context: dict[str, Any], model: str) -> None:
    training_context["model_name"] = model


@when("the researcher trains the baseline")
def train_selected_baseline(training_context: dict[str, Any]) -> None:
    training_context["result"] = train_baseline(
        data_path=training_context["data_path"],
        split_manifest=training_context["manifest"],
        output_dir=training_context["output_dir"],
        model_name=training_context["model_name"],
        feature_columns=training_context["feature_columns"],
        random_seed=training_context["random_seed"],
    )


@then("preprocessing is fitted using training data only")
def preprocessing_uses_only_training_data(training_context: dict[str, Any]) -> None:
    result = training_context["result"]
    assert isinstance(result, BaselineTrainingResult)
    metadata = json.loads(result.run_metadata_path.read_text(encoding="utf-8"))
    assert metadata["preprocessing_fit_partition"] == "train"
    assert metadata["preprocessing_fit_cells"] == ["cell-a", "cell-b", "cell-c"]

    frame = pd.read_csv(training_context["data_path"])
    train_rows = frame[frame["cell_id"].isin(training_context["manifest"].train_cells)]
    expected_means = train_rows[list(training_context["feature_columns"])].mean().tolist()
    assert metadata["preprocessing_feature_means"] == pytest.approx(expected_means)


@then("predictions are generated for the test partition")
def predictions_are_saved(training_context: dict[str, Any]) -> None:
    result = training_context["result"]
    assert isinstance(result, BaselineTrainingResult)
    predictions = pd.read_csv(result.predictions_path)
    assert set(predictions["cell_id"]) == {"cell-f", "cell-g"}
    assert predictions["partition"].unique().tolist() == ["test"]
    assert len(predictions) == 8
    assert predictions["predicted_soh"].notna().all()


@then("model configuration and random seed are saved")
def configuration_is_traceable(training_context: dict[str, Any]) -> None:
    result = training_context["result"]
    assert isinstance(result, BaselineTrainingResult)
    metadata = json.loads(result.run_metadata_path.read_text(encoding="utf-8"))
    assert metadata["model_name"] == training_context["model_name"]
    assert metadata["random_seed"] == 73
    assert metadata["feature_columns"] == ["cycle_index", "temperature_c"]
    assert metadata["split_strategy"] == "group_by_cell"

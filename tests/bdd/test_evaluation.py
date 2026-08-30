import importlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pytest_bdd import given, scenarios, then, when

from battery_health.data.split import SplitManifest

scenarios("../../features/evaluation.feature")


def _prediction_rows() -> list[dict[str, object]]:
    return [
        {
            "cell_id": "cell-test-a",
            "cycle_index": 1,
            "actual_soh": 1.00,
            "predicted_soh": 0.99,
        },
        {
            "cell_id": "cell-test-a",
            "cycle_index": 2,
            "actual_soh": 0.95,
            "predicted_soh": 0.96,
        },
        {
            "cell_id": "cell-test-b",
            "cycle_index": 1,
            "actual_soh": 0.90,
            "predicted_soh": 0.89,
        },
        {
            "cell_id": "cell-test-b",
            "cycle_index": 2,
            "actual_soh": 0.85,
            "predicted_soh": 0.86,
        },
    ]


@given("test data has actual and predicted SOH values", target_fixture="evaluation_context")
def prediction_data(tmp_path: Path) -> dict[str, Any]:
    predictions_path = tmp_path / "test_predictions.csv"
    pd.DataFrame(_prediction_rows()).to_csv(predictions_path, index=False)
    return {
        "predictions_path": predictions_path,
        "manifest": SplitManifest(
            strategy="group_by_cell",
            random_seed=2026,
            train_cells=("cell-train",),
            validation_cells=("cell-validation",),
            test_cells=("cell-test-a", "cell-test-b"),
        ),
        "output_dir": tmp_path / "evaluation",
        "result": None,
        "error": None,
    }


@given("the split manifest contains no test cells", target_fixture="evaluation_context")
def empty_test_partition(tmp_path: Path) -> dict[str, Any]:
    predictions_path = tmp_path / "test_predictions.csv"
    pd.DataFrame(_prediction_rows()).to_csv(predictions_path, index=False)
    return {
        "predictions_path": predictions_path,
        "manifest": SplitManifest(
            strategy="group_by_cell",
            random_seed=2026,
            train_cells=("cell-train",),
            validation_cells=("cell-validation",),
            test_cells=(),
        ),
        "output_dir": tmp_path / "evaluation",
        "result": None,
        "error": None,
    }


@when("the researcher evaluates the predictions")
def evaluate_test_predictions(evaluation_context: dict[str, Any]) -> None:
    try:
        module = importlib.import_module("battery_health.evaluation")
        evaluation_context["result"] = module.evaluate_predictions(
            predictions_path=evaluation_context["predictions_path"],
            split_manifest=evaluation_context["manifest"],
            output_dir=evaluation_context["output_dir"],
        )
    except Exception as error:  # the red phase intentionally captures the missing behavior
        evaluation_context["error"] = error


@then("the report contains MAE RMSE MAPE and R-squared")
def report_has_required_metrics(evaluation_context: dict[str, Any]) -> None:
    result = evaluation_context["result"]
    assert result is not None, repr(evaluation_context["error"])
    metrics = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    assert {"mae", "rmse", "mape", "r_squared"} <= metrics.keys()
    assert metrics["mae"] == pytest.approx(0.01)
    assert metrics["metric_definitions"]["mape"]["unit"] == "percent"


@then("the report records test sample and cell counts")
def report_has_test_counts(evaluation_context: dict[str, Any]) -> None:
    result = evaluation_context["result"]
    metrics = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    assert metrics["test_sample_count"] == 4
    assert metrics["test_cell_count"] == 2


@then("per-cycle predictions and a comparison plot are saved")
def predictions_and_plot_are_saved(evaluation_context: dict[str, Any]) -> None:
    result = evaluation_context["result"]
    saved = pd.read_csv(result.predictions_path)
    assert len(saved) == 4
    assert result.plot_path.is_file()
    assert result.plot_path.stat().st_size > 0


@then("evaluation fails")
def evaluation_failed(evaluation_context: dict[str, Any]) -> None:
    error = evaluation_context["error"]
    assert error is not None
    assert error.__class__.__name__ == "EvaluationError"


@then("no apparently valid metrics file is generated")
def no_metrics_are_written(evaluation_context: dict[str, Any]) -> None:
    assert not (evaluation_context["output_dir"] / "metrics.json").exists()

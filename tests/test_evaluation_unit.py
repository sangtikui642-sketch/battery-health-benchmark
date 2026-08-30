from pathlib import Path

import pandas as pd
import pytest

from battery_health.data.split import SplitManifest
from battery_health.evaluation import EvaluationError, evaluate_predictions


def _manifest(*, test_cells: tuple[str, ...] = ("cell-test",)) -> SplitManifest:
    return SplitManifest(
        strategy="group_by_cell",
        random_seed=2026,
        train_cells=("cell-train",),
        validation_cells=("cell-validation",),
        test_cells=test_cells,
    )


def _write_predictions(path: Path, rows: list[dict[str, object]]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def test_rejects_zero_actual_soh_before_writing_metrics(tmp_path: Path) -> None:
    predictions_path = tmp_path / "predictions.csv"
    _write_predictions(
        predictions_path,
        [
            {
                "cell_id": "cell-test",
                "cycle_index": 1,
                "actual_soh": 0.0,
                "predicted_soh": 0.1,
            }
        ],
    )
    output_dir = tmp_path / "evaluation"

    with pytest.raises(EvaluationError, match="actual SOH is zero"):
        evaluate_predictions(
            predictions_path=predictions_path,
            split_manifest=_manifest(),
            output_dir=output_dir,
        )

    assert not (output_dir / "metrics.json").exists()


def test_rejects_prediction_from_a_non_test_cell(tmp_path: Path) -> None:
    predictions_path = tmp_path / "predictions.csv"
    _write_predictions(
        predictions_path,
        [
            {
                "cell_id": "cell-train",
                "cycle_index": 1,
                "actual_soh": 0.9,
                "predicted_soh": 0.9,
            }
        ],
    )

    with pytest.raises(EvaluationError, match="non-test cells"):
        evaluate_predictions(
            predictions_path=predictions_path,
            split_manifest=_manifest(),
            output_dir=tmp_path / "evaluation",
        )


def test_constant_actual_target_uses_documented_r_squared_policy(tmp_path: Path) -> None:
    predictions_path = tmp_path / "predictions.csv"
    _write_predictions(
        predictions_path,
        [
            {
                "cell_id": "cell-test",
                "cycle_index": cycle,
                "actual_soh": 0.9,
                "predicted_soh": 0.8,
            }
            for cycle in (1, 2)
        ],
    )

    result = evaluate_predictions(
        predictions_path=predictions_path,
        split_manifest=_manifest(),
        output_dir=tmp_path / "evaluation",
    )

    import json

    metrics = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    assert metrics["r_squared"] == 0.0

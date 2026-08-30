from pathlib import Path

import pandas as pd
import pytest

from battery_health.data.split import SplitManifest, SplitValidationError
from battery_health.modeling.baselines import BaselineTrainingError, train_baseline


def _labelled_data(tmp_path: Path) -> Path:
    data_path = tmp_path / "cycles_with_soh.csv"
    rows = [
        {
            "cell_id": cell_id,
            "cycle_index": cycle_index,
            "temperature_c": 24.0 + cycle_index,
            "soh": 1.0 - 0.015 * cycle_index + offset,
        }
        for cell_id, offset in {
            "cell-a": 0.000,
            "cell-b": 0.003,
            "cell-c": -0.002,
            "cell-d": 0.001,
            "cell-e": -0.001,
            "cell-f": 0.002,
        }.items()
        for cycle_index in range(1, 5)
    ]
    pd.DataFrame(rows).to_csv(data_path, index=False)
    return data_path


def _valid_manifest() -> SplitManifest:
    return SplitManifest(
        strategy="group_by_cell",
        random_seed=2026,
        train_cells=("cell-a", "cell-b", "cell-c"),
        validation_cells=("cell-d",),
        test_cells=("cell-e", "cell-f"),
    )


def test_rejects_unregistered_model_before_writing_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "run"

    with pytest.raises(BaselineTrainingError, match="Unsupported model"):
        train_baseline(
            data_path=_labelled_data(tmp_path),
            split_manifest=_valid_manifest(),
            output_dir=output_dir,
            model_name="unreviewed_transformer",
            feature_columns=("cycle_index", "temperature_c"),
            random_seed=1,
        )

    assert not output_dir.exists()


def test_rejects_leaking_split_before_training(tmp_path: Path) -> None:
    leaking_manifest = SplitManifest(
        strategy="group_by_cell",
        random_seed=2026,
        train_cells=("cell-a", "cell-b", "cell-c"),
        validation_cells=("cell-d",),
        test_cells=("cell-a", "cell-e", "cell-f"),
    )
    output_dir = tmp_path / "run"

    with pytest.raises(SplitValidationError, match="cell-a"):
        train_baseline(
            data_path=_labelled_data(tmp_path),
            split_manifest=leaking_manifest,
            output_dir=output_dir,
            model_name="linear_regression",
            feature_columns=("cycle_index", "temperature_c"),
            random_seed=1,
        )

    assert not output_dir.exists()


def test_rejects_target_as_input_feature(tmp_path: Path) -> None:
    with pytest.raises(BaselineTrainingError, match="cannot be used as an input feature"):
        train_baseline(
            data_path=_labelled_data(tmp_path),
            split_manifest=_valid_manifest(),
            output_dir=tmp_path / "run",
            model_name="linear_regression",
            feature_columns=("cycle_index", "soh"),
            random_seed=1,
        )


def test_random_forest_is_repeatable_for_the_same_seed(tmp_path: Path) -> None:
    data_path = _labelled_data(tmp_path)
    first = train_baseline(
        data_path=data_path,
        split_manifest=_valid_manifest(),
        output_dir=tmp_path / "run-one",
        model_name="random_forest",
        feature_columns=("cycle_index", "temperature_c"),
        random_seed=73,
    )
    second = train_baseline(
        data_path=data_path,
        split_manifest=_valid_manifest(),
        output_dir=tmp_path / "run-two",
        model_name="random_forest",
        feature_columns=("cycle_index", "temperature_c"),
        random_seed=73,
    )

    first_predictions = pd.read_csv(first.predictions_path)
    second_predictions = pd.read_csv(second.predictions_path)
    pd.testing.assert_frame_equal(first_predictions, second_predictions)

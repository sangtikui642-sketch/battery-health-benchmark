import json
from pathlib import Path

import pandas as pd
import pytest

from battery_health.reproducibility import (
    ReproducibilityError,
    predictions_agree,
    record_reproducible_run,
    sha256_file,
)


def _inputs(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "input_path": tmp_path / "input.csv",
        "config_path": tmp_path / "config.yaml",
        "split_manifest_path": tmp_path / "split.json",
        "dependency_lock_path": tmp_path / "uv.lock",
        "predictions_path": tmp_path / "source_predictions.csv",
    }
    paths["input_path"].write_text("cell_id,cycle_index\ncell-001,1\n", encoding="utf-8")
    paths["config_path"].write_text('{"seed": 7}\n', encoding="utf-8")
    paths["split_manifest_path"].write_text('{"test_cells": ["cell-001"]}\n', encoding="utf-8")
    paths["dependency_lock_path"].write_text("version = 1\n", encoding="utf-8")
    pd.DataFrame(
        [
            {
                "cell_id": "cell-001",
                "cycle_index": 1,
                "actual_soh": 0.9,
                "predicted_soh": 0.89,
            }
        ]
    ).to_csv(paths["predictions_path"], index=False)
    return paths


def test_record_contains_portable_hashes_and_unavailable_git(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    result = record_reproducible_run(
        run_dir=tmp_path / "run",
        effective_config={"seed": 7, "target": "soh"},
        random_seed=7,
        deterministic_tolerance=1e-12,
        repository_path=tmp_path / "not-a-repository",
        **paths,
    )

    manifest_text = result.manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["git"] == {"revision": None, "status": "unavailable"}
    assert manifest["sha256"]["input"] == sha256_file(result.run_dir / "input.csv")
    assert str(tmp_path.resolve()) not in manifest_text


def test_record_refuses_to_overwrite_an_existing_run(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    run_dir = tmp_path / "existing-run"
    run_dir.mkdir()

    with pytest.raises(ReproducibilityError, match="already exists"):
        record_reproducible_run(
            run_dir=run_dir,
            effective_config={"seed": 7},
            random_seed=7,
            deterministic_tolerance=0.0,
            repository_path=tmp_path,
            **paths,
        )


def test_record_rejects_an_absolute_path_in_effective_config(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    synthetic_absolute_path = "C:" + "\\personal" + "\\battery.csv"

    with pytest.raises(ReproducibilityError, match="absolute path"):
        record_reproducible_run(
            run_dir=tmp_path / "run",
            effective_config={"local_data": synthetic_absolute_path},
            random_seed=7,
            deterministic_tolerance=0.0,
            repository_path=tmp_path,
            **paths,
        )


def test_prediction_comparison_detects_values_outside_tolerance(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    second_path = tmp_path / "second.csv"
    second = pd.read_csv(paths["predictions_path"])
    second["predicted_soh"] = 0.88
    second.to_csv(second_path, index=False)

    assert not predictions_agree(paths["predictions_path"], second_path, tolerance=1e-4)

import importlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
from pytest_bdd import given, scenarios, then, when

scenarios("../../features/reproducibility.feature")


@given(
    "data manifest configuration dependency lock and random seed are unchanged",
    target_fixture="reproducibility_context",
)
def unchanged_run_inputs(tmp_path: Path) -> dict[str, Any]:
    input_path = tmp_path / "cycles.csv"
    input_path.write_text("cell_id,cycle_index,soh\ncell-001,1,0.98\n", encoding="utf-8")
    config_path = tmp_path / "demo.yaml"
    config_path.write_text('{"random_seed": 73}\n', encoding="utf-8")
    split_path = tmp_path / "split_manifest.json"
    split_path.write_text(
        json.dumps(
            {
                "strategy": "group_by_cell",
                "random_seed": 2026,
                "train_cells": ["cell-001"],
                "validation_cells": ["cell-002"],
                "test_cells": ["cell-003"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    lock_path = tmp_path / "uv.lock"
    lock_path.write_text('version = 1\nrequires-python = ">=3.12"\n', encoding="utf-8")
    predictions_path = tmp_path / "predictions.csv"
    pd.DataFrame(
        [
            {
                "cell_id": "cell-003",
                "cycle_index": 1,
                "actual_soh": 0.94,
                "predicted_soh": 0.939,
            }
        ]
    ).to_csv(predictions_path, index=False)
    return {
        "input_path": input_path,
        "config_path": config_path,
        "split_path": split_path,
        "lock_path": lock_path,
        "predictions_path": predictions_path,
        "effective_config": {"random_seed": 73, "target": "soh"},
        "random_seed": 73,
        "tolerance": 1e-12,
        "run_root": tmp_path / "runs",
        "records": [],
        "error": None,
    }


@when("the researcher runs the same experiment twice")
def record_two_runs(reproducibility_context: dict[str, Any]) -> None:
    try:
        module = importlib.import_module("battery_health.reproducibility")
        for run_name in ("run-001", "run-002"):
            record = module.record_reproducible_run(
                run_dir=reproducibility_context["run_root"] / run_name,
                effective_config=reproducibility_context["effective_config"],
                input_path=reproducibility_context["input_path"],
                config_path=reproducibility_context["config_path"],
                split_manifest_path=reproducibility_context["split_path"],
                dependency_lock_path=reproducibility_context["lock_path"],
                predictions_path=reproducibility_context["predictions_path"],
                random_seed=reproducibility_context["random_seed"],
                deterministic_tolerance=reproducibility_context["tolerance"],
                repository_path=reproducibility_context["run_root"],
            )
            reproducibility_context["records"].append(record)
    except Exception as error:  # the red phase intentionally captures the missing behavior
        reproducibility_context["error"] = error


@then("both runs use the same split")
def runs_share_the_split(reproducibility_context: dict[str, Any]) -> None:
    records = reproducibility_context["records"]
    assert len(records) == 2, repr(reproducibility_context["error"])
    manifests = [json.loads(record.manifest_path.read_text(encoding="utf-8")) for record in records]
    assert manifests[0]["sha256"]["split_manifest"] == manifests[1]["sha256"]["split_manifest"]


@then("deterministic predictions agree within the documented tolerance")
def predictions_are_repeatable(reproducibility_context: dict[str, Any]) -> None:
    module = importlib.import_module("battery_health.reproducibility")
    first, second = reproducibility_context["records"]
    assert module.predictions_agree(
        first.predictions_path,
        second.predictions_path,
        tolerance=reproducibility_context["tolerance"],
    )
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["deterministic_tolerance"] == reproducibility_context["tolerance"]


@then("each run has a separate traceable record")
def each_run_is_traceable(reproducibility_context: dict[str, Any]) -> None:
    first, second = reproducibility_context["records"]
    assert first.run_dir != second.run_dir
    assert first.manifest_path.is_file()
    assert second.manifest_path.is_file()
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["random_seed"] == 73
    assert set(manifest["sha256"]) >= {
        "input",
        "source_config",
        "split_manifest",
        "dependency_lock",
    }
    assert manifest["git"]["status"] in {"available", "unavailable"}

import json
from pathlib import Path
from typing import Any

from pytest_bdd import given, scenarios, then, when

from battery_health.data.split import (
    SplitManifest,
    SplitValidationError,
    create_group_split,
    validate_split_manifest,
)

scenarios("../../features/data_split.feature")


@given(
    "normalized data containing several cells and cycles",
    target_fixture="split_context",
)
def normalized_cell_groups(tmp_path: Path) -> dict[str, Any]:
    cell_ids = [cell_id for index in range(10) for cell_id in (f"cell-{index:02d}",) * 3]
    return {
        "cell_ids": cell_ids,
        "manifest_path": tmp_path / "split_manifest.json",
        "manifest": None,
        "error": None,
    }


@given("a cell identity appears in both train and test", target_fixture="split_context")
def leaking_manifest() -> dict[str, Any]:
    return {
        "manifest": SplitManifest(
            strategy="group_by_cell",
            random_seed=42,
            train_cells=("cell-a", "cell-b"),
            validation_cells=("cell-c",),
            test_cells=("cell-a", "cell-d"),
        ),
        "error": None,
    }


@when("the researcher creates a split using cell identity")
def create_split(split_context: dict[str, Any]) -> None:
    split_context["manifest"] = create_group_split(
        cell_ids=split_context["cell_ids"],
        manifest_path=split_context["manifest_path"],
        random_seed=2026,
        train_fraction=0.6,
        validation_fraction=0.2,
    )


@when("the researcher validates the split manifest")
def validate_manifest(split_context: dict[str, Any]) -> None:
    try:
        validate_split_manifest(split_context["manifest"])
    except SplitValidationError as error:
        split_context["error"] = error


@then("each cell occurs in exactly one partition")
def cells_are_disjoint(split_context: dict[str, Any]) -> None:
    manifest = split_context["manifest"]
    assert isinstance(manifest, SplitManifest)
    train = set(manifest.train_cells)
    validation = set(manifest.validation_cells)
    test = set(manifest.test_cells)
    assert train.isdisjoint(validation)
    assert train.isdisjoint(test)
    assert validation.isdisjoint(test)
    assert train | validation | test == set(split_context["cell_ids"])


@then("the split manifest records the random seed and cell lists")
def manifest_is_traceable(split_context: dict[str, Any]) -> None:
    manifest = split_context["manifest"]
    assert isinstance(manifest, SplitManifest)
    stored = json.loads(split_context["manifest_path"].read_text(encoding="utf-8"))
    assert stored["strategy"] == "group_by_cell"
    assert stored["random_seed"] == 2026
    assert stored["train_cells"] == list(manifest.train_cells)
    assert stored["validation_cells"] == list(manifest.validation_cells)
    assert stored["test_cells"] == list(manifest.test_cells)


@then("validation fails")
def split_validation_failed(split_context: dict[str, Any]) -> None:
    assert isinstance(split_context["error"], SplitValidationError)


@then("the error identifies the overlapping cell")
def error_names_leaking_cell(split_context: dict[str, Any]) -> None:
    assert "cell-a" in str(split_context["error"])

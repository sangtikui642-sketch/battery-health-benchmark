import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

import battery_health.agent.planning as planning
from battery_health.agent.execution import execute_experiment_plan
from battery_health.agent.validation import (
    ValidationError,
    finalize_locked_selection,
    validate_and_lock_execution,
    verify_selection_lock,
)
from tests.bdd.test_agent_execution import _execution_context
from tests.bdd.test_agent_planning import MODELS


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _executed_context(tmp_path: Path) -> dict[str, Any]:
    context = _execution_context(tmp_path)
    execute_experiment_plan(
        plan_dir=context["plan_dir"],
        evidence_dir=context["evidence_dir"],
        output_dir=context["output_dir"],
    )
    context["validation_dir"] = tmp_path / "validation"
    context["final_dir"] = tmp_path / "final"
    return context


def _validated_context(tmp_path: Path) -> dict[str, Any]:
    context = _executed_context(tmp_path)
    result = validate_and_lock_execution(
        execution_dir=context["output_dir"],
        output_dir=context["validation_dir"],
    )
    assert result.state == "LOCKED"
    return context


def _prediction_path(context: dict[str, Any], candidate_id: str) -> Path:
    return context["output_dir"] / "candidates" / candidate_id / "seed-73" / "predictions.csv"


def test_missing_predictions_reject_every_candidate_without_a_lock(tmp_path: Path) -> None:
    context = _executed_context(tmp_path)
    for candidate_id in MODELS:
        _prediction_path(context, candidate_id).unlink()

    result = validate_and_lock_execution(
        execution_dir=context["output_dir"],
        output_dir=context["validation_dir"],
    )

    assert result.state == "REJECTED"
    assert result.selection_fingerprint is None
    assert not (context["validation_dir"] / "selection_lock.json").exists()
    for candidate_id in MODELS:
        candidate = _read_json(
            context["validation_dir"] / "candidate_validations" / f"{candidate_id}.json"
        )
        gate = next(
            item for item in candidate["gates"] if item["gate_id"] == "finite_complete_predictions"
        )
        assert gate["passed"] is False


def test_nonfinite_prediction_is_ineligible_and_never_corrected(tmp_path: Path) -> None:
    context = _executed_context(tmp_path)
    path = _prediction_path(context, "linear_regression")
    frame = pd.read_csv(path)
    frame.loc[0, "predicted_soh"] = np.nan
    frame.to_csv(path, index=False)
    before = path.read_bytes()

    result = validate_and_lock_execution(
        execution_dir=context["output_dir"],
        output_dir=context["validation_dir"],
    )

    assert result.state == "LOCKED"
    assert path.read_bytes() == before
    lock = _read_json(context["validation_dir"] / "selection_lock.json")
    assert lock["selected_candidate"]["plugin_id"] != "linear_regression"


def test_self_consistent_overlapping_split_is_rejected_before_output(tmp_path: Path) -> None:
    context = _executed_context(tmp_path)
    plan_dir = context["output_dir"] / "plan"
    split_path = plan_dir / "split_snapshot.json"
    split = _read_json(split_path)
    split["test_cells"] = ["cell-c"]
    _write_json(split_path, split)

    plan_path = plan_dir / "plan.json"
    plan = _read_json(plan_path)
    plan["fingerprints"]["split_manifest"] = _sha256(split_path)
    plan["split"]["manifest_fingerprint"] = _sha256(split_path)
    fingerprint = planning._canonical_fingerprint(planning._fingerprint_payload(plan))
    plan["plan_fingerprint"] = fingerprint
    plan["plan_id"] = "plan-" + fingerprint[:16]
    _write_json(plan_path, plan)

    execution_manifest_path = context["output_dir"] / "execution_manifest.json"
    execution_manifest = _read_json(execution_manifest_path)
    execution_manifest["plan_id"] = plan["plan_id"]
    execution_manifest["plan_fingerprint"] = fingerprint
    execution_manifest["split_manifest_fingerprint"] = _sha256(split_path)
    for summary in execution_manifest["candidates"]:
        record_path = context["output_dir"] / summary["record"]
        record = _read_json(record_path)
        record["plan_id"] = plan["plan_id"]
        record["plan_fingerprint"] = fingerprint
        record["split_manifest_fingerprint"] = _sha256(split_path)
        _write_json(record_path, record)
        summary["record_sha256"] = _sha256(record_path)
    _write_json(execution_manifest_path, execution_manifest)

    with pytest.raises(ValidationError, match="leakage-safe"):
        validate_and_lock_execution(
            execution_dir=context["output_dir"],
            output_dir=context["validation_dir"],
        )

    assert not context["validation_dir"].exists()


def test_tampered_execution_input_produces_structured_rejection(tmp_path: Path) -> None:
    context = _executed_context(tmp_path)
    input_path = context["output_dir"] / "inputs" / "train_validation_soh.csv"
    input_path.write_text(input_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = validate_and_lock_execution(
        execution_dir=context["output_dir"],
        output_dir=context["validation_dir"],
    )

    assert result.state == "REJECTED"
    manifest = _read_json(context["validation_dir"] / "validation_manifest.json")
    assert manifest["run_gates"]["execution_input_integrity"]["passed"] is False
    assert not (context["validation_dir"] / "selection_lock.json").exists()


def test_tampered_candidate_record_cannot_win(tmp_path: Path) -> None:
    context = _executed_context(tmp_path)
    record_path = (
        context["output_dir"] / "candidates" / "linear_regression" / "candidate_record.json"
    )
    record_path.write_text(record_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = validate_and_lock_execution(
        execution_dir=context["output_dir"],
        output_dir=context["validation_dir"],
    )

    assert result.state == "LOCKED"
    candidate = _read_json(
        context["validation_dir"] / "candidate_validations" / "linear_regression.json"
    )
    assert candidate["record_fingerprint_verified"] is False
    assert candidate["eligible"] is False
    lock = _read_json(context["validation_dir"] / "selection_lock.json")
    assert lock["selected_candidate"]["plugin_id"] != "linear_regression"


def test_cell_bootstrap_and_selection_fingerprint_are_deterministic(tmp_path: Path) -> None:
    context = _executed_context(tmp_path)
    first_dir = context["validation_dir"]
    second_dir = tmp_path / "second-validation"

    first = validate_and_lock_execution(
        execution_dir=context["output_dir"],
        output_dir=first_dir,
    )
    second = validate_and_lock_execution(
        execution_dir=context["output_dir"],
        output_dir=second_dir,
    )

    assert first.selection_fingerprint == second.selection_fingerprint
    assert (first_dir / "leaderboard.json").read_bytes() == (
        second_dir / "leaderboard.json"
    ).read_bytes()
    leaderboard = _read_json(first_dir / "leaderboard.json")
    eligible = next(item for item in leaderboard["candidates"] if item["eligible"])
    assert eligible["uncertainty"]["method"] == "bootstrap_by_cell"
    assert eligible["uncertainty"]["bootstrap_unit"] == "cell_id"
    assert eligible["uncertainty"]["confidence_level"] == 0.95
    assert eligible["uncertainty"]["resamples"] == 1000
    assert eligible["uncertainty"]["random_seed"] == 73


def test_declared_lexicographic_tie_breaker_is_deterministic(tmp_path: Path) -> None:
    context = _executed_context(tmp_path)
    source_root = context["output_dir"] / "candidates" / "linear_regression" / "seed-73"
    for candidate_id in ("random_forest", "histogram_gradient_boosting"):
        target_root = context["output_dir"] / "candidates" / candidate_id / "seed-73"
        shutil.copyfile(source_root / "predictions.csv", target_root / "predictions.csv")
        shutil.copyfile(source_root / "metrics.json", target_root / "metrics.json")

    validate_and_lock_execution(
        execution_dir=context["output_dir"],
        output_dir=context["validation_dir"],
    )

    lock = _read_json(context["validation_dir"] / "selection_lock.json")
    assert lock["selected_candidate"]["plugin_id"] == "histogram_gradient_boosting"


def test_validation_output_is_non_overwriting(tmp_path: Path) -> None:
    context = _executed_context(tmp_path)
    context["validation_dir"].mkdir()
    marker = context["validation_dir"] / "preserve.txt"
    marker.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="already exists"):
        validate_and_lock_execution(
            execution_dir=context["output_dir"],
            output_dir=context["validation_dir"],
        )

    assert marker.read_text(encoding="utf-8") == "preserve\n"


def test_tampered_selection_lock_is_rejected_before_test_access(tmp_path: Path) -> None:
    context = _validated_context(tmp_path)
    lock_path = context["validation_dir"] / "selection_lock.json"
    lock = _read_json(lock_path)
    lock["configuration"]["finalization_seed"] = 999
    _write_json(lock_path, lock)

    assert verify_selection_lock(lock_path) is False
    with pytest.raises(ValidationError, match="fingerprint"):
        finalize_locked_selection(
            validation_dir=context["validation_dir"],
            evidence_dir=context["evidence_dir"],
            output_dir=context["final_dir"],
        )

    assert not (context["validation_dir"] / "finalization_claim.json").exists()
    assert not context["final_dir"].exists()


def test_finalization_output_is_non_overwriting_and_does_not_claim_test(tmp_path: Path) -> None:
    context = _validated_context(tmp_path)
    context["final_dir"].mkdir()
    marker = context["final_dir"] / "preserve.txt"
    marker.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="already exists"):
        finalize_locked_selection(
            validation_dir=context["validation_dir"],
            evidence_dir=context["evidence_dir"],
            output_dir=context["final_dir"],
        )

    assert marker.read_text(encoding="utf-8") == "preserve\n"
    assert not (context["validation_dir"] / "finalization_claim.json").exists()

import importlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pytest_bdd import given, scenarios, then, when
from typer.testing import CliRunner

from battery_health.agent.execution import execute_experiment_plan
from battery_health.cli import app
from tests.bdd.test_agent_execution import _execution_context
from tests.bdd.test_agent_planning import MODELS

scenarios("../../features/agent_validation.feature")


def _completed_execution(tmp_path: Path) -> dict[str, Any]:
    context = _execution_context(tmp_path)
    execution = execute_experiment_plan(
        plan_dir=context["plan_dir"],
        evidence_dir=context["evidence_dir"],
        output_dir=context["output_dir"],
    )
    return {
        **context,
        "execution_result": execution,
        "validation_dir": tmp_path / "validation",
        "final_dir": tmp_path / "final",
        "validation_result": None,
        "finalization_result": None,
        "error": None,
    }


def _predictions_path(context: dict[str, Any], candidate_id: str) -> Path:
    return context["output_dir"] / "candidates" / candidate_id / "seed-73" / "predictions.csv"


def _make_predictions_implausible(context: dict[str, Any], candidate_ids: tuple[str, ...]) -> None:
    for candidate_id in candidate_ids:
        path = _predictions_path(context, candidate_id)
        frame = pd.read_csv(path)
        frame["predicted_soh"] = 2.0
        frame.to_csv(path, index=False)


def _validate(context: dict[str, Any]) -> None:
    try:
        module = importlib.import_module("battery_health.agent.validation")
        context["validation_result"] = module.validate_and_lock_execution(
            execution_dir=context["output_dir"],
            output_dir=context["validation_dir"],
        )
    except Exception as error:  # red phase captures the missing FR-11 module
        context["error"] = error


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@given(
    "completed candidate runs with validation predictions",
    target_fixture="validation_context",
)
def completed_candidate_runs(tmp_path: Path) -> dict[str, Any]:
    return _completed_execution(tmp_path)


@given(
    "a candidate predicts SOH outside the configured plausible range",
    target_fixture="validation_context",
)
def implausible_candidate(tmp_path: Path) -> dict[str, Any]:
    context = _completed_execution(tmp_path)
    _make_predictions_implausible(context, ("linear_regression",))
    return context


@given(
    "all completed candidates fail at least one mandatory gate",
    target_fixture="validation_context",
)
def all_candidates_fail(tmp_path: Path) -> dict[str, Any]:
    context = _completed_execution(tmp_path)
    _make_predictions_implausible(context, MODELS)
    return context


@given(
    "completed candidate evidence claims access to the test partition",
    target_fixture="validation_context",
)
def execution_claims_test_access(tmp_path: Path) -> dict[str, Any]:
    context = _completed_execution(tmp_path)
    manifest_path = context["output_dir"] / "execution_manifest.json"
    manifest = _read_json(manifest_path)
    manifest["test_partition_accessed"] = True
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return context


@given(
    "a selected candidate and configuration are locked",
    target_fixture="finalization_context",
)
def locked_candidate(tmp_path: Path) -> dict[str, Any]:
    context = _completed_execution(tmp_path)
    _validate(context)
    return context


@given("all mandatory validation gates passed")
def mandatory_gates_passed(finalization_context: dict[str, Any]) -> None:
    if finalization_context["validation_result"] is not None:
        assert finalization_context["validation_result"].state == "LOCKED"


@given(
    "portable evidence and a completed candidate execution bundle",
    target_fixture="cli_validation_context",
)
def cli_validation_inputs(tmp_path: Path) -> dict[str, Any]:
    return _completed_execution(tmp_path)


@when("the agent applies the declared selection policy")
def apply_selection_policy(validation_context: dict[str, Any]) -> None:
    _validate(validation_context)


@when("the agent applies the mandatory validation gates")
def apply_mandatory_validation_gates(validation_context: dict[str, Any]) -> None:
    _validate(validation_context)


@when("the researcher finalizes the run")
def finalize_run(finalization_context: dict[str, Any]) -> None:
    if finalization_context["validation_result"] is None:
        return
    try:
        module = importlib.import_module("battery_health.agent.validation")
        finalization_context["finalization_result"] = module.finalize_locked_selection(
            validation_dir=finalization_context["validation_dir"],
            evidence_dir=finalization_context["evidence_dir"],
            output_dir=finalization_context["final_dir"],
        )
    except Exception as error:
        finalization_context["error"] = error


@when("the researcher validates and finalizes the run through the CLI")
def validate_and_finalize_cli(cli_validation_context: dict[str, Any]) -> None:
    runner = CliRunner()
    cli_validation_context["validate_cli"] = runner.invoke(
        app,
        [
            "agent",
            "validate",
            "--execution",
            str(cli_validation_context["output_dir"]),
            "--output",
            str(cli_validation_context["validation_dir"]),
        ],
    )
    cli_validation_context["finalize_cli"] = runner.invoke(
        app,
        [
            "agent",
            "finalize",
            "--validation",
            str(cli_validation_context["validation_dir"]),
            "--evidence",
            str(cli_validation_context["evidence_dir"]),
            "--output",
            str(cli_validation_context["final_dir"]),
        ],
    )


@then("candidates are ranked without access to test labels or test metrics")
def ranked_without_test_access(validation_context: dict[str, Any]) -> None:
    assert validation_context["error"] is None
    leaderboard = _read_json(validation_context["validation_dir"] / "leaderboard.json")
    assert leaderboard["evidence_partition"] == "validation"
    assert leaderboard["test_partition_accessed"] is False
    assert {entry["candidate_id"] for entry in leaderboard["candidates"]} == set(MODELS)


@then("the selected plugin version and configuration are locked")
def selected_configuration_is_locked(validation_context: dict[str, Any]) -> None:
    lock = _read_json(validation_context["validation_dir"] / "selection_lock.json")
    assert lock["state"] == "LOCKED"
    assert lock["selected_candidate"]["plugin_id"] in MODELS
    assert lock["selected_candidate"]["implementation_version"] == "0.1.0"
    assert lock["configuration"]["finalization_seed"] == 73
    assert lock["selection_fingerprint"]


@then("the selection decision links to validation evidence")
def selection_links_validation_evidence(validation_context: dict[str, Any]) -> None:
    lock = _read_json(validation_context["validation_dir"] / "selection_lock.json")
    assert lock["validation_evidence"]["leaderboard_sha256"]
    assert lock["validation_evidence"]["candidate_validation_sha256"]


@then("the physical plausibility gate fails")
def physical_gate_fails(validation_context: dict[str, Any]) -> None:
    result = _read_json(
        validation_context["validation_dir"] / "candidate_validations" / "linear_regression.json"
    )
    gate = next(item for item in result["gates"] if item["gate_id"] == "configured_soh_bounds")
    assert gate["passed"] is False


@then("the report identifies the affected cell and cycle without changing the prediction")
def affected_cycle_is_reported(validation_context: dict[str, Any]) -> None:
    before = pd.read_csv(_predictions_path(validation_context, "linear_regression"))
    result = _read_json(
        validation_context["validation_dir"] / "candidate_validations" / "linear_regression.json"
    )
    gate = next(item for item in result["gates"] if item["gate_id"] == "configured_soh_bounds")
    assert gate["details"]["affected_rows"][0] == {"cell_id": "cell-c", "cycle_index": 1}
    after = pd.read_csv(_predictions_path(validation_context, "linear_regression"))
    pd.testing.assert_frame_equal(before, after)


@then("the agent does not declare that candidate a winner")
def implausible_candidate_is_not_winner(validation_context: dict[str, Any]) -> None:
    lock = _read_json(validation_context["validation_dir"] / "selection_lock.json")
    assert lock["selected_candidate"]["plugin_id"] != "linear_regression"


@then("the locked candidate is evaluated once on the held-out test partition")
def held_out_partition_is_evaluated_once(finalization_context: dict[str, Any]) -> None:
    assert finalization_context["error"] is None
    manifest = _read_json(finalization_context["final_dir"] / "finalization_manifest.json")
    assert manifest["state"] == "FINALIZED"
    assert manifest["test_evaluation_count"] == 1
    predictions = pd.read_csv(finalization_context["final_dir"] / "test" / "predictions.csv")
    assert set(predictions["partition"]) == {"test"}
    assert set(predictions["cell_id"]) == {"cell-d"}


@then("final predictions and metrics are linked to the locked selection")
def final_evidence_links_lock(finalization_context: dict[str, Any]) -> None:
    manifest = _read_json(finalization_context["final_dir"] / "finalization_manifest.json")
    lock = _read_json(finalization_context["validation_dir"] / "selection_lock.json")
    assert manifest["selection_fingerprint"] == lock["selection_fingerprint"]
    assert manifest["artifacts"]["predictions"]["sha256"]
    assert manifest["artifacts"]["metrics"]["sha256"]


@then("another test evaluation requires a new run identity")
def repeated_finalization_requires_new_run(finalization_context: dict[str, Any]) -> None:
    module = importlib.import_module("battery_health.agent.validation")
    with pytest.raises(module.ValidationError, match="already claimed"):
        module.finalize_locked_selection(
            validation_dir=finalization_context["validation_dir"],
            evidence_dir=finalization_context["evidence_dir"],
            output_dir=finalization_context["final_dir"].with_name("second-final"),
        )
    assert not finalization_context["final_dir"].with_name("second-final").exists()


@then("the run state becomes rejected")
def run_is_rejected(validation_context: dict[str, Any]) -> None:
    assert validation_context["error"] is None
    manifest = _read_json(validation_context["validation_dir"] / "validation_manifest.json")
    assert manifest["state"] == "REJECTED"


@then("all negative evidence is retained")
def negative_evidence_is_retained(validation_context: dict[str, Any]) -> None:
    paths = list((validation_context["validation_dir"] / "candidate_validations").glob("*.json"))
    assert {path.stem for path in paths} == set(MODELS)
    assert all(_read_json(path)["eligible"] is False for path in paths)


@then("no validation threshold is weakened automatically")
def thresholds_remain_locked(validation_context: dict[str, Any]) -> None:
    source_plan = _read_json(validation_context["plan_path"])
    retained_plan = _read_json(validation_context["validation_dir"] / "plan" / "plan.json")
    assert retained_plan["soh_bounds"] == source_plan["soh_bounds"]
    assert retained_plan["validation_gates"] == source_plan["validation_gates"]
    assert not (validation_context["validation_dir"] / "selection_lock.json").exists()


@then("the test isolation violation is recorded")
def isolation_violation_is_recorded(validation_context: dict[str, Any]) -> None:
    manifest = _read_json(validation_context["validation_dir"] / "validation_manifest.json")
    assert manifest["run_gates"]["test_partition_isolated"]["passed"] is False


@then("no selection lock is created")
def no_selection_lock(validation_context: dict[str, Any]) -> None:
    assert not (validation_context["validation_dir"] / "selection_lock.json").exists()


@then("the CLI records the validated locked and finalized states")
def cli_records_states(cli_validation_context: dict[str, Any]) -> None:
    validate = cli_validation_context["validate_cli"]
    finalize = cli_validation_context["finalize_cli"]
    assert validate.exit_code == 0, validate.output
    assert finalize.exit_code == 0, finalize.output
    validation = _read_json(cli_validation_context["validation_dir"] / "validation_manifest.json")
    finalization = _read_json(cli_validation_context["final_dir"] / "finalization_manifest.json")
    assert validation["state_history"] == ["RUNNING", "VALIDATED", "LOCKED"]
    assert finalization["state_history"] == ["RUNNING", "VALIDATED", "LOCKED", "FINALIZED"]


@then("the CLI reports the run and selection identities")
def cli_reports_identities(cli_validation_context: dict[str, Any]) -> None:
    output = cli_validation_context["validate_cli"].output
    assert "run_id=plan-" in output
    assert "state=LOCKED" in output
    assert "selection_fingerprint=" in output
    assert "next=agent finalize" in output
    assert "state=FINALIZED" in cli_validation_context["finalize_cli"].output


@then("repeating finalization through the CLI is rejected without overwriting evidence")
def cli_rejects_repeated_finalization(cli_validation_context: dict[str, Any]) -> None:
    manifest_path = cli_validation_context["final_dir"] / "finalization_manifest.json"
    before = manifest_path.read_bytes()
    second_dir = cli_validation_context["final_dir"].with_name("second-final")
    result = CliRunner().invoke(
        app,
        [
            "agent",
            "finalize",
            "--validation",
            str(cli_validation_context["validation_dir"]),
            "--evidence",
            str(cli_validation_context["evidence_dir"]),
            "--output",
            str(second_dir),
        ],
    )
    assert result.exit_code != 0
    assert "already claimed" in result.output.lower()
    assert manifest_path.read_bytes() == before
    assert not second_dir.exists()

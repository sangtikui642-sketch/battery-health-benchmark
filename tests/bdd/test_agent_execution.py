import importlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
from pytest_bdd import given, scenarios, then, when
from typer.testing import CliRunner

from battery_health.agent.planning import create_experiment_plan, verify_plan_fingerprint
from battery_health.cli import app
from tests.bdd.test_agent_planning import MODELS, _planning_context, _plugin

scenarios("../../features/agent_execution.feature")

BUILTIN_SOURCE = "builtin:battery_health.modeling.baselines"


def _write_execution_registry(
    path: Path,
    *,
    failing_candidate: str | None = None,
) -> Path:
    plugins: list[dict[str, object]] = []
    selected_models = (
        MODELS if failing_candidate is None else ("linear_regression", failing_candidate)
    )
    for model_name in selected_models:
        plugin = _plugin(model_name)
        plugin["source"] = (
            "python:missing_battery_plugin" if model_name == failing_candidate else BUILTIN_SOURCE
        )
        plugins.append(plugin)
    path.write_text(
        json.dumps({"schema_version": 1, "plugins": plugins}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _execution_context(
    tmp_path: Path,
    *,
    failing_candidate: str | None = None,
) -> dict[str, Any]:
    context = _planning_context(tmp_path, compatible=True)
    context["registry_path"] = _write_execution_registry(
        context["registry_path"],
        failing_candidate=failing_candidate,
    )
    plan_result = create_experiment_plan(
        evidence_dir=context["evidence_dir"],
        registry_path=context["registry_path"],
        policy_path=context["policy_path"],
        output_dir=tmp_path / "plan",
    )
    return {
        "evidence_dir": context["evidence_dir"],
        "plan_dir": plan_result.output_dir,
        "plan_path": plan_result.plan_path,
        "plan_before": plan_result.plan_path.read_bytes(),
        "output_dir": tmp_path / "execution",
        "request_path": None,
        "result": None,
        "error": None,
    }


@given(
    "an immutable plan containing several compatible candidates",
    target_fixture="execution_context",
)
def several_candidate_plan(tmp_path: Path) -> dict[str, Any]:
    return _execution_context(tmp_path)


@given(
    "an immutable plan contains one valid and one failing candidate",
    target_fixture="execution_context",
)
def plan_with_failure(tmp_path: Path) -> dict[str, Any]:
    return _execution_context(tmp_path, failing_candidate="random_forest")


@given(
    "an immutable plan with bounded models parameters and runtime",
    target_fixture="execution_context",
)
def bounded_plan(tmp_path: Path) -> dict[str, Any]:
    context = _execution_context(tmp_path)
    request_path = tmp_path / "execution_request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "max_runtime_seconds_per_candidate": 121,
                "max_memory_mb_per_candidate": 1024,
                "candidate_ids": list(MODELS),
                "parameter_overrides": {},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    context["request_path"] = request_path
    return context


@given(
    "portable evidence and an immutable executable plan",
    target_fixture="cli_execution_context",
)
def cli_execution_inputs(tmp_path: Path) -> dict[str, Any]:
    return _execution_context(tmp_path)


@when("the agent executes the modelling plan")
def execute_plan(execution_context: dict[str, Any]) -> None:
    try:
        module = importlib.import_module("battery_health.agent.execution")
        execution_context["result"] = module.execute_experiment_plan(
            plan_dir=execution_context["plan_dir"],
            evidence_dir=execution_context["evidence_dir"],
            output_dir=execution_context["output_dir"],
        )
    except Exception as error:  # the red phase captures the missing executor
        execution_context["error"] = error


@when("an execution request exceeds a declared bound")
def execute_out_of_bounds_request(execution_context: dict[str, Any]) -> None:
    try:
        module = importlib.import_module("battery_health.agent.execution")
        execution_context["result"] = module.execute_experiment_plan(
            plan_dir=execution_context["plan_dir"],
            evidence_dir=execution_context["evidence_dir"],
            output_dir=execution_context["output_dir"],
            request_path=execution_context["request_path"],
        )
    except Exception as error:  # the red phase captures the missing executor
        execution_context["error"] = error


@when("the researcher runs the agent execute command")
def run_agent_execute(cli_execution_context: dict[str, Any]) -> None:
    cli_execution_context["result"] = CliRunner().invoke(
        app,
        [
            "agent",
            "execute",
            "--plan",
            str(cli_execution_context["plan_dir"]),
            "--evidence",
            str(cli_execution_context["evidence_dir"]),
            "--output",
            str(cli_execution_context["output_dir"]),
        ],
    )


def _manifest(context: dict[str, Any]) -> dict[str, object]:
    return json.loads(
        (context["output_dir"] / "execution_manifest.json").read_text(encoding="utf-8")
    )


def _record(context: dict[str, Any], candidate_id: str) -> dict[str, object]:
    return json.loads(
        (context["output_dir"] / "candidates" / candidate_id / "candidate_record.json").read_text(
            encoding="utf-8"
        )
    )


@then("every candidate uses the same cell-level split")
def candidates_share_split(execution_context: dict[str, Any]) -> None:
    manifest = _manifest(execution_context)
    records = [_record(execution_context, candidate_id) for candidate_id in MODELS]
    assert {record["split_manifest_fingerprint"] for record in records} == {
        manifest["split_manifest_fingerprint"]
    }


@then("preprocessing and tuning use training and validation data only")
def train_and_validation_only(execution_context: dict[str, Any]) -> None:
    execution_input = pd.read_csv(
        execution_context["output_dir"] / "inputs" / "train_validation_soh.csv"
    )
    assert set(execution_input["cell_id"]) == {"cell-a", "cell-b", "cell-c"}
    assert "cell-d" not in set(execution_input["cell_id"])
    for candidate_id in MODELS:
        record = _record(execution_context, candidate_id)
        assert record["preprocessing_fit_partition"] == "train"
        assert record["evaluation_partition"] == "validation"
        assert record["test_partition_accessed"] is False
        predictions = pd.read_csv(
            execution_context["output_dir"]
            / "candidates"
            / candidate_id
            / "seed-73"
            / "predictions.csv"
        )
        assert set(predictions["cell_id"]) == {"cell-c"}
        assert "cell-d" not in set(predictions["cell_id"])


@then("each candidate receives a traceable run record")
def each_candidate_is_recorded(execution_context: dict[str, Any]) -> None:
    assert execution_context["error"] is None
    manifest = _manifest(execution_context)
    assert manifest["state"] == "RUNNING"
    assert manifest["execution_status"] == "completed"
    assert {item["candidate_id"] for item in manifest["candidates"]} == set(MODELS)
    assert all(_record(execution_context, model)["status"] == "succeeded" for model in MODELS)


@then("the valid candidate completes normally")
def valid_candidate_completes(execution_context: dict[str, Any]) -> None:
    assert _record(execution_context, "linear_regression")["status"] == "succeeded"


@then("the failing candidate receives a structured failure record")
def failed_candidate_is_structured(execution_context: dict[str, Any]) -> None:
    record = _record(execution_context, "random_forest")
    assert record["status"] == "failed"
    assert record["failure"]["code"] == "dependency_failure"
    assert record["failure"]["stage"] == "candidate_setup"


@then("the failing candidate is not silently removed from the comparison")
def failed_candidate_remains_visible(execution_context: dict[str, Any]) -> None:
    manifest = _manifest(execution_context)
    by_id = {item["candidate_id"]: item for item in manifest["candidates"]}
    assert by_id["random_forest"]["status"] == "failed"
    assert by_id["linear_regression"]["status"] == "succeeded"


@then("the request is rejected without changing the plan")
def request_rejected_without_mutation(execution_context: dict[str, Any]) -> None:
    assert execution_context["error"] is None
    assert execution_context["plan_path"].read_bytes() == execution_context["plan_before"]
    assert verify_plan_fingerprint(execution_context["plan_path"])
    manifest = _manifest(execution_context)
    assert manifest["state"] == "REJECTED"
    assert manifest["execution_status"] == "policy_violation"
    assert not (execution_context["output_dir"] / "candidates").exists()


@then("the policy violation is recorded")
def policy_violation_recorded(execution_context: dict[str, Any]) -> None:
    violation = json.loads(
        (execution_context["output_dir"] / "policy_violation.json").read_text(encoding="utf-8")
    )
    assert violation["code"] == "runtime_bound_exceeded"
    assert violation["declared_maximum"] == 120
    assert violation["requested"] == 121


@then("the CLI creates a completed candidate execution bundle")
def cli_execution_completed(cli_execution_context: dict[str, Any]) -> None:
    result = cli_execution_context["result"]
    assert result.exit_code == 0, result.output
    manifest = _manifest(cli_execution_context)
    assert manifest["execution_status"] == "completed"
    assert len(manifest["candidates"]) == 3


@then("held-out test predictions remain unavailable to candidate execution")
def cli_hides_test_predictions(cli_execution_context: dict[str, Any]) -> None:
    for predictions_path in cli_execution_context["output_dir"].glob(
        "candidates/*/seed-*/predictions.csv"
    ):
        predictions = pd.read_csv(predictions_path)
        assert set(predictions["partition"]) == {"validation"}
        assert "cell-d" not in set(predictions["cell_id"])

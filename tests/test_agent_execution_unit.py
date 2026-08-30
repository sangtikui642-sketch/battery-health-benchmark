import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from typer.testing import CliRunner

import battery_health.agent.execution as execution
from battery_health.agent.execution import (
    CandidateJob,
    ExecutionError,
    execute_experiment_plan,
)
from battery_health.agent.planning import create_experiment_plan
from battery_health.benchmark import run_benchmark
from battery_health.cli import app
from battery_health.data.split import SplitManifest
from tests.bdd.test_agent_execution import _execution_context, _write_execution_registry
from tests.bdd.test_agent_planning import _write_policy


def test_tampered_plan_is_rejected_before_output(tmp_path: Path) -> None:
    context = _execution_context(tmp_path)
    plan = json.loads(context["plan_path"].read_text(encoding="utf-8"))
    plan["budget"]["max_candidates"] = 99
    context["plan_path"].write_text(json.dumps(plan) + "\n", encoding="utf-8")

    with pytest.raises(ExecutionError, match="fingerprint"):
        execute_experiment_plan(
            plan_dir=context["plan_dir"],
            evidence_dir=context["evidence_dir"],
            output_dir=context["output_dir"],
        )

    assert not context["output_dir"].exists()


def test_stale_evidence_is_rejected_before_output(tmp_path: Path) -> None:
    context = _execution_context(tmp_path)
    labels = context["evidence_dir"] / "cycles_with_soh.csv"
    labels.write_text(labels.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ExecutionError, match="evidence is invalid"):
        execute_experiment_plan(
            plan_dir=context["plan_dir"],
            evidence_dir=context["evidence_dir"],
            output_dir=context["output_dir"],
        )

    assert not context["output_dir"].exists()


def test_execution_output_is_non_overwriting(tmp_path: Path) -> None:
    context = _execution_context(tmp_path)
    context["output_dir"].mkdir()
    marker = context["output_dir"] / "user.txt"
    marker.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(ExecutionError, match="already exists"):
        execute_experiment_plan(
            plan_dir=context["plan_dir"],
            evidence_dir=context["evidence_dir"],
            output_dir=context["output_dir"],
        )

    assert marker.read_text(encoding="utf-8") == "preserve\n"


def test_candidate_timeout_is_visible_while_other_candidates_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _execution_context(tmp_path)

    def fake_runner(
        job: CandidateJob,
        *,
        timeout_seconds: float,
        memory_limit_mb: int,
    ) -> dict[str, Any]:
        assert timeout_seconds > 0
        assert memory_limit_mb == 1024
        if job.candidate_id == "random_forest":
            return execution._timed_out_result(0.01, timeout_seconds)
        return {
            "schema_version": 1,
            "status": "succeeded",
            "failure": None,
            "artifacts": {},
            "resource_usage": {
                "duration_seconds": 0.001,
                "peak_python_memory_mb": 1.0,
                "memory_measurement": "test_fixture",
            },
        }

    monkeypatch.setattr(execution, "_run_isolated_candidate", fake_runner)
    result = execute_experiment_plan(
        plan_dir=context["plan_dir"],
        evidence_dir=context["evidence_dir"],
        output_dir=context["output_dir"],
    )

    assert result.execution_status == "completed_with_failures"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    statuses = {item["candidate_id"]: item["status"] for item in manifest["candidates"]}
    assert statuses["random_forest"] == "failed"
    assert statuses["linear_regression"] == "succeeded"
    assert statuses["histogram_gradient_boosting"] == "succeeded"
    failed_record = json.loads(
        (
            context["output_dir"] / "candidates" / "random_forest" / "candidate_record.json"
        ).read_text(encoding="utf-8")
    )
    assert failed_record["failure"]["code"] == "runtime_limit_exceeded"


def _dummy_job(tmp_path: Path) -> CandidateJob:
    return CandidateJob(
        candidate_id="linear_regression",
        source=execution.BUILTIN_BASELINE_SOURCE,
        data_path=tmp_path / "unused.csv",
        split_manifest=SplitManifest(
            strategy="group_by_cell",
            random_seed=1,
            train_cells=("train",),
            validation_cells=("validation",),
            test_cells=("test",),
        ),
        feature_columns=("cycle_index",),
        random_seed=73,
        output_dir=tmp_path / "candidate",
    )


def test_timeout_terminates_the_worker_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"terminated": False}

    class FakeProcess:
        exitcode = None

        def start(self) -> None:
            return None

        def join(self, timeout: float | None = None) -> None:
            return None

        def is_alive(self) -> bool:
            return not state["terminated"]

        def terminate(self) -> None:
            state["terminated"] = True

    class FakeContext:
        def Process(self, *, target: object, args: object) -> FakeProcess:
            return FakeProcess()

    monkeypatch.setattr(execution.multiprocessing, "get_context", lambda _: FakeContext())
    result = execution._run_isolated_candidate(
        _dummy_job(tmp_path),
        timeout_seconds=0.001,
        memory_limit_mb=64,
    )

    assert state["terminated"] is True
    assert result["status"] == "timed_out"
    assert result["failure"]["code"] == "runtime_limit_exceeded"


def test_measured_memory_excess_is_a_structured_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _dummy_job(tmp_path)

    class FakeProcess:
        exitcode = 0

        def start(self) -> None:
            execution._write_json(
                job.output_dir / "worker_result.json",
                {
                    "schema_version": 1,
                    "status": "succeeded",
                    "failure": None,
                    "artifacts": {},
                    "resource_usage": {
                        "duration_seconds": 0.01,
                        "peak_python_memory_mb": 65.0,
                        "memory_measurement": "python_tracemalloc_only",
                    },
                },
            )

        def join(self, timeout: float | None = None) -> None:
            return None

        def is_alive(self) -> bool:
            return False

    class FakeContext:
        def Process(self, *, target: object, args: object) -> FakeProcess:
            return FakeProcess()

    monkeypatch.setattr(execution.multiprocessing, "get_context", lambda _: FakeContext())
    result = execution._run_isolated_candidate(
        job,
        timeout_seconds=1.0,
        memory_limit_mb=64,
    )

    assert result["status"] == "failed"
    assert result["failure"]["code"] == "memory_limit_exceeded"


def test_candidate_set_mismatch_is_recorded_without_running(tmp_path: Path) -> None:
    context = _execution_context(tmp_path)
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "candidate_ids": ["linear_regression"],
                "parameter_overrides": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = execute_experiment_plan(
        plan_dir=context["plan_dir"],
        evidence_dir=context["evidence_dir"],
        output_dir=context["output_dir"],
        request_path=request_path,
    )

    assert result.execution_status == "policy_violation"
    violation = json.loads(
        (context["output_dir"] / "policy_violation.json").read_text(encoding="utf-8")
    )
    assert violation["code"] == "candidate_set_mismatch"
    assert not (context["output_dir"] / "candidates").exists()


def test_cli_policy_violation_returns_nonzero_with_evidence(tmp_path: Path) -> None:
    context = _execution_context(tmp_path)
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "max_runtime_seconds_per_candidate": 999,
                "parameter_overrides": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "execute",
            "--plan",
            str(context["plan_dir"]),
            "--evidence",
            str(context["evidence_dir"]),
            "--request",
            str(request_path),
            "--output",
            str(context["output_dir"]),
        ],
    )

    assert result.exit_code != 0
    assert "rejected" in result.output.lower()
    assert (context["output_dir"] / "policy_violation.json").is_file()


def test_executor_consumes_a_real_benchmark_evidence_bundle(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    evidence = run_benchmark(
        config_path=repository_root / "configs" / "demo.yaml",
        output_dir=tmp_path / "benchmark-evidence",
        repository_path=repository_root,
    )
    registry_path = _write_execution_registry(tmp_path / "registry.json")
    policy_path = _write_policy(tmp_path / "policy.yaml")
    plan = create_experiment_plan(
        evidence_dir=evidence.output_dir,
        registry_path=registry_path,
        policy_path=policy_path,
        output_dir=tmp_path / "plan",
    )

    result = execute_experiment_plan(
        plan_dir=plan.output_dir,
        evidence_dir=evidence.output_dir,
        output_dir=tmp_path / "execution",
    )

    assert result.execution_status == "completed"
    split = json.loads((evidence.output_dir / "split_manifest.json").read_text(encoding="utf-8"))
    execution_input = pd.read_csv(result.output_dir / "inputs" / "train_validation_soh.csv")
    assert set(execution_input["cell_id"]) == set(split["train_cells"]) | set(
        split["validation_cells"]
    )
    assert not (set(execution_input["cell_id"]) & set(split["test_cells"]))
    for prediction_path in result.output_dir.glob("candidates/*/seed-*/predictions.csv"):
        predictions = pd.read_csv(prediction_path)
        assert set(predictions["cell_id"]) == set(split["validation_cells"])

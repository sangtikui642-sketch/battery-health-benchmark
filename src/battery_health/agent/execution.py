"""Bounded, failure-isolated candidate execution for immutable FR-10 plans."""

from __future__ import annotations

import json
import multiprocessing
import shutil
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self, cast

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from battery_health.data.split import SplitManifest
from battery_health.evaluation import EvaluationError, evaluate_predictions
from battery_health.modeling.baselines import (
    SUPPORTED_MODELS,
    BaselineTrainingError,
    train_baseline,
)
from battery_health.reproducibility import ReproducibilityError, sha256_file

from .planning import (
    PlanningError,
    PlanningEvidence,
    load_verified_planning_evidence,
    verify_plan_fingerprint,
)

BUILTIN_BASELINE_SOURCE = "builtin:battery_health.modeling.baselines"


class ExecutionError(ValueError):
    """Raised when a locked plan cannot be executed safely."""


class StrictExecutionModel(BaseModel):
    """Strict immutable execution input model."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class FeatureContract(StrictExecutionModel):
    """One model input and its explicit unit."""

    name: str = Field(min_length=1)
    unit: str = Field(min_length=1)


class CandidateContract(StrictExecutionModel):
    """Execution-relevant candidate fields retained in the locked plan."""

    plugin_id: str = Field(min_length=1)
    implementation_version: str = Field(min_length=1)
    required_features: tuple[str, ...] = Field(min_length=1)
    preprocessing_policy: str = Field(min_length=1)
    deterministic_supported: bool
    seed_parameter: str = Field(min_length=1)
    bounded_parameters: dict[str, object]
    source: str = Field(min_length=1)
    licenses: dict[str, str]
    manifest_fingerprint: str = Field(min_length=64, max_length=64)


class ExecutionBudget(StrictExecutionModel):
    """Hard limits inherited from the immutable plan."""

    max_candidates: int = Field(ge=1)
    max_runtime_seconds_per_candidate: int = Field(ge=1)
    max_total_runtime_seconds: int = Field(ge=1)
    max_memory_mb_per_candidate: int = Field(ge=1)


class SplitContract(StrictExecutionModel):
    """Fingerprint and counts for the shared cell-level split."""

    strategy: str = Field(min_length=1)
    random_seed: int
    train_cell_count: int = Field(ge=1)
    validation_cell_count: int = Field(ge=1)
    test_cell_count: int = Field(ge=1)
    manifest_fingerprint: str = Field(min_length=64, max_length=64)


class PreprocessingContract(StrictExecutionModel):
    """Locked partition and per-candidate preprocessing declarations."""

    fit_partition: Literal["train"]
    candidate_policies: dict[str, str]


class LockedPlan(BaseModel):
    """Strict view of fields required to execute an FR-09 plan."""

    model_config = ConfigDict(extra="ignore", frozen=True, allow_inf_nan=False)

    schema_version: Literal[1]
    state: Literal["PLANNED"]
    execution_status: Literal["not_started"]
    plan_id: str = Field(min_length=1)
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    candidates: tuple[CandidateContract, ...] = Field(min_length=1)
    features: tuple[FeatureContract, ...] = Field(min_length=1)
    split: SplitContract
    preprocessing_policy: PreprocessingContract
    budget: ExecutionBudget
    random_seeds: tuple[int, ...] = Field(min_length=1)
    fingerprints: dict[str, str]

    @field_validator("candidates")
    @classmethod
    def candidate_ids_are_unique(
        cls, value: tuple[CandidateContract, ...]
    ) -> tuple[CandidateContract, ...]:
        """Reject an ambiguous candidate identity before any execution."""

        identifiers = [candidate.plugin_id for candidate in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("candidate identifiers must be unique")
        return value

    @field_validator("random_seeds")
    @classmethod
    def seeds_are_unique(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        """Require each predeclared seed exactly once."""

        if len(value) != len(set(value)):
            raise ValueError("random seeds must be unique")
        return value

    @model_validator(mode="after")
    def candidate_contract_is_executable(self) -> Self:
        """Require reviewed built-ins and no unimplemented parameter overrides."""

        if len(self.candidates) > self.budget.max_candidates:
            raise ValueError("candidate count exceeds plan budget")
        feature_names = {feature.name for feature in self.features}
        policy_ids = set(self.preprocessing_policy.candidate_policies)
        candidate_ids = {candidate.plugin_id for candidate in self.candidates}
        if policy_ids != candidate_ids:
            raise ValueError("preprocessing policies must exactly match candidates")
        for candidate in self.candidates:
            if candidate.plugin_id not in SUPPORTED_MODELS:
                raise ValueError("plan contains an unsupported candidate")
            if not set(candidate.required_features) <= feature_names:
                raise ValueError("candidate requires an unavailable plan feature")
            if candidate.bounded_parameters:
                raise ValueError("builtin FR-10 adapter does not yet support parameter overrides")
        return self


class ExecutionRequest(StrictExecutionModel):
    """Optional request that may only narrow the plan's resource limits."""

    schema_version: Literal[1] = 1
    max_runtime_seconds_per_candidate: int | None = Field(default=None, ge=1)
    max_memory_mb_per_candidate: int | None = Field(default=None, ge=1)
    candidate_ids: tuple[str, ...] | None = None
    parameter_overrides: dict[str, dict[str, object]] = Field(default_factory=dict)

    @field_validator("candidate_ids")
    @classmethod
    def requested_ids_are_unique(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        """Reject duplicate candidate requests."""

        if value is not None and len(value) != len(set(value)):
            raise ValueError("requested candidate identifiers must be unique")
        return value


@dataclass(frozen=True)
class ExecutionResult:
    """Top-level FR-10 execution artifact."""

    output_dir: Path
    manifest_path: Path
    execution_status: str


@dataclass(frozen=True)
class CandidateJob:
    """Pickle-safe input passed to one isolated worker process."""

    candidate_id: str
    source: str
    data_path: Path
    split_manifest: SplitManifest
    feature_columns: tuple[str, ...]
    random_seed: int
    output_dir: Path


@dataclass(frozen=True)
class PolicyViolation:
    """Machine-readable request violation recorded without changing the plan."""

    code: str
    message: str
    declared_maximum: int | None = None
    requested: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a portable JSON representation."""

        result: dict[str, object] = {
            "schema_version": 1,
            "code": self.code,
            "message": self.message,
        }
        if self.declared_maximum is not None:
            result["declared_maximum"] = self.declared_maximum
        if self.requested is not None:
            result["requested"] = self.requested
        return result


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path, *, label: str) -> object:
    try:
        return cast(object, json.loads(path.read_text(encoding="utf-8")))
    except OSError as error:
        raise ExecutionError(f"Unable to read {label}: {path.name}") from error
    except json.JSONDecodeError as error:
        raise ExecutionError(f"{label.capitalize()} is not valid JSON") from error


def _load_locked_plan(plan_dir: Path) -> LockedPlan:
    plan_path = plan_dir / "plan.json"
    try:
        fingerprint_valid = verify_plan_fingerprint(plan_path)
    except PlanningError as error:
        raise ExecutionError("Unable to verify immutable plan") from error
    if not fingerprint_valid:
        raise ExecutionError("Immutable plan fingerprint or source snapshot is invalid")
    try:
        return LockedPlan.model_validate(_read_json(plan_path, label="immutable plan"))
    except ValidationError as error:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise ExecutionError("Invalid executable plan field(s): " + ", ".join(fields)) from error


def _load_request(request_path: Path | None) -> ExecutionRequest:
    if request_path is None:
        return ExecutionRequest()
    try:
        return ExecutionRequest.model_validate(_read_json(request_path, label="execution request"))
    except ValidationError as error:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise ExecutionError("Invalid execution request field(s): " + ", ".join(fields)) from error


def _load_evidence(evidence_dir: Path) -> PlanningEvidence:
    try:
        return load_verified_planning_evidence(evidence_dir)
    except PlanningError as error:
        raise ExecutionError(f"Execution evidence is invalid: {error}") from error


def _validate_evidence_matches_plan(plan: LockedPlan, evidence: PlanningEvidence) -> None:
    required_fingerprints = {
        "dataset": evidence.artifact_hashes["soh_labels"],
        "split_manifest": evidence.artifact_hashes["split_manifest"],
        "effective_config": evidence.artifact_hashes["effective_config"],
        "dependency_lock": evidence.artifact_hashes["dependency_lock"],
    }
    for name, actual in required_fingerprints.items():
        if plan.fingerprints.get(name) != actual:
            raise ExecutionError(f"Evidence does not match locked plan fingerprint: {name}")

    feature_names = tuple(feature.name for feature in plan.features)
    if feature_names != evidence.config.feature_columns:
        raise ExecutionError("Evidence feature order does not match the locked plan")
    split = evidence.split
    split_checks = (
        plan.split.strategy == split.strategy,
        plan.split.random_seed == split.random_seed,
        plan.split.train_cell_count == len(split.train_cells),
        plan.split.validation_cell_count == len(split.validation_cells),
        plan.split.test_cell_count == len(split.test_cells),
    )
    if not all(split_checks):
        raise ExecutionError("Evidence split contract does not match the locked plan")


def _request_violation(plan: LockedPlan, request: ExecutionRequest) -> PolicyViolation | None:
    planned_ids = tuple(candidate.plugin_id for candidate in plan.candidates)
    if request.candidate_ids is not None and set(request.candidate_ids) != set(planned_ids):
        return PolicyViolation(
            code="candidate_set_mismatch",
            message="Execution request must retain every candidate in the locked plan.",
        )
    if request.parameter_overrides:
        return PolicyViolation(
            code="parameter_outside_locked_plan",
            message="The locked baseline plan declares no executable parameter overrides.",
        )
    runtime = request.max_runtime_seconds_per_candidate
    if runtime is not None and runtime > plan.budget.max_runtime_seconds_per_candidate:
        return PolicyViolation(
            code="runtime_bound_exceeded",
            message="Requested candidate runtime exceeds the locked plan.",
            declared_maximum=plan.budget.max_runtime_seconds_per_candidate,
            requested=runtime,
        )
    memory = request.max_memory_mb_per_candidate
    if memory is not None and memory > plan.budget.max_memory_mb_per_candidate:
        return PolicyViolation(
            code="memory_bound_exceeded",
            message="Requested candidate memory exceeds the locked plan.",
            declared_maximum=plan.budget.max_memory_mb_per_candidate,
            requested=memory,
        )
    return None


def _copy_plan_bundle(plan_dir: Path, output_dir: Path) -> None:
    snapshot_dir = output_dir / "plan"
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    for name in (
        "plan.json",
        "registry_snapshot.json",
        "policy_snapshot.yaml",
        "split_snapshot.json",
    ):
        source = plan_dir / name
        if not source.is_file():
            raise ExecutionError(f"Immutable plan bundle is missing: {name}")
        shutil.copyfile(source, snapshot_dir / name)


def _failure_payload(code: str, stage: str, error: BaseException) -> dict[str, str]:
    message = str(error).strip() or error.__class__.__name__
    return {
        "code": code,
        "stage": stage,
        "type": error.__class__.__name__,
        "message": message,
    }


def _candidate_worker(job: CandidateJob) -> None:
    """Execute one candidate seed in a child process and always retain a result."""

    started = time.perf_counter()
    tracemalloc.start()
    stage = "candidate_setup"
    result: dict[str, Any]
    try:
        if job.source != BUILTIN_BASELINE_SOURCE:
            raise ModuleNotFoundError(f"Candidate source is not installed: {job.source}")
        stage = "training"
        training = train_baseline(
            data_path=job.data_path,
            split_manifest=job.split_manifest,
            output_dir=job.output_dir,
            model_name=job.candidate_id,
            feature_columns=job.feature_columns,
            random_seed=job.random_seed,
            prediction_partition="validation",
            data_scope="train_validation",
        )
        stage = "validation_evaluation"
        evaluation = evaluate_predictions(
            predictions_path=training.predictions_path,
            split_manifest=job.split_manifest,
            output_dir=job.output_dir,
            partition="validation",
        )
        result = {
            "schema_version": 1,
            "status": "succeeded",
            "failure": None,
            "artifacts": {
                "predictions": evaluation.predictions_path.name,
                "metrics": evaluation.metrics_path.name,
                "actual_vs_predicted_plot": evaluation.plot_path.name,
                "training_metadata": training.run_metadata_path.name,
            },
        }
    except ModuleNotFoundError as error:
        result = {
            "schema_version": 1,
            "status": "failed",
            "failure": _failure_payload("dependency_failure", stage, error),
            "artifacts": {},
        }
    except BaselineTrainingError as error:
        code = "invalid_predictions" if "non-finite" in str(error).lower() else "training_failure"
        result = {
            "schema_version": 1,
            "status": "failed",
            "failure": _failure_payload(code, stage, error),
            "artifacts": {},
        }
    except EvaluationError as error:
        message = str(error).lower()
        code = (
            "invalid_predictions"
            if "finite" in message or "prediction" in message
            else "evaluation_failure"
        )
        result = {
            "schema_version": 1,
            "status": "failed",
            "failure": _failure_payload(code, stage, error),
            "artifacts": {},
        }
    except OSError as error:
        result = {
            "schema_version": 1,
            "status": "failed",
            "failure": _failure_payload("filesystem_failure", stage, error),
            "artifacts": {},
        }
    except Exception as error:
        result = {
            "schema_version": 1,
            "status": "failed",
            "failure": _failure_payload("unexpected_failure", stage, error),
            "artifacts": {},
        }
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result["resource_usage"] = {
        "duration_seconds": time.perf_counter() - started,
        "peak_python_memory_mb": peak_bytes / (1024 * 1024),
        "memory_measurement": "python_tracemalloc_only",
    }
    try:
        _write_json(job.output_dir / "worker_result.json", result)
    except OSError:
        return


def _timed_out_result(duration: float, timeout_seconds: float) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "timed_out",
        "failure": {
            "code": "runtime_limit_exceeded",
            "stage": "candidate_execution",
            "type": "TimeoutError",
            "message": "Candidate exceeded the locked wall-clock runtime.",
        },
        "artifacts": {},
        "resource_usage": {
            "duration_seconds": duration,
            "timeout_seconds": timeout_seconds,
            "peak_python_memory_mb": None,
            "memory_measurement": "unavailable_after_termination",
        },
    }


def _run_isolated_candidate(
    job: CandidateJob,
    *,
    timeout_seconds: float,
    memory_limit_mb: int,
) -> dict[str, Any]:
    job.output_dir.mkdir(parents=True, exist_ok=False)
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_candidate_worker, args=(job,))
    started = time.perf_counter()
    try:
        process.start()
        process.join(timeout_seconds)
    except OSError as error:
        return {
            "schema_version": 1,
            "status": "failed",
            "failure": _failure_payload("process_start_failure", "candidate_setup", error),
            "artifacts": {},
            "resource_usage": {
                "duration_seconds": time.perf_counter() - started,
                "peak_python_memory_mb": None,
                "memory_measurement": "unavailable",
            },
        }
    if process.is_alive():
        process.terminate()
        process.join()
        return _timed_out_result(time.perf_counter() - started, timeout_seconds)

    worker_result_path = job.output_dir / "worker_result.json"
    if process.exitcode != 0 or not worker_result_path.is_file():
        return {
            "schema_version": 1,
            "status": "failed",
            "failure": {
                "code": "worker_process_failure",
                "stage": "candidate_execution",
                "type": "ChildProcessError",
                "message": "Candidate worker exited without a complete result.",
            },
            "artifacts": {},
            "resource_usage": {
                "duration_seconds": time.perf_counter() - started,
                "peak_python_memory_mb": None,
                "memory_measurement": "unavailable",
            },
        }
    value = _read_json(worker_result_path, label="candidate worker result")
    if not isinstance(value, dict):
        raise ExecutionError("Candidate worker result must be a JSON object")
    result = cast(dict[str, Any], value)
    usage = result.get("resource_usage")
    peak = usage.get("peak_python_memory_mb") if isinstance(usage, dict) else None
    if isinstance(peak, (int, float)) and float(peak) > memory_limit_mb:
        result["status"] = "failed"
        result["failure"] = {
            "code": "memory_limit_exceeded",
            "stage": "candidate_execution",
            "type": "MemoryError",
            "message": "Measured Python allocation peak exceeded the locked memory budget.",
        }
    return result


def _prepare_train_validation_input(
    evidence: PlanningEvidence,
    output_dir: Path,
) -> Path:
    """Create the only labelled file visible to candidate worker processes."""

    try:
        frame = pd.read_csv(evidence.labelled_path)
    except (OSError, pd.errors.ParserError) as error:
        raise ExecutionError("Unable to prepare train-validation execution input") from error
    allowed_cells = set(evidence.split.train_cells) | set(evidence.split.validation_cells)
    cell_ids = frame["cell_id"].astype(str)
    restricted = frame.loc[cell_ids.isin(allowed_cells)].copy()
    restricted_cells = set(restricted["cell_id"].astype(str))
    if restricted_cells != allowed_cells:
        raise ExecutionError("Train-validation execution input has incomplete cell coverage")
    if restricted_cells & set(evidence.split.test_cells):
        raise ExecutionError("Train-validation execution input contains held-out test cells")
    input_path = output_dir / "inputs" / "train_validation_soh.csv"
    input_path.parent.mkdir(parents=True, exist_ok=False)
    restricted.to_csv(input_path, index=False)
    return input_path


def _candidate_record(
    *,
    plan: LockedPlan,
    candidate: CandidateContract,
    evidence: PlanningEvidence,
    request: ExecutionRequest,
    execution_started: float,
    output_root: Path,
    execution_data_path: Path,
) -> dict[str, Any]:
    candidate_runs: list[dict[str, Any]] = []
    candidate_started = time.perf_counter()
    per_candidate_limit = float(
        request.max_runtime_seconds_per_candidate or plan.budget.max_runtime_seconds_per_candidate
    )
    memory_limit = request.max_memory_mb_per_candidate or plan.budget.max_memory_mb_per_candidate
    for seed in plan.random_seeds:
        candidate_elapsed = time.perf_counter() - candidate_started
        total_elapsed = time.perf_counter() - execution_started
        remaining_candidate = per_candidate_limit - candidate_elapsed
        remaining_total = plan.budget.max_total_runtime_seconds - total_elapsed
        timeout_seconds = min(remaining_candidate, remaining_total)
        run_dir_name = f"seed-{seed}"
        if timeout_seconds <= 0:
            outcome = _timed_out_result(0.0, 0.0)
            timeout_failure = cast(dict[str, object], outcome["failure"])
            timeout_failure["code"] = "total_runtime_exhausted"
        else:
            job = CandidateJob(
                candidate_id=candidate.plugin_id,
                source=candidate.source,
                data_path=execution_data_path,
                split_manifest=evidence.split,
                feature_columns=tuple(feature.name for feature in plan.features),
                random_seed=seed,
                output_dir=(output_root / "candidates" / candidate.plugin_id / run_dir_name),
            )
            outcome = _run_isolated_candidate(
                job,
                timeout_seconds=timeout_seconds,
                memory_limit_mb=memory_limit,
            )
        artifacts = cast(dict[str, str], outcome.get("artifacts", {}))
        candidate_runs.append(
            {
                "seed": seed,
                "status": outcome["status"],
                "failure": outcome.get("failure"),
                "resource_usage": outcome.get("resource_usage"),
                "artifacts": {
                    name: f"{run_dir_name}/{portable_name}"
                    for name, portable_name in artifacts.items()
                },
            }
        )
        if outcome["status"] != "succeeded":
            break

    status = (
        "succeeded"
        if len(candidate_runs) == len(plan.random_seeds)
        and all(run["status"] == "succeeded" for run in candidate_runs)
        else "failed"
    )
    failure: object | None = None
    for run in candidate_runs:
        if run["status"] != "succeeded":
            failure = run["failure"]
            break
    return {
        "schema_version": 1,
        "candidate_id": candidate.plugin_id,
        "implementation_version": candidate.implementation_version,
        "manifest_fingerprint": candidate.manifest_fingerprint,
        "plan_id": plan.plan_id,
        "plan_fingerprint": plan.plan_fingerprint,
        "split_manifest_fingerprint": plan.split.manifest_fingerprint,
        "status": status,
        "failure": failure,
        "preprocessing_fit_partition": "train",
        "evaluation_partition": "validation",
        "test_partition_accessed": False,
        "parameter_overrides": {},
        "runs": candidate_runs,
    }


def _record_rejection(
    *,
    plan_dir: Path,
    output_dir: Path,
    plan: LockedPlan,
    violation: PolicyViolation,
) -> ExecutionResult:
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        _copy_plan_bundle(plan_dir, output_dir)
        violation_path = output_dir / "policy_violation.json"
        _write_json(violation_path, violation.to_dict())
        manifest_path = output_dir / "execution_manifest.json"
        _write_json(
            manifest_path,
            {
                "schema_version": 1,
                "state": "REJECTED",
                "execution_status": "policy_violation",
                "plan_id": plan.plan_id,
                "plan_fingerprint": plan.plan_fingerprint,
                "split_manifest_fingerprint": plan.split.manifest_fingerprint,
                "candidates": [],
                "policy_violation": violation_path.name,
                "test_partition_accessed": False,
            },
        )
    except OSError as error:
        raise ExecutionError("Unable to retain policy-violation evidence") from error
    return ExecutionResult(
        output_dir=output_dir,
        manifest_path=manifest_path,
        execution_status="policy_violation",
    )


def execute_experiment_plan(
    *,
    plan_dir: Path,
    evidence_dir: Path,
    output_dir: Path,
    request_path: Path | None = None,
) -> ExecutionResult:
    """Execute every locked candidate on validation data with failure isolation."""

    if output_dir.exists():
        raise ExecutionError(f"Execution output directory already exists: {output_dir.name}")
    plan = _load_locked_plan(plan_dir)
    evidence = _load_evidence(evidence_dir)
    _validate_evidence_matches_plan(plan, evidence)
    request = _load_request(request_path)
    violation = _request_violation(plan, request)
    if violation is not None:
        return _record_rejection(
            plan_dir=plan_dir,
            output_dir=output_dir,
            plan=plan,
            violation=violation,
        )

    output_dir.mkdir(parents=True, exist_ok=False)
    execution_started = time.perf_counter()
    candidate_summaries: list[dict[str, object]] = []
    try:
        _copy_plan_bundle(plan_dir, output_dir)
        execution_data_path = _prepare_train_validation_input(evidence, output_dir)
        for candidate in plan.candidates:
            candidate_dir = output_dir / "candidates" / candidate.plugin_id
            record = _candidate_record(
                plan=plan,
                candidate=candidate,
                evidence=evidence,
                request=request,
                execution_started=execution_started,
                output_root=output_dir,
                execution_data_path=execution_data_path,
            )
            record_path = candidate_dir / "candidate_record.json"
            _write_json(record_path, record)
            candidate_summaries.append(
                {
                    "candidate_id": candidate.plugin_id,
                    "status": record["status"],
                    "failure": record["failure"],
                    "record": (
                        Path("candidates") / candidate.plugin_id / record_path.name
                    ).as_posix(),
                    "record_sha256": sha256_file(record_path),
                }
            )
        if not verify_plan_fingerprint(plan_dir / "plan.json"):
            raise ExecutionError("Immutable plan changed during candidate execution")
        execution_status = (
            "completed"
            if all(item["status"] == "succeeded" for item in candidate_summaries)
            else "completed_with_failures"
        )
        manifest_path = output_dir / "execution_manifest.json"
        _write_json(
            manifest_path,
            {
                "schema_version": 1,
                "state": "RUNNING",
                "execution_status": execution_status,
                "plan_id": plan.plan_id,
                "plan_fingerprint": plan.plan_fingerprint,
                "split_manifest_fingerprint": plan.split.manifest_fingerprint,
                "evaluation_partition": "validation",
                "test_partition_accessed": False,
                "execution_input": {
                    "path": (Path("inputs") / execution_data_path.name).as_posix(),
                    "sha256": sha256_file(execution_data_path),
                    "scope": "train_validation_only",
                    "test_cells_included": False,
                },
                "resource_policy": {
                    "max_runtime_seconds_per_candidate": (
                        request.max_runtime_seconds_per_candidate
                        or plan.budget.max_runtime_seconds_per_candidate
                    ),
                    "max_total_runtime_seconds": plan.budget.max_total_runtime_seconds,
                    "max_memory_mb_per_candidate": (
                        request.max_memory_mb_per_candidate
                        or plan.budget.max_memory_mb_per_candidate
                    ),
                    "memory_enforcement": "python_allocation_peak_post_run",
                    "wall_clock_timeout_enforcement": "child_process_termination",
                },
                "duration_seconds": time.perf_counter() - execution_started,
                "candidates": candidate_summaries,
                "plan_verified_after_execution": True,
            },
        )
    except ReproducibilityError as error:
        raise ExecutionError("Unable to fingerprint candidate execution records") from error
    except OSError as error:
        raise ExecutionError("Unable to retain candidate execution evidence") from error

    return ExecutionResult(
        output_dir=output_dir,
        manifest_path=manifest_path,
        execution_status=execution_status,
    )

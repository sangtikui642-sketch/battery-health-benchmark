"""Leakage-safe validation, selection locking, and one-shot finalization for FR-11."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from battery_health.data.split import SplitManifest, SplitValidationError, validate_split_manifest
from battery_health.evaluation import EvaluationError, evaluate_predictions
from battery_health.modeling.baselines import BaselineTrainingError, train_baseline
from battery_health.reproducibility import ReproducibilityError, sha256_file

from .planning import (
    PlanningError,
    SplitEvidence,
    load_verified_planning_evidence,
    verify_plan_fingerprint,
)

METRIC_NAMES = ("mae", "rmse", "mape", "r_squared")
UNKNOWN_LICENSE_VALUES = {"", "unknown", "not_declared", "unreviewed"}
BUILTIN_SOURCE = "builtin:battery_health.modeling.baselines"
ModelT = TypeVar("ModelT", bound=BaseModel)


class ValidationError(ValueError):
    """Raised when FR-11 evidence cannot be validated or finalized safely."""


class StrictEvidenceModel(BaseModel):
    """Immutable schema base used for security-relevant evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class PlanFeature(StrictEvidenceModel):
    """One locked input feature and unit."""

    name: str = Field(min_length=1)
    unit: str = Field(min_length=1)


class PlanCandidate(StrictEvidenceModel):
    """Candidate identity and configuration inherited from the immutable plan."""

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


class SelectionPolicy(StrictEvidenceModel):
    """Predeclared validation-only ranking policy."""

    metric: Literal["mae", "rmse", "mape", "r_squared"]
    direction: Literal["minimize", "maximize"]
    tie_breakers: tuple[str, ...] = Field(min_length=1)


class SOHBounds(StrictEvidenceModel):
    """Locked plausible interval for SOH predictions."""

    minimum: float
    maximum: float


class ValidationBudget(StrictEvidenceModel):
    """Locked execution resource limits checked during selection."""

    max_candidates: int = Field(ge=1)
    max_runtime_seconds_per_candidate: int = Field(ge=1)
    max_total_runtime_seconds: int = Field(ge=1)
    max_memory_mb_per_candidate: int = Field(ge=1)


class UncertaintyPolicy(StrictEvidenceModel):
    """Predeclared cell-bootstrap contract."""

    method: Literal["bootstrap_by_cell"]
    confidence_level: float = Field(gt=0, lt=1)
    resamples: int = Field(ge=100)
    random_seed: int


class ValidationPlan(StrictEvidenceModel):
    """Complete strict view of an immutable v0.2 experiment plan."""

    schema_version: Literal[1]
    state: Literal["PLANNED"]
    execution_status: Literal["not_started"]
    target: dict[str, object]
    dataset: dict[str, object]
    split: dict[str, object]
    features: tuple[PlanFeature, ...] = Field(min_length=1)
    candidates: tuple[PlanCandidate, ...] = Field(min_length=1)
    preprocessing_policy: dict[str, object]
    selection: SelectionPolicy
    required_metrics: tuple[Literal["mae", "rmse", "mape", "r_squared"], ...]
    validation_gates: tuple[str, ...] = Field(min_length=1)
    soh_bounds: SOHBounds
    budget: ValidationBudget
    uncertainty: UncertaintyPolicy
    random_seeds: tuple[int, ...] = Field(min_length=1)
    finalization_seed: int
    deterministic_settings: dict[str, object]
    fingerprints: dict[str, str]
    software_revision: dict[str, object]
    source_snapshots: dict[str, str]
    plan_id: str = Field(min_length=1)
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    fingerprint_algorithm: str = Field(min_length=1)


class ExecutionInput(StrictEvidenceModel):
    """Labelled execution input explicitly restricted to train and validation."""

    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    scope: Literal["train_validation_only"]
    test_cells_included: Literal[False]


class ExecutionCandidateSummary(StrictEvidenceModel):
    """Portable pointer to one candidate record."""

    candidate_id: str = Field(min_length=1)
    status: Literal["succeeded", "failed"]
    failure: dict[str, object] | None
    record: str = Field(min_length=1)
    record_sha256: str = Field(min_length=64, max_length=64)


class ExecutionManifest(StrictEvidenceModel):
    """Strict FR-10 manifest accepted by selection."""

    schema_version: Literal[1]
    state: Literal["RUNNING"]
    execution_status: Literal["completed", "completed_with_failures"]
    plan_id: str
    plan_fingerprint: str
    split_manifest_fingerprint: str
    evaluation_partition: Literal["validation"]
    test_partition_accessed: bool
    execution_input: ExecutionInput
    resource_policy: dict[str, object]
    duration_seconds: float = Field(ge=0)
    candidates: tuple[ExecutionCandidateSummary, ...] = Field(min_length=1)
    plan_verified_after_execution: Literal[True]


class CandidateRun(StrictEvidenceModel):
    """One seed-level outcome produced by the isolated executor."""

    seed: int
    status: Literal["succeeded", "failed", "timed_out"]
    failure: dict[str, object] | None
    resource_usage: dict[str, object] | None
    artifacts: dict[str, str]


class CandidateRecord(StrictEvidenceModel):
    """Strict execution record used by mandatory validation gates."""

    schema_version: Literal[1]
    candidate_id: str
    implementation_version: str
    manifest_fingerprint: str
    plan_id: str
    plan_fingerprint: str
    split_manifest_fingerprint: str
    status: Literal["succeeded", "failed"]
    failure: dict[str, object] | None
    preprocessing_fit_partition: Literal["train"]
    evaluation_partition: Literal["validation"]
    test_partition_accessed: bool
    parameter_overrides: dict[str, object]
    runs: tuple[CandidateRun, ...] = Field(min_length=1)


class GateDecision(StrictEvidenceModel):
    """Machine-readable mandatory gate outcome."""

    gate_id: str = Field(min_length=1)
    passed: bool
    code: str = Field(min_length=1)
    details: dict[str, object]


class LockConfiguration(StrictEvidenceModel):
    """Configuration frozen before held-out test access."""

    finalization_seed: int
    feature_columns: tuple[str, ...] = Field(min_length=1)
    bounded_parameters: dict[str, object]
    preprocessing_fit_partition: Literal["train"]


class ValidationEvidenceLinks(StrictEvidenceModel):
    """Fingerprinted validation evidence supporting a selection."""

    leaderboard: Literal["leaderboard.json"]
    leaderboard_sha256: str = Field(min_length=64, max_length=64)
    candidate_validation: str = Field(min_length=1)
    candidate_validation_sha256: str = Field(min_length=64, max_length=64)
    execution_manifest: Literal["execution_manifest.json"]
    execution_manifest_sha256: str = Field(min_length=64, max_length=64)


class SelectionLock(StrictEvidenceModel):
    """Immutable winning candidate and validation-only decision fingerprint."""

    schema_version: Literal[1]
    state: Literal["LOCKED"]
    run_id: str
    plan_id: str
    plan_fingerprint: str
    selected_candidate: PlanCandidate
    configuration: LockConfiguration
    selection_policy: SelectionPolicy
    validation_evidence: ValidationEvidenceLinks
    winner: dict[str, object]
    selection_fingerprint: str = Field(min_length=64, max_length=64)
    fingerprint_algorithm: Literal["sha256-canonical-json-v1"]


@dataclass(frozen=True)
class ValidationResult:
    """Top-level artifacts from validation and optional selection locking."""

    output_dir: Path
    manifest_path: Path
    state: Literal["LOCKED", "REJECTED"]
    run_id: str
    selection_fingerprint: str | None


@dataclass(frozen=True)
class FinalizationResult:
    """One authorized held-out test evaluation linked to a selection lock."""

    output_dir: Path
    manifest_path: Path
    run_id: str
    selection_fingerprint: str


def _read_json(path: Path, *, label: str) -> object:
    try:
        return cast(object, json.loads(path.read_text(encoding="utf-8")))
    except OSError as error:
        raise ValidationError(f"Unable to read {label}: {path.name}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"{label.capitalize()} is not valid JSON") from error


def _parse(model_type: type[ModelT], value: object, *, label: str) -> ModelT:
    try:
        return model_type.model_validate(value)
    except PydanticValidationError as error:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise ValidationError(f"Invalid {label} field(s): " + ", ".join(fields)) from error


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    try:
        return sha256_file(path)
    except ReproducibilityError as error:
        raise ValidationError(f"Unable to fingerprint validation artifact: {path.name}") from error


def _canonical_fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve(root: Path, portable_path: str, *, label: str) -> Path:
    supplied = Path(portable_path)
    if supplied.is_absolute():
        raise ValidationError(f"{label.capitalize()} path must be portable")
    resolved_root = root.resolve()
    candidate = (root / supplied).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValidationError(f"{label.capitalize()} path escapes its evidence bundle") from error
    if not candidate.is_file():
        raise ValidationError(f"{label.capitalize()} artifact is missing")
    return candidate


def _load_plan(plan_dir: Path) -> ValidationPlan:
    try:
        verified = verify_plan_fingerprint(plan_dir / "plan.json")
    except PlanningError as error:
        raise ValidationError("Unable to verify immutable plan") from error
    if not verified:
        raise ValidationError("Immutable plan fingerprint or source snapshot is invalid")
    return _parse(
        ValidationPlan,
        _read_json(plan_dir / "plan.json", label="immutable plan"),
        label="immutable plan",
    )


def _load_split(plan_dir: Path, plan: ValidationPlan) -> SplitManifest:
    split_path = plan_dir / "split_snapshot.json"
    if _sha256(split_path) != plan.fingerprints.get("split_manifest"):
        raise ValidationError("Split snapshot fingerprint does not match the immutable plan")
    split_evidence = _parse(
        SplitEvidence,
        _read_json(split_path, label="split snapshot"),
        label="split snapshot",
    )
    manifest = split_evidence.to_manifest()
    try:
        validate_split_manifest(manifest)
    except SplitValidationError as error:
        raise ValidationError(f"Split snapshot is not leakage-safe: {error}") from error
    return manifest


def _metrics(
    actual: np.ndarray[Any, np.dtype[np.float64]], predicted: np.ndarray[Any, np.dtype[np.float64]]
) -> dict[str, float]:
    if actual.size == 0 or actual.shape != predicted.shape:
        raise ValidationError("Metric inputs are empty or have mismatched shapes")
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all():
        raise ValidationError("Metric inputs must be finite")
    if (actual == 0).any():
        raise ValidationError("MAPE is undefined when actual SOH is zero")
    errors = predicted - actual
    residual_sum = float(np.sum(np.square(errors)))
    centered_sum = float(np.sum(np.square(actual - np.mean(actual))))
    r_squared = (
        1.0 - residual_sum / centered_sum if centered_sum else (1.0 if residual_sum == 0 else 0.0)
    )
    result = {
        "mae": float(np.mean(np.abs(errors))),
        "rmse": float(np.sqrt(np.mean(np.square(errors)))),
        "mape": float(np.mean(np.abs(errors / actual)) * 100.0),
        "r_squared": r_squared,
    }
    if not all(math.isfinite(value) for value in result.values()):
        raise ValidationError("Validation produced a non-finite metric")
    return result


def _bootstrap_by_cell(
    frames: list[pd.DataFrame],
    *,
    metric: str,
    confidence_level: float,
    resamples: int,
    random_seed: int,
) -> dict[str, object]:
    cells = sorted({str(cell) for frame in frames for cell in frame["cell_id"].astype(str)})
    if not cells:
        raise ValidationError("Cell bootstrap requires validation cells")
    rng = np.random.default_rng(random_seed)
    values: list[float] = []
    for _ in range(resamples):
        sampled = rng.choice(cells, size=len(cells), replace=True)
        actual_parts: list[np.ndarray[Any, np.dtype[np.float64]]] = []
        predicted_parts: list[np.ndarray[Any, np.dtype[np.float64]]] = []
        for sampled_cell in sampled:
            for frame in frames:
                rows = frame.loc[frame["cell_id"].astype(str) == str(sampled_cell)]
                actual_parts.append(rows["actual_soh"].to_numpy(dtype="float64"))
                predicted_parts.append(rows["predicted_soh"].to_numpy(dtype="float64"))
        sampled_metrics = _metrics(np.concatenate(actual_parts), np.concatenate(predicted_parts))
        values.append(sampled_metrics[metric])
    alpha = (1.0 - confidence_level) / 2.0
    return {
        "method": "bootstrap_by_cell",
        "metric": metric,
        "confidence_level": confidence_level,
        "resamples": resamples,
        "random_seed": random_seed,
        "lower": float(np.quantile(values, alpha)),
        "upper": float(np.quantile(values, 1.0 - alpha)),
        "bootstrap_unit": "cell_id",
    }


def _gate(
    gate_id: str, passed: bool, code: str, details: dict[str, object] | None = None
) -> dict[str, object]:
    return GateDecision(
        gate_id=gate_id,
        passed=passed,
        code=code,
        details=details or {},
    ).model_dump(mode="json")


def _known_license(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in UNKNOWN_LICENSE_VALUES


def _row_keys(frame: pd.DataFrame) -> set[tuple[str, int]]:
    cells: list[str] = frame["cell_id"].astype(str).tolist()
    cycles: list[int] = pd.to_numeric(frame["cycle_index"], errors="raise").astype("int64").tolist()
    return set(zip(cells, cycles, strict=True))


def _values_by_key(frame: pd.DataFrame, column: str) -> dict[tuple[str, int], float]:
    keys = list(
        zip(
            frame["cell_id"].astype(str).tolist(),
            pd.to_numeric(frame["cycle_index"], errors="raise").astype("int64").tolist(),
            strict=True,
        )
    )
    if len(keys) != len(set(keys)):
        raise ValidationError("Prediction keys are not unique")
    values: list[float] = pd.to_numeric(frame[column], errors="raise").astype("float64").tolist()
    return dict(zip(keys, values, strict=True))


def _candidate_validation(
    *,
    execution_dir: Path,
    summary: ExecutionCandidateSummary,
    candidate: PlanCandidate,
    plan: ValidationPlan,
    split: SplitManifest,
    expected_validation: pd.DataFrame,
) -> dict[str, object]:
    record_path = _resolve(execution_dir, summary.record, label="candidate record")
    record_hash_valid = _sha256(record_path) == summary.record_sha256
    try:
        record = _parse(
            CandidateRecord,
            _read_json(record_path, label="candidate record"),
            label="candidate record",
        )
    except ValidationError:
        record = None

    identity_ok = bool(
        record is not None
        and record_hash_valid
        and record.candidate_id == candidate.plugin_id
        and record.implementation_version == candidate.implementation_version
        and record.manifest_fingerprint == candidate.manifest_fingerprint
        and record.plan_id == plan.plan_id
        and record.plan_fingerprint == plan.plan_fingerprint
        and record.split_manifest_fingerprint == plan.fingerprints["split_manifest"]
    )
    execution_ok = bool(
        identity_ok
        and summary.status == "succeeded"
        and record is not None
        and record.status == "succeeded"
        and len(record.runs) == len(plan.random_seeds)
        and {run.seed for run in record.runs} == set(plan.random_seeds)
        and all(run.status == "succeeded" for run in record.runs)
    )
    train_only = bool(
        record is not None
        and record.preprocessing_fit_partition == "train"
        and record.evaluation_partition == "validation"
    )
    no_test_access = bool(record is not None and record.test_partition_accessed is False)
    finite_complete = execution_ok
    bounds_ok = execution_ok
    metrics_ok = execution_ok
    resources_ok = execution_ok
    affected_rows: list[dict[str, object]] = []
    frames: list[pd.DataFrame] = []
    run_metrics: list[dict[str, float]] = []
    expected_keys = _row_keys(expected_validation)
    expected_actual = _values_by_key(expected_validation, "soh")

    if record is not None:
        candidate_root = record_path.parent
        for run in record.runs:
            if run.status != "succeeded":
                finite_complete = False
                metrics_ok = False
                resources_ok = False
                continue
            usage = run.resource_usage or {}
            duration = usage.get("duration_seconds")
            peak = usage.get("peak_python_memory_mb")
            if (
                not isinstance(duration, (int, float))
                or float(duration) > plan.budget.max_runtime_seconds_per_candidate
            ):
                resources_ok = False
            if (
                isinstance(peak, (int, float))
                and float(peak) > plan.budget.max_memory_mb_per_candidate
            ):
                resources_ok = False
            try:
                predictions_path = _resolve(
                    candidate_root, run.artifacts["predictions"], label="validation predictions"
                )
                metrics_path = _resolve(
                    candidate_root, run.artifacts["metrics"], label="validation metrics"
                )
                metadata_path = _resolve(
                    candidate_root, run.artifacts["training_metadata"], label="training metadata"
                )
                frame = pd.read_csv(predictions_path)
                required_columns = {
                    "cell_id",
                    "cycle_index",
                    "partition",
                    "actual_soh",
                    "predicted_soh",
                }
                if frame.empty or not required_columns <= set(frame.columns):
                    raise ValidationError("Validation predictions are empty or incomplete")
                numeric = frame.loc[:, ["cycle_index", "actual_soh", "predicted_soh"]].apply(
                    pd.to_numeric, errors="coerce"
                )
                if not np.isfinite(numeric.to_numpy(dtype="float64")).all():
                    raise ValidationError("Validation predictions contain non-finite values")
                if frame.duplicated(subset=["cell_id", "cycle_index"]).any():
                    raise ValidationError("Validation predictions contain duplicate rows")
                keys = _row_keys(frame)
                if keys != expected_keys or set(frame["partition"].astype(str)) != {"validation"}:
                    raise ValidationError(
                        "Validation predictions do not cover the declared partition"
                    )
                actual_by_key = _values_by_key(frame, "actual_soh")
                if any(
                    not math.isclose(actual_by_key[key], expected_actual[key], abs_tol=1e-12)
                    for key in expected_keys
                ):
                    raise ValidationError(
                        "Validation actual SOH does not match the execution input"
                    )
                predicted = frame["predicted_soh"].to_numpy(dtype="float64")
                outside = (predicted < plan.soh_bounds.minimum) | (
                    predicted > plan.soh_bounds.maximum
                )
                if outside.any():
                    bounds_ok = False
                    for cell_id, cycle_index in sorted(
                        _row_keys(frame.loc[outside, ["cell_id", "cycle_index"]])
                    ):
                        item = {"cell_id": cell_id, "cycle_index": cycle_index}
                        if item not in affected_rows:
                            affected_rows.append(item)
                calculated = _metrics(
                    frame["actual_soh"].to_numpy(dtype="float64"),
                    predicted,
                )
                recorded = _read_json(metrics_path, label="validation metrics")
                if not isinstance(recorded, dict):
                    raise ValidationError("Validation metrics must be a JSON object")
                if recorded.get("partition") != "validation":
                    raise ValidationError("Candidate metrics are not validation metrics")
                tolerance_value = plan.deterministic_settings.get("prediction_tolerance", 0.0)
                if not isinstance(tolerance_value, (int, float)):
                    raise ValidationError("Prediction tolerance must be numeric")
                tolerance = float(tolerance_value)
                for metric_name in plan.required_metrics:
                    value = recorded.get(metric_name)
                    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                        raise ValidationError("Required validation metric is missing or non-finite")
                    if not math.isclose(
                        float(value), calculated[metric_name], abs_tol=tolerance, rel_tol=1e-12
                    ):
                        raise ValidationError(
                            "Recorded validation metric does not match predictions"
                        )
                metadata = _read_json(metadata_path, label="training metadata")
                if not isinstance(metadata, dict):
                    raise ValidationError("Training metadata must be a JSON object")
                if metadata.get("preprocessing_fit_partition") != "train" or set(
                    metadata.get("preprocessing_fit_cells", [])
                ) != set(split.train_cells):
                    train_only = False
                frames.append(frame)
                run_metrics.append(calculated)
            except (KeyError, OSError, pd.errors.ParserError, ValidationError):
                finite_complete = False
                metrics_ok = False
                train_only = False

    aggregate_metrics: dict[str, float] = {}
    uncertainty: dict[str, object] | None = None
    if frames and metrics_ok and len(frames) == len(plan.random_seeds):
        aggregate_metrics = {
            name: float(np.mean([metric[name] for metric in run_metrics])) for name in METRIC_NAMES
        }
        try:
            uncertainty = _bootstrap_by_cell(
                frames,
                metric=plan.selection.metric,
                confidence_level=plan.uncertainty.confidence_level,
                resamples=plan.uncertainty.resamples,
                random_seed=plan.uncertainty.random_seed,
            )
        except ValidationError:
            metrics_ok = False
    else:
        metrics_ok = False

    licenses_ok = _known_license(candidate.licenses.get("code")) and _known_license(
        candidate.licenses.get("weights")
    )
    gate_values: dict[str, tuple[bool, str, dict[str, object]]] = {
        "cell_partitions_disjoint": (
            identity_ok,
            "split_and_identity_verified" if identity_ok else "candidate_identity_mismatch",
            {"split_manifest_fingerprint": plan.fingerprints["split_manifest"]},
        ),
        "train_only_preprocessing": (
            train_only,
            "train_only_preprocessing_verified" if train_only else "preprocessing_scope_invalid",
            {"expected_fit_partition": "train"},
        ),
        "finite_complete_predictions": (
            finite_complete and no_test_access,
            "validation_predictions_complete"
            if finite_complete and no_test_access
            else "predictions_invalid_or_test_exposed",
            {
                "expected_row_count": len(expected_keys),
                "test_partition_accessed": not no_test_access,
            },
        ),
        "configured_soh_bounds": (
            bounds_ok,
            "predictions_within_bounds" if bounds_ok else "predicted_soh_out_of_bounds",
            {
                "minimum": plan.soh_bounds.minimum,
                "maximum": plan.soh_bounds.maximum,
                "affected_rows": affected_rows,
            },
        ),
        "required_metrics_present": (
            metrics_ok and uncertainty is not None,
            "metrics_and_uncertainty_present"
            if metrics_ok and uncertainty is not None
            else "metrics_or_uncertainty_invalid",
            {
                "required_metrics": list(plan.required_metrics),
                "uncertainty_method": plan.uncertainty.method,
            },
        ),
        "resource_budget_respected": (
            resources_ok,
            "resource_budget_respected" if resources_ok else "candidate_resource_violation",
            {
                "max_runtime_seconds_per_candidate": plan.budget.max_runtime_seconds_per_candidate,
                "max_memory_mb_per_candidate": plan.budget.max_memory_mb_per_candidate,
            },
        ),
        "known_licenses": (
            licenses_ok,
            "licenses_traceable" if licenses_ok else "license_unknown",
            {"licenses": candidate.licenses},
        ),
    }
    gates: list[dict[str, object]] = []
    for gate_id in plan.validation_gates:
        passed, code, details = gate_values[gate_id]
        gates.append(_gate(gate_id, passed, code, details))
    eligible = execution_ok and all(bool(gate["passed"]) for gate in gates)
    return {
        "schema_version": 1,
        "candidate_id": candidate.plugin_id,
        "implementation_version": candidate.implementation_version,
        "manifest_fingerprint": candidate.manifest_fingerprint,
        "execution_status": summary.status,
        "record_sha256": _sha256(record_path),
        "record_fingerprint_verified": record_hash_valid,
        "test_partition_accessed": not no_test_access,
        "metrics": aggregate_metrics,
        "primary_metric": aggregate_metrics.get(plan.selection.metric),
        "uncertainty": uncertainty,
        "gates": gates,
        "eligible": eligible,
    }


def _ranking_key(item: dict[str, object], plan: ValidationPlan) -> tuple[object, ...]:
    metrics = cast(dict[str, float], item["metrics"])
    key: list[object] = []
    primary = metrics[plan.selection.metric]
    key.append(primary if plan.selection.direction == "minimize" else -primary)
    for tie_breaker in plan.selection.tie_breakers:
        if tie_breaker == "plugin_id_lexicographic":
            key.append(cast(str, item["candidate_id"]))
        elif tie_breaker in METRIC_NAMES:
            value = metrics[tie_breaker]
            key.append(-value if tie_breaker == "r_squared" else value)
        else:
            raise ValidationError(f"Unsupported locked tie-breaker: {tie_breaker}")
    return tuple(key)


def verify_selection_lock(lock_path: Path) -> bool:
    """Return whether a selection lock matches its canonical validation fingerprint."""

    try:
        value = _read_json(lock_path, label="selection lock")
        lock = _parse(SelectionLock, value, label="selection lock")
    except ValidationError:
        return False
    payload = lock.model_dump(mode="json")
    fingerprint = cast(str, payload.pop("selection_fingerprint"))
    payload.pop("fingerprint_algorithm")
    return fingerprint == _canonical_fingerprint(payload)


def validate_and_lock_execution(*, execution_dir: Path, output_dir: Path) -> ValidationResult:
    """Apply mandatory gates using validation evidence only and lock one winner."""

    if output_dir.exists():
        raise ValidationError(f"Validation output directory already exists: {output_dir.name}")
    plan_dir = execution_dir / "plan"
    plan = _load_plan(plan_dir)
    split = _load_split(plan_dir, plan)
    manifest_path = execution_dir / "execution_manifest.json"
    manifest = _parse(
        ExecutionManifest,
        _read_json(manifest_path, label="execution manifest"),
        label="execution manifest",
    )
    candidate_by_id = {candidate.plugin_id: candidate for candidate in plan.candidates}
    if set(candidate_by_id) != {item.candidate_id for item in manifest.candidates}:
        raise ValidationError("Execution candidate set does not match the immutable plan")

    input_path = _resolve(execution_dir, manifest.execution_input.path, label="execution input")
    input_hash_valid = _sha256(input_path) == manifest.execution_input.sha256
    try:
        execution_input = pd.read_csv(input_path)
    except (OSError, pd.errors.ParserError) as error:
        raise ValidationError("Unable to read train-validation execution input") from error
    required_input_columns = {"cell_id", "cycle_index", "soh"}
    if execution_input.empty or not required_input_columns <= set(execution_input.columns):
        raise ValidationError("Execution input is empty or lacks the validation contract")
    input_cells = set(execution_input["cell_id"].astype(str))
    expected_input_cells = set(split.train_cells) | set(split.validation_cells)
    split_input_valid = input_cells == expected_input_cells and not (
        input_cells & set(split.test_cells)
    )
    expected_validation = execution_input.loc[
        execution_input["cell_id"].astype(str).isin(split.validation_cells)
    ].copy()

    output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copytree(plan_dir, output_dir / "plan")
    candidate_results: list[dict[str, object]] = []
    candidate_hashes: dict[str, str] = {}
    for summary in manifest.candidates:
        result = _candidate_validation(
            execution_dir=execution_dir,
            summary=summary,
            candidate=candidate_by_id[summary.candidate_id],
            plan=plan,
            split=split,
            expected_validation=expected_validation,
        )
        result_path = output_dir / "candidate_validations" / f"{summary.candidate_id}.json"
        _write_json(result_path, result)
        candidate_results.append(result)
        candidate_hashes[summary.candidate_id] = _sha256(result_path)

    record_test_isolated = all(
        result["test_partition_accessed"] is False for result in candidate_results
    )
    test_isolated = bool(
        manifest.test_partition_accessed is False
        and manifest.evaluation_partition == "validation"
        and manifest.execution_input.test_cells_included is False
        and split_input_valid
        and record_test_isolated
    )
    execution_consistent = bool(
        manifest.plan_id == plan.plan_id
        and manifest.plan_fingerprint == plan.plan_fingerprint
        and manifest.split_manifest_fingerprint == plan.fingerprints["split_manifest"]
        and manifest.plan_verified_after_execution is True
        and manifest.duration_seconds <= plan.budget.max_total_runtime_seconds
    )
    run_gates = {
        "immutable_plan_verified": _gate(
            "immutable_plan_verified", True, "plan_fingerprint_verified"
        ),
        "execution_input_integrity": _gate(
            "execution_input_integrity",
            input_hash_valid and split_input_valid,
            "execution_input_verified"
            if input_hash_valid and split_input_valid
            else "execution_input_invalid",
        ),
        "execution_contract_consistent": _gate(
            "execution_contract_consistent",
            execution_consistent,
            "execution_contract_verified"
            if execution_consistent
            else "execution_contract_mismatch",
        ),
        "test_partition_isolated": _gate(
            "test_partition_isolated",
            test_isolated,
            "test_partition_unavailable" if test_isolated else "test_partition_exposed",
            {"selection_input": "validation_only"},
        ),
    }
    eligible = [item for item in candidate_results if item["eligible"] is True]
    ranked = sorted(eligible, key=lambda item: _ranking_key(item, plan))
    ranks = {cast(str, item["candidate_id"]): index + 1 for index, item in enumerate(ranked)}
    leaderboard_candidates = []
    for item in sorted(candidate_results, key=lambda value: cast(str, value["candidate_id"])):
        failed_gates = [
            gate["gate_id"]
            for gate in cast(list[dict[str, object]], item["gates"])
            if gate["passed"] is False
        ]
        leaderboard_candidates.append(
            {
                "candidate_id": item["candidate_id"],
                "eligible": item["eligible"],
                "rank": ranks.get(cast(str, item["candidate_id"])),
                "metrics": item["metrics"],
                "primary_metric": item["primary_metric"],
                "uncertainty": item["uncertainty"],
                "failed_gates": failed_gates,
            }
        )
    leaderboard_path = output_dir / "leaderboard.json"
    _write_json(
        leaderboard_path,
        {
            "schema_version": 1,
            "state": "VALIDATED",
            "run_id": plan.plan_id,
            "plan_fingerprint": plan.plan_fingerprint,
            "evidence_partition": "validation",
            "test_partition_accessed": False,
            "selection_policy": plan.selection.model_dump(mode="json"),
            "uncertainty_policy": plan.uncertainty.model_dump(mode="json"),
            "candidates": leaderboard_candidates,
        },
    )

    run_passed = all(gate["passed"] is True for gate in run_gates.values())
    state: Literal["LOCKED", "REJECTED"] = "LOCKED" if ranked and run_passed else "REJECTED"
    selection_fingerprint: str | None = None
    lock_path: Path | None = None
    if state == "LOCKED":
        winner = ranked[0]
        winner_id = cast(str, winner["candidate_id"])
        winner_candidate = candidate_by_id[winner_id]
        candidate_validation_path = Path("candidate_validations") / f"{winner_id}.json"
        payload: dict[str, object] = {
            "schema_version": 1,
            "state": "LOCKED",
            "run_id": plan.plan_id,
            "plan_id": plan.plan_id,
            "plan_fingerprint": plan.plan_fingerprint,
            "selected_candidate": winner_candidate.model_dump(mode="json"),
            "configuration": {
                "finalization_seed": plan.finalization_seed,
                "feature_columns": [feature.name for feature in plan.features],
                "bounded_parameters": winner_candidate.bounded_parameters,
                "preprocessing_fit_partition": "train",
            },
            "selection_policy": plan.selection.model_dump(mode="json"),
            "validation_evidence": {
                "leaderboard": leaderboard_path.name,
                "leaderboard_sha256": _sha256(leaderboard_path),
                "candidate_validation": candidate_validation_path.as_posix(),
                "candidate_validation_sha256": candidate_hashes[winner_id],
                "execution_manifest": manifest_path.name,
                "execution_manifest_sha256": _sha256(manifest_path),
            },
            "winner": {
                "metric": plan.selection.metric,
                "direction": plan.selection.direction,
                "value": winner["primary_metric"],
                "uncertainty": winner["uncertainty"],
            },
        }
        selection_fingerprint = _canonical_fingerprint(payload)
        payload["selection_fingerprint"] = selection_fingerprint
        payload["fingerprint_algorithm"] = "sha256-canonical-json-v1"
        lock_path = output_dir / "selection_lock.json"
        _write_json(lock_path, payload)
        if not verify_selection_lock(lock_path):
            raise ValidationError("Generated selection lock failed fingerprint verification")

    validation_manifest_path = output_dir / "validation_manifest.json"
    _write_json(
        validation_manifest_path,
        {
            "schema_version": 1,
            "state": state,
            "state_history": ["RUNNING", "VALIDATED", state],
            "run_id": plan.plan_id,
            "plan_id": plan.plan_id,
            "plan_fingerprint": plan.plan_fingerprint,
            "execution_manifest_sha256": _sha256(manifest_path),
            "run_gates": run_gates,
            "leaderboard": leaderboard_path.name,
            "leaderboard_sha256": _sha256(leaderboard_path),
            "candidate_validation_sha256": candidate_hashes,
            "selection_lock": lock_path.name if lock_path else None,
            "selection_fingerprint": selection_fingerprint,
            "test_partition_accessed": False,
        },
    )
    return ValidationResult(
        output_dir=output_dir,
        manifest_path=validation_manifest_path,
        state=state,
        run_id=plan.plan_id,
        selection_fingerprint=selection_fingerprint,
    )


def _claim_finalization(validation_dir: Path, lock: SelectionLock, output_dir: Path) -> Path:
    claim_path = validation_dir / "finalization_claim.json"
    claim = {
        "schema_version": 1,
        "state": "TEST_ACCESS_CLAIMED",
        "run_id": lock.run_id,
        "selection_fingerprint": lock.selection_fingerprint,
        "output_directory_name": output_dir.name,
        "test_evaluation_limit": 1,
    }
    try:
        with claim_path.open("x", encoding="utf-8") as stream:
            json.dump(claim, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError as error:
        raise ValidationError(
            "Held-out test access was already claimed for this run identity"
        ) from error
    except OSError as error:
        raise ValidationError("Unable to claim one-time held-out test access") from error
    return claim_path


def finalize_locked_selection(
    *,
    validation_dir: Path,
    evidence_dir: Path,
    output_dir: Path,
) -> FinalizationResult:
    """Evaluate the locked candidate on held-out test data exactly once per run identity."""

    if output_dir.exists():
        raise ValidationError(f"Finalization output directory already exists: {output_dir.name}")
    lock_path = validation_dir / "selection_lock.json"
    if not verify_selection_lock(lock_path):
        raise ValidationError("Selection lock fingerprint is invalid")
    lock = _parse(
        SelectionLock,
        _read_json(lock_path, label="selection lock"),
        label="selection lock",
    )
    validation_manifest = _read_json(
        validation_dir / "validation_manifest.json",
        label="validation manifest",
    )
    if not isinstance(validation_manifest, dict) or validation_manifest.get("state") != "LOCKED":
        raise ValidationError("Finalization requires a LOCKED validation manifest")
    if validation_manifest.get("selection_fingerprint") != lock.selection_fingerprint:
        raise ValidationError("Validation manifest does not match the selection lock")
    plan_dir = validation_dir / "plan"
    plan = _load_plan(plan_dir)
    split = _load_split(plan_dir, plan)
    if lock.plan_fingerprint != plan.plan_fingerprint or lock.run_id != plan.plan_id:
        raise ValidationError("Selection lock does not match its immutable plan")
    if lock.selected_candidate.source != BUILTIN_SOURCE:
        raise ValidationError("Locked candidate source is unavailable for finalization")
    if lock.configuration.finalization_seed != plan.finalization_seed:
        raise ValidationError("Locked finalization seed does not match the plan")
    _claim_finalization(validation_dir, lock, output_dir)

    try:
        evidence = load_verified_planning_evidence(evidence_dir)
    except PlanningError as error:
        raise ValidationError(f"Finalization evidence is invalid: {error}") from error
    required_fingerprints = {
        "dataset": evidence.artifact_hashes["soh_labels"],
        "split_manifest": evidence.artifact_hashes["split_manifest"],
        "effective_config": evidence.artifact_hashes["effective_config"],
        "dependency_lock": evidence.artifact_hashes["dependency_lock"],
    }
    if any(plan.fingerprints.get(name) != digest for name, digest in required_fingerprints.items()):
        raise ValidationError("Finalization evidence does not match the immutable plan")
    if evidence.split != split:
        raise ValidationError("Finalization split does not match the locked split snapshot")

    output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copytree(plan_dir, output_dir / "plan")
    shutil.copyfile(lock_path, output_dir / "selection_lock.json")
    test_dir = output_dir / "test"
    try:
        training = train_baseline(
            data_path=evidence.labelled_path,
            split_manifest=evidence.split,
            output_dir=test_dir,
            model_name=lock.selected_candidate.plugin_id,
            feature_columns=lock.configuration.feature_columns,
            random_seed=lock.configuration.finalization_seed,
            prediction_partition="test",
            data_scope="all",
        )
        evaluation = evaluate_predictions(
            predictions_path=training.predictions_path,
            split_manifest=evidence.split,
            output_dir=test_dir,
            partition="test",
        )
    except (BaselineTrainingError, EvaluationError) as error:
        raise ValidationError(f"Locked candidate finalization failed: {error}") from error

    artifacts = {
        "predictions": {
            "path": (Path("test") / evaluation.predictions_path.name).as_posix(),
            "sha256": _sha256(evaluation.predictions_path),
        },
        "metrics": {
            "path": (Path("test") / evaluation.metrics_path.name).as_posix(),
            "sha256": _sha256(evaluation.metrics_path),
        },
        "actual_vs_predicted_plot": {
            "path": (Path("test") / evaluation.plot_path.name).as_posix(),
            "sha256": _sha256(evaluation.plot_path),
        },
        "training_metadata": {
            "path": (Path("test") / training.run_metadata_path.name).as_posix(),
            "sha256": _sha256(training.run_metadata_path),
        },
    }
    manifest_payload: dict[str, object] = {
        "schema_version": 1,
        "state": "FINALIZED",
        "state_history": ["RUNNING", "VALIDATED", "LOCKED", "FINALIZED"],
        "run_id": lock.run_id,
        "plan_id": lock.plan_id,
        "plan_fingerprint": lock.plan_fingerprint,
        "selection_fingerprint": lock.selection_fingerprint,
        "selected_candidate": lock.selected_candidate.model_dump(mode="json"),
        "configuration": lock.configuration.model_dump(mode="json"),
        "test_partition_accessed": True,
        "test_evaluation_count": 1,
        "artifacts": artifacts,
    }
    manifest_payload["finalization_fingerprint"] = _canonical_fingerprint(manifest_payload)
    manifest_path = output_dir / "finalization_manifest.json"
    _write_json(manifest_path, manifest_payload)
    _write_json(
        validation_dir / "finalization_receipt.json",
        {
            "schema_version": 1,
            "state": "FINALIZED",
            "run_id": lock.run_id,
            "selection_fingerprint": lock.selection_fingerprint,
            "finalization_manifest_sha256": _sha256(manifest_path),
            "test_evaluation_count": 1,
        },
    )
    return FinalizationResult(
        output_dir=output_dir,
        manifest_path=manifest_path,
        run_id=lock.run_id,
        selection_fingerprint=lock.selection_fingerprint,
    )

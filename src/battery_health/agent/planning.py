"""Deterministic, immutable experiment planning for FR-09."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, cast

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from battery_health.data.split import (
    SplitManifest,
    SplitValidationError,
    validate_split_manifest,
)
from battery_health.modeling.baselines import SUPPORTED_MODELS
from battery_health.reproducibility import ReproducibilityError, sha256_file

REQUIRED_EVIDENCE_KEYS = {
    "soh_labels",
    "soh_metadata",
    "quality_report",
    "effective_config",
    "split_manifest",
    "dependency_lock",
}
REQUIRED_VALIDATION_GATES = {
    "cell_partitions_disjoint",
    "train_only_preprocessing",
    "finite_complete_predictions",
    "configured_soh_bounds",
    "required_metrics_present",
    "resource_budget_respected",
    "known_licenses",
}
UNKNOWN_LICENSE_VALUES = {"", "unknown", "not_declared", "unreviewed"}


class PlanningError(ValueError):
    """Raised when prerequisites cannot produce an executable immutable plan."""


class StrictPlanningModel(BaseModel):
    """Strict immutable input model for planning contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class PluginManifest(StrictPlanningModel):
    """Minimum FR-09 view of a model plugin manifest."""

    plugin_id: str = Field(min_length=1)
    implementation_version: str = Field(min_length=1)
    supported_target: str = Field(min_length=1)
    required_features: tuple[str, ...] = Field(min_length=1)
    preprocessing_policy: str = Field(min_length=1)
    deterministic_supported: bool
    seed_parameter: str = Field(min_length=1)
    code_license: str = Field(min_length=1)
    weight_license: str = Field(min_length=1)
    source: str = Field(min_length=1)
    bounded_parameters: dict[str, object]

    @field_validator("required_features")
    @classmethod
    def features_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject an ambiguous duplicate feature contract."""

        if len(value) != len(set(value)):
            raise ValueError("required features must be unique")
        return value


class ModelRegistry(StrictPlanningModel):
    """Versioned set of declared model plugins."""

    schema_version: Literal[1]
    plugins: tuple[PluginManifest, ...] = Field(min_length=1)

    @field_validator("plugins")
    @classmethod
    def plugin_ids_are_unique(cls, value: tuple[PluginManifest, ...]) -> tuple[PluginManifest, ...]:
        """Reject shadowed plugin identifiers."""

        identifiers = [plugin.plugin_id for plugin in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("plugin identifiers must be unique")
        return value


class SelectionPolicy(StrictPlanningModel):
    """Validation-only selection policy recorded before execution."""

    metric: Literal["mae", "rmse", "mape", "r_squared"]
    direction: Literal["minimize", "maximize"]
    tie_breakers: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def direction_matches_metric(self) -> Self:
        """Reject a direction that contradicts the declared metric definition."""

        expected = "maximize" if self.metric == "r_squared" else "minimize"
        if self.direction != expected:
            raise ValueError(f"{self.metric} selection direction must be {expected}")
        return self


class SOHBounds(StrictPlanningModel):
    """Explicit physical-plausibility bounds supplied by policy."""

    minimum: float
    maximum: float

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> Self:
        """Require a non-empty finite interval."""

        if not math.isfinite(self.minimum) or not math.isfinite(self.maximum):
            raise ValueError("SOH bounds must be finite")
        if self.minimum >= self.maximum:
            raise ValueError("SOH minimum must be below maximum")
        return self


class ResourceBudget(StrictPlanningModel):
    """Hard candidate and execution resource limits."""

    max_candidates: int = Field(ge=1)
    max_runtime_seconds_per_candidate: int = Field(ge=1)
    max_total_runtime_seconds: int = Field(ge=1)
    max_memory_mb_per_candidate: int = Field(ge=1)


class UncertaintyPolicy(StrictPlanningModel):
    """Predeclared uncertainty calculation contract for later validation."""

    method: Literal["bootstrap_by_cell"]
    confidence_level: float = Field(gt=0, lt=1)
    resamples: int = Field(ge=100)
    random_seed: int


class PlanningPolicy(StrictPlanningModel):
    """Versioned resource and mandatory-validation policy."""

    schema_version: Literal[1]
    selection: SelectionPolicy
    required_metrics: tuple[Literal["mae", "rmse", "mape", "r_squared"], ...] = Field(
        min_length=4, max_length=4
    )
    validation_gates: tuple[str, ...] = Field(min_length=1)
    soh_bounds: SOHBounds
    budget: ResourceBudget
    uncertainty: UncertaintyPolicy
    random_seeds: tuple[int, ...] = Field(min_length=1)
    deterministic_required: bool
    deterministic_tolerance: float = Field(ge=0)

    @field_validator("required_metrics")
    @classmethod
    def metrics_are_complete_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require all MVP metrics without silently adding one."""

        required = {"mae", "rmse", "mape", "r_squared"}
        if len(value) != len(set(value)) or set(value) != required:
            raise ValueError("required metrics must contain MAE RMSE MAPE and R-squared")
        return value

    @field_validator("validation_gates")
    @classmethod
    def gates_are_complete_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require every mandatory gate without silently adding one."""

        if len(value) != len(set(value)):
            raise ValueError("validation gates must be unique")
        missing = sorted(REQUIRED_VALIDATION_GATES - set(value))
        if missing:
            raise ValueError("missing mandatory validation gates: " + ", ".join(missing))
        return value

    @field_validator("random_seeds")
    @classmethod
    def seeds_are_unique(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        """Reject repeated seeds that would fabricate stability evidence."""

        if len(value) != len(set(value)):
            raise ValueError("random seeds must be unique")
        return value


class GitEvidence(StrictPlanningModel):
    """Software revision inherited from the evidence bundle."""

    status: Literal["available", "unavailable"]
    revision: str | None

    @model_validator(mode="after")
    def revision_matches_status(self) -> Self:
        """Require a revision only when it was available."""

        if self.status == "available" and not self.revision:
            raise ValueError("available Git status requires a revision")
        if self.status == "unavailable" and self.revision is not None:
            raise ValueError("unavailable Git status cannot include a revision")
        return self


class EvidenceManifest(StrictPlanningModel):
    """Required subset of an FR-07 reproducibility manifest."""

    schema_version: Literal[1]
    random_seed: int
    deterministic_tolerance: float = Field(ge=0)
    files: dict[str, str]
    sha256: dict[str, str]
    git: GitEvidence


class EffectiveEvidenceConfig(BaseModel):
    """Planning-relevant fields retained by the benchmark."""

    model_config = ConfigDict(extra="ignore", frozen=True, allow_inf_nan=False)

    target: Literal["soh"]
    feature_columns: tuple[str, ...] = Field(min_length=1)
    feature_units: dict[str, str]
    model_random_seed: int

    @model_validator(mode="after")
    def feature_units_are_explicit(self) -> Self:
        """Require one non-empty configured unit for every feature."""

        if set(self.feature_units) != set(self.feature_columns):
            raise ValueError("feature units must exactly match feature columns")
        if any(not unit.strip() for unit in self.feature_units.values()):
            raise ValueError("feature units must not be empty")
        return self


class SOHMetadata(BaseModel):
    """Explicit target definition retained by FR-04."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    target_name: Literal["soh"]
    target_definition: str = Field(min_length=1)
    reference_capacity_source: str = Field(min_length=1)


class QualityEvidence(BaseModel):
    """Required successful validation status from FR-02."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    status: Literal["passed"]


class SplitEvidence(StrictPlanningModel):
    """Serialized cell-level split used to reconstruct its validated contract."""

    strategy: str = Field(min_length=1)
    random_seed: int
    train_cells: tuple[str, ...]
    validation_cells: tuple[str, ...]
    test_cells: tuple[str, ...]

    def to_manifest(self) -> SplitManifest:
        """Convert to the existing leakage validator contract."""

        return SplitManifest(
            strategy=self.strategy,
            random_seed=self.random_seed,
            train_cells=self.train_cells,
            validation_cells=self.validation_cells,
            test_cells=self.test_cells,
        )


@dataclass(frozen=True)
class PlanningEvidence:
    """Validated evidence needed to build a plan without test metrics."""

    manifest: EvidenceManifest
    config: EffectiveEvidenceConfig
    metadata: SOHMetadata
    split: SplitManifest
    labelled_path: Path
    artifact_paths: dict[str, Path]
    artifact_hashes: dict[str, str]
    row_count: int
    cell_count: int


@dataclass(frozen=True)
class PlanResult:
    """Immutable plan artifact and its stable fingerprint."""

    output_dir: Path
    plan_path: Path
    plan_fingerprint: str


def _read_json(path: Path) -> object:
    try:
        return cast(object, json.loads(path.read_text(encoding="utf-8")))
    except OSError as error:
        raise PlanningError(f"Unable to read planning input: {path.name}") from error
    except json.JSONDecodeError as error:
        raise PlanningError(f"Planning input is not valid JSON: {path.name}") from error


def _validate_model(model_type: type[BaseModel], value: object, *, name: str) -> BaseModel:
    try:
        return model_type.model_validate(value)
    except ValidationError as error:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise PlanningError(f"Invalid {name} field(s): " + ", ".join(fields)) from error


def _resolve_evidence_path(evidence_dir: Path, portable_path: str, *, name: str) -> Path:
    supplied = Path(portable_path)
    if supplied.is_absolute():
        raise PlanningError(f"Evidence path must be portable: {name}")
    root = evidence_dir.resolve()
    candidate = (evidence_dir / supplied).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise PlanningError(f"Evidence path escapes the bundle: {name}") from error
    if not candidate.is_file():
        raise PlanningError(f"Evidence artifact is missing: {name}")
    return candidate


def _load_evidence(evidence_dir: Path) -> PlanningEvidence:
    manifest_path = evidence_dir / "reproducibility_manifest.json"
    manifest = cast(
        EvidenceManifest,
        _validate_model(EvidenceManifest, _read_json(manifest_path), name="evidence manifest"),
    )
    missing = sorted(REQUIRED_EVIDENCE_KEYS - manifest.files.keys())
    if missing:
        raise PlanningError("Evidence manifest is missing artifact(s): " + ", ".join(missing))

    artifact_paths: dict[str, Path] = {}
    artifact_hashes: dict[str, str] = {}
    for name in sorted(REQUIRED_EVIDENCE_KEYS):
        if name not in manifest.sha256:
            raise PlanningError(f"Evidence manifest is missing SHA-256: {name}")
        artifact_path = _resolve_evidence_path(evidence_dir, manifest.files[name], name=name)
        try:
            digest = sha256_file(artifact_path)
        except ReproducibilityError as error:
            raise PlanningError(f"Unable to verify evidence artifact: {name}") from error
        if digest != manifest.sha256[name]:
            raise PlanningError(f"Evidence fingerprint mismatch: {name}")
        artifact_paths[name] = artifact_path
        artifact_hashes[name] = digest

    quality = cast(
        QualityEvidence,
        _validate_model(
            QualityEvidence,
            _read_json(artifact_paths["quality_report"]),
            name="quality evidence",
        ),
    )
    if quality.status != "passed":
        raise PlanningError("Data quality evidence did not pass")
    config = cast(
        EffectiveEvidenceConfig,
        _validate_model(
            EffectiveEvidenceConfig,
            _read_json(artifact_paths["effective_config"]),
            name="effective configuration",
        ),
    )
    metadata = cast(
        SOHMetadata,
        _validate_model(
            SOHMetadata,
            _read_json(artifact_paths["soh_metadata"]),
            name="SOH metadata",
        ),
    )
    split_evidence = cast(
        SplitEvidence,
        _validate_model(
            SplitEvidence,
            _read_json(artifact_paths["split_manifest"]),
            name="split manifest",
        ),
    )
    split = split_evidence.to_manifest()
    try:
        validate_split_manifest(split)
    except SplitValidationError as error:
        raise PlanningError(f"Split manifest is not leakage-safe: {error}") from error

    try:
        labelled = pd.read_csv(artifact_paths["soh_labels"])
    except (OSError, pd.errors.ParserError) as error:
        raise PlanningError("Unable to read SOH-labelled data") from error
    required_columns = {"cell_id", "cycle_index", "soh", *config.feature_columns}
    missing_columns = sorted(required_columns - set(labelled.columns))
    if missing_columns:
        raise PlanningError("SOH-labelled data is missing column(s): " + ", ".join(missing_columns))
    if labelled.empty:
        raise PlanningError("SOH-labelled data is empty")
    if labelled.duplicated(subset=["cell_id", "cycle_index"]).any():
        raise PlanningError("SOH-labelled data contains duplicate cycles")
    numeric = labelled.loc[:, ["soh", *config.feature_columns]].apply(
        pd.to_numeric, errors="coerce"
    )
    if not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        raise PlanningError("Target and planning features must be finite numeric values")
    data_cells = set(labelled["cell_id"].astype(str))
    split_cells = set(split.train_cells) | set(split.validation_cells) | set(split.test_cells)
    if data_cells != split_cells:
        raise PlanningError("Split manifest does not exactly cover the SOH-labelled data")

    return PlanningEvidence(
        manifest=manifest,
        config=config,
        metadata=metadata,
        split=split,
        labelled_path=artifact_paths["soh_labels"],
        artifact_paths=artifact_paths,
        artifact_hashes=artifact_hashes,
        row_count=int(len(labelled)),
        cell_count=int(labelled["cell_id"].astype(str).nunique()),
    )


def load_verified_planning_evidence(evidence_dir: Path) -> PlanningEvidence:
    """Load and fingerprint-check the evidence contract used by planning and execution."""

    return _load_evidence(evidence_dir)


def _canonical_fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_registry(registry_path: Path) -> ModelRegistry:
    return cast(
        ModelRegistry,
        _validate_model(ModelRegistry, _read_json(registry_path), name="model registry"),
    )


def _load_policy(policy_path: Path) -> PlanningPolicy:
    return cast(
        PlanningPolicy,
        _validate_model(PlanningPolicy, _read_json(policy_path), name="planning policy"),
    )


def _known_license(value: str) -> bool:
    return value.strip().lower() not in UNKNOWN_LICENSE_VALUES


def _compatible_candidates(
    *, evidence: PlanningEvidence, registry: ModelRegistry, policy: PlanningPolicy
) -> list[dict[str, object]]:
    available_features = set(evidence.config.feature_columns)
    candidates: list[dict[str, object]] = []
    for plugin in sorted(registry.plugins, key=lambda item: item.plugin_id):
        compatible = (
            plugin.plugin_id in SUPPORTED_MODELS
            and plugin.supported_target == evidence.config.target
            and set(plugin.required_features) <= available_features
            and (not policy.deterministic_required or plugin.deterministic_supported)
            and _known_license(plugin.code_license)
            and _known_license(plugin.weight_license)
        )
        if not compatible:
            continue
        plugin_payload = plugin.model_dump(mode="json")
        candidates.append(
            {
                "plugin_id": plugin.plugin_id,
                "implementation_version": plugin.implementation_version,
                "required_features": list(plugin.required_features),
                "preprocessing_policy": plugin.preprocessing_policy,
                "deterministic_supported": plugin.deterministic_supported,
                "seed_parameter": plugin.seed_parameter,
                "bounded_parameters": plugin.bounded_parameters,
                "source": plugin.source,
                "licenses": {
                    "code": plugin.code_license,
                    "weights": plugin.weight_license,
                },
                "manifest_fingerprint": _canonical_fingerprint(plugin_payload),
            }
        )
    if not candidates:
        raise PlanningError("No compatible registered model candidates satisfy the plan contract")
    if len(candidates) > policy.budget.max_candidates:
        raise PlanningError("Compatible candidate count exceeds the declared resource budget")
    return candidates


def _build_plan(
    *,
    evidence: PlanningEvidence,
    registry: ModelRegistry,
    policy: PlanningPolicy,
    registry_path: Path,
    policy_path: Path,
) -> dict[str, object]:
    candidates = _compatible_candidates(evidence=evidence, registry=registry, policy=policy)
    split = evidence.split
    plan: dict[str, object] = {
        "schema_version": 1,
        "state": "PLANNED",
        "execution_status": "not_started",
        "target": {
            "name": evidence.metadata.target_name,
            "definition": evidence.metadata.target_definition,
            "reference_capacity_source": evidence.metadata.reference_capacity_source,
        },
        "dataset": {
            "row_count": evidence.row_count,
            "cell_count": evidence.cell_count,
            "fingerprint": evidence.artifact_hashes["soh_labels"],
        },
        "split": {
            "strategy": split.strategy,
            "random_seed": split.random_seed,
            "train_cell_count": len(split.train_cells),
            "validation_cell_count": len(split.validation_cells),
            "test_cell_count": len(split.test_cells),
            "manifest_fingerprint": evidence.artifact_hashes["split_manifest"],
        },
        "features": [
            {"name": feature, "unit": evidence.config.feature_units[feature]}
            for feature in evidence.config.feature_columns
        ],
        "candidates": candidates,
        "preprocessing_policy": {
            "fit_partition": "train",
            "candidate_policies": {
                cast(str, candidate["plugin_id"]): candidate["preprocessing_policy"]
                for candidate in candidates
            },
        },
        "selection": policy.selection.model_dump(mode="json"),
        "required_metrics": list(policy.required_metrics),
        "validation_gates": list(policy.validation_gates),
        "soh_bounds": policy.soh_bounds.model_dump(mode="json"),
        "budget": policy.budget.model_dump(mode="json"),
        "uncertainty": policy.uncertainty.model_dump(mode="json"),
        "random_seeds": list(policy.random_seeds),
        "finalization_seed": policy.random_seeds[0],
        "deterministic_settings": {
            "required": policy.deterministic_required,
            "prediction_tolerance": policy.deterministic_tolerance,
            "predeclared_before_execution": True,
        },
        "fingerprints": {
            "dataset": evidence.artifact_hashes["soh_labels"],
            "split_manifest": evidence.artifact_hashes["split_manifest"],
            "effective_config": evidence.artifact_hashes["effective_config"],
            "dependency_lock": evidence.artifact_hashes["dependency_lock"],
            "model_registry": sha256_file(registry_path),
            "resource_policy": sha256_file(policy_path),
            "planner_implementation": sha256_file(Path(__file__)),
        },
        "software_revision": evidence.manifest.git.model_dump(mode="json"),
        "source_snapshots": {
            "model_registry": "registry_snapshot.json",
            "resource_policy": "policy_snapshot.yaml",
            "split_manifest": "split_snapshot.json",
        },
    }
    fingerprint = _canonical_fingerprint(plan)
    plan["plan_id"] = "plan-" + fingerprint[:16]
    plan["plan_fingerprint"] = fingerprint
    plan["fingerprint_algorithm"] = "sha256-canonical-json-v1"
    return plan


def _fingerprint_payload(plan: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in plan.items()
        if key not in {"plan_id", "plan_fingerprint", "fingerprint_algorithm"}
    }


def verify_plan_fingerprint(plan_path: Path) -> bool:
    """Verify a plan has not changed since its PLANNED fingerprint was assigned."""

    value = _read_json(plan_path)
    if not isinstance(value, dict):
        return False
    plan = cast(dict[str, object], value)
    fingerprint = plan.get("plan_fingerprint")
    plan_id = plan.get("plan_id")
    if not isinstance(fingerprint, str) or not isinstance(plan_id, str):
        return False
    expected = _canonical_fingerprint(_fingerprint_payload(plan))
    plan_is_valid = (
        plan.get("state") == "PLANNED"
        and fingerprint == expected
        and plan_id == "plan-" + expected[:16]
    )
    if not plan_is_valid:
        return False
    snapshots = plan.get("source_snapshots")
    fingerprints = plan.get("fingerprints")
    if not isinstance(snapshots, dict) or not isinstance(fingerprints, dict):
        return False
    snapshot_keys = {
        "model_registry": "model_registry",
        "resource_policy": "resource_policy",
        "split_manifest": "split_manifest",
    }
    for snapshot_name, fingerprint_name in snapshot_keys.items():
        portable_path = snapshots.get(snapshot_name)
        declared_hash = fingerprints.get(fingerprint_name)
        if not isinstance(portable_path, str) or not isinstance(declared_hash, str):
            return False
        try:
            snapshot_path = _resolve_evidence_path(
                plan_path.parent,
                portable_path,
                name=snapshot_name,
            )
            if sha256_file(snapshot_path) != declared_hash:
                return False
        except (PlanningError, ReproducibilityError):
            return False
    return True


def create_experiment_plan(
    *,
    evidence_dir: Path,
    registry_path: Path,
    policy_path: Path,
    output_dir: Path,
) -> PlanResult:
    """Validate prerequisites and create a non-overwriting immutable FR-09 plan."""

    if output_dir.exists():
        raise PlanningError(f"Plan output directory already exists: {output_dir.name}")
    evidence = _load_evidence(evidence_dir)
    registry = _load_registry(registry_path)
    policy = _load_policy(policy_path)
    try:
        plan = _build_plan(
            evidence=evidence,
            registry=registry,
            policy=policy,
            registry_path=registry_path,
            policy_path=policy_path,
        )
    except ReproducibilityError as error:
        raise PlanningError("Unable to fingerprint planning inputs") from error

    output_dir.mkdir(parents=True, exist_ok=False)
    registry_snapshot = output_dir / "registry_snapshot.json"
    policy_snapshot = output_dir / "policy_snapshot.yaml"
    split_snapshot = output_dir / "split_snapshot.json"
    plan_path = output_dir / "plan.json"
    try:
        shutil.copyfile(registry_path, registry_snapshot)
        shutil.copyfile(policy_path, policy_snapshot)
        shutil.copyfile(evidence.artifact_paths["split_manifest"], split_snapshot)
        plan_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise PlanningError("Unable to retain immutable planning artifacts") from error
    fingerprint = plan["plan_fingerprint"]
    if not isinstance(fingerprint, str):
        raise PlanningError("Planner did not generate a valid fingerprint")
    return PlanResult(
        output_dir=output_dir,
        plan_path=plan_path,
        plan_fingerprint=fingerprint,
    )

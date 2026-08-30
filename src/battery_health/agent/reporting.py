"""Deterministic FR-12 evidence bundles and offline artifact verification."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from battery_health.agent.planning import (
    PlanningError,
    load_verified_planning_evidence,
    verify_plan_fingerprint,
)
from battery_health.agent.validation import (
    CandidateRecord,
    ExecutionManifest,
    GateDecision,
    LockConfiguration,
    PlanCandidate,
    SelectionLock,
    SelectionPolicy,
    UncertaintyPolicy,
    ValidationPlan,
    verify_selection_lock,
)
from battery_health.reproducibility import ReproducibilityError, sha256_file

ModelT = TypeVar("ModelT", bound=BaseModel)
METRIC_NAMES = ("mae", "rmse", "mape", "r_squared")
FINGERPRINT_ALGORITHM = "sha256-canonical-json-v1"


class ReportError(ValueError):
    """Raised when FR-12 evidence cannot be assembled or verified safely."""


class StrictReportModel(BaseModel):
    """Strict immutable base for security-relevant reporting contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ValidationManifest(StrictReportModel):
    """Strict view of the FR-11 top-level validation result."""

    schema_version: Literal[1]
    state: Literal["LOCKED", "REJECTED"]
    state_history: tuple[str, ...]
    run_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    execution_manifest_sha256: str = Field(min_length=64, max_length=64)
    run_gates: dict[str, GateDecision]
    leaderboard: Literal["leaderboard.json"]
    leaderboard_sha256: str = Field(min_length=64, max_length=64)
    candidate_validation_sha256: dict[str, str]
    selection_lock: str | None
    selection_fingerprint: str | None
    test_partition_accessed: Literal[False]

    @model_validator(mode="after")
    def state_is_consistent(self) -> ValidationManifest:
        """Require lock fields only for a LOCKED result."""

        expected_history = ("RUNNING", "VALIDATED", self.state)
        if self.state_history != expected_history:
            raise ValueError("validation state history is inconsistent")
        if self.state == "LOCKED":
            if self.selection_lock != "selection_lock.json" or not self.selection_fingerprint:
                raise ValueError("LOCKED validation requires a selection lock")
        elif self.selection_lock is not None or self.selection_fingerprint is not None:
            raise ValueError("REJECTED validation cannot declare a selection")
        return self


class UncertaintyEvidence(StrictReportModel):
    """Recorded cell-bootstrap interval supporting one validation metric."""

    method: Literal["bootstrap_by_cell"]
    metric: Literal["mae", "rmse", "mape", "r_squared"]
    confidence_level: float = Field(gt=0, lt=1)
    resamples: int = Field(ge=100)
    random_seed: int
    lower: float
    upper: float
    bootstrap_unit: Literal["cell_id"]


class CandidateValidation(StrictReportModel):
    """Strict candidate gate and metric evidence retained by FR-11."""

    schema_version: Literal[1]
    candidate_id: str = Field(min_length=1)
    implementation_version: str = Field(min_length=1)
    manifest_fingerprint: str = Field(min_length=64, max_length=64)
    execution_status: Literal["succeeded", "failed"]
    record_sha256: str = Field(min_length=64, max_length=64)
    record_fingerprint_verified: bool
    test_partition_accessed: bool
    metrics: dict[str, float]
    primary_metric: float | None
    uncertainty: UncertaintyEvidence | None
    gates: tuple[GateDecision, ...]
    eligible: bool


class LeaderboardCandidate(StrictReportModel):
    """One validation-only leaderboard entry."""

    candidate_id: str
    eligible: bool
    rank: int | None
    metrics: dict[str, float]
    primary_metric: float | None
    uncertainty: UncertaintyEvidence | None
    failed_gates: tuple[str, ...]


class ValidationLeaderboard(StrictReportModel):
    """Strict validation leaderboard input."""

    schema_version: Literal[1]
    state: Literal["VALIDATED"]
    run_id: str
    plan_fingerprint: str
    evidence_partition: Literal["validation"]
    test_partition_accessed: Literal[False]
    selection_policy: SelectionPolicy
    uncertainty_policy: UncertaintyPolicy
    candidates: tuple[LeaderboardCandidate, ...]


class FinalArtifact(StrictReportModel):
    """One held-out finalization artifact and its recorded digest."""

    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)


class FinalizationManifest(StrictReportModel):
    """Strict view of one authorized held-out test evaluation."""

    schema_version: Literal[1]
    state: Literal["FINALIZED"]
    state_history: tuple[str, ...]
    run_id: str
    plan_id: str
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    selection_fingerprint: str = Field(min_length=64, max_length=64)
    selected_candidate: PlanCandidate
    configuration: LockConfiguration
    test_partition_accessed: Literal[True]
    test_evaluation_count: Literal[1]
    artifacts: dict[str, FinalArtifact]
    finalization_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def history_is_finalized(self) -> FinalizationManifest:
        """Require the complete one-shot state history."""

        if self.state_history != ("RUNNING", "VALIDATED", "LOCKED", "FINALIZED"):
            raise ValueError("finalization state history is inconsistent")
        return self


class GenerationPolicy(StrictReportModel):
    """External-service boundary for deterministic report generation."""

    language_model: Literal["disabled"]
    network_access: Literal[False]


class SourceFingerprints(StrictReportModel):
    """Locked source identities referenced by the consolidated bundle."""

    input_dataset: str = Field(min_length=64, max_length=64)
    split_manifest: str = Field(min_length=64, max_length=64)
    code: dict[str, str]
    dependency_lock: str = Field(min_length=64, max_length=64)
    models: dict[str, str]

    @field_validator("code", "models")
    @classmethod
    def mapping_values_are_sha256(cls, value: dict[str, str]) -> dict[str, str]:
        """Require non-empty SHA-256 mappings without inventing identities."""

        if not value or any(not _is_sha256(digest) for digest in value.values()):
            raise ValueError("fingerprint mappings must contain SHA-256 values")
        return value


class ArtifactReference(StrictReportModel):
    """Portable artifact identity protected by SHA-256."""

    artifact_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    role: str = Field(min_length=1)
    media_type: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def path_is_portable(cls, value: str) -> str:
        """Reject absolute and escaping paths in a portable bundle."""

        supplied = Path(value)
        if supplied.is_absolute() or ".." in supplied.parts or supplied.as_posix() != value:
            raise ValueError("artifact path must be portable and normalized")
        return value

    @field_validator("sha256")
    @classmethod
    def digest_is_sha256(cls, value: str) -> str:
        """Require a lowercase hexadecimal SHA-256 digest."""

        if not _is_sha256(value):
            raise ValueError("artifact digest must be lowercase SHA-256")
        return value


class CandidateOutcome(StrictReportModel):
    """Execution and validation outcomes retained for one candidate."""

    candidate_id: str = Field(min_length=1)
    implementation_version: str = Field(min_length=1)
    execution_outcome: Literal["succeeded", "failed", "timed_out"]
    validation_outcome: Literal["eligible", "rejected"]
    outcome_tags: tuple[Literal["successful", "failed", "timed_out", "rejected"], ...]
    failure_code: str | None
    failed_gates: tuple[str, ...]
    artifact_ids: tuple[str, ...] = Field(min_length=2)


class QuantitativeClaim(StrictReportModel):
    """One numeric statement resolvable to a JSON artifact location."""

    claim_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    partition: Literal["validation", "test", "resource"]
    artifact_ids: tuple[str, ...] = Field(min_length=1)
    json_pointer: str = Field(pattern=r"^/")

    @field_validator("artifact_ids")
    @classmethod
    def artifact_ids_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject ambiguous duplicate claim references."""

        if len(value) != len(set(value)):
            raise ValueError("claim artifact identifiers must be unique")
        return value


class FinalTestEvidence(StrictReportModel):
    """Whether held-out test evidence is authorized and present."""

    status: Literal["finalized", "not_finalized", "not_permitted"]
    evaluation_count: Literal[0, 1]
    artifact_ids: tuple[str, ...]

    @model_validator(mode="after")
    def status_matches_artifacts(self) -> FinalTestEvidence:
        """Prevent a non-finalized report from implying test performance."""

        if self.status == "finalized":
            if self.evaluation_count != 1 or not self.artifact_ids:
                raise ValueError("finalized test evidence requires artifacts")
        elif self.evaluation_count != 0 or self.artifact_ids:
            raise ValueError("unavailable test evidence cannot reference artifacts")
        return self


class EvidenceManifest(StrictReportModel):
    """Versioned, deterministic, machine-readable FR-12 evidence manifest."""

    schema_version: Literal[1]
    bundle_state: Literal["LOCKED", "FINALIZED", "REJECTED"]
    run_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    selection_fingerprint: str | None
    generation_policy: GenerationPolicy
    source_fingerprints: SourceFingerprints
    artifacts: tuple[ArtifactReference, ...] = Field(min_length=1)
    candidate_outcomes: tuple[CandidateOutcome, ...] = Field(min_length=1)
    quantitative_claims: tuple[QuantitativeClaim, ...] = Field(min_length=1)
    final_test: FinalTestEvidence
    limitations: tuple[str, ...] = Field(min_length=1)
    unsupported_claims: tuple[str, ...] = Field(min_length=1)
    evidence_fingerprint: str = Field(min_length=64, max_length=64)
    fingerprint_algorithm: Literal["sha256-canonical-json-v1"]

    @model_validator(mode="after")
    def identifiers_and_state_are_consistent(self) -> EvidenceManifest:
        """Require unique references and prohibit implied test evidence."""

        artifact_ids = [item.artifact_id for item in self.artifacts]
        paths = [item.path for item in self.artifacts]
        claim_ids = [item.claim_id for item in self.quantitative_claims]
        candidate_ids = [item.candidate_id for item in self.candidate_outcomes]
        if len(artifact_ids) != len(set(artifact_ids)) or len(paths) != len(set(paths)):
            raise ValueError("artifact identifiers and paths must be unique")
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("quantitative claim identifiers must be unique")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate outcomes must be unique")
        known = set(artifact_ids)
        if any(not set(claim.artifact_ids) <= known for claim in self.quantitative_claims):
            raise ValueError("quantitative claim references an unknown artifact")
        if any(not set(item.artifact_ids) <= known for item in self.candidate_outcomes):
            raise ValueError("candidate outcome references an unknown artifact")
        if not set(self.final_test.artifact_ids) <= known:
            raise ValueError("final test references an unknown artifact")
        has_test_claim = any(claim.partition == "test" for claim in self.quantitative_claims)
        if self.final_test.status != "finalized" and has_test_claim:
            raise ValueError("test claims require finalized evidence")
        if self.bundle_state == "FINALIZED" and self.final_test.status != "finalized":
            raise ValueError("FINALIZED bundle must contain final test evidence")
        if self.bundle_state == "LOCKED" and self.final_test.status != "not_finalized":
            raise ValueError("LOCKED bundle must be marked not finalized")
        if self.bundle_state == "REJECTED" and self.final_test.status != "not_permitted":
            raise ValueError("REJECTED bundle cannot contain test evidence")
        return self


@dataclass(frozen=True)
class EvidenceBundleResult:
    """Paths and identity of one generated FR-12 evidence bundle."""

    output_dir: Path
    manifest_path: Path
    report_path: Path
    run_id: str
    state: str
    evidence_fingerprint: str


@dataclass(frozen=True)
class EvidenceVerificationResult:
    """Successful offline verification summary."""

    valid: bool
    evidence_fingerprint: str
    artifact_count: int
    claim_count: int


@dataclass(frozen=True)
class ArtifactSource:
    """Verified source file scheduled for a portable bundle copy."""

    artifact_id: str
    source: Path
    destination: Path
    sha256: str
    role: str
    media_type: str


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _read_json(path: Path, *, label: str) -> object:
    try:
        return cast(object, json.loads(path.read_text(encoding="utf-8")))
    except OSError as error:
        raise ReportError(f"Unable to read {label}: {path.name}") from error
    except json.JSONDecodeError as error:
        raise ReportError(f"{label.capitalize()} is not valid JSON") from error


def _parse(model_type: type[ModelT], value: object, *, label: str) -> ModelT:
    try:
        return model_type.model_validate(value)
    except PydanticValidationError as error:
        details: set[str] = set()
        for item in error.errors():
            location = ".".join(str(part) for part in item["loc"])
            message = str(item["msg"]).removeprefix("Value error, ")
            details.add(f"{location}: {message}" if location else message)
        raise ReportError(f"Invalid {label}: " + ", ".join(sorted(details))) from error


def _sha256(path: Path) -> str:
    try:
        return sha256_file(path)
    except ReproducibilityError as error:
        raise ReportError(f"Unable to fingerprint evidence artifact: {path.name}") from error


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
        raise ReportError(f"{label.capitalize()} path must be portable")
    resolved_root = root.resolve()
    candidate = (root / supplied).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ReportError(f"{label.capitalize()} path escapes its evidence bundle") from error
    if not candidate.is_file():
        raise ReportError(f"{label.capitalize()} artifact is missing")
    return candidate


def _media_type(path: Path) -> str:
    return {
        ".csv": "text/csv",
        ".json": "application/json",
        ".lock": "text/plain",
        ".png": "image/png",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
    }.get(path.suffix.lower(), "application/octet-stream")


def _register_source(
    sources: list[ArtifactSource],
    *,
    artifact_id: str,
    source: Path,
    destination: Path,
    role: str,
    expected_sha256: str | None = None,
) -> str:
    if any(item.artifact_id == artifact_id for item in sources):
        raise ReportError(f"Duplicate artifact identifier: {artifact_id}")
    if destination.is_absolute() or ".." in destination.parts:
        raise ReportError("Bundle artifact destination must be portable")
    digest = _sha256(source)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ReportError(f"SHA-256 mismatch for {role}")
    sources.append(
        ArtifactSource(
            artifact_id=artifact_id,
            source=source,
            destination=destination,
            sha256=digest,
            role=role,
            media_type=_media_type(source),
        )
    )
    return artifact_id


def _fingerprint_payload(manifest: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in manifest.items()
        if key not in {"evidence_fingerprint", "fingerprint_algorithm"}
    }


def _finalization_fingerprint_valid(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    payload = cast(dict[str, object], dict(value))
    fingerprint = payload.pop("finalization_fingerprint", None)
    return isinstance(fingerprint, str) and fingerprint == _canonical_fingerprint(payload)


def _claim_unit(metric: str) -> str:
    return {
        "mae": "soh_ratio",
        "rmse": "soh_ratio",
        "mape": "percent",
        "r_squared": "dimensionless",
        "duration_seconds": "seconds",
        "peak_python_memory_mb": "mebibytes",
    }.get(metric, "dimensionless")


def _numeric_claim(
    *,
    claim_id: str,
    subject: str,
    metric: str,
    value: int | float,
    partition: Literal["validation", "test", "resource"],
    artifact_id: str,
    json_pointer: str,
) -> QuantitativeClaim:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ReportError(f"Quantitative claim is non-finite: {claim_id}")
    return QuantitativeClaim(
        claim_id=claim_id,
        subject=subject,
        metric=metric,
        value=numeric,
        unit=_claim_unit(metric),
        partition=partition,
        artifact_ids=(artifact_id,),
        json_pointer=json_pointer,
    )


def _pointer_value(value: object, pointer: str) -> object:
    current = value
    for raw_token in pointer.lstrip("/").split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as error:
                raise ReportError(f"Claim JSON pointer is invalid: {pointer}") from error
        elif isinstance(current, dict):
            if token not in current:
                raise ReportError(f"Claim JSON pointer is invalid: {pointer}")
            current = current[token]
        else:
            raise ReportError(f"Claim JSON pointer is invalid: {pointer}")
    return current


def _render_report(manifest: EvidenceManifest) -> str:
    lines = [
        "# Battery Health AutoBench Evidence Report",
        "",
        f"- Run: `{manifest.run_id}`",
        f"- State: `{manifest.bundle_state}`",
        f"- Plan fingerprint: `{manifest.plan_fingerprint}`",
        f"- Evidence fingerprint: `{manifest.evidence_fingerprint}`",
        "- Generation: deterministic; language model disabled; network access disabled",
        "",
        "## Final held-out test",
        "",
    ]
    if manifest.final_test.status == "finalized":
        lines.append("FINALIZED: one authorized held-out test evaluation is recorded.")
    elif manifest.final_test.status == "not_finalized":
        lines.append("NOT FINALIZED: no held-out test performance is available or implied.")
    else:
        lines.append("NOT PERMITTED: the run was rejected and no held-out test was authorized.")
    lines.extend(
        [
            "",
            "## Candidate outcomes",
            "",
            "| Candidate | Execution | Validation | Tags | Failure |",
            "|---|---|---|---|---|",
        ]
    )
    for outcome in manifest.candidate_outcomes:
        lines.append(
            "| "
            + " | ".join(
                (
                    outcome.candidate_id,
                    outcome.execution_outcome,
                    outcome.validation_outcome,
                    ", ".join(outcome.outcome_tags),
                    outcome.failure_code or "none",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Quantitative claims",
            "",
            "| Claim | Subject | Metric | Value | Unit | Partition | Evidence |",
            "|---|---|---|---:|---|---|---|",
        ]
    )
    for claim in manifest.quantitative_claims:
        lines.append(
            "| "
            + " | ".join(
                (
                    claim.claim_id,
                    claim.subject,
                    claim.metric,
                    format(claim.value, ".12g"),
                    claim.unit,
                    claim.partition,
                    f"{claim.artifact_ids[0]} `{claim.json_pointer}`",
                )
            )
            + " |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in manifest.limitations)
    lines.extend(["", "## Unsupported claims", ""])
    lines.extend(f"- {item}" for item in manifest.unsupported_claims)
    return "\n".join(lines) + "\n"


def generate_evidence_bundle(
    *,
    evidence_dir: Path,
    execution_dir: Path,
    validation_dir: Path,
    output_dir: Path,
    finalization_dir: Path | None = None,
) -> EvidenceBundleResult:
    """Create a non-overwriting deterministic evidence bundle without an LLM."""

    if output_dir.exists():
        raise ReportError(f"Evidence output directory already exists: {output_dir.name}")
    try:
        evidence = load_verified_planning_evidence(evidence_dir)
    except PlanningError as error:
        raise ReportError(f"Source evidence is invalid: {error}") from error

    plan_dir = validation_dir / "plan"
    plan_path = plan_dir / "plan.json"
    try:
        plan_verified = verify_plan_fingerprint(plan_path)
    except PlanningError as error:
        raise ReportError("Unable to verify immutable plan") from error
    if not plan_verified:
        raise ReportError("Immutable plan fingerprint or source snapshot is invalid")
    plan = _parse(ValidationPlan, _read_json(plan_path, label="immutable plan"), label="plan")

    execution_path = execution_dir / "execution_manifest.json"
    execution = _parse(
        ExecutionManifest,
        _read_json(execution_path, label="execution manifest"),
        label="execution manifest",
    )
    validation_path = validation_dir / "validation_manifest.json"
    validation = _parse(
        ValidationManifest,
        _read_json(validation_path, label="validation manifest"),
        label="validation manifest",
    )
    identity_checks = (
        execution.plan_id == plan.plan_id,
        execution.plan_fingerprint == plan.plan_fingerprint,
        validation.plan_id == plan.plan_id,
        validation.run_id == plan.plan_id,
        validation.plan_fingerprint == plan.plan_fingerprint,
        validation.execution_manifest_sha256 == _sha256(execution_path),
    )
    if not all(identity_checks):
        raise ReportError("Execution validation and plan identities do not match")
    evidence_fingerprints = {
        "dataset": evidence.artifact_hashes["soh_labels"],
        "split_manifest": evidence.artifact_hashes["split_manifest"],
        "effective_config": evidence.artifact_hashes["effective_config"],
        "dependency_lock": evidence.artifact_hashes["dependency_lock"],
    }
    if any(plan.fingerprints.get(name) != digest for name, digest in evidence_fingerprints.items()):
        raise ReportError("Source evidence does not match the immutable plan")

    sources: list[ArtifactSource] = []
    for name, role in (
        ("plan.json", "immutable_plan"),
        ("registry_snapshot.json", "model_registry"),
        ("policy_snapshot.yaml", "resource_policy"),
        ("split_snapshot.json", "split_manifest"),
    ):
        _register_source(
            sources,
            artifact_id="plan." + Path(name).stem,
            source=plan_dir / name,
            destination=Path("artifacts") / "plan" / name,
            role=role,
        )
    _register_source(
        sources,
        artifact_id="source.reproducibility_manifest",
        source=evidence_dir / "reproducibility_manifest.json",
        destination=Path("artifacts/source/reproducibility_manifest.json"),
        role="source_reproducibility_manifest",
    )
    for key, role in (
        ("effective_config", "effective_configuration"),
        ("soh_metadata", "target_metadata"),
        ("quality_report", "data_quality"),
        ("dependency_lock", "dependency_lock"),
    ):
        _register_source(
            sources,
            artifact_id="source." + key,
            source=evidence.artifact_paths[key],
            destination=Path("artifacts/source") / evidence.artifact_paths[key].name,
            role=role,
            expected_sha256=evidence.artifact_hashes[key],
        )
    execution_artifact_id = _register_source(
        sources,
        artifact_id="execution.manifest",
        source=execution_path,
        destination=Path("artifacts/execution/execution_manifest.json"),
        role="execution_manifest",
        expected_sha256=validation.execution_manifest_sha256,
    )
    execution_input_path = _resolve(
        execution_dir,
        execution.execution_input.path,
        label="execution input",
    )
    _register_source(
        sources,
        artifact_id="execution.input",
        source=execution_input_path,
        destination=Path("artifacts/execution/inputs") / execution_input_path.name,
        role="execution_input",
        expected_sha256=execution.execution_input.sha256,
    )

    _register_source(
        sources,
        artifact_id="validation.manifest",
        source=validation_path,
        destination=Path("artifacts/validation/validation_manifest.json"),
        role="validation_manifest",
    )
    leaderboard_path = validation_dir / validation.leaderboard
    leaderboard = _parse(
        ValidationLeaderboard,
        _read_json(leaderboard_path, label="validation leaderboard"),
        label="validation leaderboard",
    )
    if (
        _sha256(leaderboard_path) != validation.leaderboard_sha256
        or leaderboard.run_id != plan.plan_id
        or leaderboard.plan_fingerprint != plan.plan_fingerprint
    ):
        raise ReportError("Validation leaderboard identity or SHA-256 is invalid")
    _register_source(
        sources,
        artifact_id="validation.leaderboard",
        source=leaderboard_path,
        destination=Path("artifacts/validation/leaderboard.json"),
        role="validation_leaderboard",
        expected_sha256=validation.leaderboard_sha256,
    )

    plan_candidates = {candidate.plugin_id: candidate for candidate in plan.candidates}
    execution_summaries = {item.candidate_id: item for item in execution.candidates}
    if set(plan_candidates) != set(execution_summaries):
        raise ReportError("Execution candidate set does not match the immutable plan")
    candidate_records: dict[str, CandidateRecord] = {}
    candidate_record_ids: dict[str, str] = {}
    candidate_validations: dict[str, CandidateValidation] = {}
    candidate_validation_ids: dict[str, str] = {}

    for candidate_id in sorted(plan_candidates):
        summary = execution_summaries[candidate_id]
        record_path = _resolve(execution_dir, summary.record, label="candidate record")
        if _sha256(record_path) != summary.record_sha256:
            raise ReportError(f"Candidate record SHA-256 is invalid: {candidate_id}")
        record = _parse(
            CandidateRecord,
            _read_json(record_path, label="candidate record"),
            label="candidate record",
        )
        if (
            record.candidate_id != candidate_id
            or record.plan_id != plan.plan_id
            or record.plan_fingerprint != plan.plan_fingerprint
        ):
            raise ReportError(f"Candidate record identity is invalid: {candidate_id}")
        record_artifact_id = _register_source(
            sources,
            artifact_id=f"execution.candidate.{candidate_id}.record",
            source=record_path,
            destination=(
                Path("artifacts/execution/candidates") / candidate_id / "candidate_record.json"
            ),
            role="candidate_execution_record",
            expected_sha256=summary.record_sha256,
        )
        candidate_records[candidate_id] = record
        candidate_record_ids[candidate_id] = record_artifact_id
        for run in record.runs:
            for artifact_name, portable_path in sorted(run.artifacts.items()):
                artifact_path = _resolve(
                    record_path.parent,
                    portable_path,
                    label=f"{candidate_id} {artifact_name}",
                )
                _register_source(
                    sources,
                    artifact_id=(
                        f"execution.candidate.{candidate_id}.seed.{run.seed}.{artifact_name}"
                    ),
                    source=artifact_path,
                    destination=(
                        Path("artifacts/execution/candidates")
                        / candidate_id
                        / f"seed-{run.seed}"
                        / artifact_path.name
                    ),
                    role=f"candidate_{artifact_name}",
                )

        candidate_validation_path = (
            validation_dir / "candidate_validations" / f"{candidate_id}.json"
        )
        declared_validation_hash = validation.candidate_validation_sha256.get(candidate_id)
        if declared_validation_hash is None:
            raise ReportError(f"Candidate validation digest is missing: {candidate_id}")
        candidate_validation = _parse(
            CandidateValidation,
            _read_json(candidate_validation_path, label="candidate validation"),
            label="candidate validation",
        )
        if (
            candidate_validation.candidate_id != candidate_id
            or candidate_validation.record_sha256 != summary.record_sha256
        ):
            raise ReportError(f"Candidate validation identity is invalid: {candidate_id}")
        validation_artifact = _register_source(
            sources,
            artifact_id=f"validation.candidate.{candidate_id}",
            source=candidate_validation_path,
            destination=(
                Path("artifacts/validation/candidate_validations") / f"{candidate_id}.json"
            ),
            role="candidate_validation",
            expected_sha256=declared_validation_hash,
        )
        candidate_validations[candidate_id] = candidate_validation
        candidate_validation_ids[candidate_id] = validation_artifact

    selection: SelectionLock | None = None
    if validation.state == "LOCKED":
        lock_path = validation_dir / "selection_lock.json"
        if not verify_selection_lock(lock_path):
            raise ReportError("Selection lock fingerprint is invalid")
        selection = _parse(
            SelectionLock,
            _read_json(lock_path, label="selection lock"),
            label="selection lock",
        )
        if (
            selection.run_id != plan.plan_id
            or selection.plan_fingerprint != plan.plan_fingerprint
            or selection.selection_fingerprint != validation.selection_fingerprint
            or selection.validation_evidence.execution_manifest_sha256
            != validation.execution_manifest_sha256
        ):
            raise ReportError("Selection lock does not match the retained evidence")
        _register_source(
            sources,
            artifact_id="validation.selection_lock",
            source=lock_path,
            destination=Path("artifacts/validation/selection_lock.json"),
            role="selection_lock",
        )
    elif (validation_dir / "selection_lock.json").exists():
        raise ReportError("REJECTED validation contains an unauthorized selection lock")

    for name, role in (
        ("finalization_claim.json", "finalization_claim"),
        ("finalization_receipt.json", "finalization_receipt"),
    ):
        path = validation_dir / name
        if path.is_file():
            _register_source(
                sources,
                artifact_id="validation." + Path(name).stem,
                source=path,
                destination=Path("artifacts/validation") / name,
                role=role,
            )

    finalization: FinalizationManifest | None = None
    final_metrics: dict[str, object] | None = None
    final_artifact_ids: list[str] = []
    if finalization_dir is not None:
        if selection is None:
            raise ReportError("Finalization evidence requires a LOCKED selection")
        finalization_path = finalization_dir / "finalization_manifest.json"
        finalization_value = _read_json(finalization_path, label="finalization manifest")
        if not _finalization_fingerprint_valid(finalization_value):
            raise ReportError("Finalization fingerprint is invalid")
        finalization = _parse(
            FinalizationManifest,
            finalization_value,
            label="finalization manifest",
        )
        if (
            finalization.run_id != plan.plan_id
            or finalization.plan_fingerprint != plan.plan_fingerprint
            or finalization.selection_fingerprint != selection.selection_fingerprint
            or finalization.selected_candidate != selection.selected_candidate
            or finalization.configuration != selection.configuration
        ):
            raise ReportError("Finalization evidence does not match the locked selection")
        copied_lock = finalization_dir / "selection_lock.json"
        if copied_lock.read_bytes() != (validation_dir / "selection_lock.json").read_bytes():
            raise ReportError("Finalization selection lock copy is inconsistent")
        _register_source(
            sources,
            artifact_id="finalization.manifest",
            source=finalization_path,
            destination=Path("artifacts/finalization/finalization_manifest.json"),
            role="finalization_manifest",
        )
        _register_source(
            sources,
            artifact_id="finalization.selection_lock",
            source=copied_lock,
            destination=Path("artifacts/finalization/selection_lock.json"),
            role="finalization_selection_lock",
        )
        required_final_artifacts = {"predictions", "metrics", "training_metadata"}
        if not required_final_artifacts <= set(finalization.artifacts):
            raise ReportError("Finalization manifest is missing required artifacts")
        for artifact_name, declared in sorted(finalization.artifacts.items()):
            artifact_path = _resolve(
                finalization_dir,
                declared.path,
                label=f"finalization {artifact_name}",
            )
            role = {
                "predictions": "final_test_predictions",
                "metrics": "final_test_metrics",
                "training_metadata": "final_training_metadata",
                "actual_vs_predicted_plot": "final_test_plot",
            }.get(artifact_name, f"final_{artifact_name}")
            artifact_id = _register_source(
                sources,
                artifact_id=f"finalization.{artifact_name}",
                source=artifact_path,
                destination=(Path("artifacts/finalization") / declared.path),
                role=role,
                expected_sha256=declared.sha256,
            )
            final_artifact_ids.append(artifact_id)
            if artifact_name == "metrics":
                metrics_value = _read_json(artifact_path, label="final test metrics")
                if not isinstance(metrics_value, dict) or metrics_value.get("partition") != "test":
                    raise ReportError("Final metrics are not held-out test metrics")
                final_metrics = cast(dict[str, object], metrics_value)
    elif validation.state == "REJECTED":
        finalization = None

    outcomes: list[CandidateOutcome] = []
    claims: list[QuantitativeClaim] = []
    claims.append(
        _numeric_claim(
            claim_id="resource.execution.duration_seconds",
            subject="agent_execution",
            metric="duration_seconds",
            value=execution.duration_seconds,
            partition="resource",
            artifact_id=execution_artifact_id,
            json_pointer="/duration_seconds",
        )
    )
    for candidate_id in sorted(plan_candidates):
        record = candidate_records[candidate_id]
        candidate_validation = candidate_validations[candidate_id]
        run_statuses = {run.status for run in record.runs}
        if "timed_out" in run_statuses:
            execution_outcome: Literal["succeeded", "failed", "timed_out"] = "timed_out"
        elif record.status == "failed":
            execution_outcome = "failed"
        else:
            execution_outcome = "succeeded"
        tags: list[Literal["successful", "failed", "timed_out", "rejected"]] = []
        if execution_outcome == "succeeded":
            tags.append("successful")
        else:
            tags.append("failed")
            if execution_outcome == "timed_out":
                tags.append("timed_out")
        validation_outcome: Literal["eligible", "rejected"] = (
            "eligible" if candidate_validation.eligible else "rejected"
        )
        if validation_outcome == "rejected":
            tags.append("rejected")
        failed_gates = tuple(gate.gate_id for gate in candidate_validation.gates if not gate.passed)
        failure_code = None
        if record.failure is not None:
            raw_code = record.failure.get("code")
            failure_code = str(raw_code) if raw_code is not None else "unspecified_failure"
        outcomes.append(
            CandidateOutcome(
                candidate_id=candidate_id,
                implementation_version=record.implementation_version,
                execution_outcome=execution_outcome,
                validation_outcome=validation_outcome,
                outcome_tags=tuple(tags),
                failure_code=failure_code,
                failed_gates=failed_gates,
                artifact_ids=(
                    candidate_record_ids[candidate_id],
                    candidate_validation_ids[candidate_id],
                ),
            )
        )
        validation_artifact_id_for_claim = candidate_validation_ids[candidate_id]
        for metric, value in sorted(candidate_validation.metrics.items()):
            claims.append(
                _numeric_claim(
                    claim_id=f"validation.{candidate_id}.{metric}",
                    subject=candidate_id,
                    metric=metric,
                    value=value,
                    partition="validation",
                    artifact_id=validation_artifact_id_for_claim,
                    json_pointer=f"/metrics/{metric}",
                )
            )
        if candidate_validation.uncertainty is not None:
            uncertainty = candidate_validation.uncertainty
            for bound in ("lower", "upper"):
                uncertainty_value = getattr(uncertainty, bound)
                claims.append(
                    _numeric_claim(
                        claim_id=(f"validation.{candidate_id}.{uncertainty.metric}.ci_{bound}"),
                        subject=candidate_id,
                        metric=f"{uncertainty.metric}_ci_{bound}",
                        value=uncertainty_value,
                        partition="validation",
                        artifact_id=validation_artifact_id_for_claim,
                        json_pointer=f"/uncertainty/{bound}",
                    )
                )
        record_artifact_id = candidate_record_ids[candidate_id]
        for index, run in enumerate(record.runs):
            usage = run.resource_usage or {}
            for metric in ("duration_seconds", "peak_python_memory_mb"):
                usage_value = usage.get(metric)
                if isinstance(usage_value, int | float) and math.isfinite(float(usage_value)):
                    claims.append(
                        _numeric_claim(
                            claim_id=f"resource.{candidate_id}.seed.{run.seed}.{metric}",
                            subject=f"{candidate_id}:seed-{run.seed}",
                            metric=metric,
                            value=usage_value,
                            partition="resource",
                            artifact_id=record_artifact_id,
                            json_pointer=f"/runs/{index}/resource_usage/{metric}",
                        )
                    )

    if finalization is not None and final_metrics is not None:
        for metric in METRIC_NAMES:
            final_metric_value = final_metrics.get(metric)
            if not isinstance(final_metric_value, int | float):
                raise ReportError(f"Final test metric is missing: {metric}")
            claims.append(
                _numeric_claim(
                    claim_id=f"test.{finalization.selected_candidate.plugin_id}.{metric}",
                    subject=finalization.selected_candidate.plugin_id,
                    metric=metric,
                    value=final_metric_value,
                    partition="test",
                    artifact_id="finalization.metrics",
                    json_pointer=f"/{metric}",
                )
            )

    if finalization is not None:
        bundle_state: Literal["LOCKED", "FINALIZED", "REJECTED"] = "FINALIZED"
        final_test = FinalTestEvidence(
            status="finalized",
            evaluation_count=1,
            artifact_ids=tuple(sorted(final_artifact_ids)),
        )
    elif validation.state == "LOCKED":
        bundle_state = "LOCKED"
        final_test = FinalTestEvidence(
            status="not_finalized",
            evaluation_count=0,
            artifact_ids=(),
        )
    else:
        bundle_state = "REJECTED"
        final_test = FinalTestEvidence(
            status="not_permitted",
            evaluation_count=0,
            artifact_ids=(),
        )

    code_fingerprints = {
        "locked_planner": plan.fingerprints.get("planner_implementation", ""),
        "reporter": _sha256(Path(__file__)),
    }
    if any(not _is_sha256(value) for value in code_fingerprints.values()):
        raise ReportError("Locked code fingerprint evidence is incomplete")
    source_fingerprints = SourceFingerprints(
        input_dataset=plan.fingerprints["dataset"],
        split_manifest=plan.fingerprints["split_manifest"],
        code=code_fingerprints,
        dependency_lock=plan.fingerprints["dependency_lock"],
        models={
            candidate.plugin_id: candidate.manifest_fingerprint
            for candidate in sorted(plan.candidates, key=lambda item: item.plugin_id)
        },
    )
    limitations = [
        "code_fingerprint_scope_is_limited_to_the_locked_planner_plugin_manifests_and_reporter",
        "labelled_source_dataset_is_fingerprinted_but_not_bundled",
        "native_library_rss_is_not_a_hard_resource_limit",
        "serialized_model_size_is_unavailable",
        "dedicated_prediction_latency_is_unavailable",
    ]
    if len(plan.random_seeds) == 1:
        limitations.append("multi_seed_dispersion_is_unavailable_for_a_single_seed")
    unsupported_claims = [
        "bms_production_readiness",
        "serialized_model_size",
        "dedicated_prediction_latency",
    ]
    if final_test.status != "finalized":
        limitations.append("held_out_test_evidence_is_unavailable")
        unsupported_claims.append("held_out_test_performance")

    artifact_references = tuple(
        ArtifactReference(
            artifact_id=item.artifact_id,
            path=item.destination.as_posix(),
            sha256=item.sha256,
            role=item.role,
            media_type=item.media_type,
        )
        for item in sorted(sources, key=lambda source: source.artifact_id)
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "bundle_state": bundle_state,
        "run_id": plan.plan_id,
        "plan_id": plan.plan_id,
        "plan_fingerprint": plan.plan_fingerprint,
        "selection_fingerprint": (
            selection.selection_fingerprint if selection is not None else None
        ),
        "generation_policy": GenerationPolicy(
            language_model="disabled",
            network_access=False,
        ).model_dump(mode="json"),
        "source_fingerprints": source_fingerprints.model_dump(mode="json"),
        "artifacts": [item.model_dump(mode="json") for item in artifact_references],
        "candidate_outcomes": [item.model_dump(mode="json") for item in outcomes],
        "quantitative_claims": [
            item.model_dump(mode="json") for item in sorted(claims, key=lambda item: item.claim_id)
        ],
        "final_test": final_test.model_dump(mode="json"),
        "limitations": sorted(limitations),
        "unsupported_claims": sorted(unsupported_claims),
    }
    fingerprint = _canonical_fingerprint(payload)
    payload["evidence_fingerprint"] = fingerprint
    payload["fingerprint_algorithm"] = FINGERPRINT_ALGORITHM
    manifest = _parse(EvidenceManifest, payload, label="generated evidence manifest")

    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        for source in sources:
            destination = output_dir / source.destination
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source.source, destination)
            if _sha256(destination) != source.sha256:
                raise ReportError(f"Artifact changed during bundle copy: {source.artifact_id}")
        manifest_path = output_dir / "evidence_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        report_path = output_dir / "report.md"
        report_path.write_text(_render_report(manifest), encoding="utf-8")
    except OSError as error:
        raise ReportError("Unable to write deterministic evidence bundle") from error
    verify_evidence_bundle(output_dir)
    return EvidenceBundleResult(
        output_dir=output_dir,
        manifest_path=manifest_path,
        report_path=report_path,
        run_id=manifest.run_id,
        state=manifest.bundle_state,
        evidence_fingerprint=manifest.evidence_fingerprint,
    )


def verify_evidence_bundle(bundle_dir: Path) -> EvidenceVerificationResult:
    """Verify strict schema, fingerprints, artifacts, claims, and deterministic report offline."""

    manifest_path = bundle_dir / "evidence_manifest.json"
    manifest = _parse(
        EvidenceManifest,
        _read_json(manifest_path, label="evidence manifest"),
        label="evidence manifest",
    )
    payload = manifest.model_dump(mode="json")
    expected_fingerprint = _canonical_fingerprint(_fingerprint_payload(payload))
    if manifest.evidence_fingerprint != expected_fingerprint:
        raise ReportError("Evidence fingerprint is invalid")

    artifacts = {artifact.artifact_id: artifact for artifact in manifest.artifacts}
    parsed_json: dict[str, object] = {}
    for artifact in manifest.artifacts:
        artifact_path = _resolve(bundle_dir, artifact.path, label=artifact.artifact_id)
        if _sha256(artifact_path) != artifact.sha256:
            raise ReportError(f"SHA-256 mismatch for artifact: {artifact.artifact_id}")
        if artifact.media_type == "application/json":
            parsed_json[artifact.artifact_id] = _read_json(
                artifact_path,
                label=artifact.artifact_id,
            )

    for claim in manifest.quantitative_claims:
        artifact_id = claim.artifact_ids[0]
        artifact = artifacts[artifact_id]
        if artifact.media_type != "application/json" or artifact_id not in parsed_json:
            raise ReportError(f"Quantitative claim requires JSON evidence: {claim.claim_id}")
        value = _pointer_value(parsed_json[artifact_id], claim.json_pointer)
        if not isinstance(value, int | float) or not math.isfinite(float(value)):
            raise ReportError(f"Quantitative claim does not resolve to a number: {claim.claim_id}")
        if float(value) != claim.value:
            raise ReportError(f"Quantitative claim value mismatch: {claim.claim_id}")

    report_path = bundle_dir / "report.md"
    try:
        report_text = report_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReportError("Human-readable report is missing") from error
    if report_text != _render_report(manifest):
        raise ReportError("Human-readable report does not match the evidence manifest")
    return EvidenceVerificationResult(
        valid=True,
        evidence_fingerprint=manifest.evidence_fingerprint,
        artifact_count=len(manifest.artifacts),
        claim_count=len(manifest.quantitative_claims),
    )

"""Deterministic model-plugin governance and license quarantine for FR-13."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class PluginGovernanceError(ValueError):
    """Raised when plugin declarations or governed evidence cannot be trusted."""


class StrictGovernanceModel(BaseModel):
    """Strict immutable base for governance input and output contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class FeatureDeclaration(StrictGovernanceModel):
    """One required feature and its physical or semantic unit."""

    name: str = Field(min_length=1)
    unit: str = Field(min_length=1)


class CapabilityDeclaration(StrictGovernanceModel):
    """Training and prediction capabilities exposed by a plugin."""

    training: bool
    prediction: bool


class DeterminismDeclaration(StrictGovernanceModel):
    """Declared seed and same-seed reproducibility behavior."""

    supported: bool
    seed_parameter: str | None
    same_seed_reproducible: bool

    @model_validator(mode="after")
    def seed_contract_is_coherent(self) -> Self:
        """Require an explicit seed parameter for deterministic plugins."""

        if self.supported and not self.seed_parameter:
            raise ValueError("deterministic support requires a seed parameter")
        if not self.supported and self.same_seed_reproducible:
            raise ValueError("same-seed reproducibility requires deterministic support")
        return self


class DependencyDeclaration(StrictGovernanceModel):
    """One declared runtime dependency constraint."""

    name: str = Field(min_length=1)
    version_specifier: str = Field(min_length=1)
    required: bool


class RequirementDeclaration(StrictGovernanceModel):
    """Dependency, accelerator, and minimum-memory requirements."""

    dependencies: tuple[DependencyDeclaration, ...]
    accelerator: str = Field(min_length=1)
    minimum_memory_mb: int = Field(ge=1)

    @field_validator("dependencies")
    @classmethod
    def dependencies_are_unique(
        cls, value: tuple[DependencyDeclaration, ...]
    ) -> tuple[DependencyDeclaration, ...]:
        """Reject shadowed dependency declarations."""

        names = [dependency.name for dependency in value]
        if len(names) != len(set(names)):
            raise ValueError("dependency names must be unique")
        return value


class LicenseDeclaration(StrictGovernanceModel):
    """Reviewed or explicitly unknown license status for code or weights."""

    identifier: str | None = None
    status: Literal["known", "unknown", "unreviewed", "not_applicable"] = "unknown"
    redistribution_approved: bool = False

    @model_validator(mode="after")
    def status_matches_identifier(self) -> Self:
        """Prevent unknown licenses from carrying redistribution approval."""

        if self.status in {"known", "not_applicable"} and not self.identifier:
            raise ValueError("known license status requires an identifier")
        if self.status in {"unknown", "unreviewed"} and self.redistribution_approved:
            raise ValueError("unknown license status cannot approve redistribution")
        return self


class PluginLicenses(StrictGovernanceModel):
    """Code and weight licenses; omissions remain explicit quarantine inputs."""

    code: LicenseDeclaration | None = None
    weights: LicenseDeclaration | None = None


class UpstreamDeclaration(StrictGovernanceModel):
    """Source and citation retained without downloading upstream artifacts."""

    source: str = Field(min_length=1)
    repository_url: str = Field(min_length=1)
    citation: str = Field(min_length=1)


Scalar = str | int | float | bool


class ParameterDomain(StrictGovernanceModel):
    """Finite domain for one bounded search parameter."""

    kind: Literal["integer", "float", "categorical"]
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[Scalar, ...] = ()

    @model_validator(mode="after")
    def domain_is_bounded(self) -> Self:
        """Require a closed numeric interval or a finite categorical set."""

        if self.kind == "categorical":
            if not self.choices or self.minimum is not None or self.maximum is not None:
                raise ValueError("categorical domains require only non-empty choices")
            if len({repr(item) for item in self.choices}) != len(self.choices):
                raise ValueError("categorical choices must be unique")
            return self
        if self.choices or self.minimum is None or self.maximum is None:
            raise ValueError("numeric domains require minimum and maximum only")
        if self.minimum > self.maximum:
            raise ValueError("parameter minimum must not exceed maximum")
        if self.kind == "integer" and (
            not self.minimum.is_integer() or not self.maximum.is_integer()
        ):
            raise ValueError("integer parameter bounds must be integral")
        return self


class SearchSpaceDeclaration(StrictGovernanceModel):
    """Predeclared finite search strategy and maximum trial count."""

    strategy: Literal["grid", "random"]
    max_trials: int = Field(ge=1)
    parameters: dict[str, ParameterDomain]

    @field_validator("parameters")
    @classmethod
    def parameter_names_are_nonempty(
        cls, value: dict[str, ParameterDomain]
    ) -> dict[str, ParameterDomain]:
        """Reject unnamed search dimensions."""

        if any(not name.strip() for name in value):
            raise ValueError("search parameter names must not be empty")
        return value


class SerializationDeclaration(StrictGovernanceModel):
    """Explicit save/load contract, including an honest unsupported state."""

    format: str = Field(min_length=1)
    artifact_extension: str | None
    save_supported: bool
    load_supported: bool

    @model_validator(mode="after")
    def unsupported_contract_is_explicit(self) -> Self:
        """Reserve the `none` format for plugins without serialization support."""

        if self.format == "none" and (
            self.artifact_extension is not None or self.save_supported or self.load_supported
        ):
            raise ValueError("none serialization cannot declare save load or an extension")
        if self.format != "none" and not self.artifact_extension:
            raise ValueError("serialized formats require an artifact extension")
        return self


class ExecutionDeclaration(StrictGovernanceModel):
    """Reviewed adapter identity and default-execution opt-in."""

    adapter: str = Field(min_length=1)
    default_enabled: bool


class PluginDeclaration(StrictGovernanceModel):
    """Complete FR-13 plugin declaration."""

    plugin_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$", min_length=1, max_length=100)
    implementation_version: str = Field(min_length=1)
    supported_target: str = Field(min_length=1)
    required_features: tuple[FeatureDeclaration, ...] = Field(min_length=1)
    preprocessing_policy: str = Field(min_length=1)
    capabilities: CapabilityDeclaration
    determinism: DeterminismDeclaration
    requirements: RequirementDeclaration
    licenses: PluginLicenses
    upstream: UpstreamDeclaration
    search_space: SearchSpaceDeclaration
    serialization: SerializationDeclaration
    execution: ExecutionDeclaration

    @field_validator("required_features")
    @classmethod
    def features_are_unique(
        cls, value: tuple[FeatureDeclaration, ...]
    ) -> tuple[FeatureDeclaration, ...]:
        """Reject conflicting feature contracts."""

        names = [feature.name for feature in value]
        if len(names) != len(set(names)):
            raise ValueError("required feature names must be unique")
        return value


class PluginCatalog(StrictGovernanceModel):
    """Versioned collection of complete plugin declarations."""

    schema_version: Literal[1]
    plugins: tuple[PluginDeclaration, ...] = Field(min_length=1)

    @field_validator("plugins")
    @classmethod
    def plugin_identities_are_unique(
        cls, value: tuple[PluginDeclaration, ...]
    ) -> tuple[PluginDeclaration, ...]:
        """Reject duplicate stable plugin identities."""

        identities = [plugin.plugin_id for plugin in value]
        if len(identities) != len(set(identities)):
            raise ValueError("plugin identifiers must be unique")
        return value


class GovernancePolicy(StrictGovernanceModel):
    """Reviewed compatibility and license policy applied before planning."""

    schema_version: Literal[1]
    target: str = Field(min_length=1)
    feature_contract: tuple[FeatureDeclaration, ...] = Field(min_length=1)
    require_training: bool
    require_prediction: bool
    require_determinism: bool
    allowed_dependencies: tuple[str, ...]
    allowed_accelerators: tuple[str, ...] = Field(min_length=1)
    max_memory_mb: int = Field(ge=1)
    allowed_code_licenses: tuple[str, ...] = Field(min_length=1)
    allowed_weight_licenses: tuple[str, ...] = Field(min_length=1)
    approved_execution_adapters: tuple[str, ...] = Field(min_length=1)
    max_search_trials: int = Field(ge=1)
    serialization_required: bool
    allowed_serialization_formats: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def policy_sets_are_unambiguous(self) -> Self:
        """Require unique feature names and unique allow-list entries."""

        feature_names = [feature.name for feature in self.feature_contract]
        if len(feature_names) != len(set(feature_names)):
            raise ValueError("policy feature names must be unique")
        fields = (
            self.allowed_dependencies,
            self.allowed_accelerators,
            self.allowed_code_licenses,
            self.allowed_weight_licenses,
            self.approved_execution_adapters,
            self.allowed_serialization_formats,
        )
        if any(len(items) != len(set(items)) for items in fields):
            raise ValueError("governance allow lists must contain unique values")
        if any(not item.strip() for items in fields for item in items):
            raise ValueError("governance allow-list values must not be empty")
        return self


class GovernancePermissions(StrictGovernanceModel):
    """Machine-enforced operations permitted by one decision."""

    execution_allowed: bool
    default_execution_allowed: bool
    code_bundling_allowed: bool
    weight_bundling_allowed: bool
    redistribution_approved: bool
    external_reference_only: bool


class GovernanceDecision(StrictGovernanceModel):
    """Deterministic approval, rejection, or quarantine decision."""

    schema_version: Literal[1]
    plugin_id: str = Field(min_length=1)
    implementation_version: str = Field(min_length=1)
    status: Literal["APPROVED", "REJECTED", "QUARANTINED"]
    reason_codes: tuple[str, ...] = Field(min_length=1)
    gate_results: dict[str, bool]
    declaration_fingerprint: str = Field(min_length=64, max_length=64)
    executable_manifest_fingerprint: str | None
    permissions: GovernancePermissions

    @model_validator(mode="after")
    def permissions_match_status(self) -> Self:
        """Prevent a non-approved decision from carrying executable permissions."""

        if self.status == "APPROVED":
            if (
                self.executable_manifest_fingerprint is None
                or not self.permissions.execution_allowed
            ):
                raise ValueError("approved plugins require an executable manifest")
            if self.permissions.external_reference_only:
                raise ValueError("approved plugins cannot be reference-only")
        elif self.executable_manifest_fingerprint is not None:
            raise ValueError("non-approved plugins cannot carry executable manifests")
        elif any(
            (
                self.permissions.execution_allowed,
                self.permissions.default_execution_allowed,
                self.permissions.code_bundling_allowed,
                self.permissions.weight_bundling_allowed,
                self.permissions.redistribution_approved,
            )
        ):
            raise ValueError("non-approved plugins cannot carry execution or distribution rights")
        if self.status == "QUARANTINED" and not self.permissions.external_reference_only:
            raise ValueError("quarantined plugins must be reference-only")
        if self.status == "REJECTED" and self.permissions.external_reference_only:
            raise ValueError("rejected plugins cannot be presented as approved references")
        return self


class ExecutablePlugin(StrictGovernanceModel):
    """FR-09-compatible projection containing only approved execution fields."""

    plugin_id: str
    implementation_version: str
    supported_target: str
    required_features: tuple[str, ...]
    preprocessing_policy: str
    deterministic_supported: bool
    seed_parameter: str
    code_license: str
    weight_license: str
    source: str
    bounded_parameters: dict[str, object]


class ApprovedRegistry(StrictGovernanceModel):
    """Planning-compatible registry that may be empty after governance."""

    schema_version: Literal[1]
    plugins: tuple[ExecutablePlugin, ...]


class GovernanceCounts(StrictGovernanceModel):
    """Outcome counts retained in the governed bundle."""

    approved: int = Field(ge=0)
    rejected: int = Field(ge=0)
    quarantined: int = Field(ge=0)


class GovernanceManifest(StrictGovernanceModel):
    """Protected index for a deterministic declarations-only governance bundle."""

    schema_version: Literal[1]
    state: Literal["GOVERNED"]
    catalog_fingerprint: str = Field(min_length=64, max_length=64)
    policy_fingerprint: str = Field(min_length=64, max_length=64)
    counts: GovernanceCounts
    decisions: dict[str, str]
    files: dict[str, str]
    sha256: dict[str, str]
    artifact_inventory: tuple[str, ...]
    governance_fingerprint: str = Field(min_length=64, max_length=64)
    fingerprint_algorithm: Literal["sha256-canonical-json-v1"]

    @model_validator(mode="after")
    def protected_file_index_is_complete(self) -> Self:
        """Require one digest per protected portable file and no bundled artifacts."""

        if set(self.files) != set(self.sha256):
            raise ValueError("governance file and SHA-256 keys must match")
        if set(self.decisions) != {
            key.removeprefix("decision.") for key in self.files if key.startswith("decision.")
        }:
            raise ValueError("governance decision index is incomplete")
        if self.artifact_inventory:
            raise ValueError("governance bundles may contain declarations only")
        if self.counts.approved + self.counts.rejected + self.counts.quarantined != len(
            self.decisions
        ):
            raise ValueError("governance counts do not match decisions")
        return self


@dataclass(frozen=True)
class GovernanceResult:
    """Created governed plugin bundle and its outcome counts."""

    output_dir: Path
    manifest_path: Path
    governance_fingerprint: str
    approved: int
    rejected: int
    quarantined: int


@dataclass(frozen=True)
class GovernanceVerificationResult:
    """Successful offline verification result."""

    valid: bool
    governance_fingerprint: str
    approved: int
    rejected: int
    quarantined: int


@dataclass(frozen=True)
class _Evaluation:
    decisions: tuple[GovernanceDecision, ...]
    registry: dict[str, object]
    external_references: dict[str, object]


ModelT = TypeVar("ModelT", bound=BaseModel)


def _read_json(path: Path, *, label: str) -> object:
    try:
        return cast(object, json.loads(path.read_text(encoding="utf-8")))
    except OSError as error:
        raise PluginGovernanceError(f"Unable to read {label}: {path.name}") from error
    except json.JSONDecodeError as error:
        raise PluginGovernanceError(f"{label.capitalize()} is not valid JSON") from error


def _parse(model_type: type[ModelT], value: object, *, label: str) -> ModelT:
    try:
        return model_type.model_validate(value)
    except ValidationError as error:
        details = []
        for item in error.errors():
            location = ".".join(str(part) for part in item["loc"]) or "root"
            details.append(f"{location}: {item['msg']}")
        raise PluginGovernanceError(f"Invalid {label}: " + "; ".join(sorted(details))) from error


def _canonical_fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise PluginGovernanceError(f"Unable to fingerprint governed file: {path.name}") from error


def _write_json(path: Path, value: object) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise PluginGovernanceError(f"Unable to write governed file: {path.name}") from error


def _resolve(root: Path, portable_path: str, *, label: str) -> Path:
    supplied = Path(portable_path)
    if supplied.is_absolute():
        raise PluginGovernanceError(f"Governance path must be portable: {label}")
    resolved_root = root.resolve()
    candidate = (root / supplied).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise PluginGovernanceError(f"Governance path escapes the bundle: {label}") from error
    if not candidate.is_file():
        raise PluginGovernanceError(f"Governed file is missing: {label}")
    return candidate


def _catalog_payload(catalog: PluginCatalog) -> dict[str, object]:
    return {
        "schema_version": 1,
        "plugins": [
            plugin.model_dump(mode="json")
            for plugin in sorted(catalog.plugins, key=lambda item: item.plugin_id)
        ],
    }


def _license_unknown(value: LicenseDeclaration | None) -> bool:
    return value is None or value.status in {"unknown", "unreviewed"}


def _license_allowed(value: LicenseDeclaration | None, allowed: tuple[str, ...]) -> bool:
    return (
        value is not None
        and value.status in {"known", "not_applicable"}
        and value.identifier is not None
        and value.identifier in allowed
        and value.redistribution_approved
    )


def _executable_plugin(plugin: PluginDeclaration) -> dict[str, object]:
    code = plugin.licenses.code
    weights = plugin.licenses.weights
    if code is None or code.identifier is None or weights is None or weights.identifier is None:
        raise PluginGovernanceError("Approved plugin is missing executable license identifiers")
    return {
        "plugin_id": plugin.plugin_id,
        "implementation_version": plugin.implementation_version,
        "supported_target": plugin.supported_target,
        "required_features": [feature.name for feature in plugin.required_features],
        "preprocessing_policy": plugin.preprocessing_policy,
        "deterministic_supported": plugin.determinism.supported,
        "seed_parameter": plugin.determinism.seed_parameter or "",
        "code_license": code.identifier,
        "weight_license": weights.identifier,
        "source": plugin.execution.adapter,
        "bounded_parameters": {
            name: domain.model_dump(mode="json")
            for name, domain in sorted(plugin.search_space.parameters.items())
        },
    }


_GATE_REASON = {
    "target_compatible": "target_incompatible",
    "features_compatible": "feature_contract_incompatible",
    "training_capability": "training_capability_missing",
    "prediction_capability": "prediction_capability_missing",
    "determinism_compatible": "determinism_contract_incompatible",
    "dependencies_allowed": "dependency_not_allowed",
    "accelerator_allowed": "accelerator_not_allowed",
    "memory_within_policy": "memory_requirement_exceeds_policy",
    "search_space_bounded": "search_space_exceeds_policy",
    "serialization_compatible": "serialization_contract_incompatible",
    "execution_adapter_approved": "execution_adapter_not_approved",
}


def _evaluate_plugin(
    plugin: PluginDeclaration, policy: GovernancePolicy
) -> tuple[GovernanceDecision, dict[str, object] | None]:
    policy_features = {(feature.name, feature.unit) for feature in policy.feature_contract}
    plugin_features = {(feature.name, feature.unit) for feature in plugin.required_features}
    dependencies = {dependency.name for dependency in plugin.requirements.dependencies}
    gates = {
        "target_compatible": plugin.supported_target == policy.target,
        "features_compatible": plugin_features <= policy_features,
        "training_capability": not policy.require_training or plugin.capabilities.training,
        "prediction_capability": not policy.require_prediction or plugin.capabilities.prediction,
        "determinism_compatible": (
            not policy.require_determinism
            or (
                plugin.determinism.supported
                and plugin.determinism.same_seed_reproducible
                and plugin.determinism.seed_parameter is not None
            )
        ),
        "dependencies_allowed": dependencies <= set(policy.allowed_dependencies),
        "accelerator_allowed": plugin.requirements.accelerator in policy.allowed_accelerators,
        "memory_within_policy": plugin.requirements.minimum_memory_mb <= policy.max_memory_mb,
        "search_space_bounded": plugin.search_space.max_trials <= policy.max_search_trials,
        "serialization_compatible": (
            plugin.serialization.format in policy.allowed_serialization_formats
            and (
                not policy.serialization_required
                or (plugin.serialization.save_supported and plugin.serialization.load_supported)
            )
        ),
        "execution_adapter_approved": (
            plugin.execution.adapter in policy.approved_execution_adapters
        ),
    }
    code_unknown = _license_unknown(plugin.licenses.code)
    weights_unknown = _license_unknown(plugin.licenses.weights)
    code_allowed = _license_allowed(plugin.licenses.code, policy.allowed_code_licenses)
    weights_allowed = _license_allowed(plugin.licenses.weights, policy.allowed_weight_licenses)
    gates["licenses_known"] = not code_unknown and not weights_unknown
    gates["licenses_policy_approved"] = code_allowed and weights_allowed

    reasons = [_GATE_REASON[name] for name in _GATE_REASON if not gates[name]]
    license_quarantine = code_unknown or weights_unknown or not code_allowed or not weights_allowed
    if code_unknown or weights_unknown:
        reasons.append("license_status_unknown")
    elif not code_allowed or not weights_allowed:
        reasons.append("license_not_approved")

    executable: dict[str, object] | None = None
    if license_quarantine:
        status: Literal["APPROVED", "REJECTED", "QUARANTINED"] = "QUARANTINED"
        permissions = GovernancePermissions(
            execution_allowed=False,
            default_execution_allowed=False,
            code_bundling_allowed=False,
            weight_bundling_allowed=False,
            redistribution_approved=False,
            external_reference_only=True,
        )
    elif any(not value for name, value in gates.items() if not name.startswith("licenses_")):
        status = "REJECTED"
        permissions = GovernancePermissions(
            execution_allowed=False,
            default_execution_allowed=False,
            code_bundling_allowed=False,
            weight_bundling_allowed=False,
            redistribution_approved=False,
            external_reference_only=False,
        )
    else:
        status = "APPROVED"
        executable = _executable_plugin(plugin)
        reasons = ["all_governance_gates_passed"]
        permissions = GovernancePermissions(
            execution_allowed=True,
            default_execution_allowed=plugin.execution.default_enabled,
            code_bundling_allowed=True,
            weight_bundling_allowed=True,
            redistribution_approved=True,
            external_reference_only=False,
        )

    declaration = plugin.model_dump(mode="json")
    decision = GovernanceDecision(
        schema_version=1,
        plugin_id=plugin.plugin_id,
        implementation_version=plugin.implementation_version,
        status=status,
        reason_codes=tuple(dict.fromkeys(reasons)),
        gate_results=dict(sorted(gates.items())),
        declaration_fingerprint=_canonical_fingerprint(declaration),
        executable_manifest_fingerprint=(
            _canonical_fingerprint(executable) if executable is not None else None
        ),
        permissions=permissions,
    )
    return decision, executable


def _evaluate(catalog: PluginCatalog, policy: GovernancePolicy) -> _Evaluation:
    decisions: list[GovernanceDecision] = []
    executable_plugins: list[dict[str, object]] = []
    references: list[dict[str, object]] = []
    for plugin in sorted(catalog.plugins, key=lambda item: item.plugin_id):
        decision, executable = _evaluate_plugin(plugin, policy)
        decisions.append(decision)
        if executable is not None:
            executable_plugins.append(executable)
        if decision.status == "QUARANTINED":
            references.append(
                {
                    "plugin_id": plugin.plugin_id,
                    "implementation_version": plugin.implementation_version,
                    "upstream": plugin.upstream.model_dump(mode="json"),
                    "license_status": {
                        "code": (
                            plugin.licenses.code.status
                            if plugin.licenses.code is not None
                            else "not_declared"
                        ),
                        "weights": (
                            plugin.licenses.weights.status
                            if plugin.licenses.weights is not None
                            else "not_declared"
                        ),
                    },
                    "reason_codes": list(decision.reason_codes),
                }
            )
    registry: dict[str, object] = {"schema_version": 1, "plugins": executable_plugins}
    _parse(ApprovedRegistry, registry, label="approved registry")
    return _Evaluation(
        decisions=tuple(decisions),
        registry=registry,
        external_references={"schema_version": 1, "plugins": references},
    )


def _counts(decisions: tuple[GovernanceDecision, ...]) -> GovernanceCounts:
    return GovernanceCounts(
        approved=sum(decision.status == "APPROVED" for decision in decisions),
        rejected=sum(decision.status == "REJECTED" for decision in decisions),
        quarantined=sum(decision.status == "QUARANTINED" for decision in decisions),
    )


def _manifest_payload(manifest: GovernanceManifest) -> dict[str, object]:
    payload = manifest.model_dump(mode="json")
    del payload["governance_fingerprint"]
    return payload


def govern_model_plugins(
    *, catalog_path: Path, policy_path: Path, output_dir: Path
) -> GovernanceResult:
    """Validate plugin declarations and create a non-overwriting governed registry bundle."""

    if output_dir.exists():
        raise PluginGovernanceError(
            f"Governance output directory already exists: {output_dir.name}"
        )
    catalog = _parse(
        PluginCatalog,
        _read_json(catalog_path, label="plugin catalog"),
        label="plugin catalog",
    )
    policy = _parse(
        GovernancePolicy,
        _read_json(policy_path, label="governance policy"),
        label="governance policy",
    )
    evaluation = _evaluate(catalog, policy)
    catalog_payload = _catalog_payload(catalog)
    policy_payload = policy.model_dump(mode="json")

    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        raise PluginGovernanceError("Unable to create governance output directory") from error

    paths: dict[str, str] = {
        "catalog_snapshot": "catalog_snapshot.json",
        "policy_snapshot": "policy_snapshot.json",
        "approved_registry": "approved_registry.json",
        "external_references": "external_references.json",
    }
    _write_json(output_dir / paths["catalog_snapshot"], catalog_payload)
    _write_json(output_dir / paths["policy_snapshot"], policy_payload)
    _write_json(output_dir / paths["approved_registry"], evaluation.registry)
    _write_json(
        output_dir / paths["external_references"],
        evaluation.external_references,
    )
    decision_paths: dict[str, str] = {}
    for decision in evaluation.decisions:
        portable = f"decisions/{decision.plugin_id}.json"
        decision_paths[decision.plugin_id] = portable
        paths[f"decision.{decision.plugin_id}"] = portable
        _write_json(output_dir / portable, decision.model_dump(mode="json"))

    hashes = {name: _sha256(output_dir / path) for name, path in sorted(paths.items())}
    counts = _counts(evaluation.decisions)
    manifest_without_fingerprint: dict[str, object] = {
        "schema_version": 1,
        "state": "GOVERNED",
        "catalog_fingerprint": _canonical_fingerprint(catalog_payload),
        "policy_fingerprint": _canonical_fingerprint(policy_payload),
        "counts": counts.model_dump(mode="json"),
        "decisions": dict(sorted(decision_paths.items())),
        "files": dict(sorted(paths.items())),
        "sha256": dict(sorted(hashes.items())),
        "artifact_inventory": [],
        "fingerprint_algorithm": "sha256-canonical-json-v1",
    }
    fingerprint = _canonical_fingerprint(manifest_without_fingerprint)
    manifest = _parse(
        GovernanceManifest,
        {**manifest_without_fingerprint, "governance_fingerprint": fingerprint},
        label="governance manifest",
    )
    manifest_path = output_dir / "governance_manifest.json"
    _write_json(manifest_path, manifest.model_dump(mode="json"))
    return GovernanceResult(
        output_dir=output_dir,
        manifest_path=manifest_path,
        governance_fingerprint=fingerprint,
        approved=counts.approved,
        rejected=counts.rejected,
        quarantined=counts.quarantined,
    )


def verify_governance_bundle(bundle_dir: Path) -> GovernanceVerificationResult:
    """Verify governed declarations, decisions, hashes, and semantics without network access."""

    manifest = _parse(
        GovernanceManifest,
        _read_json(bundle_dir / "governance_manifest.json", label="governance manifest"),
        label="governance manifest",
    )
    expected_fingerprint = _canonical_fingerprint(_manifest_payload(manifest))
    if manifest.governance_fingerprint != expected_fingerprint:
        raise PluginGovernanceError("Governance manifest fingerprint is invalid")

    expected_paths = {
        "catalog_snapshot": "catalog_snapshot.json",
        "policy_snapshot": "policy_snapshot.json",
        "approved_registry": "approved_registry.json",
        "external_references": "external_references.json",
        **{
            f"decision.{plugin_id}": f"decisions/{plugin_id}.json"
            for plugin_id in sorted(manifest.decisions)
        },
    }
    if manifest.files != expected_paths or manifest.decisions != {
        plugin_id: f"decisions/{plugin_id}.json" for plugin_id in sorted(manifest.decisions)
    }:
        raise PluginGovernanceError("Governance file layout is invalid")

    resolved: dict[str, Path] = {}
    for name, portable in sorted(manifest.files.items()):
        path = _resolve(bundle_dir, portable, label=name)
        if path.suffix.lower() != ".json":
            raise PluginGovernanceError("Governance bundle contains a non-declaration file")
        if _sha256(path) != manifest.sha256[name]:
            raise PluginGovernanceError(f"Governed file SHA-256 is invalid: {name}")
        resolved[name] = path

    catalog = _parse(
        PluginCatalog,
        _read_json(resolved["catalog_snapshot"], label="catalog snapshot"),
        label="catalog snapshot",
    )
    policy = _parse(
        GovernancePolicy,
        _read_json(resolved["policy_snapshot"], label="policy snapshot"),
        label="policy snapshot",
    )
    catalog_payload = _catalog_payload(catalog)
    policy_payload = policy.model_dump(mode="json")
    if manifest.catalog_fingerprint != _canonical_fingerprint(catalog_payload):
        raise PluginGovernanceError("Catalog fingerprint is invalid")
    if manifest.policy_fingerprint != _canonical_fingerprint(policy_payload):
        raise PluginGovernanceError("Governance policy fingerprint is invalid")

    expected = _evaluate(catalog, policy)
    if _read_json(resolved["approved_registry"], label="approved registry") != expected.registry:
        raise PluginGovernanceError("Approved registry does not match governed decisions")
    if (
        _read_json(resolved["external_references"], label="external references")
        != expected.external_references
    ):
        raise PluginGovernanceError("External references do not match quarantine decisions")

    expected_decisions = {decision.plugin_id: decision for decision in expected.decisions}
    if set(expected_decisions) != set(manifest.decisions):
        raise PluginGovernanceError("Governance decision set does not match the catalog")
    for plugin_id, expected_decision in expected_decisions.items():
        actual = _parse(
            GovernanceDecision,
            _read_json(resolved[f"decision.{plugin_id}"], label="governance decision"),
            label="governance decision",
        )
        if actual != expected_decision:
            raise PluginGovernanceError(f"Governance decision is not reproducible: {plugin_id}")

    counts = _counts(expected.decisions)
    if manifest.counts != counts:
        raise PluginGovernanceError("Governance counts do not match evaluated decisions")
    return GovernanceVerificationResult(
        valid=True,
        governance_fingerprint=manifest.governance_fingerprint,
        approved=counts.approved,
        rejected=counts.rejected,
        quarantined=counts.quarantined,
    )

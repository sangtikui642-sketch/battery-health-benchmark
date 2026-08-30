import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when
from typer.testing import CliRunner

from battery_health.agent.planning import PlanningError, create_experiment_plan
from battery_health.cli import app
from tests.bdd.test_agent_planning import _write_evidence, _write_policy

scenarios("../../features/model_plugin_governance.feature")


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _plugin(plugin_id: str = "linear_regression") -> dict[str, object]:
    return {
        "plugin_id": plugin_id,
        "implementation_version": "0.1.0",
        "supported_target": "soh",
        "required_features": [{"name": "cycle_index", "unit": "dimensionless cycle index"}],
        "preprocessing_policy": "StandardScaler fitted on train partition only",
        "capabilities": {"training": True, "prediction": True},
        "determinism": {
            "supported": True,
            "seed_parameter": "random_seed",
            "same_seed_reproducible": True,
        },
        "requirements": {
            "dependencies": [
                {"name": "scikit-learn", "version_specifier": ">=1.4", "required": True}
            ],
            "accelerator": "cpu",
            "minimum_memory_mb": 128,
        },
        "licenses": {
            "code": {
                "identifier": "BSD-3-Clause",
                "status": "known",
                "redistribution_approved": True,
            },
            "weights": {
                "identifier": "not_applicable",
                "status": "not_applicable",
                "redistribution_approved": True,
            },
        },
        "upstream": {
            "source": "synthetic:plugin-governance-fixture",
            "repository_url": "https://example.invalid/battery-plugin",
            "citation": "Synthetic plugin-governance acceptance fixture",
        },
        "search_space": {"strategy": "grid", "max_trials": 1, "parameters": {}},
        "serialization": {
            "format": "none",
            "artifact_extension": None,
            "save_supported": False,
            "load_supported": False,
        },
        "execution": {
            "adapter": "builtin:battery_health.modeling.baselines",
            "default_enabled": True,
        },
    }


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "target": "soh",
        "feature_contract": [{"name": "cycle_index", "unit": "dimensionless cycle index"}],
        "require_training": True,
        "require_prediction": True,
        "require_determinism": True,
        "allowed_dependencies": ["scikit-learn"],
        "allowed_accelerators": ["cpu"],
        "max_memory_mb": 1024,
        "allowed_code_licenses": ["BSD-3-Clause"],
        "allowed_weight_licenses": ["not_applicable"],
        "approved_execution_adapters": ["builtin:battery_health.modeling.baselines"],
        "max_search_trials": 8,
        "serialization_required": False,
        "allowed_serialization_formats": ["none", "joblib"],
    }


def _context(tmp_path: Path, plugins: list[dict[str, object]]) -> dict[str, Any]:
    return {
        "catalog_path": _write_json(
            tmp_path / "plugin-catalog.json", {"schema_version": 1, "plugins": plugins}
        ),
        "governance_policy_path": _write_json(tmp_path / "governance-policy.json", _policy()),
        "output_dir": tmp_path / "governed-plugins",
        "error": None,
    }


def _govern(context: dict[str, Any], output_dir: Path | None = None) -> object:
    module = importlib.import_module("battery_health.agent.governance")
    return module.govern_model_plugins(
        catalog_path=context["catalog_path"],
        policy_path=context["governance_policy_path"],
        output_dir=output_dir or context["output_dir"],
    )


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _decision(context: dict[str, Any], plugin_id: str) -> dict[str, Any]:
    return _read(context["output_dir"] / "decisions" / f"{plugin_id}.json")


@given(
    "a complete model plugin declaration compatible with the governance policy",
    target_fixture="governance_context",
)
def compatible_plugin(tmp_path: Path) -> dict[str, Any]:
    return _context(tmp_path, [_plugin()])


@given(
    "a complete model plugin declaration with an incompatible target",
    target_fixture="governance_context",
)
def incompatible_plugin(tmp_path: Path) -> dict[str, Any]:
    plugin = _plugin("incompatible_target_model")
    plugin["supported_target"] = "rul"
    context = _context(tmp_path, [plugin])
    context["evidence_dir"] = _write_evidence(tmp_path / "planning-evidence")
    context["planning_policy_path"] = _write_policy(tmp_path / "planning-policy.json")
    context["plan_dir"] = tmp_path / "forbidden-plan"
    return context


@given(
    "a complete model plugin declaration omits code and weight licenses",
    target_fixture="governance_context",
)
def unknown_license_plugin(tmp_path: Path) -> dict[str, Any]:
    plugin = _plugin("external_research_model")
    plugin["licenses"] = {}
    plugin["execution"] = {
        "adapter": "python:external_research_model",
        "default_enabled": False,
    }
    return _context(tmp_path, [plugin])


@given(
    "a model plugin declaration omits its serialization and loading contract",
    target_fixture="governance_context",
)
def incomplete_plugin(tmp_path: Path) -> dict[str, Any]:
    plugin = _plugin()
    del plugin["serialization"]
    return _context(tmp_path, [plugin])


@given(
    "a governed plugin bundle with one altered decision",
    target_fixture="tamper_context",
)
def altered_governance_decision(tmp_path: Path) -> dict[str, Any]:
    context = _context(tmp_path, [_plugin()])
    try:
        _govern(context)
        decision_path = context["output_dir"] / "decisions" / "linear_regression.json"
        decision_path.write_bytes(decision_path.read_bytes() + b"\n")
    except Exception as error:
        context["error"] = error
    return context


@given(
    "one compatible plugin catalog used for two governance outputs",
    target_fixture="determinism_context",
)
def deterministic_catalog(tmp_path: Path) -> dict[str, Any]:
    context = _context(tmp_path, [_plugin()])
    context["first_output"] = tmp_path / "governance-a"
    context["second_output"] = tmp_path / "governance-b"
    context["evidence_dir"] = _write_evidence(tmp_path / "planning-evidence")
    context["planning_policy_path"] = _write_policy(tmp_path / "planning-policy.json")
    context["plan_dir"] = tmp_path / "approved-plan"
    return context


@given(
    "a catalog containing approved incompatible and unknown-license plugins",
    target_fixture="cli_context",
)
def mixed_catalog(tmp_path: Path) -> dict[str, Any]:
    approved = _plugin()
    incompatible = _plugin("incompatible_target_model")
    incompatible["supported_target"] = "rul"
    unknown = _plugin("external_research_model")
    unknown["licenses"] = {}
    unknown["execution"] = {
        "adapter": "python:external_research_model",
        "default_enabled": False,
    }
    return _context(tmp_path, [approved, incompatible, unknown])


@when("the agent validates the plugin manifest")
def validate_plugin_manifest(governance_context: dict[str, Any]) -> None:
    try:
        governance_context["result"] = _govern(governance_context)
    except Exception as error:
        governance_context["error"] = error


@when("the researcher verifies plugin governance through the API and CLI")
def verify_governance_tamper(tamper_context: dict[str, Any]) -> None:
    if tamper_context["error"] is not None:
        return
    module = importlib.import_module("battery_health.agent.governance")
    try:
        module.verify_governance_bundle(tamper_context["output_dir"])
    except Exception as error:
        tamper_context["api_error"] = error
    tamper_context["cli_result"] = CliRunner().invoke(
        app,
        ["agent", "verify-plugins", "--bundle", str(tamper_context["output_dir"])],
    )


@when("the agent creates both governed plugin bundles")
def create_two_governance_bundles(determinism_context: dict[str, Any]) -> None:
    try:
        determinism_context["first_result"] = _govern(
            determinism_context, determinism_context["first_output"]
        )
        determinism_context["second_result"] = _govern(
            determinism_context, determinism_context["second_output"]
        )
    except Exception as error:
        determinism_context["error"] = error


@when("the researcher runs the plugin governance and verification commands")
def run_governance_cli(cli_context: dict[str, Any]) -> None:
    runner = CliRunner()
    cli_context["govern_result"] = runner.invoke(
        app,
        [
            "agent",
            "govern-plugins",
            "--catalog",
            str(cli_context["catalog_path"]),
            "--policy",
            str(cli_context["governance_policy_path"]),
            "--output",
            str(cli_context["output_dir"]),
        ],
    )
    if cli_context["govern_result"].exit_code == 0:
        cli_context["verify_result"] = runner.invoke(
            app,
            ["agent", "verify-plugins", "--bundle", str(cli_context["output_dir"])],
        )


@then("the governance decision approves the plugin for compatible plans")
def approved_for_plans(governance_context: dict[str, Any]) -> None:
    assert governance_context["error"] is None
    decision = _decision(governance_context, "linear_regression")
    registry = _read(governance_context["output_dir"] / "approved_registry.json")
    assert decision["status"] == "APPROVED"
    assert decision["permissions"] == {
        "code_bundling_allowed": True,
        "default_execution_allowed": True,
        "execution_allowed": True,
        "external_reference_only": False,
        "redistribution_approved": True,
        "weight_bundling_allowed": True,
    }
    assert [item["plugin_id"] for item in registry["plugins"]] == ["linear_regression"]


@then("every required declaration and its fingerprints are retained")
def all_declarations_retained(governance_context: dict[str, Any]) -> None:
    catalog = _read(governance_context["output_dir"] / "catalog_snapshot.json")
    plugin = catalog["plugins"][0]
    assert set(plugin) == {
        "capabilities",
        "determinism",
        "execution",
        "implementation_version",
        "licenses",
        "plugin_id",
        "preprocessing_policy",
        "required_features",
        "requirements",
        "search_space",
        "serialization",
        "supported_target",
        "upstream",
    }
    decision = _decision(governance_context, "linear_regression")
    assert len(decision["declaration_fingerprint"]) == 64
    assert len(decision["executable_manifest_fingerprint"]) == 64
    manifest = _read(governance_context["output_dir"] / "governance_manifest.json")
    assert len(manifest["governance_fingerprint"]) == 64
    assert manifest["artifact_inventory"] == []


@then("the governance decision rejects the incompatible plugin")
def incompatible_is_rejected(governance_context: dict[str, Any]) -> None:
    assert governance_context["error"] is None
    decision = _decision(governance_context, "incompatible_target_model")
    registry = _read(governance_context["output_dir"] / "approved_registry.json")
    assert decision["status"] == "REJECTED"
    assert "target_incompatible" in decision["reason_codes"]
    assert decision["permissions"]["execution_allowed"] is False
    assert registry["plugins"] == []


@then("no executable plan or training artifact is created")
def no_plan_or_training(governance_context: dict[str, Any]) -> None:
    with pytest.raises(PlanningError):
        create_experiment_plan(
            evidence_dir=governance_context["evidence_dir"],
            registry_path=governance_context["output_dir"] / "approved_registry.json",
            policy_path=governance_context["planning_policy_path"],
            output_dir=governance_context["plan_dir"],
        )
    assert not governance_context["plan_dir"].exists()
    assert not (governance_context["output_dir"] / "models").exists()
    assert not (governance_context["output_dir"] / "candidates").exists()


@then("the plugin is excluded from executable plans")
def quarantined_is_not_executable(governance_context: dict[str, Any]) -> None:
    assert governance_context["error"] is None
    decision = _decision(governance_context, "external_research_model")
    registry = _read(governance_context["output_dir"] / "approved_registry.json")
    assert decision["status"] == "QUARANTINED"
    assert "license_status_unknown" in decision["reason_codes"]
    assert decision["permissions"]["execution_allowed"] is False
    assert decision["permissions"]["default_execution_allowed"] is False
    assert registry["plugins"] == []


@then("it may only be listed as an external research reference")
def external_reference_only(governance_context: dict[str, Any]) -> None:
    references = _read(governance_context["output_dir"] / "external_references.json")
    decision = _decision(governance_context, "external_research_model")
    assert [item["plugin_id"] for item in references["plugins"]] == ["external_research_model"]
    assert set(references["plugins"][0]) == {
        "implementation_version",
        "license_status",
        "plugin_id",
        "reason_codes",
        "upstream",
    }
    assert decision["permissions"]["external_reference_only"] is True
    assert decision["permissions"]["redistribution_approved"] is False


@then("no external code or weight is bundled")
def no_external_artifacts(governance_context: dict[str, Any]) -> None:
    manifest = _read(governance_context["output_dir"] / "governance_manifest.json")
    assert manifest["artifact_inventory"] == []
    files = [path for path in governance_context["output_dir"].rglob("*") if path.is_file()]
    assert files
    assert all(path.suffix == ".json" for path in files)
    assert not any(path.suffix in {".bin", ".joblib", ".pkl", ".pt", ".py"} for path in files)


@then("manifest validation fails before governance output exists")
def incomplete_fails_before_output(governance_context: dict[str, Any]) -> None:
    error = governance_context["error"]
    assert error is not None
    assert error.__class__.__name__ == "PluginGovernanceError"
    assert "serialization" in str(error).lower()
    assert not governance_context["output_dir"].exists()


@then("both governance verification paths reject the bundle")
def tamper_is_rejected(tamper_context: dict[str, Any]) -> None:
    assert tamper_context["error"] is None
    assert tamper_context.get("api_error") is not None
    assert "sha-256" in str(tamper_context["api_error"]).lower()
    result = tamper_context["cli_result"]
    assert result.exit_code != 0
    assert "verification failed" in result.output.lower()


@then("both governance fingerprints are identical without timestamps")
def governance_is_deterministic(determinism_context: dict[str, Any]) -> None:
    assert determinism_context["error"] is None
    first = _read(determinism_context["first_output"] / "governance_manifest.json")
    second = _read(determinism_context["second_output"] / "governance_manifest.json")
    assert first == second
    assert first["governance_fingerprint"] == second["governance_fingerprint"]
    assert "timestamp" not in json.dumps(first).lower()


@then("the approved registry creates a compatible immutable plan")
def approved_registry_plans(determinism_context: dict[str, Any]) -> None:
    result = create_experiment_plan(
        evidence_dir=determinism_context["evidence_dir"],
        registry_path=determinism_context["first_output"] / "approved_registry.json",
        policy_path=determinism_context["planning_policy_path"],
        output_dir=determinism_context["plan_dir"],
    )
    plan = _read(result.plan_path)
    decision = _read(determinism_context["first_output"] / "decisions" / "linear_regression.json")
    assert plan["candidates"][0]["plugin_id"] == "linear_regression"
    assert (
        plan["candidates"][0]["manifest_fingerprint"] == decision["executable_manifest_fingerprint"]
    )


@then("the CLI reports every governance outcome and a valid offline bundle")
def cli_reports_and_verifies(cli_context: dict[str, Any]) -> None:
    govern_result = cli_context["govern_result"]
    assert govern_result.exit_code == 0, govern_result.output
    assert "approved=1" in govern_result.output
    assert "rejected=1" in govern_result.output
    assert "quarantined=1" in govern_result.output
    verify_result = cli_context["verify_result"]
    assert verify_result.exit_code == 0, verify_result.output
    assert "valid=true" in verify_result.output
    manifest = _read(cli_context["output_dir"] / "governance_manifest.json")
    assert manifest["counts"] == {"approved": 1, "quarantined": 1, "rejected": 1}

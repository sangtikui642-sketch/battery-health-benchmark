import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from battery_health.agent.governance import (
    PluginGovernanceError,
    govern_model_plugins,
    verify_governance_bundle,
)
from battery_health.cli import app
from tests.bdd.test_model_plugin_governance import _context, _plugin, _read, _write_json


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rehash_and_refingerprint(bundle: Path, role: str) -> None:
    manifest_path = bundle / "governance_manifest.json"
    manifest = _read(manifest_path)
    protected = bundle / manifest["files"][role]
    manifest["sha256"][role] = hashlib.sha256(protected.read_bytes()).hexdigest()
    payload = {key: value for key, value in manifest.items() if key != "governance_fingerprint"}
    manifest["governance_fingerprint"] = _fingerprint(payload)
    _write_json(manifest_path, manifest)


def test_governance_output_is_non_overwriting(tmp_path: Path) -> None:
    context = _context(tmp_path, [_plugin()])
    context["output_dir"].mkdir()
    marker = context["output_dir"] / "user.json"
    marker.write_text('{"preserve": true}\n', encoding="utf-8")

    with pytest.raises(PluginGovernanceError, match="already exists"):
        govern_model_plugins(
            catalog_path=context["catalog_path"],
            policy_path=context["governance_policy_path"],
            output_dir=context["output_dir"],
        )

    assert marker.read_text(encoding="utf-8") == '{"preserve": true}\n'


def test_duplicate_plugin_identity_fails_before_output(tmp_path: Path) -> None:
    context = _context(tmp_path, [_plugin(), _plugin()])

    with pytest.raises(PluginGovernanceError, match="identifiers must be unique"):
        govern_model_plugins(
            catalog_path=context["catalog_path"],
            policy_path=context["governance_policy_path"],
            output_dir=context["output_dir"],
        )

    assert not context["output_dir"].exists()


def test_plugin_identity_cannot_inject_an_artifact_path(tmp_path: Path) -> None:
    plugin = _plugin()
    plugin["plugin_id"] = "../external-model"
    context = _context(tmp_path, [plugin])

    with pytest.raises(PluginGovernanceError, match="plugin_id"):
        govern_model_plugins(
            catalog_path=context["catalog_path"],
            policy_path=context["governance_policy_path"],
            output_dir=context["output_dir"],
        )

    assert not context["output_dir"].exists()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("training", "training_capability_missing"),
        ("prediction", "prediction_capability_missing"),
        ("determinism", "determinism_contract_incompatible"),
        ("dependency", "dependency_not_allowed"),
        ("accelerator", "accelerator_not_allowed"),
        ("memory", "memory_requirement_exceeds_policy"),
        ("search", "search_space_exceeds_policy"),
        ("adapter", "execution_adapter_not_approved"),
    ],
)
def test_incompatible_contract_is_rejected_with_a_reason(
    tmp_path: Path, mutation: str, reason: str
) -> None:
    plugin = _plugin()
    if mutation == "training":
        plugin["capabilities"] = {"training": False, "prediction": True}
    elif mutation == "prediction":
        plugin["capabilities"] = {"training": True, "prediction": False}
    elif mutation == "determinism":
        plugin["determinism"] = {
            "supported": False,
            "seed_parameter": None,
            "same_seed_reproducible": False,
        }
    elif mutation == "dependency":
        plugin["requirements"] = {
            "dependencies": [{"name": "torch", "version_specifier": ">=2", "required": True}],
            "accelerator": "cpu",
            "minimum_memory_mb": 128,
        }
    elif mutation == "accelerator":
        plugin["requirements"] = {
            "dependencies": [],
            "accelerator": "cuda",
            "minimum_memory_mb": 128,
        }
    elif mutation == "memory":
        plugin["requirements"] = {
            "dependencies": [],
            "accelerator": "cpu",
            "minimum_memory_mb": 2048,
        }
    elif mutation == "search":
        plugin["search_space"] = {"strategy": "random", "max_trials": 9, "parameters": {}}
    else:
        plugin["execution"] = {
            "adapter": "python:unreviewed_adapter",
            "default_enabled": True,
        }
    context = _context(tmp_path, [plugin])

    govern_model_plugins(
        catalog_path=context["catalog_path"],
        policy_path=context["governance_policy_path"],
        output_dir=context["output_dir"],
    )

    decision = _read(context["output_dir"] / "decisions" / "linear_regression.json")
    registry = _read(context["output_dir"] / "approved_registry.json")
    assert decision["status"] == "REJECTED"
    assert reason in decision["reason_codes"]
    assert registry["plugins"] == []


def test_known_but_disallowed_license_is_quarantined(tmp_path: Path) -> None:
    plugin = _plugin("copyleft_research_model")
    licenses = dict(plugin["licenses"])
    licenses["code"] = {
        "identifier": "GPL-3.0-only",
        "status": "known",
        "redistribution_approved": True,
    }
    plugin["licenses"] = licenses
    context = _context(tmp_path, [plugin])

    govern_model_plugins(
        catalog_path=context["catalog_path"],
        policy_path=context["governance_policy_path"],
        output_dir=context["output_dir"],
    )

    decision = _read(context["output_dir"] / "decisions" / "copyleft_research_model.json")
    references = _read(context["output_dir"] / "external_references.json")
    assert decision["status"] == "QUARANTINED"
    assert "license_not_approved" in decision["reason_codes"]
    assert references["plugins"][0]["plugin_id"] == "copyleft_research_model"


def test_required_serialization_rejects_an_explicit_none_contract(tmp_path: Path) -> None:
    context = _context(tmp_path, [_plugin()])
    policy = _read(context["governance_policy_path"])
    policy["serialization_required"] = True
    _write_json(context["governance_policy_path"], policy)

    govern_model_plugins(
        catalog_path=context["catalog_path"],
        policy_path=context["governance_policy_path"],
        output_dir=context["output_dir"],
    )

    decision = _read(context["output_dir"] / "decisions" / "linear_regression.json")
    assert decision["status"] == "REJECTED"
    assert "serialization_contract_incompatible" in decision["reason_codes"]


def test_bounded_numeric_search_domain_is_retained_in_approved_registry(tmp_path: Path) -> None:
    plugin = _plugin()
    plugin["search_space"] = {
        "strategy": "grid",
        "max_trials": 3,
        "parameters": {
            "alpha": {
                "kind": "float",
                "minimum": 0.0,
                "maximum": 1.0,
                "choices": [],
            }
        },
    }
    context = _context(tmp_path, [plugin])

    govern_model_plugins(
        catalog_path=context["catalog_path"],
        policy_path=context["governance_policy_path"],
        output_dir=context["output_dir"],
    )

    registry = _read(context["output_dir"] / "approved_registry.json")
    assert registry["plugins"][0]["bounded_parameters"]["alpha"] == {
        "choices": [],
        "kind": "float",
        "maximum": 1.0,
        "minimum": 0.0,
    }


def test_unbounded_numeric_search_domain_fails_before_output(tmp_path: Path) -> None:
    plugin = _plugin()
    plugin["search_space"] = {
        "strategy": "grid",
        "max_trials": 1,
        "parameters": {
            "alpha": {
                "kind": "float",
                "minimum": None,
                "maximum": 1.0,
                "choices": [],
            }
        },
    }
    context = _context(tmp_path, [plugin])

    with pytest.raises(PluginGovernanceError, match="numeric domains"):
        govern_model_plugins(
            catalog_path=context["catalog_path"],
            policy_path=context["governance_policy_path"],
            output_dir=context["output_dir"],
        )

    assert not context["output_dir"].exists()


def test_deterministic_plugin_requires_a_seed_parameter(tmp_path: Path) -> None:
    plugin = _plugin()
    plugin["determinism"] = {
        "supported": True,
        "seed_parameter": None,
        "same_seed_reproducible": True,
    }
    context = _context(tmp_path, [plugin])

    with pytest.raises(PluginGovernanceError, match="seed parameter"):
        govern_model_plugins(
            catalog_path=context["catalog_path"],
            policy_path=context["governance_policy_path"],
            output_dir=context["output_dir"],
        )


def test_unknown_license_cannot_claim_redistribution_approval(tmp_path: Path) -> None:
    plugin = _plugin()
    plugin["licenses"] = {
        "code": {
            "identifier": None,
            "status": "unknown",
            "redistribution_approved": True,
        },
        "weights": None,
    }
    context = _context(tmp_path, [plugin])

    with pytest.raises(PluginGovernanceError, match="cannot approve redistribution"):
        govern_model_plugins(
            catalog_path=context["catalog_path"],
            policy_path=context["governance_policy_path"],
            output_dir=context["output_dir"],
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("same_seed_without_support", "same-seed reproducibility"),
        ("duplicate_dependency", "dependency names must be unique"),
        ("known_license_without_identifier", "requires an identifier"),
        ("empty_categorical_domain", "categorical domains require"),
        ("duplicate_categorical_choice", "categorical choices must be unique"),
        ("reversed_numeric_domain", "minimum must not exceed maximum"),
        ("fractional_integer_domain", "integer parameter bounds must be integral"),
        ("empty_parameter_name", "parameter names must not be empty"),
        ("none_format_with_save", "none serialization cannot declare"),
        ("serialized_format_without_extension", "require an artifact extension"),
        ("duplicate_feature", "feature names must be unique"),
    ],
)
def test_invalid_plugin_contract_fails_before_output(
    tmp_path: Path, mutation: str, message: str
) -> None:
    plugin = _plugin()
    if mutation == "same_seed_without_support":
        plugin["determinism"] = {
            "supported": False,
            "seed_parameter": None,
            "same_seed_reproducible": True,
        }
    elif mutation == "duplicate_dependency":
        dependency = {"name": "numpy", "version_specifier": ">=1.26", "required": True}
        plugin["requirements"] = {
            "dependencies": [dependency, dependency],
            "accelerator": "cpu",
            "minimum_memory_mb": 128,
        }
    elif mutation == "known_license_without_identifier":
        plugin["licenses"] = {
            "code": {
                "identifier": None,
                "status": "known",
                "redistribution_approved": True,
            },
            "weights": {
                "identifier": "not_applicable",
                "status": "not_applicable",
                "redistribution_approved": True,
            },
        }
    elif mutation == "empty_categorical_domain":
        plugin["search_space"] = {
            "strategy": "grid",
            "max_trials": 1,
            "parameters": {
                "solver": {
                    "kind": "categorical",
                    "minimum": None,
                    "maximum": None,
                    "choices": [],
                }
            },
        }
    elif mutation == "duplicate_categorical_choice":
        plugin["search_space"] = {
            "strategy": "grid",
            "max_trials": 2,
            "parameters": {
                "solver": {
                    "kind": "categorical",
                    "minimum": None,
                    "maximum": None,
                    "choices": ["svd", "svd"],
                }
            },
        }
    elif mutation == "reversed_numeric_domain":
        plugin["search_space"] = {
            "strategy": "grid",
            "max_trials": 1,
            "parameters": {
                "alpha": {
                    "kind": "float",
                    "minimum": 2.0,
                    "maximum": 1.0,
                    "choices": [],
                }
            },
        }
    elif mutation == "fractional_integer_domain":
        plugin["search_space"] = {
            "strategy": "grid",
            "max_trials": 1,
            "parameters": {
                "depth": {
                    "kind": "integer",
                    "minimum": 1.5,
                    "maximum": 3.0,
                    "choices": [],
                }
            },
        }
    elif mutation == "empty_parameter_name":
        plugin["search_space"] = {
            "strategy": "grid",
            "max_trials": 1,
            "parameters": {
                " ": {
                    "kind": "float",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "choices": [],
                }
            },
        }
    elif mutation == "none_format_with_save":
        plugin["serialization"] = {
            "format": "none",
            "artifact_extension": None,
            "save_supported": True,
            "load_supported": False,
        }
    elif mutation == "serialized_format_without_extension":
        plugin["serialization"] = {
            "format": "joblib",
            "artifact_extension": None,
            "save_supported": True,
            "load_supported": True,
        }
    else:
        feature = {"name": "cycle_index", "unit": "dimensionless cycle index"}
        plugin["required_features"] = [feature, feature]
    context = _context(tmp_path, [plugin])

    with pytest.raises(PluginGovernanceError, match=message):
        govern_model_plugins(
            catalog_path=context["catalog_path"],
            policy_path=context["governance_policy_path"],
            output_dir=context["output_dir"],
        )

    assert not context["output_dir"].exists()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_feature", "policy feature names must be unique"),
        ("duplicate_allow_value", "allow lists must contain unique values"),
        ("empty_allow_value", "allow-list values must not be empty"),
    ],
)
def test_ambiguous_governance_policy_fails_before_output(
    tmp_path: Path, mutation: str, message: str
) -> None:
    context = _context(tmp_path, [_plugin()])
    policy = _read(context["governance_policy_path"])
    if mutation == "duplicate_feature":
        policy["feature_contract"] = policy["feature_contract"] * 2
    elif mutation == "duplicate_allow_value":
        policy["allowed_accelerators"] = ["cpu", "cpu"]
    else:
        policy["allowed_serialization_formats"] = ["none", " "]
    _write_json(context["governance_policy_path"], policy)

    with pytest.raises(PluginGovernanceError, match=message):
        govern_model_plugins(
            catalog_path=context["catalog_path"],
            policy_path=context["governance_policy_path"],
            output_dir=context["output_dir"],
        )

    assert not context["output_dir"].exists()


def test_refingerprinted_false_decision_is_rejected_semantically(tmp_path: Path) -> None:
    context = _context(tmp_path, [_plugin()])
    govern_model_plugins(
        catalog_path=context["catalog_path"],
        policy_path=context["governance_policy_path"],
        output_dir=context["output_dir"],
    )
    decision_path = context["output_dir"] / "decisions" / "linear_regression.json"
    decision = _read(decision_path)
    decision["reason_codes"] = ["invented_approval_reason"]
    _write_json(decision_path, decision)
    _rehash_and_refingerprint(context["output_dir"], "decision.linear_regression")

    with pytest.raises(PluginGovernanceError, match="not reproducible"):
        verify_governance_bundle(context["output_dir"])


def test_refingerprinted_registry_change_is_rejected_semantically(tmp_path: Path) -> None:
    context = _context(tmp_path, [_plugin()])
    govern_model_plugins(
        catalog_path=context["catalog_path"],
        policy_path=context["governance_policy_path"],
        output_dir=context["output_dir"],
    )
    registry_path = context["output_dir"] / "approved_registry.json"
    registry = _read(registry_path)
    registry["plugins"][0]["supported_target"] = "rul"
    _write_json(registry_path, registry)
    _rehash_and_refingerprint(context["output_dir"], "approved_registry")

    with pytest.raises(PluginGovernanceError, match="does not match governed decisions"):
        verify_governance_bundle(context["output_dir"])


def test_unknown_manifest_field_is_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path, [_plugin()])
    govern_model_plugins(
        catalog_path=context["catalog_path"],
        policy_path=context["governance_policy_path"],
        output_dir=context["output_dir"],
    )
    manifest_path = context["output_dir"] / "governance_manifest.json"
    manifest = _read(manifest_path)
    manifest["unreviewed_extension"] = True
    _write_json(manifest_path, manifest)

    with pytest.raises(PluginGovernanceError, match="unreviewed_extension"):
        verify_governance_bundle(context["output_dir"])


def test_missing_protected_file_is_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path, [_plugin()])
    govern_model_plugins(
        catalog_path=context["catalog_path"],
        policy_path=context["governance_policy_path"],
        output_dir=context["output_dir"],
    )
    (context["output_dir"] / "external_references.json").unlink()

    with pytest.raises(PluginGovernanceError, match="missing"):
        verify_governance_bundle(context["output_dir"])


def test_cli_rejects_an_incomplete_catalog_without_output(tmp_path: Path) -> None:
    plugin = _plugin()
    del plugin["upstream"]
    context = _context(tmp_path, [plugin])

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "govern-plugins",
            "--catalog",
            str(context["catalog_path"]),
            "--policy",
            str(context["governance_policy_path"]),
            "--output",
            str(context["output_dir"]),
        ],
    )

    assert result.exit_code != 0
    assert "plugin governance failed" in result.output.lower()
    assert "upstream" in result.output.lower()
    assert not context["output_dir"].exists()


def test_repository_example_is_safe_by_default(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]

    result = govern_model_plugins(
        catalog_path=repository / "configs" / "plugin_catalog.quarantine-example.json",
        policy_path=repository / "configs" / "plugin_governance_policy.example.json",
        output_dir=tmp_path / "example-governance",
    )

    registry = _read(result.output_dir / "approved_registry.json")
    references = _read(result.output_dir / "external_references.json")
    assert result.approved == 0
    assert result.quarantined == 1
    assert registry["plugins"] == []
    assert references["plugins"][0]["plugin_id"] == "external_research_model"
    assert verify_governance_bundle(result.output_dir).valid is True

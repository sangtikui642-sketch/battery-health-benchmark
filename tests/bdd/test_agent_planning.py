import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
from pytest_bdd import given, scenarios, then, when
from typer.testing import CliRunner

from battery_health.cli import app

scenarios("../../features/agent_planning.feature")

MODELS = (
    "linear_regression",
    "random_forest",
    "histogram_gradient_boosting",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_evidence(root: Path) -> Path:
    root.mkdir(parents=True)
    labelled_path = root / "cycles_with_soh.csv"
    pd.DataFrame(
        [
            {
                "cell_id": cell_id,
                "cycle_index": cycle_index,
                "capacity_ah": 2.0 * (1.0 - 0.01 * cycle_index),
                "soh": 1.0 - 0.01 * cycle_index,
            }
            for cell_id in ("cell-a", "cell-b", "cell-c", "cell-d")
            for cycle_index in (1, 2)
        ]
    ).to_csv(labelled_path, index=False)
    split_path = root / "split_manifest.json"
    split_path.write_text(
        json.dumps(
            {
                "strategy": "group_by_cell",
                "random_seed": 2026,
                "train_cells": ["cell-a", "cell-b"],
                "validation_cells": ["cell-c"],
                "test_cells": ["cell-d"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    config_path = root / "effective_config.json"
    config_path.write_text(
        json.dumps(
            {
                "target": "soh",
                "feature_columns": ["cycle_index"],
                "feature_units": {"cycle_index": "dimensionless cycle index"},
                "model_random_seed": 73,
                "split": {"random_seed": 2026},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    metadata_path = root / "soh_metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "target_name": "soh",
                "target_definition": "soh = capacity_ah / reference_capacity_ah",
                "capacity_unit": "Ah",
                "reference_capacity_source": "configuration:dataset.reference_capacity_ah",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    quality_path = root / "quality_report.json"
    quality_path.write_text('{"status": "passed"}\n', encoding="utf-8")
    lock_path = root / "uv.lock"
    lock_path.write_text('version = 1\nrequires-python = ">=3.12"\n', encoding="utf-8")
    manifest_path = root / "reproducibility_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "random_seed": 73,
                "deterministic_tolerance": 1e-12,
                "files": {
                    "soh_labels": labelled_path.name,
                    "split_manifest": split_path.name,
                    "effective_config": config_path.name,
                    "dependency_lock": lock_path.name,
                    "soh_metadata": metadata_path.name,
                    "quality_report": quality_path.name,
                },
                "sha256": {
                    "soh_labels": _sha256(labelled_path),
                    "split_manifest": _sha256(split_path),
                    "effective_config": _sha256(config_path),
                    "dependency_lock": _sha256(lock_path),
                    "soh_metadata": _sha256(metadata_path),
                    "quality_report": _sha256(quality_path),
                },
                "git": {"status": "unavailable", "revision": None},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def _plugin(model_name: str, *, required_features: list[str] | None = None) -> dict[str, object]:
    return {
        "plugin_id": model_name,
        "implementation_version": "0.1.0",
        "supported_target": "soh",
        "required_features": required_features or ["cycle_index"],
        "preprocessing_policy": "StandardScaler fitted on train partition only",
        "deterministic_supported": True,
        "seed_parameter": "random_seed",
        "code_license": "BSD-3-Clause",
        "weight_license": "not_applicable",
        "source": "synthetic:planning-contract-fixture",
        "bounded_parameters": {},
    }


def _write_registry(path: Path, *, compatible: bool) -> Path:
    plugins = (
        [_plugin(model_name) for model_name in MODELS]
        if compatible
        else [_plugin("incompatible_model", required_features=["voltage_v"])]
    )
    path.write_text(
        json.dumps({"schema_version": 1, "plugins": plugins}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _write_policy(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "selection": {
                    "metric": "mae",
                    "direction": "minimize",
                    "tie_breakers": ["rmse", "plugin_id_lexicographic"],
                },
                "required_metrics": ["mae", "rmse", "mape", "r_squared"],
                "validation_gates": [
                    "cell_partitions_disjoint",
                    "train_only_preprocessing",
                    "finite_complete_predictions",
                    "configured_soh_bounds",
                    "required_metrics_present",
                    "resource_budget_respected",
                    "known_licenses",
                ],
                "soh_bounds": {"minimum": 0.0, "maximum": 1.2},
                "budget": {
                    "max_candidates": 3,
                    "max_runtime_seconds_per_candidate": 120,
                    "max_total_runtime_seconds": 360,
                    "max_memory_mb_per_candidate": 1024,
                },
                "uncertainty": {
                    "method": "bootstrap_by_cell",
                    "confidence_level": 0.95,
                    "resamples": 1000,
                    "random_seed": 73,
                },
                "random_seeds": [73],
                "deterministic_required": True,
                "deterministic_tolerance": 1e-12,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _planning_context(tmp_path: Path, *, compatible: bool) -> dict[str, Any]:
    return {
        "evidence_dir": _write_evidence(tmp_path / "evidence"),
        "registry_path": _write_registry(tmp_path / "registry.json", compatible=compatible),
        "policy_path": _write_policy(tmp_path / "policy.yaml"),
        "output_dirs": [tmp_path / "plan-one", tmp_path / "plan-two"],
        "results": [],
        "error": None,
    }


@given(
    "validated SOH-labelled data and a leakage-free split manifest",
    target_fixture="planning_context",
)
def valid_planning_inputs(tmp_path: Path) -> dict[str, Any]:
    return _planning_context(tmp_path, compatible=True)


@given("a registry containing compatible model plugins")
def compatible_registry(planning_context: dict[str, Any]) -> None:
    assert planning_context["registry_path"].is_file()


@given("a resource and validation policy")
def planning_policy(planning_context: dict[str, Any]) -> None:
    assert planning_context["policy_path"].is_file()


@given("no registered model satisfies the feature contract")
def incompatible_registry(planning_context: dict[str, Any]) -> None:
    planning_context["registry_path"] = _write_registry(
        planning_context["registry_path"], compatible=False
    )


@given(
    "a completed portable benchmark evidence bundle and planning files",
    target_fixture="cli_planning_context",
)
def cli_planning_inputs(tmp_path: Path) -> dict[str, Any]:
    context = _planning_context(tmp_path, compatible=True)
    context["output_dir"] = tmp_path / "cli-plan"
    context["result"] = None
    return context


@when("the researcher asks the agent to plan an experiment")
def plan_experiment(planning_context: dict[str, Any]) -> None:
    try:
        module = importlib.import_module("battery_health.agent.planning")
        for output_dir in planning_context["output_dirs"]:
            planning_context["results"].append(
                module.create_experiment_plan(
                    evidence_dir=planning_context["evidence_dir"],
                    registry_path=planning_context["registry_path"],
                    policy_path=planning_context["policy_path"],
                    output_dir=output_dir,
                )
            )
    except Exception as error:  # the red phase intentionally captures the missing behavior
        planning_context["error"] = error


@when("the researcher runs the agent plan command")
def run_agent_plan(cli_planning_context: dict[str, Any]) -> None:
    runner = CliRunner()
    cli_planning_context["result"] = runner.invoke(
        app,
        [
            "agent",
            "plan",
            "--evidence",
            str(cli_planning_context["evidence_dir"]),
            "--registry",
            str(cli_planning_context["registry_path"]),
            "--policy",
            str(cli_planning_context["policy_path"]),
            "--output",
            str(cli_planning_context["output_dir"]),
        ],
    )


@then("the plan records the target data split features models metrics gates budget and seeds")
def plan_records_experiment_contract(planning_context: dict[str, Any]) -> None:
    assert len(planning_context["results"]) == 2, repr(planning_context["error"])
    plan = json.loads(planning_context["results"][0].plan_path.read_text(encoding="utf-8"))
    assert plan["state"] == "PLANNED"
    assert plan["target"]["name"] == "soh"
    assert plan["features"] == [{"name": "cycle_index", "unit": "dimensionless cycle index"}]
    assert {candidate["plugin_id"] for candidate in plan["candidates"]} == set(MODELS)
    assert plan["selection"]["metric"] == "mae"
    assert plan["required_metrics"] == ["mae", "rmse", "mape", "r_squared"]
    assert len(plan["validation_gates"]) == 7
    assert plan["budget"]["max_candidates"] == 3
    assert plan["uncertainty"]["method"] == "bootstrap_by_cell"
    assert plan["random_seeds"] == [73]
    assert plan["deterministic_settings"]["prediction_tolerance"] == 1e-12


@then("the plan records input code environment and license fingerprints")
def plan_records_fingerprints(planning_context: dict[str, Any]) -> None:
    plan = json.loads(planning_context["results"][0].plan_path.read_text(encoding="utf-8"))
    assert set(plan["fingerprints"]) >= {
        "dataset",
        "split_manifest",
        "effective_config",
        "dependency_lock",
        "model_registry",
        "resource_policy",
    }
    assert plan["software_revision"]["status"] in {"available", "unavailable"}
    for candidate in plan["candidates"]:
        assert candidate["manifest_fingerprint"]
        assert candidate["licenses"] == {
            "code": "BSD-3-Clause",
            "weights": "not_applicable",
        }


@then("the plan receives a stable fingerprint before execution")
def plan_fingerprint_is_stable(planning_context: dict[str, Any]) -> None:
    first, second = planning_context["results"]
    assert first.plan_fingerprint == second.plan_fingerprint
    module = importlib.import_module("battery_health.agent.planning")
    assert module.verify_plan_fingerprint(first.plan_path)


@then("planning fails before model training")
def planning_fails_before_training(planning_context: dict[str, Any]) -> None:
    error = planning_context["error"]
    assert error is not None
    assert error.__class__.__name__ == "PlanningError"
    assert "compatible" in str(error).lower()


@then("no apparently executable plan is created")
def no_plan_is_created(planning_context: dict[str, Any]) -> None:
    assert planning_context["results"] == []
    assert all(not output_dir.exists() for output_dir in planning_context["output_dirs"])


@then("the CLI creates an immutable plan in PLANNED state")
def cli_creates_planned_state(cli_planning_context: dict[str, Any]) -> None:
    result = cli_planning_context["result"]
    assert result.exit_code == 0, result.output
    plan = json.loads(
        (cli_planning_context["output_dir"] / "plan.json").read_text(encoding="utf-8")
    )
    assert plan["state"] == "PLANNED"
    assert plan["plan_fingerprint"]


@then("no candidate training artifacts are created")
def cli_does_not_train_candidates(cli_planning_context: dict[str, Any]) -> None:
    output_dir = cli_planning_context["output_dir"]
    assert not (output_dir / "candidates").exists()
    assert not (output_dir / "models").exists()

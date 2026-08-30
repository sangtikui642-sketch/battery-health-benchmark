import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from battery_health.agent.planning import (
    PlanningError,
    create_experiment_plan,
    verify_plan_fingerprint,
)
from battery_health.benchmark import run_benchmark
from battery_health.cli import app
from tests.bdd.test_agent_planning import _planning_context, _write_policy, _write_registry


def test_plan_output_is_non_overwriting(tmp_path: Path) -> None:
    context = _planning_context(tmp_path, compatible=True)
    output_dir = context["output_dirs"][0]
    output_dir.mkdir()
    marker = output_dir / "user.txt"
    marker.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(PlanningError, match="already exists"):
        create_experiment_plan(
            evidence_dir=context["evidence_dir"],
            registry_path=context["registry_path"],
            policy_path=context["policy_path"],
            output_dir=output_dir,
        )

    assert marker.read_text(encoding="utf-8") == "preserve\n"


def test_stale_evidence_fingerprint_fails_before_output(tmp_path: Path) -> None:
    context = _planning_context(tmp_path, compatible=True)
    labelled_path = context["evidence_dir"] / "cycles_with_soh.csv"
    labelled_path.write_text(labelled_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(PlanningError, match="fingerprint mismatch"):
        create_experiment_plan(
            evidence_dir=context["evidence_dir"],
            registry_path=context["registry_path"],
            policy_path=context["policy_path"],
            output_dir=context["output_dirs"][0],
        )

    assert not context["output_dirs"][0].exists()


def test_missing_feature_unit_is_never_invented(tmp_path: Path) -> None:
    context = _planning_context(tmp_path, compatible=True)
    evidence_dir = context["evidence_dir"]
    config_path = evidence_dir / "effective_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["feature_units"] = {}
    config_path.write_text(json.dumps(config) + "\n", encoding="utf-8")
    manifest_path = evidence_dir / "reproducibility_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    import hashlib

    manifest["sha256"]["effective_config"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    with pytest.raises(PlanningError, match="effective configuration"):
        create_experiment_plan(
            evidence_dir=evidence_dir,
            registry_path=context["registry_path"],
            policy_path=context["policy_path"],
            output_dir=context["output_dirs"][0],
        )


def test_tampering_invalidates_plan_fingerprint(tmp_path: Path) -> None:
    context = _planning_context(tmp_path, compatible=True)
    result = create_experiment_plan(
        evidence_dir=context["evidence_dir"],
        registry_path=context["registry_path"],
        policy_path=context["policy_path"],
        output_dir=context["output_dirs"][0],
    )
    plan = json.loads(result.plan_path.read_text(encoding="utf-8"))
    plan["budget"]["max_candidates"] = 99
    result.plan_path.write_text(json.dumps(plan) + "\n", encoding="utf-8")

    assert not verify_plan_fingerprint(result.plan_path)


def test_tampering_with_registry_snapshot_invalidates_plan(tmp_path: Path) -> None:
    context = _planning_context(tmp_path, compatible=True)
    result = create_experiment_plan(
        evidence_dir=context["evidence_dir"],
        registry_path=context["registry_path"],
        policy_path=context["policy_path"],
        output_dir=context["output_dirs"][0],
    )
    snapshot = result.output_dir / "registry_snapshot.json"
    snapshot.write_text(snapshot.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert not verify_plan_fingerprint(result.plan_path)


def test_planner_does_not_require_or_copy_test_metrics(tmp_path: Path) -> None:
    context = _planning_context(tmp_path, compatible=True)
    untrusted_metrics = context["evidence_dir"] / "test_metrics.json"
    untrusted_metrics.write_text("this is deliberately not valid JSON\n", encoding="utf-8")

    result = create_experiment_plan(
        evidence_dir=context["evidence_dir"],
        registry_path=context["registry_path"],
        policy_path=context["policy_path"],
        output_dir=context["output_dirs"][0],
    )

    plan_text = result.plan_path.read_text(encoding="utf-8")
    assert "test_metrics" not in plan_text
    assert not (result.output_dir / "test_metrics.json").exists()


def test_planner_consumes_a_real_benchmark_evidence_bundle(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    evidence = run_benchmark(
        config_path=repository_root / "configs" / "demo.yaml",
        output_dir=tmp_path / "benchmark-evidence",
        repository_path=repository_root,
    )
    registry_path = _write_registry(tmp_path / "registry.json", compatible=True)
    policy_path = _write_policy(tmp_path / "policy.yaml")

    result = create_experiment_plan(
        evidence_dir=evidence.output_dir,
        registry_path=registry_path,
        policy_path=policy_path,
        output_dir=tmp_path / "plan",
    )

    plan = json.loads(result.plan_path.read_text(encoding="utf-8"))
    manifest = json.loads(evidence.reproducibility_manifest_path.read_text(encoding="utf-8"))
    assert plan["fingerprints"]["dataset"] == manifest["sha256"]["soh_labels"]
    assert plan["fingerprints"]["split_manifest"] == manifest["sha256"]["split_manifest"]
    assert str(tmp_path.resolve()) not in result.plan_path.read_text(encoding="utf-8")
    assert verify_plan_fingerprint(result.plan_path)


def test_agent_plan_cli_rejects_incompatible_registry_without_output(tmp_path: Path) -> None:
    context = _planning_context(tmp_path, compatible=False)
    output_dir = tmp_path / "rejected-plan"

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "plan",
            "--evidence",
            str(context["evidence_dir"]),
            "--registry",
            str(context["registry_path"]),
            "--policy",
            str(context["policy_path"]),
            "--output",
            str(output_dir),
        ],
    )

    assert result.exit_code != 0
    assert "compatible" in result.output.lower()
    assert not output_dir.exists()

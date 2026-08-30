import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

from pytest_bdd import given, scenarios, then, when
from typer.testing import CliRunner

from battery_health.agent.validation import (
    finalize_locked_selection,
    validate_and_lock_execution,
)
from battery_health.cli import app
from tests.bdd.test_agent_validation import (
    _completed_execution,
    _make_predictions_implausible,
)

scenarios("../../features/agent_reporting.feature")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _locked_context(tmp_path: Path, *, finalized: bool) -> dict[str, Any]:
    context = _completed_execution(tmp_path)
    validation = validate_and_lock_execution(
        execution_dir=context["output_dir"],
        output_dir=context["validation_dir"],
    )
    assert validation.state == "LOCKED"
    context["validation_result"] = validation
    context["bundle_dir"] = tmp_path / "evidence-bundle"
    context["error"] = None
    context["use_cli"] = finalized
    if finalized:
        context["finalization_result"] = finalize_locked_selection(
            validation_dir=context["validation_dir"],
            evidence_dir=context["evidence_dir"],
            output_dir=context["final_dir"],
        )
    return context


def _rejected_mixed_context(tmp_path: Path) -> dict[str, Any]:
    context = _completed_execution(tmp_path)
    manifest_path = context["output_dir"] / "execution_manifest.json"
    manifest = _read_json(manifest_path)

    candidate_id = "random_forest"
    record_path = context["output_dir"] / "candidates" / candidate_id / "candidate_record.json"
    record = _read_json(record_path)
    timeout_failure = {
        "code": "runtime_limit_exceeded",
        "stage": "candidate_execution",
        "type": "TimeoutError",
        "message": "Candidate exceeded the locked wall-clock runtime.",
    }
    record["status"] = "failed"
    record["failure"] = timeout_failure
    record["runs"][0]["status"] = "timed_out"
    record["runs"][0]["failure"] = timeout_failure
    _write_json(record_path, record)
    for summary in manifest["candidates"]:
        if summary["candidate_id"] == candidate_id:
            summary["status"] = "failed"
            summary["failure"] = timeout_failure
            summary["record_sha256"] = _sha256(record_path)
    manifest["execution_status"] = "completed_with_failures"
    manifest["test_partition_accessed"] = True
    _write_json(manifest_path, manifest)

    _make_predictions_implausible(context, ("histogram_gradient_boosting",))
    validation = validate_and_lock_execution(
        execution_dir=context["output_dir"],
        output_dir=context["validation_dir"],
    )
    assert validation.state == "REJECTED"
    context["validation_result"] = validation
    context["bundle_dir"] = tmp_path / "rejected-evidence-bundle"
    context["error"] = None
    context["use_cli"] = False
    return context


def _report_arguments(context: dict[str, Any], output_dir: Path) -> list[str]:
    arguments = [
        "agent",
        "report",
        "--evidence",
        str(context["evidence_dir"]),
        "--execution",
        str(context["output_dir"]),
        "--validation",
        str(context["validation_dir"]),
        "--output",
        str(output_dir),
    ]
    if context.get("finalization_result") is not None:
        arguments.extend(["--finalization", str(context["final_dir"])])
    return arguments


def _generate_api(context: dict[str, Any], output_dir: Path) -> object:
    module = importlib.import_module("battery_health.agent.reporting")
    return module.generate_evidence_bundle(
        evidence_dir=context["evidence_dir"],
        execution_dir=context["output_dir"],
        validation_dir=context["validation_dir"],
        finalization_dir=(
            context["final_dir"] if context.get("finalization_result") is not None else None
        ),
        output_dir=output_dir,
    )


def _generate(context: dict[str, Any], output_dir: Path) -> None:
    try:
        if context["use_cli"]:
            context["report_cli"] = CliRunner().invoke(
                app,
                _report_arguments(context, output_dir),
            )
        else:
            context["report_result"] = _generate_api(context, output_dir)
    except Exception as error:
        context["error"] = error


def _artifact_by_role(manifest: dict[str, Any], role: str) -> dict[str, Any]:
    return next(item for item in manifest["artifacts"] if item["role"] == role)


def _resolve_pointer(value: object, pointer: str) -> object:
    current = value
    for raw_token in pointer.lstrip("/").split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise AssertionError(f"Pointer cannot traverse {pointer}")
    return current


@given(
    "a finalized agent run with portable source evidence",
    target_fixture="report_context",
)
def finalized_run(tmp_path: Path) -> dict[str, Any]:
    return _locked_context(tmp_path, finalized=True)


@given("no language model or network service is configured")
def no_external_advisor(report_context: dict[str, Any]) -> None:
    assert "advisor" not in report_context


@given(
    "a rejected run containing successful failed timed-out and gate-rejected candidates",
    target_fixture="report_context",
)
def rejected_mixed_run(tmp_path: Path) -> dict[str, Any]:
    return _rejected_mixed_context(tmp_path)


@given(
    "a locked agent run without held-out test evaluation",
    target_fixture="report_context",
)
def locked_unfinalized_run(tmp_path: Path) -> dict[str, Any]:
    return _locked_context(tmp_path, finalized=False)


@given(
    "a generated evidence bundle with one altered protected artifact",
    target_fixture="tamper_context",
)
def tampered_bundle(tmp_path: Path) -> dict[str, Any]:
    context = _locked_context(tmp_path, finalized=False)
    source_paths = (
        context["evidence_dir"] / "reproducibility_manifest.json",
        context["output_dir"] / "execution_manifest.json",
        context["validation_dir"] / "leaderboard.json",
    )
    context["source_bytes"] = {str(path): path.read_bytes() for path in source_paths}
    try:
        _generate_api(context, context["bundle_dir"])
        manifest = _read_json(context["bundle_dir"] / "evidence_manifest.json")
        artifact = _artifact_by_role(manifest, "validation_leaderboard")
        protected = context["bundle_dir"] / artifact["path"]
        protected.write_bytes(protected.read_bytes() + b"\n")
    except Exception as error:
        context["error"] = error
    return context


@given(
    "one locked agent run used for two report outputs",
    target_fixture="determinism_context",
)
def one_run_two_outputs(tmp_path: Path) -> dict[str, Any]:
    context = _locked_context(tmp_path, finalized=False)
    context["first_bundle"] = tmp_path / "bundle-a"
    context["second_bundle"] = tmp_path / "bundle-b"
    return context


@when("the researcher generates the deterministic evidence report")
def generate_report(report_context: dict[str, Any]) -> None:
    _generate(report_context, report_context["bundle_dir"])


@when("the researcher verifies the evidence bundle through the API and CLI")
def verify_tampered_bundle(tamper_context: dict[str, Any]) -> None:
    if tamper_context["error"] is not None:
        return
    module = importlib.import_module("battery_health.agent.reporting")
    try:
        module.verify_evidence_bundle(tamper_context["bundle_dir"])
    except Exception as error:
        tamper_context["api_error"] = error
    tamper_context["verify_cli"] = CliRunner().invoke(
        app,
        ["agent", "verify", "--bundle", str(tamper_context["bundle_dir"])],
    )


@when("the researcher generates both deterministic evidence reports")
def generate_two_reports(determinism_context: dict[str, Any]) -> None:
    try:
        determinism_context["first_result"] = _generate_api(
            determinism_context,
            determinism_context["first_bundle"],
        )
        determinism_context["second_result"] = _generate_api(
            determinism_context,
            determinism_context["second_bundle"],
        )
    except Exception as error:
        determinism_context["error"] = error


@then("the strict evidence manifest links plans configurations fingerprints outcomes and gates")
def manifest_links_required_evidence(report_context: dict[str, Any]) -> None:
    assert report_context["error"] is None
    cli = report_context["report_cli"]
    assert cli.exit_code == 0, cli.output
    manifest = _read_json(report_context["bundle_dir"] / "evidence_manifest.json")
    assert manifest["schema_version"] == 1
    assert manifest["bundle_state"] == "FINALIZED"
    assert manifest["generation_policy"] == {
        "language_model": "disabled",
        "network_access": False,
    }
    assert set(manifest["source_fingerprints"]) == {
        "input_dataset",
        "split_manifest",
        "code",
        "dependency_lock",
        "models",
    }
    roles = {artifact["role"] for artifact in manifest["artifacts"]}
    assert {
        "immutable_plan",
        "effective_configuration",
        "dependency_lock",
        "execution_input",
        "execution_manifest",
        "validation_manifest",
        "validation_leaderboard",
        "selection_lock",
        "finalization_manifest",
        "final_test_predictions",
        "final_test_metrics",
    } <= roles
    assert len(manifest["candidate_outcomes"]) == 3


@then("the held-out test claims link to finalization artifacts")
def final_claims_link_artifacts(report_context: dict[str, Any]) -> None:
    manifest = _read_json(report_context["bundle_dir"] / "evidence_manifest.json")
    metrics_artifact = _artifact_by_role(manifest, "final_test_metrics")
    test_claims = [
        claim for claim in manifest["quantitative_claims"] if claim["partition"] == "test"
    ]
    assert manifest["final_test"]["status"] == "finalized"
    assert {"mae", "rmse", "mape", "r_squared"} <= {claim["metric"] for claim in test_claims}
    assert all(metrics_artifact["artifact_id"] in claim["artifact_ids"] for claim in test_claims)


@then("the evidence bundle verifies offline")
def bundle_verifies_offline(report_context: dict[str, Any]) -> None:
    module = importlib.import_module("battery_health.agent.reporting")
    result = module.verify_evidence_bundle(report_context["bundle_dir"])
    assert result.valid is True
    assert result.evidence_fingerprint


@then("successful failed timed-out and rejected outcomes remain visible")
def all_outcomes_visible(report_context: dict[str, Any]) -> None:
    assert report_context["error"] is None
    manifest = _read_json(report_context["bundle_dir"] / "evidence_manifest.json")
    tags = {
        tag for candidate in manifest["candidate_outcomes"] for tag in candidate["outcome_tags"]
    }
    assert {"successful", "failed", "timed_out", "rejected"} <= tags


@then("no winner or final-test performance is claimed")
def rejected_report_has_no_winner(report_context: dict[str, Any]) -> None:
    manifest = _read_json(report_context["bundle_dir"] / "evidence_manifest.json")
    assert manifest["bundle_state"] == "REJECTED"
    assert manifest["selection_fingerprint"] is None
    assert manifest["final_test"]["status"] == "not_permitted"
    assert all(claim["partition"] != "test" for claim in manifest["quantitative_claims"])


@then("the report explicitly marks the final test as not finalized")
def report_marks_unfinalized(report_context: dict[str, Any]) -> None:
    assert report_context["error"] is None
    manifest = _read_json(report_context["bundle_dir"] / "evidence_manifest.json")
    report = (report_context["bundle_dir"] / "report.md").read_text(encoding="utf-8")
    assert manifest["bundle_state"] == "LOCKED"
    assert manifest["final_test"]["status"] == "not_finalized"
    assert "NOT FINALIZED" in report


@then("no quantitative claim uses the test partition")
def no_test_claim_without_finalization(report_context: dict[str, Any]) -> None:
    manifest = _read_json(report_context["bundle_dir"] / "evidence_manifest.json")
    assert all(claim["partition"] != "test" for claim in manifest["quantitative_claims"])
    assert "held_out_test_performance" in manifest["unsupported_claims"]


@then("both verification paths reject the bundle")
def verification_rejects_tamper(tamper_context: dict[str, Any]) -> None:
    assert tamper_context["error"] is None
    assert tamper_context.get("api_error") is not None
    assert "sha-256" in str(tamper_context["api_error"]).lower()
    cli = tamper_context["verify_cli"]
    assert cli.exit_code != 0
    assert "verification failed" in cli.output.lower()


@then("the original evidence sources remain unchanged")
def sources_unchanged(tamper_context: dict[str, Any]) -> None:
    for raw_path, original in tamper_context["source_bytes"].items():
        assert Path(raw_path).read_bytes() == original


@then("every quantitative claim resolves to a hashed artifact and numeric JSON location")
def claims_resolve_to_numeric_evidence(report_context: dict[str, Any]) -> None:
    assert report_context["error"] is None
    manifest = _read_json(report_context["bundle_dir"] / "evidence_manifest.json")
    artifacts = {item["artifact_id"]: item for item in manifest["artifacts"]}
    assert manifest["quantitative_claims"]
    for claim in manifest["quantitative_claims"]:
        assert claim["artifact_ids"]
        artifact = artifacts[claim["artifact_ids"][0]]
        artifact_path = report_context["bundle_dir"] / artifact["path"]
        assert _sha256(artifact_path) == artifact["sha256"]
        value = _resolve_pointer(_read_json(artifact_path), claim["json_pointer"])
        assert isinstance(value, int | float)
        assert float(value) == float(claim["value"])


@then("both evidence fingerprints are identical")
def fingerprints_are_identical(determinism_context: dict[str, Any]) -> None:
    assert determinism_context["error"] is None
    first = _read_json(determinism_context["first_bundle"] / "evidence_manifest.json")
    second = _read_json(determinism_context["second_bundle"] / "evidence_manifest.json")
    assert first["evidence_fingerprint"] == second["evidence_fingerprint"]
    assert first == second
    assert (determinism_context["first_bundle"] / "report.md").read_bytes() == (
        determinism_context["second_bundle"] / "report.md"
    ).read_bytes()


@then("no timestamp participates in the evidence fingerprint")
def timestamps_are_excluded(determinism_context: dict[str, Any]) -> None:
    manifest = _read_json(determinism_context["first_bundle"] / "evidence_manifest.json")
    serialized = json.dumps(manifest, sort_keys=True).lower()
    assert "generated_at" not in serialized
    assert "timestamp" not in serialized

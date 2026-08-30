import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import battery_health.agent.reporting as reporting
from battery_health.agent.reporting import (
    ReportError,
    generate_evidence_bundle,
    verify_evidence_bundle,
)
from battery_health.cli import app
from tests.bdd.test_agent_reporting import _locked_context, _rejected_mixed_context


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def locked_source(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _locked_context(tmp_path_factory.mktemp("report-locked"), finalized=False)


@pytest.fixture(scope="module")
def finalized_source(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _locked_context(tmp_path_factory.mktemp("report-finalized"), finalized=True)


@pytest.fixture(scope="module")
def rejected_source(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _rejected_mixed_context(tmp_path_factory.mktemp("report-rejected"))


def _clone_context(source: dict[str, Any], root: Path) -> dict[str, Any]:
    context: dict[str, Any] = {
        "evidence_dir": root / "evidence",
        "output_dir": root / "execution",
        "validation_dir": root / "validation",
        "final_dir": root / "final",
        "bundle_dir": root / "bundle",
    }
    for key in ("evidence_dir", "output_dir", "validation_dir"):
        shutil.copytree(source[key], context[key])
    if source["final_dir"].is_dir():
        shutil.copytree(source["final_dir"], context["final_dir"])
        context["finalized"] = True
    else:
        context["finalized"] = False
    return context


def _generate(context: dict[str, Any]) -> object:
    return generate_evidence_bundle(
        evidence_dir=context["evidence_dir"],
        execution_dir=context["output_dir"],
        validation_dir=context["validation_dir"],
        finalization_dir=context["final_dir"] if context["finalized"] else None,
        output_dir=context["bundle_dir"],
    )


def _refingerprint_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["evidence_fingerprint"] = reporting._canonical_fingerprint(
        reporting._fingerprint_payload(manifest)
    )
    _write_json(path, manifest)


def test_evidence_output_is_non_overwriting(locked_source: dict[str, Any], tmp_path: Path) -> None:
    context = _clone_context(locked_source, tmp_path)
    context["bundle_dir"].mkdir()
    marker = context["bundle_dir"] / "user.txt"
    marker.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(ReportError, match="already exists"):
        _generate(context)

    assert marker.read_text(encoding="utf-8") == "preserve\n"


def test_execution_identity_tamper_is_rejected_before_output(
    locked_source: dict[str, Any], tmp_path: Path
) -> None:
    context = _clone_context(locked_source, tmp_path)
    manifest_path = context["output_dir"] / "execution_manifest.json"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ReportError, match="identities do not match"):
        _generate(context)

    assert not context["bundle_dir"].exists()


def test_strict_manifest_rejects_an_unknown_field(
    locked_source: dict[str, Any], tmp_path: Path
) -> None:
    context = _clone_context(locked_source, tmp_path)
    _generate(context)
    manifest_path = context["bundle_dir"] / "evidence_manifest.json"
    manifest = _read_json(manifest_path)
    manifest["unexpected"] = "not allowed"
    _write_json(manifest_path, manifest)

    with pytest.raises(ReportError, match="unexpected"):
        verify_evidence_bundle(context["bundle_dir"])


def test_absolute_artifact_path_is_rejected(locked_source: dict[str, Any], tmp_path: Path) -> None:
    context = _clone_context(locked_source, tmp_path)
    _generate(context)
    manifest_path = context["bundle_dir"] / "evidence_manifest.json"
    manifest = _read_json(manifest_path)
    manifest["artifacts"][0]["path"] = str((tmp_path / "outside.json").resolve())
    _write_json(manifest_path, manifest)

    with pytest.raises(ReportError, match="artifacts.0.path"):
        verify_evidence_bundle(context["bundle_dir"])


def test_unknown_claim_artifact_is_rejected(locked_source: dict[str, Any], tmp_path: Path) -> None:
    context = _clone_context(locked_source, tmp_path)
    _generate(context)
    manifest_path = context["bundle_dir"] / "evidence_manifest.json"
    manifest = _read_json(manifest_path)
    manifest["quantitative_claims"][0]["artifact_ids"] = ["missing.artifact"]
    _write_json(manifest_path, manifest)

    with pytest.raises(ReportError, match="unknown artifact"):
        verify_evidence_bundle(context["bundle_dir"])


def test_refingerprinted_claim_with_wrong_value_is_rejected(
    locked_source: dict[str, Any], tmp_path: Path
) -> None:
    context = _clone_context(locked_source, tmp_path)
    _generate(context)
    manifest_path = context["bundle_dir"] / "evidence_manifest.json"
    manifest = _read_json(manifest_path)
    manifest["quantitative_claims"][0]["value"] += 1.0
    _refingerprint_manifest(manifest_path, manifest)

    with pytest.raises(ReportError, match="claim value mismatch"):
        verify_evidence_bundle(context["bundle_dir"])


def test_human_report_tampering_is_rejected(locked_source: dict[str, Any], tmp_path: Path) -> None:
    context = _clone_context(locked_source, tmp_path)
    _generate(context)
    report_path = context["bundle_dir"] / "report.md"
    report_path.write_text(report_path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

    with pytest.raises(ReportError, match="does not match"):
        verify_evidence_bundle(context["bundle_dir"])


def test_finalization_fingerprint_tamper_is_rejected_before_output(
    finalized_source: dict[str, Any], tmp_path: Path
) -> None:
    context = _clone_context(finalized_source, tmp_path)
    manifest_path = context["final_dir"] / "finalization_manifest.json"
    manifest = _read_json(manifest_path)
    manifest["test_evaluation_count"] = 2
    _write_json(manifest_path, manifest)

    with pytest.raises(ReportError, match="fingerprint is invalid"):
        _generate(context)

    assert not context["bundle_dir"].exists()


def test_cli_reports_and_verifies_an_unfinalized_bundle(
    locked_source: dict[str, Any], tmp_path: Path
) -> None:
    context = _clone_context(locked_source, tmp_path)
    runner = CliRunner()
    report = runner.invoke(
        app,
        [
            "agent",
            "report",
            "--evidence",
            str(context["evidence_dir"]),
            "--execution",
            str(context["output_dir"]),
            "--validation",
            str(context["validation_dir"]),
            "--output",
            str(context["bundle_dir"]),
        ],
    )

    assert report.exit_code == 0, report.output
    assert "state=LOCKED" in report.output
    assert "evidence_fingerprint=" in report.output
    assert "next=agent verify" in report.output
    verify = runner.invoke(
        app,
        ["agent", "verify", "--bundle", str(context["bundle_dir"])],
    )
    assert verify.exit_code == 0, verify.output
    assert "valid=true" in verify.output
    assert "next=none" in verify.output


def test_rejected_run_cannot_attach_finalization_evidence(
    rejected_source: dict[str, Any], tmp_path: Path
) -> None:
    context = _clone_context(rejected_source, tmp_path)

    with pytest.raises(ReportError, match="requires a LOCKED selection"):
        generate_evidence_bundle(
            evidence_dir=context["evidence_dir"],
            execution_dir=context["output_dir"],
            validation_dir=context["validation_dir"],
            finalization_dir=context["validation_dir"],
            output_dir=context["bundle_dir"],
        )

    assert not context["bundle_dir"].exists()


def test_missing_human_report_is_rejected(locked_source: dict[str, Any], tmp_path: Path) -> None:
    context = _clone_context(locked_source, tmp_path)
    _generate(context)
    (context["bundle_dir"] / "report.md").unlink()

    with pytest.raises(ReportError, match="report is missing"):
        verify_evidence_bundle(context["bundle_dir"])

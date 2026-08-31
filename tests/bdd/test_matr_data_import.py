import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import h5py
import pandas as pd
from pytest_bdd import given, scenarios, then, when
from typer.testing import CliRunner

from battery_health.cli import app
from battery_health.data.matr import (
    MATRImportError,
    MATRImportResult,
    import_matr_hdf5,
)

scenarios("../../features/matr_data_import.feature")

SOURCE_URL = "https://data.matr.io/1/api/v1/file/example/download"
ACQUIRED_ON = "2026-08-31"
USAGE_STATUS = "public-access-research-no-redistribution-claim"


def _write_matr_fixture(
    path: Path,
    *,
    cells: tuple[tuple[list[float], list[float]], ...] = (
        ([2.0, 1.0], [1.04, 1.05]),
        ([1.0, 2.0], [1.03, 1.01]),
    ),
    include_summary: bool = True,
) -> None:
    with h5py.File(path, "w") as handle:
        batch = handle.create_group("batch")
        if not include_summary:
            batch.create_dataset("unrelated", data=[1.0])
            return
        references = handle.create_group("#refs#")
        summary_refs = handle.create_dataset(
            "summary_refs",
            shape=(len(cells), 1),
            dtype=h5py.ref_dtype,
        )
        for index, (cycles, capacities) in enumerate(cells):
            summary = references.create_group(f"summary_{index}")
            summary.create_dataset("cycle", data=[cycles])
            summary.create_dataset("QDischarge", data=[capacities])
            summary_refs[index, 0] = summary.ref
        batch["summary"] = summary_refs


def _context(input_path: Path, output_dir: Path) -> dict[str, Any]:
    return {
        "input_path": input_path,
        "output_dir": output_dir,
        "batch_id": "b2",
        "source_url": SOURCE_URL,
        "acquired_on": ACQUIRED_ON,
        "usage_terms_status": USAGE_STATUS,
        "result": None,
        "error": None,
    }


@given(
    "a structurally faithful anonymous MATR HDF5 batch",
    target_fixture="matr_context",
)
def valid_matr_batch(tmp_path: Path) -> dict[str, Any]:
    input_path = tmp_path / "anonymous_batch.mat"
    _write_matr_fixture(input_path)
    return _context(input_path, tmp_path / "matr-output")


@given(
    "a structurally faithful anonymous MATR HDF5 batch with unsorted source cycles",
    target_fixture="matr_context",
)
def unsorted_matr_batch(tmp_path: Path) -> dict[str, Any]:
    input_path = tmp_path / "unsorted_batch.mat"
    _write_matr_fixture(input_path)
    return _context(input_path, tmp_path / "matr-output")


@given(
    "an HDF5 file without the required MATR summary structure",
    target_fixture="matr_context",
)
def missing_structure(tmp_path: Path) -> dict[str, Any]:
    input_path = tmp_path / "missing_summary.mat"
    _write_matr_fixture(input_path, include_summary=False)
    return _context(input_path, tmp_path / "matr-output")


@given(
    "a MATR HDF5 batch with invalid cycle summary values",
    target_fixture="matr_context",
)
def invalid_values(tmp_path: Path) -> dict[str, Any]:
    input_path = tmp_path / "invalid_values.mat"
    _write_matr_fixture(input_path, cells=(([1.0, 2.0], [1.05, -0.1]),))
    return _context(input_path, tmp_path / "matr-output")


@given(
    "a MATR HDF5 batch with a repeated source cycle index",
    target_fixture="matr_context",
)
def duplicate_cycles(tmp_path: Path) -> dict[str, Any]:
    input_path = tmp_path / "duplicate_cycles.mat"
    _write_matr_fixture(input_path, cells=(([1.0, 1.0], [1.05, 1.04]),))
    return _context(input_path, tmp_path / "matr-output")


@given(
    "two byte-identical anonymous MATR HDF5 batches",
    target_fixture="matr_context",
)
def identical_batches(tmp_path: Path) -> dict[str, Any]:
    first = tmp_path / "source-a" / "anonymous_batch.mat"
    second = tmp_path / "source-b" / "anonymous_batch.mat"
    first.parent.mkdir()
    second.parent.mkdir()
    _write_matr_fixture(first)
    shutil.copyfile(first, second)
    context = _context(first, tmp_path / "output-a")
    context["second_input_path"] = second
    context["second_output_dir"] = tmp_path / "output-b"
    context["second_result"] = None
    return context


@given(
    "a structurally faithful anonymous MATR HDF5 batch for the CLI",
    target_fixture="matr_context",
)
def cli_batch(tmp_path: Path) -> dict[str, Any]:
    input_path = tmp_path / "cli_batch.mat"
    _write_matr_fixture(input_path)
    context = _context(input_path, tmp_path / "cli-output")
    context["cli_result"] = None
    return context


@given("reviewed public-source metadata for batch b2")
def reviewed_metadata(matr_context: dict[str, Any]) -> None:
    assert matr_context["batch_id"] == "b2"
    assert matr_context["usage_terms_status"] == USAGE_STATUS


@when("the researcher imports the MATR batch")
def import_batch(matr_context: dict[str, Any]) -> None:
    try:
        matr_context["result"] = import_matr_hdf5(
            input_path=matr_context["input_path"],
            output_dir=matr_context["output_dir"],
            batch_id=matr_context["batch_id"],
            source_url=matr_context["source_url"],
            acquired_on=matr_context["acquired_on"],
            usage_terms_status=matr_context["usage_terms_status"],
        )
    except MATRImportError as error:
        matr_context["error"] = error


@when("the researcher imports both MATR batches independently")
def import_identical_batches(matr_context: dict[str, Any]) -> None:
    import_batch(matr_context)
    matr_context["second_result"] = import_matr_hdf5(
        input_path=matr_context["second_input_path"],
        output_dir=matr_context["second_output_dir"],
        batch_id=matr_context["batch_id"],
        source_url=matr_context["source_url"],
        acquired_on=matr_context["acquired_on"],
        usage_terms_status=matr_context["usage_terms_status"],
    )


@when("the researcher runs the MATR import command")
def run_cli(matr_context: dict[str, Any]) -> None:
    matr_context["cli_result"] = CliRunner().invoke(
        app,
        [
            "import-matr",
            "--input",
            str(matr_context["input_path"]),
            "--output",
            str(matr_context["output_dir"]),
            "--batch-id",
            "b2",
            "--source-url",
            SOURCE_URL,
            "--acquired-on",
            ACQUIRED_ON,
            "--usage-terms-status",
            USAGE_STATUS,
        ],
    )


@then("normalized cycles and a quality report are created")
def normalized_outputs_exist(matr_context: dict[str, Any]) -> None:
    result = matr_context["result"]
    assert isinstance(result, MATRImportResult)
    assert result.normalized_path.is_file()
    assert result.quality_report_path.is_file()
    frame = pd.read_csv(result.normalized_path)
    assert list(frame.columns) == ["cell_id", "cycle_index", "capacity_ah"]
    report = json.loads(result.quality_report_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["cell_count"] == 2
    assert report["cycle_count"] == 4


@then("the normalized fields have explicit types units and source mappings")
def field_contract_is_explicit(matr_context: dict[str, Any]) -> None:
    result = matr_context["result"]
    assert isinstance(result, MATRImportResult)
    manifest = json.loads(result.source_manifest_path.read_text(encoding="utf-8"))
    assert manifest["field_contract"] == {
        "cell_id": {"source": "batch/summary index", "type": "string", "unit": "dimensionless"},
        "cycle_index": {"source": "summary/cycle", "type": "int64", "unit": "cycle"},
        "capacity_ah": {"source": "summary/QDischarge", "type": "float64", "unit": "Ah"},
    }


@then("cell identifiers use the stable batch and source index")
def stable_cell_ids(matr_context: dict[str, Any]) -> None:
    result = matr_context["result"]
    assert isinstance(result, MATRImportResult)
    frame = pd.read_csv(result.normalized_path)
    assert frame["cell_id"].tolist() == ["b2c0", "b2c0", "b2c1", "b2c1"]


@then("source cycle indices are preserved in deterministic order")
def deterministic_cycles(matr_context: dict[str, Any]) -> None:
    result = matr_context["result"]
    assert isinstance(result, MATRImportResult)
    frame = pd.read_csv(result.normalized_path)
    assert frame["cycle_index"].tolist() == [1, 2, 1, 2]
    assert frame.loc[frame["cell_id"] == "b2c0", "capacity_ah"].tolist() == [1.05, 1.04]


@then("the source manifest records portable provenance and the input SHA-256")
def portable_provenance(matr_context: dict[str, Any]) -> None:
    result = matr_context["result"]
    assert isinstance(result, MATRImportResult)
    text = result.source_manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(text)
    expected = hashlib.sha256(matr_context["input_path"].read_bytes()).hexdigest()
    assert manifest["source"]["sha256"] == expected
    assert manifest["source"]["source_url"] == SOURCE_URL
    assert manifest["source"]["acquired_on"] == ACQUIRED_ON
    assert manifest["source"]["usage_terms_status"] == USAGE_STATUS
    assert str(matr_context["input_path"].resolve()) not in text


@then("every published output hash verifies against its bytes")
def output_hashes_verify(matr_context: dict[str, Any]) -> None:
    result = matr_context["result"]
    assert isinstance(result, MATRImportResult)
    manifest = json.loads(result.source_manifest_path.read_text(encoding="utf-8"))
    for name, path in {
        "cycles.csv": result.normalized_path,
        "quality_report.json": result.quality_report_path,
    }.items():
        assert manifest["outputs"][name]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


@then("the MATR import fails with an actionable structure error")
def actionable_structure_error(matr_context: dict[str, Any]) -> None:
    assert isinstance(matr_context["error"], MATRImportError)
    assert "batch/summary" in str(matr_context["error"])


@then("the MATR import fails with an actionable value error")
def actionable_value_error(matr_context: dict[str, Any]) -> None:
    assert isinstance(matr_context["error"], MATRImportError)
    assert "finite positive" in str(matr_context["error"])


@then("the MATR import fails with an actionable duplicate error")
def actionable_duplicate_error(matr_context: dict[str, Any]) -> None:
    assert isinstance(matr_context["error"], MATRImportError)
    assert "duplicate" in str(matr_context["error"]).lower()


@then("no partial MATR output is left behind")
def no_partial_output(matr_context: dict[str, Any]) -> None:
    assert not matr_context["output_dir"].exists()


@then("their normalized CSV and source manifest fingerprints are identical")
def reproducible_outputs(matr_context: dict[str, Any]) -> None:
    first = matr_context["result"]
    second = matr_context["second_result"]
    assert isinstance(first, MATRImportResult)
    assert isinstance(second, MATRImportResult)
    assert first.normalized_path.read_bytes() == second.normalized_path.read_bytes()
    first_manifest = json.loads(first.source_manifest_path.read_text(encoding="utf-8"))
    second_manifest = json.loads(second.source_manifest_path.read_text(encoding="utf-8"))
    assert first_manifest == second_manifest
    assert first.import_fingerprint == second.import_fingerprint


@then("the CLI reports the imported cell cycle and fingerprint identity")
def cli_reports_identity(matr_context: dict[str, Any]) -> None:
    result = matr_context["cli_result"]
    assert result.exit_code == 0, result.output
    assert "cells=2" in result.output
    assert "cycles=4" in result.output
    assert "import_fingerprint=" in result.output


@then("the CLI output bundle passes the same provenance checks")
def cli_bundle_is_valid(matr_context: dict[str, Any]) -> None:
    output_dir = matr_context["output_dir"]
    assert (output_dir / "cycles.csv").is_file()
    assert (output_dir / "quality_report.json").is_file()
    manifest = json.loads((output_dir / "source_manifest.json").read_text(encoding="utf-8"))
    assert (
        manifest["source"]["sha256"]
        == hashlib.sha256(matr_context["input_path"].read_bytes()).hexdigest()
    )

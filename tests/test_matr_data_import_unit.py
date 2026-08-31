import json
from pathlib import Path

import h5py
import pytest
from typer.testing import CliRunner

from battery_health.cli import app
from battery_health.data.matr import MATRImportError, import_matr_hdf5

SOURCE = "https://data.matr.io/1/api/v1/file/example/download"
USAGE = "public-access-research-no-redistribution-claim"


def _fixture(
    path: Path,
    *,
    cycles: list[float] | None = None,
    capacities: list[float] | None = None,
    reference_array: bool = True,
) -> None:
    cycles = [1.0, 2.0] if cycles is None else cycles
    capacities = [1.05, 1.04] if capacities is None else capacities
    with h5py.File(path, "w") as handle:
        batch = handle.create_group("batch")
        if not reference_array:
            batch.create_dataset("summary", data=[[1.0]])
            return
        refs = handle.create_group("#refs#")
        summary = refs.create_group("summary_0")
        cycle_values = refs.create_dataset("cycle_values_0", data=[cycles])
        capacity_values = refs.create_dataset("capacity_values_0", data=[capacities])
        cycle_reference = summary.create_dataset("cycle", shape=(1, 1), dtype=h5py.ref_dtype)
        capacity_reference = summary.create_dataset(
            "QDischarge", shape=(1, 1), dtype=h5py.ref_dtype
        )
        cycle_reference[0, 0] = cycle_values.ref
        capacity_reference[0, 0] = capacity_values.ref
        summaries = handle.create_dataset("summary_refs", shape=(1, 1), dtype=h5py.ref_dtype)
        summaries[0, 0] = summary.ref
        batch["summary"] = summaries


def _import(path: Path, output: Path, **overrides: str) -> object:
    values = {
        "batch_id": "b2",
        "source_url": SOURCE,
        "acquired_on": "2026-08-31",
        "usage_terms_status": USAGE,
    }
    values.update(overrides)
    return import_matr_hdf5(input_path=path, output_dir=output, **values)


@pytest.mark.parametrize("batch_id", ["B2", "../b2", "b2/c0", ""])
def test_rejects_unsafe_batch_id(tmp_path: Path, batch_id: str) -> None:
    path = tmp_path / "batch.mat"
    _fixture(path)
    with pytest.raises(MATRImportError, match="batch_id"):
        _import(path, tmp_path / "output", batch_id=batch_id)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_url", "file:///private/batch.mat", "source_url"),
        ("acquired_on", "31-08-2026", "acquired_on"),
        ("usage_terms_status", "", "usage_terms_status"),
    ],
)
def test_rejects_invalid_source_metadata(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    path = tmp_path / "batch.mat"
    _fixture(path)
    with pytest.raises(MATRImportError, match=message):
        _import(path, tmp_path / "output", **{field: value})


def test_rejects_non_hdf5_input_without_output(tmp_path: Path) -> None:
    path = tmp_path / "batch.mat"
    path.write_text("not hdf5", encoding="utf-8")
    output = tmp_path / "output"
    with pytest.raises(MATRImportError, match="HDF5"):
        _import(path, output)
    assert not output.exists()


def test_rejects_non_reference_summary_array(tmp_path: Path) -> None:
    path = tmp_path / "batch.mat"
    _fixture(path, reference_array=False)
    with pytest.raises(MATRImportError, match="reference"):
        _import(path, tmp_path / "output")


def test_rejects_nonnumeric_direct_summary_vector(tmp_path: Path) -> None:
    path = tmp_path / "batch.mat"
    _fixture(path)
    with h5py.File(path, "r+") as handle:
        summary = handle[handle["batch/summary"][0, 0]]
        del summary["cycle"]
        summary.create_dataset("cycle", data=[[b"first", b"second"]])
    with pytest.raises(MATRImportError, match="summary/cycle must be a numeric vector"):
        _import(path, tmp_path / "output")


def test_rejects_mismatched_vectors(tmp_path: Path) -> None:
    path = tmp_path / "batch.mat"
    _fixture(path, cycles=[1.0, 2.0], capacities=[1.05])
    with pytest.raises(MATRImportError, match="equal lengths"):
        _import(path, tmp_path / "output")


@pytest.mark.parametrize("cycles", [[0.0], [1.5], [float("nan")]])
def test_rejects_invalid_cycle_indices(tmp_path: Path, cycles: list[float]) -> None:
    path = tmp_path / "batch.mat"
    _fixture(path, cycles=cycles, capacities=[1.0])
    with pytest.raises(MATRImportError, match="finite positive integers"):
        _import(path, tmp_path / "output")


@pytest.mark.parametrize("capacities", [[0.0], [-1.0], [float("inf")], [float("nan")]])
def test_rejects_invalid_capacities(tmp_path: Path, capacities: list[float]) -> None:
    path = tmp_path / "batch.mat"
    _fixture(path, cycles=[1.0], capacities=capacities)
    with pytest.raises(MATRImportError, match="finite positive"):
        _import(path, tmp_path / "output")


def test_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    path = tmp_path / "batch.mat"
    _fixture(path)
    output = tmp_path / "output"
    output.mkdir()
    marker = output / "owned.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(MATRImportError, match="already exists"):
        _import(path, output)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_manifest_contains_only_portable_file_identity(tmp_path: Path) -> None:
    path = tmp_path / "batch.mat"
    _fixture(path)
    result = _import(path, tmp_path / "output")
    manifest_text = result.source_manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert str(path.resolve()) not in manifest_text
    assert manifest["source"]["original_file_name"] == "batch.mat"
    assert manifest["schema_version"] == 1


def test_cli_returns_nonzero_without_partial_output(tmp_path: Path) -> None:
    path = tmp_path / "invalid.mat"
    path.write_bytes(b"invalid")
    output = tmp_path / "output"
    result = CliRunner().invoke(
        app,
        [
            "import-matr",
            "--input",
            str(path),
            "--output",
            str(output),
            "--batch-id",
            "b2",
            "--source-url",
            SOURCE,
            "--acquired-on",
            "2026-08-31",
            "--usage-terms-status",
            USAGE,
        ],
    )
    assert result.exit_code == 1
    assert "MATR import failed" in result.output
    assert not output.exists()

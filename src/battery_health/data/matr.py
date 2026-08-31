"""Auditable import of MATR MATLAB v7.3/HDF5 battery batches."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import h5py  # type: ignore[import-untyped]
import numpy as np
import pandas as pd

_BATCH_ID = re.compile(r"^b[1-9][0-9]*$")
_NORMALIZED_COLUMNS = ("cell_id", "cycle_index", "capacity_ah")
_FIELD_CONTRACT = {
    "cell_id": {
        "source": "batch/summary index",
        "type": "string",
        "unit": "dimensionless",
    },
    "cycle_index": {
        "source": "summary/cycle",
        "type": "int64",
        "unit": "cycle",
    },
    "capacity_ah": {
        "source": "summary/QDischarge",
        "type": "float64",
        "unit": "Ah",
    },
}


class MATRImportError(ValueError):
    """Raised when a MATR batch cannot satisfy the reviewed import contract."""


@dataclass(frozen=True)
class MATRImportResult:
    """Portable artifacts and identity produced by one successful import."""

    normalized_path: Path
    quality_report_path: Path
    source_manifest_path: Path
    import_fingerprint: str
    cell_count: int
    cycle_count: int


def _canonical_fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise MATRImportError(f"Unable to hash required file: {path.name}") from error
    return digest.hexdigest()


def _validate_metadata(
    *,
    batch_id: str,
    source_url: str,
    acquired_on: str,
    usage_terms_status: str,
) -> None:
    if _BATCH_ID.fullmatch(batch_id) is None:
        raise MATRImportError(
            "batch_id must match the stable identifier pattern b<positive integer>"
        )

    parsed = urlparse(source_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise MATRImportError("source_url must be an absolute HTTPS URL")

    try:
        parsed_date = date.fromisoformat(acquired_on)
    except ValueError as error:
        raise MATRImportError("acquired_on must be an ISO 8601 date (YYYY-MM-DD)") from error
    if parsed_date.isoformat() != acquired_on:
        raise MATRImportError("acquired_on must be an ISO 8601 date (YYYY-MM-DD)")

    if not usage_terms_status.strip():
        raise MATRImportError("usage_terms_status must record the reviewed source status")


def _read_vector(
    handle: h5py.File,
    group: h5py.Group,
    name: str,
    *,
    cell_id: str,
) -> tuple[np.ndarray, str]:
    if name not in group or not isinstance(group[name], h5py.Dataset):
        raise MATRImportError(f"{cell_id} is missing required summary/{name} dataset")
    dataset = group[name]
    if h5py.check_dtype(ref=dataset.dtype) is not None:
        try:
            references = np.asarray(dataset[()]).reshape(-1)
        except OSError as error:
            raise MATRImportError(f"Unable to read {cell_id} summary/{name} reference") from error
        if references.size != 1 or not references[0]:
            raise MATRImportError(
                f"{cell_id} summary/{name} must contain exactly one valid object reference"
            )
        try:
            target = handle[references[0]]
        except (KeyError, ValueError) as error:
            raise MATRImportError(f"{cell_id} summary/{name} reference is invalid") from error
        if not isinstance(target, h5py.Dataset):
            raise MATRImportError(f"{cell_id} summary/{name} reference must target a dataset")
        storage_mode = "object_reference_to_numeric_dataset"
    else:
        target = dataset
        storage_mode = "direct_numeric_dataset"
    try:
        vector = np.asarray(target[()], dtype=np.float64).reshape(-1)
    except (OSError, TypeError, ValueError) as error:
        raise MATRImportError(f"{cell_id} summary/{name} must be a numeric vector") from error
    if vector.size == 0:
        raise MATRImportError(f"{cell_id} summary/{name} must not be empty")
    return vector, storage_mode


def _read_normalized_rows(
    input_path: Path, batch_id: str
) -> tuple[list[tuple[str, int, float]], str]:
    if not input_path.is_file():
        raise MATRImportError(f"MATR input file does not exist: {input_path.name}")
    try:
        is_hdf5 = h5py.is_hdf5(input_path)
    except OSError as error:
        raise MATRImportError(f"Unable to inspect MATR input: {input_path.name}") from error
    if not is_hdf5:
        raise MATRImportError("MATR input must be a MATLAB v7.3/HDF5 file")

    rows: list[tuple[str, int, float]] = []
    identities: set[tuple[str, int]] = set()
    observed_storage_mode: str | None = None
    try:
        with h5py.File(input_path, "r") as handle:
            if "batch" not in handle or not isinstance(handle["batch"], h5py.Group):
                raise MATRImportError("Required MATR structure batch/summary is missing")
            batch = handle["batch"]
            if "summary" not in batch or not isinstance(batch["summary"], h5py.Dataset):
                raise MATRImportError("Required MATR structure batch/summary is missing")
            summaries = batch["summary"]
            if h5py.check_dtype(ref=summaries.dtype) is None:
                raise MATRImportError("batch/summary must be an HDF5 object-reference array")

            try:
                references = np.asarray(summaries[()]).reshape(-1)
            except OSError as error:
                raise MATRImportError("Unable to read batch/summary reference array") from error
            if references.size == 0:
                raise MATRImportError("batch/summary reference array must not be empty")

            for source_index, reference in enumerate(references):
                cell_id = f"{batch_id}c{source_index}"
                if not reference:
                    raise MATRImportError(
                        f"batch/summary reference at source index {source_index} is null"
                    )
                try:
                    summary = handle[reference]
                except (KeyError, ValueError) as error:
                    raise MATRImportError(
                        f"batch/summary reference at source index {source_index} is invalid"
                    ) from error
                if not isinstance(summary, h5py.Group):
                    raise MATRImportError(f"{cell_id} batch/summary reference must target a group")

                cycles, cycle_storage = _read_vector(handle, summary, "cycle", cell_id=cell_id)
                capacities, capacity_storage = _read_vector(
                    handle, summary, "QDischarge", cell_id=cell_id
                )
                if cycle_storage != capacity_storage:
                    raise MATRImportError(
                        f"{cell_id} has an ambiguous mixed summary vector storage layout"
                    )
                if observed_storage_mode is None:
                    observed_storage_mode = cycle_storage
                elif observed_storage_mode != cycle_storage:
                    raise MATRImportError("MATR batch mixes summary vector storage layouts")
                if cycles.size != capacities.size:
                    raise MATRImportError(
                        f"{cell_id} summary/cycle and summary/QDischarge must have equal lengths"
                    )
                if not np.all(np.isfinite(cycles) & (cycles > 0) & (cycles == np.floor(cycles))):
                    raise MATRImportError(
                        f"{cell_id} summary/cycle values must be finite positive integers"
                    )
                if not np.all(np.isfinite(capacities) & (capacities > 0)):
                    raise MATRImportError(
                        f"{cell_id} summary/QDischarge values must be finite positive Ah values"
                    )

                for cycle_value, capacity_value in zip(cycles, capacities, strict=True):
                    cycle_index = int(cycle_value)
                    identity = (cell_id, cycle_index)
                    if identity in identities:
                        raise MATRImportError(
                            f"Duplicate cell/cycle identity detected: {cell_id}/{cycle_index}"
                        )
                    identities.add(identity)
                    rows.append((cell_id, cycle_index, float(capacity_value)))
    except MATRImportError:
        raise
    except OSError as error:
        raise MATRImportError(f"Unable to read MATR HDF5 file: {input_path.name}") from error

    rows.sort(key=lambda row: (row[0], row[1]))
    if observed_storage_mode is None:
        raise MATRImportError("batch/summary contains no readable cell summaries")
    return rows, observed_storage_mode


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def import_matr_hdf5(
    *,
    input_path: Path,
    output_dir: Path,
    batch_id: str,
    source_url: str,
    acquired_on: str,
    usage_terms_status: str,
) -> MATRImportResult:
    """Import a reviewed MATR HDF5 batch into a deterministic evidence bundle."""

    _validate_metadata(
        batch_id=batch_id,
        source_url=source_url,
        acquired_on=acquired_on,
        usage_terms_status=usage_terms_status,
    )
    if output_dir.exists():
        raise MATRImportError(f"MATR output directory already exists: {output_dir.name}")

    rows, vector_storage = _read_normalized_rows(input_path, batch_id)
    source_sha256 = _sha256_file(input_path)
    try:
        source_size = input_path.stat().st_size
    except OSError as error:
        raise MATRImportError(f"Unable to inspect MATR input: {input_path.name}") from error

    cell_count = len({row[0] for row in rows})
    cycle_count = len(rows)
    frame = pd.DataFrame(rows, columns=_NORMALIZED_COLUMNS).astype(
        {"cell_id": "string", "cycle_index": "int64", "capacity_ah": "float64"}
    )
    quality_report = {
        "status": "passed",
        "cell_count": cell_count,
        "cycle_count": cycle_count,
        "duplicate_identity_count": 0,
        "invalid_value_count": 0,
        "fields": list(_NORMALIZED_COLUMNS),
        "batch_id": batch_id,
    }

    temporary_dir = output_dir.with_name(f".{output_dir.name}.tmp-{uuid.uuid4().hex}")
    try:
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary_dir.mkdir(parents=False, exist_ok=False)
        normalized_path = temporary_dir / "cycles.csv"
        quality_report_path = temporary_dir / "quality_report.json"
        source_manifest_path = temporary_dir / "source_manifest.json"

        frame.to_csv(normalized_path, index=False, lineterminator="\n", float_format="%.17g")
        _write_json(quality_report_path, quality_report)

        outputs = {
            name: {
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for name, path in {
                "cycles.csv": normalized_path,
                "quality_report.json": quality_report_path,
            }.items()
        }
        manifest: dict[str, object] = {
            "schema_version": 1,
            "dataset": "MATR-Severson-2019",
            "batch_id": batch_id,
            "source": {
                "original_file_name": input_path.name,
                "file_size_bytes": source_size,
                "sha256": source_sha256,
                "source_url": source_url,
                "acquired_on": acquired_on,
                "usage_terms_status": usage_terms_status,
                "redistribution_claim": False,
            },
            "observed_contract": {
                "container": "MATLAB v7.3/HDF5",
                "summary_reference_path": "batch/summary",
                "cycle_path": "summary/cycle",
                "capacity_path": "summary/QDischarge",
                "summary_vector_storage": vector_storage,
            },
            "field_contract": _FIELD_CONTRACT,
            "statistics": {
                "cell_count": cell_count,
                "cycle_count": cycle_count,
                "minimum_cycle_index": min(row[1] for row in rows),
                "maximum_cycle_index": max(row[1] for row in rows),
            },
            "outputs": outputs,
            "unsupported_claims": [
                "No redistribution permission is inferred from public download access.",
                "No SOH, RUL, or model-performance claim is produced by this import.",
            ],
            "fingerprint_algorithm": "sha256-canonical-json-v1",
        }
        import_fingerprint = _canonical_fingerprint(manifest)
        manifest["import_fingerprint"] = import_fingerprint
        _write_json(source_manifest_path, manifest)

        if output_dir.exists():
            raise MATRImportError(f"MATR output directory already exists: {output_dir.name}")
        temporary_dir.rename(output_dir)
    except MATRImportError:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    except (OSError, ValueError) as error:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise MATRImportError(f"Unable to publish MATR import bundle: {output_dir.name}") from error

    return MATRImportResult(
        normalized_path=output_dir / "cycles.csv",
        quality_report_path=output_dir / "quality_report.json",
        source_manifest_path=output_dir / "source_manifest.json",
        import_fingerprint=import_fingerprint,
        cell_count=cell_count,
        cycle_count=cycle_count,
    )

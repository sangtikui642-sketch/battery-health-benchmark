"""Portable run records and deterministic prediction comparison."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

PREDICTION_COLUMNS = ("cell_id", "cycle_index", "actual_soh", "predicted_soh")
WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
REQUIRED_RUN_ARTIFACTS = {
    "input",
    "source_config",
    "effective_config",
    "split_manifest",
    "dependency_lock",
}


class ReproducibilityError(ValueError):
    """Raised when a portable, immutable run record cannot be created."""


@dataclass(frozen=True)
class ReproducibilityRecord:
    """Paths retained for one independently recorded run."""

    run_dir: Path
    manifest_path: Path
    effective_config_path: Path
    split_manifest_path: Path
    dependency_lock_path: Path
    predictions_path: Path


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 digest of a file."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ReproducibilityError(f"Unable to hash required file: {path.name}") from error
    return digest.hexdigest()


def _validate_required_files(paths: Mapping[str, Path]) -> None:
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ReproducibilityError("Missing reproducibility input(s): " + ", ".join(missing))


def _contains_absolute_path(value: object) -> bool:
    if isinstance(value, Path):
        return value.is_absolute()
    if isinstance(value, str):
        return value.startswith("/") or WINDOWS_ABSOLUTE_PATH.match(value) is not None
    if isinstance(value, Mapping):
        return any(_contains_absolute_path(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_absolute_path(item) for item in value)
    return False


def _serialized_config(effective_config: Mapping[str, object]) -> str:
    if _contains_absolute_path(effective_config):
        raise ReproducibilityError("Effective configuration contains an absolute path")
    try:
        return json.dumps(effective_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    except (TypeError, ValueError) as error:
        raise ReproducibilityError("Effective configuration is not JSON serializable") from error


def _git_revision(repository_path: Path) -> dict[str, str | None]:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return {"status": "unavailable", "revision": None}
    revision = completed.stdout.strip()
    if completed.returncode != 0 or not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        return {"status": "unavailable", "revision": None}
    return {"status": "available", "revision": revision.lower()}


def _copy(source: Path, destination: Path) -> None:
    try:
        shutil.copyfile(source, destination)
    except OSError as error:
        raise ReproducibilityError(f"Unable to retain required file: {source.name}") from error


def record_reproducible_run(
    *,
    run_dir: Path,
    effective_config: Mapping[str, object],
    input_path: Path,
    config_path: Path,
    split_manifest_path: Path,
    dependency_lock_path: Path,
    predictions_path: Path,
    random_seed: int,
    deterministic_tolerance: float,
    repository_path: Path,
) -> ReproducibilityRecord:
    """Create one non-overwriting run directory with hashes and portable snapshots."""

    required_files = {
        "input": input_path,
        "source_config": config_path,
        "split_manifest": split_manifest_path,
        "dependency_lock": dependency_lock_path,
        "predictions": predictions_path,
    }
    _validate_required_files(required_files)
    if not math.isfinite(deterministic_tolerance) or deterministic_tolerance < 0:
        raise ReproducibilityError("Deterministic tolerance must be finite and non-negative")
    serialized_config = _serialized_config(effective_config)
    if run_dir.exists():
        raise ReproducibilityError(f"Run directory already exists: {run_dir.name}")

    run_dir.mkdir(parents=True, exist_ok=False)
    effective_config_path = run_dir / "effective_config.json"
    retained_input_path = run_dir / ("input" + input_path.suffix.lower())
    retained_config_path = run_dir / "source_config.yaml"
    retained_split_path = run_dir / "split_manifest.json"
    retained_lock_path = run_dir / "uv.lock"
    retained_predictions_path = run_dir / "predictions.csv"
    manifest_path = run_dir / "reproducibility_manifest.json"

    effective_config_path.write_text(serialized_config, encoding="utf-8")
    _copy(input_path, retained_input_path)
    _copy(config_path, retained_config_path)
    _copy(split_manifest_path, retained_split_path)
    _copy(dependency_lock_path, retained_lock_path)
    _copy(predictions_path, retained_predictions_path)

    retained_files = {
        "input": retained_input_path,
        "source_config": retained_config_path,
        "effective_config": effective_config_path,
        "split_manifest": retained_split_path,
        "dependency_lock": retained_lock_path,
        "predictions": retained_predictions_path,
    }
    manifest: dict[str, object] = {
        "schema_version": 1,
        "random_seed": random_seed,
        "deterministic_tolerance": deterministic_tolerance,
        "files": {name: path.name for name, path in retained_files.items()},
        "sha256": {name: sha256_file(path) for name, path in retained_files.items()},
        "git": _git_revision(repository_path),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ReproducibilityRecord(
        run_dir=run_dir,
        manifest_path=manifest_path,
        effective_config_path=effective_config_path,
        split_manifest_path=retained_split_path,
        dependency_lock_path=retained_lock_path,
        predictions_path=retained_predictions_path,
    )


def write_run_manifest(
    *,
    run_dir: Path,
    artifact_paths: Mapping[str, Path],
    random_seed: int,
    deterministic_tolerance: float,
    repository_path: Path,
) -> Path:
    """Write provenance for artifacts already retained inside one run directory."""

    missing = sorted(REQUIRED_RUN_ARTIFACTS - artifact_paths.keys())
    if missing:
        raise ReproducibilityError(
            "Run manifest is missing required artifact(s): " + ", ".join(missing)
        )
    if not math.isfinite(deterministic_tolerance) or deterministic_tolerance < 0:
        raise ReproducibilityError("Deterministic tolerance must be finite and non-negative")
    resolved_run_dir = run_dir.resolve()
    portable_files: dict[str, str] = {}
    for name, artifact_path in sorted(artifact_paths.items()):
        if not artifact_path.is_file():
            raise ReproducibilityError(f"Missing run artifact: {name}")
        try:
            relative = artifact_path.resolve().relative_to(resolved_run_dir)
        except ValueError as error:
            raise ReproducibilityError(
                f"Run artifact is outside the run directory: {name}"
            ) from error
        portable_files[name] = relative.as_posix()

    manifest_path = run_dir / "reproducibility_manifest.json"
    if manifest_path.exists():
        raise ReproducibilityError("Reproducibility manifest already exists")
    manifest: dict[str, object] = {
        "schema_version": 1,
        "random_seed": random_seed,
        "deterministic_tolerance": deterministic_tolerance,
        "files": portable_files,
        "sha256": {name: sha256_file(artifact_paths[name]) for name in sorted(artifact_paths)},
        "git": _git_revision(repository_path),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _prediction_frame(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError) as error:
        raise ReproducibilityError(f"Unable to read predictions: {path.name}") from error
    missing = [column for column in PREDICTION_COLUMNS if column not in frame.columns]
    if missing:
        raise ReproducibilityError(
            "Predictions are missing required column(s): " + ", ".join(missing)
        )
    return frame.loc[:, list(PREDICTION_COLUMNS)].sort_values(
        ["cell_id", "cycle_index"], ignore_index=True
    )


def predictions_agree(first_path: Path, second_path: Path, *, tolerance: float) -> bool:
    """Return whether identifiers and numeric predictions agree within tolerance."""

    if not math.isfinite(tolerance) or tolerance < 0:
        raise ReproducibilityError("Deterministic tolerance must be finite and non-negative")
    first = _prediction_frame(first_path)
    second = _prediction_frame(second_path)
    if len(first) != len(second):
        return False
    if not first[["cell_id", "cycle_index"]].equals(second[["cell_id", "cycle_index"]]):
        return False
    first_values = first[["actual_soh", "predicted_soh"]].apply(pd.to_numeric, errors="coerce")
    second_values = second[["actual_soh", "predicted_soh"]].apply(pd.to_numeric, errors="coerce")
    first_array = first_values.to_numpy(dtype="float64")
    second_array = second_values.to_numpy(dtype="float64")
    if not np.isfinite(first_array).all() or not np.isfinite(second_array).all():
        return False
    return bool(np.allclose(first_array, second_array, rtol=0.0, atol=tolerance))

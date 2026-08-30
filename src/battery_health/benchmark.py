"""Offline orchestration for the synthetic SOH benchmark."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from battery_health.data.importer import DataImportError, import_cycle_csv
from battery_health.data.labels import SOHLabelError, generate_soh_labels
from battery_health.data.quality import DataQualityError, validate_cycle_data
from battery_health.data.split import SplitValidationError, create_group_split
from battery_health.demo import DemoDataError, generate_anonymous_cycle_csv
from battery_health.evaluation import EvaluationError, evaluate_predictions
from battery_health.modeling.baselines import (
    SUPPORTED_MODELS,
    BaselineTrainingError,
    train_baseline,
)
from battery_health.reproducibility import (
    ReproducibilityError,
    sha256_file,
    write_run_manifest,
)
from battery_health.safety import RepositorySafetyError, check_repository_safety

ModelName = Literal[
    "linear_regression",
    "random_forest",
    "histogram_gradient_boosting",
]


class BenchmarkError(ValueError):
    """Raised when the offline benchmark cannot complete truthfully."""


class StrictConfigModel(BaseModel):
    """Shared strict settings for JSON-compatible YAML configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class SyntheticDatasetConfig(StrictConfigModel):
    """Anonymous deterministic dataset settings."""

    kind: Literal["synthetic"]
    random_seed: int
    cell_count: int = Field(ge=3)
    cycles_per_cell: int = Field(ge=2)
    reference_capacity_ah: float = Field(gt=0)


class FieldMappingConfig(StrictConfigModel):
    """Configured source-to-normalized cycle fields."""

    cell_id: str
    cycle_index: str
    capacity_ah: str


class SplitConfig(StrictConfigModel):
    """Cell-level split policy."""

    random_seed: int
    train_fraction: float = Field(gt=0, lt=1)
    validation_fraction: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def leaves_a_test_partition(self) -> Self:
        """Require an explicit held-out fraction."""

        if self.train_fraction + self.validation_fraction >= 1:
            raise ValueError("train and validation fractions must leave a test partition")
        return self


class BenchmarkConfig(StrictConfigModel):
    """Validated effective configuration for the MVP benchmark."""

    schema_version: Literal[1]
    target: Literal["soh"]
    dataset: SyntheticDatasetConfig
    field_mapping: FieldMappingConfig
    split: SplitConfig
    feature_columns: tuple[str, ...] = Field(min_length=1)
    feature_units: dict[str, str]
    models: tuple[ModelName, ...] = Field(min_length=3, max_length=3)
    model_random_seed: int
    deterministic_tolerance: float = Field(ge=0)

    @field_validator("feature_columns")
    @classmethod
    def features_are_unique_and_leakage_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject duplicate features and direct target leakage."""

        if len(set(value)) != len(value):
            raise ValueError("feature columns must be unique")
        if "soh" in value:
            raise ValueError("SOH cannot be used as an input feature")
        return value

    @field_validator("models")
    @classmethod
    def models_are_the_registered_mvp_set(
        cls, value: tuple[ModelName, ...]
    ) -> tuple[ModelName, ...]:
        """Require all three reviewed baselines exactly once."""

        if len(set(value)) != len(value) or set(value) != set(SUPPORTED_MODELS):
            raise ValueError("models must contain each registered MVP baseline exactly once")
        return value

    @model_validator(mode="after")
    def feature_units_are_explicit(self) -> Self:
        """Require one configured unit for every model feature."""

        if set(self.feature_units) != set(self.feature_columns):
            raise ValueError("feature units must exactly match feature columns")
        if any(not unit.strip() for unit in self.feature_units.values()):
            raise ValueError("feature units must not be empty")
        return self


@dataclass(frozen=True)
class BenchmarkResult:
    """Top-level artifacts generated only after a successful run."""

    output_dir: Path
    report_path: Path
    reproducibility_manifest_path: Path


def load_benchmark_config(config_path: Path) -> BenchmarkConfig:
    """Load strict JSON-compatible YAML without adding a YAML dependency."""

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        return BenchmarkConfig.model_validate(raw)
    except OSError as error:
        raise BenchmarkError(f"Unable to read configuration: {config_path.name}") from error
    except json.JSONDecodeError as error:
        raise BenchmarkError("Configuration must use JSON-compatible YAML syntax") from error
    except ValidationError as error:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise BenchmarkError("Invalid configuration field(s): " + ", ".join(fields)) from error


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _limitations() -> dict[str, object]:
    return {
        "schema_version": 1,
        "claims_status": "supported only by artifacts from this synthetic run",
        "limitations": [
            "The executable dataset is anonymous deterministic synthetic data, not MATR.",
            "A real MATR adapter has not been implemented or validated.",
            "Trained model serialization and loading are not implemented.",
            "Automatic model selection is not performed; test metrics do not choose a winner.",
            "FR-11 and later candidate selection and final validation are not implemented.",
        ],
        "unsupported_claims": [
            "real-world battery accuracy",
            "production BMS readiness",
            "cross-dataset generalization",
        ],
    }


def _repository_root(repository_path: Path | None) -> Path:
    return Path(__file__).resolve().parents[2] if repository_path is None else repository_path


def run_benchmark(
    *,
    config_path: Path,
    output_dir: Path,
    repository_path: Path | None = None,
) -> BenchmarkResult:
    """Run import-through-evaluation for three baselines without network access."""

    config = load_benchmark_config(config_path)
    if output_dir.exists():
        raise BenchmarkError(f"Output directory already exists: {output_dir.name}")
    repository_root = _repository_root(repository_path)
    dependency_lock_source = repository_root / "uv.lock"
    if not dependency_lock_source.is_file():
        raise BenchmarkError("Required dependency lock is unavailable")

    stage = "initialization"
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
        stage = "repository safety check"
        safety_report_path = output_dir / "safety_report.json"
        check_repository_safety(
            repository_root=repository_root,
            report_path=safety_report_path,
        )

        stage = "configuration retention"
        source_config_path = output_dir / "source_config.yaml"
        effective_config_path = output_dir / "effective_config.json"
        dependency_lock_path = output_dir / "uv.lock"
        shutil.copyfile(config_path, source_config_path)
        shutil.copyfile(dependency_lock_source, dependency_lock_path)
        effective_config = config.model_dump(mode="json")
        _write_json(effective_config_path, effective_config)

        stage = "synthetic data generation"
        input_path = generate_anonymous_cycle_csv(
            output_path=output_dir / "input.csv",
            random_seed=config.dataset.random_seed,
            cell_count=config.dataset.cell_count,
            cycles_per_cell=config.dataset.cycles_per_cell,
            reference_capacity_ah=config.dataset.reference_capacity_ah,
        )

        stage = "data import"
        import_result = import_cycle_csv(
            input_path=input_path,
            output_dir=output_dir / "data" / "normalized",
            field_mapping=config.field_mapping.model_dump(),
        )

        stage = "data quality validation"
        quality_report_path = output_dir / "data" / "quality_report.json"
        validate_cycle_data(
            data_path=import_result.normalized_path,
            report_path=quality_report_path,
        )

        stage = "cell-level split"
        normalized = pd.read_csv(import_result.normalized_path)
        split_manifest_path = output_dir / "split_manifest.json"
        split_manifest = create_group_split(
            cell_ids=normalized["cell_id"].astype(str),
            manifest_path=split_manifest_path,
            random_seed=config.split.random_seed,
            train_fraction=config.split.train_fraction,
            validation_fraction=config.split.validation_fraction,
        )
        split_hash = sha256_file(split_manifest_path)

        stage = "SOH label generation"
        labels = generate_soh_labels(
            data_path=import_result.normalized_path,
            output_dir=output_dir / "data" / "labelled",
            reference_capacity_ah=config.dataset.reference_capacity_ah,
            reference_capacity_source="configuration:dataset.reference_capacity_ah",
        )

        model_evidence: dict[str, dict[str, str]] = {}
        artifact_paths: dict[str, Path] = {
            "input": input_path,
            "source_config": source_config_path,
            "effective_config": effective_config_path,
            "split_manifest": split_manifest_path,
            "dependency_lock": dependency_lock_path,
            "quality_report": quality_report_path,
            "soh_labels": labels.labelled_cycles_path,
            "soh_metadata": labels.metadata_path,
            "safety_report": safety_report_path,
        }
        for model_name in config.models:
            stage = f"training {model_name}"
            model_dir = output_dir / "models" / model_name
            training = train_baseline(
                data_path=labels.labelled_cycles_path,
                split_manifest=split_manifest,
                output_dir=model_dir,
                model_name=model_name,
                feature_columns=config.feature_columns,
                random_seed=config.model_random_seed,
            )
            stage = f"evaluating {model_name}"
            evaluation = evaluate_predictions(
                predictions_path=training.predictions_path,
                split_manifest=split_manifest,
                output_dir=model_dir,
            )
            prefix = f"model_{model_name}"
            artifact_paths[f"{prefix}_predictions"] = evaluation.predictions_path
            artifact_paths[f"{prefix}_metrics"] = evaluation.metrics_path
            artifact_paths[f"{prefix}_plot"] = evaluation.plot_path
            artifact_paths[f"{prefix}_training_metadata"] = training.run_metadata_path
            relative_model_dir = Path("models") / model_name
            model_evidence[model_name] = {
                "split_manifest": "split_manifest.json",
                "split_manifest_sha256": split_hash,
                "training_metadata": (relative_model_dir / "run_metadata.json").as_posix(),
                "predictions": (relative_model_dir / "predictions.csv").as_posix(),
                "metrics": (relative_model_dir / "metrics.json").as_posix(),
                "actual_vs_predicted_plot": (
                    relative_model_dir / "actual_vs_predicted.png"
                ).as_posix(),
            }

        stage = "limitations retention"
        limitations_path = output_dir / "limitations.json"
        _write_json(limitations_path, _limitations())
        artifact_paths["limitations"] = limitations_path

        stage = "reproducibility manifest"
        reproducibility_manifest_path = write_run_manifest(
            run_dir=output_dir,
            artifact_paths=artifact_paths,
            random_seed=config.model_random_seed,
            deterministic_tolerance=config.deterministic_tolerance,
            repository_path=repository_root,
        )

        stage = "final report"
        report_path = output_dir / "benchmark_report.json"
        report = {
            "schema_version": 1,
            "status": "completed",
            "target": "soh",
            "data_source": "anonymous_deterministic_synthetic",
            "split_strategy": "group_by_cell",
            "models": model_evidence,
            "selection_status": "not_performed",
            "selection_reason": (
                "Test metrics are retained as evidence and never used to select a model."
            ),
            "reproducibility_manifest": reproducibility_manifest_path.name,
            "limitations": limitations_path.name,
        }
        _write_json(report_path, report)
    except (
        DataImportError,
        DataQualityError,
        SplitValidationError,
        SOHLabelError,
        BaselineTrainingError,
        EvaluationError,
        ReproducibilityError,
        RepositorySafetyError,
        DemoDataError,
    ) as error:
        raise BenchmarkError(f"{stage} failed: {error}") from error
    except OSError as error:
        raise BenchmarkError(f"{stage} failed with a filesystem error") from error

    return BenchmarkResult(
        output_dir=output_dir,
        report_path=report_path,
        reproducibility_manifest_path=reproducibility_manifest_path,
    )

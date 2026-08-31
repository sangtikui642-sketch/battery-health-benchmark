"""Battery dataset ingestion and validation."""

from battery_health.data.importer import DataImportError, ImportResult, import_cycle_csv
from battery_health.data.matr import MATRImportError, MATRImportResult, import_matr_hdf5
from battery_health.data.quality import DataQualityError, QualityReport, validate_cycle_data
from battery_health.data.split import (
    SplitManifest,
    SplitValidationError,
    create_group_split,
    validate_split_manifest,
)

__all__ = [
    "DataImportError",
    "DataQualityError",
    "ImportResult",
    "MATRImportError",
    "MATRImportResult",
    "QualityReport",
    "SplitManifest",
    "SplitValidationError",
    "create_group_split",
    "import_cycle_csv",
    "import_matr_hdf5",
    "validate_cycle_data",
    "validate_split_manifest",
]

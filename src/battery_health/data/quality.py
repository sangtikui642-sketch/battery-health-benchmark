"""Quality validation for normalized battery-cycle data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

import pandas as pd

REQUIRED_COLUMNS = ("cell_id", "cycle_index", "capacity_ah")


class QualityIssue(TypedDict):
    """A machine-readable problem associated with one cycle."""

    code: str
    cell_id: str
    cycle_index: int


class QualityReport(TypedDict):
    """Summary emitted for every completed validation attempt."""

    status: str
    input_file: str
    cell_count: int
    cycle_count: int
    missing_value_count: int
    duplicate_cycle_count: int
    invalid_capacity_count: int
    issues: list[QualityIssue]


class DataQualityError(ValueError):
    """Raised after a failing quality report has been written."""


def _read_cycles(data_path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(data_path)
    except (OSError, pd.errors.ParserError) as error:
        raise DataQualityError(f"Unable to read normalized data: {data_path.name}") from error

    missing_columns = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing_columns:
        joined = ", ".join(missing_columns)
        raise DataQualityError(f"Normalized data is missing required column(s): {joined}")
    return frame


def _quality_report(frame: pd.DataFrame, input_file: str) -> QualityReport:
    duplicate_mask = frame.duplicated(subset=["cell_id", "cycle_index"], keep=False)
    numeric_capacity = pd.to_numeric(frame["capacity_ah"], errors="coerce")
    invalid_capacity_mask = numeric_capacity.isna() | (numeric_capacity <= 0)

    issues: list[QualityIssue] = []
    duplicate_pairs = frame.loc[duplicate_mask, ["cell_id", "cycle_index"]].drop_duplicates()
    for row in duplicate_pairs.itertuples(index=False):
        issues.append(
            {
                "code": "duplicate_cycle",
                "cell_id": str(row.cell_id),
                "cycle_index": int(float(str(row.cycle_index))),
            }
        )

    invalid_rows = frame.loc[invalid_capacity_mask, ["cell_id", "cycle_index"]]
    for row in invalid_rows.itertuples(index=False):
        issues.append(
            {
                "code": "non_positive_capacity",
                "cell_id": str(row.cell_id),
                "cycle_index": int(float(str(row.cycle_index))),
            }
        )

    failed = bool(duplicate_mask.any() or invalid_capacity_mask.any())
    return {
        "status": "failed" if failed else "passed",
        "input_file": input_file,
        "cell_count": int(frame["cell_id"].nunique()),
        "cycle_count": int(len(frame)),
        "missing_value_count": int(frame[list(REQUIRED_COLUMNS)].isna().sum().sum()),
        "duplicate_cycle_count": int(duplicate_mask.sum()),
        "invalid_capacity_count": int(invalid_capacity_mask.sum()),
        "issues": issues,
    }


def validate_cycle_data(*, data_path: Path, report_path: Path) -> QualityReport:
    """Validate normalized cycles and persist a report before returning or failing."""

    frame = _read_cycles(data_path)
    report = _quality_report(frame, data_path.name)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if report["status"] == "failed":
        raise DataQualityError(
            f"Cycle data failed quality validation; inspect {report_path.name} for details"
        )
    return report

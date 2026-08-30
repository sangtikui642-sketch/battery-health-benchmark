"""Deterministic anonymous battery-cycle fixtures for the offline demo."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd


class DemoDataError(ValueError):
    """Raised when an anonymous demo dataset cannot be generated safely."""


def generate_anonymous_cycle_csv(
    *,
    output_path: Path,
    random_seed: int,
    cell_count: int,
    cycles_per_cell: int,
    reference_capacity_ah: float,
) -> Path:
    """Generate a byte-repeatable anonymous cycle table without network access."""

    if cell_count < 3:
        raise DemoDataError("At least three anonymous cells are required")
    if cycles_per_cell < 2:
        raise DemoDataError("At least two cycles per anonymous cell are required")
    if not math.isfinite(reference_capacity_ah) or reference_capacity_ah <= 0:
        raise DemoDataError("Reference capacity must be finite and positive")

    random = np.random.default_rng(random_seed)
    rows: list[dict[str, object]] = []
    for cell_index in range(cell_count):
        cell_offset = float(random.normal(0.0, 0.0025))
        degradation_rate = 0.0045 + 0.0002 * (cell_index % 4)
        for cycle_index in range(1, cycles_per_cell + 1):
            measurement_noise = float(random.normal(0.0, 0.0007))
            soh = 1.0 + cell_offset - degradation_rate * cycle_index + measurement_noise
            rows.append(
                {
                    "cell": f"cell-{cell_index:03d}",
                    "cycle": cycle_index,
                    "capacity": reference_capacity_ah * soh,
                }
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(
        output_path,
        index=False,
        float_format="%.10f",
        lineterminator="\n",
    )
    return output_path

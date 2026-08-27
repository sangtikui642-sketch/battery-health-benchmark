"""Deterministic, leakage-safe partitioning by battery-cell identity."""

from __future__ import annotations

import json
import random
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path


class SplitValidationError(ValueError):
    """Raised when a split manifest is incomplete or leaks a cell identity."""


@dataclass(frozen=True)
class SplitManifest:
    """Cell identities assigned to deterministic experiment partitions."""

    strategy: str
    random_seed: int
    train_cells: tuple[str, ...]
    validation_cells: tuple[str, ...]
    test_cells: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation."""

        return asdict(self)


def _duplicates(values: tuple[str, ...]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def validate_split_manifest(manifest: SplitManifest) -> None:
    """Reject empty partitions, repeated cells, and cross-partition leakage."""

    partitions = {
        "train": manifest.train_cells,
        "validation": manifest.validation_cells,
        "test": manifest.test_cells,
    }
    empty = [name for name, cells in partitions.items() if not cells]
    if empty:
        raise SplitValidationError(f"Split contains empty partition(s): {', '.join(empty)}")

    repeated = set().union(*(_duplicates(cells) for cells in partitions.values()))
    if repeated:
        raise SplitValidationError(
            "Split repeats cell(s) within a partition: " + ", ".join(sorted(repeated))
        )

    train = set(manifest.train_cells)
    validation = set(manifest.validation_cells)
    test = set(manifest.test_cells)
    overlapping = (train & validation) | (train & test) | (validation & test)
    if overlapping:
        raise SplitValidationError(
            "Cell leakage detected across partitions: " + ", ".join(sorted(overlapping))
        )


def create_group_split(
    *,
    cell_ids: Iterable[str],
    manifest_path: Path,
    random_seed: int,
    train_fraction: float,
    validation_fraction: float,
) -> SplitManifest:
    """Assign unique cells to train, validation, and test partitions."""

    if not 0 < train_fraction < 1:
        raise SplitValidationError("train_fraction must be between 0 and 1")
    if not 0 < validation_fraction < 1:
        raise SplitValidationError("validation_fraction must be between 0 and 1")
    if train_fraction + validation_fraction >= 1:
        raise SplitValidationError("train and validation fractions must leave a test partition")

    unique_cells = sorted(set(cell_ids))
    if len(unique_cells) < 3:
        raise SplitValidationError("At least three unique cells are required")

    shuffled = unique_cells.copy()
    random.Random(random_seed).shuffle(shuffled)

    train_count = max(1, int(len(shuffled) * train_fraction))
    validation_count = max(1, int(len(shuffled) * validation_fraction))
    if train_count + validation_count >= len(shuffled):
        raise SplitValidationError("Requested fractions leave no cell for the test partition")

    validation_end = train_count + validation_count
    manifest = SplitManifest(
        strategy="group_by_cell",
        random_seed=random_seed,
        train_cells=tuple(sorted(shuffled[:train_count])),
        validation_cells=tuple(sorted(shuffled[train_count:validation_end])),
        test_cells=tuple(sorted(shuffled[validation_end:])),
    )
    validate_split_manifest(manifest)

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest

from pathlib import Path

import pandas as pd
import pytest

from battery_health.demo import DemoDataError, generate_anonymous_cycle_csv


def test_demo_generator_rejects_too_few_cells(tmp_path: Path) -> None:
    with pytest.raises(DemoDataError, match="three anonymous cells"):
        generate_anonymous_cycle_csv(
            output_path=tmp_path / "demo.csv",
            random_seed=1,
            cell_count=2,
            cycles_per_cell=4,
            reference_capacity_ah=2.0,
        )


def test_demo_generator_has_expected_anonymous_shape(tmp_path: Path) -> None:
    output_path = generate_anonymous_cycle_csv(
        output_path=tmp_path / "demo.csv",
        random_seed=11,
        cell_count=5,
        cycles_per_cell=3,
        reference_capacity_ah=2.0,
    )

    frame = pd.read_csv(output_path)
    assert len(frame) == 15
    assert sorted(frame["cell"].unique()) == [f"cell-{index:03d}" for index in range(5)]
    assert (frame["capacity"] > 0).all()

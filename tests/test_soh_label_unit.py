import json
from pathlib import Path

import pandas as pd
import pytest

from battery_health.data.labels import SOHLabelError, generate_soh_labels


def test_uses_reference_capacity_from_source_data(tmp_path: Path) -> None:
    data_path = tmp_path / "cycles.csv"
    pd.DataFrame(
        [
            {
                "cell_id": "cell-a",
                "cycle_index": 1,
                "capacity_ah": 1.8,
                "reference_capacity_ah": 2.0,
            },
            {
                "cell_id": "cell-b",
                "cycle_index": 1,
                "capacity_ah": 2.7,
                "reference_capacity_ah": 3.0,
            },
        ]
    ).to_csv(data_path, index=False)

    result = generate_soh_labels(data_path=data_path, output_dir=tmp_path / "labelled")

    labelled = pd.read_csv(result.labelled_cycles_path)
    assert labelled["soh"].tolist() == pytest.approx([0.9, 0.9])
    assert labelled["reference_capacity_source"].unique().tolist() == [
        "source_data:reference_capacity_ah"
    ]
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["reference_capacity_mode"] == "source_data_column"


def test_rejects_configured_capacity_without_source(tmp_path: Path) -> None:
    data_path = tmp_path / "cycles.csv"
    pd.DataFrame([{"cell_id": "cell-a", "cycle_index": 1, "capacity_ah": 1.8}]).to_csv(
        data_path, index=False
    )
    output_dir = tmp_path / "labelled"

    with pytest.raises(SOHLabelError, match="explicit source"):
        generate_soh_labels(
            data_path=data_path,
            output_dir=output_dir,
            reference_capacity_ah=2.0,
        )

    assert not output_dir.exists()


def test_metadata_contains_only_portable_input_name(tmp_path: Path) -> None:
    data_path = tmp_path / "private-machine-folder" / "cycles.csv"
    data_path.parent.mkdir()
    pd.DataFrame([{"cell_id": "cell-a", "cycle_index": 1, "capacity_ah": 1.8}]).to_csv(
        data_path, index=False
    )

    result = generate_soh_labels(
        data_path=data_path,
        output_dir=tmp_path / "labelled",
        reference_capacity_ah=2.0,
        reference_capacity_source="configuration:nominal_capacity_ah",
    )

    metadata_text = result.metadata_path.read_text(encoding="utf-8")
    assert str(data_path.resolve()) not in metadata_text
    assert json.loads(metadata_text)["input_file"] == "cycles.csv"

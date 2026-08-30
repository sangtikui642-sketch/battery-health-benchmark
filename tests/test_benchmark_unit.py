import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from battery_health.benchmark import BenchmarkError, load_benchmark_config, run_benchmark
from battery_health.cli import app
from battery_health.reproducibility import predictions_agree


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_demo_config_is_strict_soh_only_and_has_three_models() -> None:
    config = load_benchmark_config(_repository_root() / "configs" / "demo.yaml")

    assert config.target == "soh"
    assert len(config.models) == 3
    assert "soh" not in config.feature_columns


def test_invalid_config_does_not_create_a_success_report(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text('{"schema_version": 1, "target": "rul"}\n', encoding="utf-8")
    output_dir = tmp_path / "failed-run"
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["benchmark", "--config", str(config_path), "--output", str(output_dir)],
    )

    assert result.exit_code != 0
    assert not (output_dir / "benchmark_report.json").exists()


def test_existing_output_directory_is_never_overwritten(tmp_path: Path) -> None:
    output_dir = tmp_path / "existing"
    output_dir.mkdir()
    marker = output_dir / "user-file.txt"
    marker.write_text("preserve me\n", encoding="utf-8")

    with pytest.raises(BenchmarkError, match="already exists"):
        run_benchmark(
            config_path=_repository_root() / "configs" / "demo.yaml",
            output_dir=output_dir,
            repository_path=_repository_root(),
        )

    assert marker.read_text(encoding="utf-8") == "preserve me\n"


def test_effective_demo_config_contains_no_local_path() -> None:
    config_path = _repository_root() / "configs" / "demo.yaml"
    config = load_benchmark_config(config_path)
    serialized = json.dumps(config.model_dump(mode="json"))

    assert str(_repository_root().resolve()) not in serialized


def test_two_complete_runs_repeat_all_model_predictions(tmp_path: Path) -> None:
    repository_root = _repository_root()
    config_path = repository_root / "configs" / "demo.yaml"
    first = run_benchmark(
        config_path=config_path,
        output_dir=tmp_path / "run-one",
        repository_path=repository_root,
    )
    second = run_benchmark(
        config_path=config_path,
        output_dir=tmp_path / "run-two",
        repository_path=repository_root,
    )
    config = load_benchmark_config(config_path)

    for model_name in config.models:
        assert predictions_agree(
            first.output_dir / "models" / model_name / "predictions.csv",
            second.output_dir / "models" / model_name / "predictions.csv",
            tolerance=config.deterministic_tolerance,
        )

    first_manifest = json.loads(first.reproducibility_manifest_path.read_text(encoding="utf-8"))
    second_manifest = json.loads(second.reproducibility_manifest_path.read_text(encoding="utf-8"))
    for artifact_name in ("input", "source_config", "split_manifest", "dependency_lock"):
        assert first_manifest["sha256"][artifact_name] == second_manifest["sha256"][artifact_name]

import json
from pathlib import Path
from typing import Any

from pytest_bdd import given, scenarios, then, when
from typer.testing import CliRunner

from battery_health.cli import app

scenarios("../../features/cli_benchmark.feature")

MODELS = (
    "linear_regression",
    "random_forest",
    "histogram_gradient_boosting",
)


@given("an offline deterministic demo configuration", target_fixture="benchmark_context")
def demo_configuration(tmp_path: Path) -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[2]
    return {
        "repository_root": repository_root,
        "config_path": repository_root / "configs" / "demo.yaml",
        "output_dir": tmp_path / "benchmark-run",
        "result": None,
    }


@when("the researcher runs the benchmark command")
def run_benchmark(benchmark_context: dict[str, Any]) -> None:
    runner = CliRunner()
    benchmark_context["result"] = runner.invoke(
        app,
        [
            "benchmark",
            "--config",
            str(benchmark_context["config_path"]),
            "--output",
            str(benchmark_context["output_dir"]),
        ],
    )


@then("the command succeeds without network access")
def benchmark_succeeds(benchmark_context: dict[str, Any]) -> None:
    result = benchmark_context["result"]
    assert result.exit_code == 0, result.output
    report = json.loads(
        (benchmark_context["output_dir"] / "benchmark_report.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "completed"
    assert report["data_source"] == "anonymous_deterministic_synthetic"


@then("all three registered baselines reference the same split manifest")
def models_share_one_split(benchmark_context: dict[str, Any]) -> None:
    report = json.loads(
        (benchmark_context["output_dir"] / "benchmark_report.json").read_text(encoding="utf-8")
    )
    assert set(report["models"]) == set(MODELS)
    references = {details["split_manifest"] for details in report["models"].values()}
    hashes = {details["split_manifest_sha256"] for details in report["models"].values()}
    assert references == {"split_manifest.json"}
    assert len(hashes) == 1


@then("every model retains predictions metrics and a comparison plot")
def model_evidence_is_complete(benchmark_context: dict[str, Any]) -> None:
    for model_name in MODELS:
        model_dir = benchmark_context["output_dir"] / "models" / model_name
        assert (model_dir / "predictions.csv").is_file()
        assert (model_dir / "metrics.json").is_file()
        assert (model_dir / "actual_vs_predicted.png").is_file()


@then("the run retains reproducibility evidence and limitations without selecting a winner")
def run_is_traceable_without_test_selection(benchmark_context: dict[str, Any]) -> None:
    output_dir = benchmark_context["output_dir"]
    manifest_text = (output_dir / "reproducibility_manifest.json").read_text(encoding="utf-8")
    limitations_text = (output_dir / "limitations.json").read_text(encoding="utf-8")
    report_text = (output_dir / "benchmark_report.json").read_text(encoding="utf-8")
    combined = manifest_text + limitations_text + report_text
    assert "selection_status" in report_text
    assert json.loads(report_text)["selection_status"] == "not_performed"
    assert "best_model" not in report_text
    assert "winner" not in report_text.lower()
    assert str(output_dir.resolve()) not in combined

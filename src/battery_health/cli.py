"""Command-line interface for the battery health benchmark."""

from pathlib import Path
from typing import Annotated

import typer

from battery_health.agent.execution import ExecutionError, execute_experiment_plan
from battery_health.agent.governance import (
    PluginGovernanceError,
    govern_model_plugins,
    verify_governance_bundle,
)
from battery_health.agent.planning import PlanningError, create_experiment_plan
from battery_health.agent.reporting import (
    ReportError,
    generate_evidence_bundle,
    verify_evidence_bundle,
)
from battery_health.agent.validation import (
    ValidationError,
    finalize_locked_selection,
    validate_and_lock_execution,
)
from battery_health.benchmark import BenchmarkError, run_benchmark

app = typer.Typer(
    help="Reproducible battery state-of-health benchmarking tools.",
    no_args_is_help=True,
)
agent_app = typer.Typer(
    help=(
        "Deterministic AutoBench plugin governance, planning, execution, validation, "
        "finalization, and evidence."
    ),
    no_args_is_help=True,
)
app.add_typer(agent_app, name="agent")


@app.command()
def version() -> None:
    """Print the package version."""

    from battery_health import __version__

    typer.echo(__version__)


@app.command()
def benchmark(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="JSON-compatible YAML benchmark configuration.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            help="New directory for the complete evidence bundle.",
        ),
    ],
) -> None:
    """Run the offline deterministic synthetic SOH benchmark."""

    try:
        run_benchmark(config_path=config, output_dir=output)
    except BenchmarkError as error:
        typer.echo(f"Benchmark failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo("Benchmark completed. Report: benchmark_report.json")


@agent_app.command("plan")
def agent_plan(
    evidence: Annotated[
        Path,
        typer.Option(
            "--evidence",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="Completed portable benchmark evidence directory.",
        ),
    ],
    registry: Annotated[
        Path,
        typer.Option(
            "--registry",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Reviewed model-plugin registry JSON.",
        ),
    ],
    policy: Annotated[
        Path,
        typer.Option(
            "--policy",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="JSON-compatible YAML resource and validation policy.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            help="New directory for immutable planning artifacts.",
        ),
    ],
) -> None:
    """Create an immutable PLANNED experiment without training candidates."""

    try:
        create_experiment_plan(
            evidence_dir=evidence,
            registry_path=registry,
            policy_path=policy,
            output_dir=output,
        )
    except PlanningError as error:
        typer.echo(f"Planning failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo("Experiment planned. Artifact: plan.json")


@agent_app.command("govern-plugins")
def agent_govern_plugins(
    catalog: Annotated[
        Path,
        typer.Option(
            "--catalog",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Complete FR-13 model-plugin catalog JSON.",
        ),
    ],
    policy: Annotated[
        Path,
        typer.Option(
            "--policy",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Reviewed FR-13 compatibility and license policy JSON.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            help="New directory for governed declarations and the approved registry.",
        ),
    ],
) -> None:
    """Approve, reject, or quarantine plugin declarations before planning."""

    try:
        result = govern_model_plugins(
            catalog_path=catalog,
            policy_path=policy,
            output_dir=output,
        )
    except PluginGovernanceError as error:
        typer.echo(f"Plugin governance failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"state=GOVERNED approved={result.approved} rejected={result.rejected} "
        f"quarantined={result.quarantined} "
        f"governance_fingerprint={result.governance_fingerprint} "
        f"manifest={result.manifest_path.name} next=agent verify-plugins"
    )


@agent_app.command("verify-plugins")
def agent_verify_plugins(
    bundle: Annotated[
        Path,
        typer.Option(
            "--bundle",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="FR-13 governed plugin bundle to verify offline.",
        ),
    ],
) -> None:
    """Verify FR-13 declarations, decisions, hashes, and quarantine semantics offline."""

    try:
        result = verify_governance_bundle(bundle)
    except PluginGovernanceError as error:
        typer.echo(f"Plugin governance verification failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"valid=true governance_fingerprint={result.governance_fingerprint} "
        f"approved={result.approved} rejected={result.rejected} "
        f"quarantined={result.quarantined} next=none"
    )


@agent_app.command("execute")
def agent_execute(
    plan: Annotated[
        Path,
        typer.Option(
            "--plan",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="Directory containing an immutable FR-09 plan bundle.",
        ),
    ],
    evidence: Annotated[
        Path,
        typer.Option(
            "--evidence",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="Portable benchmark evidence used to create the plan.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            help="New directory for candidate execution evidence.",
        ),
    ],
    request: Annotated[
        Path | None,
        typer.Option(
            "--request",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Optional JSON request that may only narrow locked resource limits.",
        ),
    ] = None,
) -> None:
    """Execute every locked candidate on validation data without test access."""

    try:
        result = execute_experiment_plan(
            plan_dir=plan,
            evidence_dir=evidence,
            output_dir=output,
            request_path=request,
        )
    except ExecutionError as error:
        typer.echo(f"Execution failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    if result.execution_status == "policy_violation":
        typer.echo("Execution rejected by locked policy. See: policy_violation.json", err=True)
        raise typer.Exit(code=1)
    typer.echo("Candidate execution completed. Manifest: execution_manifest.json")


@agent_app.command("validate")
def agent_validate(
    execution: Annotated[
        Path,
        typer.Option(
            "--execution",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="Completed FR-10 candidate execution evidence directory.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            help="New directory for validation gates, leaderboard, and selection lock.",
        ),
    ],
) -> None:
    """Validate candidate evidence without test access and lock one winner."""

    try:
        result = validate_and_lock_execution(
            execution_dir=execution,
            output_dir=output,
        )
    except ValidationError as error:
        typer.echo(f"Validation failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    identity = (
        f"run_id={result.run_id} state={result.state} "
        f"selection_fingerprint={result.selection_fingerprint or 'none'} "
        f"manifest={result.manifest_path.name}"
    )
    if result.state == "REJECTED":
        typer.echo(identity + " next=new plan", err=True)
        raise typer.Exit(code=1)
    typer.echo(identity + " next=agent finalize")


@agent_app.command("finalize")
def agent_finalize(
    validation: Annotated[
        Path,
        typer.Option(
            "--validation",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="LOCKED FR-11 validation evidence directory.",
        ),
    ],
    evidence: Annotated[
        Path,
        typer.Option(
            "--evidence",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="Original fingerprinted benchmark evidence with held-out test labels.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            help="New directory for the one authorized held-out test evaluation.",
        ),
    ],
) -> None:
    """Evaluate one locked candidate on held-out test data once."""

    try:
        result = finalize_locked_selection(
            validation_dir=validation,
            evidence_dir=evidence,
            output_dir=output,
        )
    except ValidationError as error:
        typer.echo(f"Finalization failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"run_id={result.run_id} state=FINALIZED "
        f"selection_fingerprint={result.selection_fingerprint} "
        f"manifest={result.manifest_path.name} next=none"
    )


@agent_app.command("report")
def agent_report(
    evidence: Annotated[
        Path,
        typer.Option(
            "--evidence",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="Original portable benchmark evidence and dependency lock.",
        ),
    ],
    execution: Annotated[
        Path,
        typer.Option(
            "--execution",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="Completed FR-10 candidate execution directory.",
        ),
    ],
    validation: Annotated[
        Path,
        typer.Option(
            "--validation",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="LOCKED or REJECTED FR-11 validation directory.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            help="New directory for the deterministic FR-12 evidence bundle.",
        ),
    ],
    finalization: Annotated[
        Path | None,
        typer.Option(
            "--finalization",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="Optional FINALIZED held-out test evidence directory.",
        ),
    ] = None,
) -> None:
    """Generate a strict offline evidence manifest and human-readable report."""

    try:
        result = generate_evidence_bundle(
            evidence_dir=evidence,
            execution_dir=execution,
            validation_dir=validation,
            finalization_dir=finalization,
            output_dir=output,
        )
    except ReportError as error:
        typer.echo(f"Reporting failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"run_id={result.run_id} state={result.state} "
        f"evidence_fingerprint={result.evidence_fingerprint} "
        f"manifest={result.manifest_path.name} next=agent verify"
    )


@agent_app.command("verify")
def agent_verify(
    bundle: Annotated[
        Path,
        typer.Option(
            "--bundle",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="FR-12 evidence bundle to verify without network access.",
        ),
    ],
) -> None:
    """Verify an FR-12 evidence bundle offline and return non-zero on tampering."""

    try:
        result = verify_evidence_bundle(bundle)
    except ReportError as error:
        typer.echo(f"Evidence verification failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"valid=true evidence_fingerprint={result.evidence_fingerprint} "
        f"artifacts={result.artifact_count} claims={result.claim_count} next=none"
    )

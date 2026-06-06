"""CLI — `topmodels run --phase 1`."""

from __future__ import annotations

import typer

from topmodels.config import load_config
from topmodels.pipeline import run_pipeline

cli = typer.Typer(help="MotoMetrics Top Models pipeline — ranked content backlog generator.")


@cli.callback()
def main() -> None:
    """Top Models — rank used-car models for content backlog and app profiles."""


@cli.command("run")
def run_cmd(
    phase: int = typer.Option(1, "--phase", help="Pipeline phase (1=free MVP, 2=reddit+keywords, 3=paid)"),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass HTTP/trends cache"),
    top_n: int | None = typer.Option(None, "--top-n", help="Override config top_n"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Score without writing outputs or enrichment API calls"),
) -> None:
    """Run the top-models pipeline for the given phase."""
    config = load_config()
    typer.echo(f"Top Models pipeline — phase {phase}, top_n={top_n or config.top_n}")

    ranked = run_pipeline(
        config,
        phase=phase,
        top_n=top_n,
        refresh=refresh,
        dry_run=dry_run,
    )

    typer.echo(f"Scored {len(ranked)} models.")
    for item in ranked[:10]:
        v = item.vehicle
        flag = " 🔺" if item.riser else ""
        typer.echo(
            f"  {item.rank:>2}. {v.display_label()} — {item.score.total:.3f}{flag} — {item.score.explanation}"
        )

    if dry_run:
        typer.echo("Dry run — no files written.")
    else:
        typer.echo(f"Wrote {config.output_path / 'top_models.csv'}")
        typer.echo(f"Wrote {config.output_path / 'top_models.json'}")
        typer.echo(f"Wrote {config.output_path / 'backlog.md'}")


app = cli  # entry point alias


if __name__ == "__main__":
    cli()

import json
import sys
from pathlib import Path

import click

from .parser import parse_input
from .engine.analyzer import analyze


@click.group()
def main() -> None:
    """CacheLens CLI."""


@main.command()
@click.argument("file", type=str)
@click.option("--format", "out_format", type=click.Choice(["human", "json"], case_sensitive=False), default="human")
@click.option("--suggestions", is_flag=True, help="Show full suggestion details (human mode)")
@click.option("--score-only", is_flag=True, help="Print only the cacheability score")
@click.option("--min-tokens", type=int, default=50, show_default=True)
def analyze_cmd(file: str, out_format: str, suggestions: bool, score_only: bool, min_tokens: int) -> None:
    """Analyze a prompt/chain/trace from a file path or '-' for stdin."""

    raw: str
    if file == "-":
        raw = sys.stdin.read()
    else:
        p = Path(file)
        if not p.exists():
            raise click.ClickException(f"File not found: {file}")
        raw = p.read_text(encoding="utf-8")

    if not raw.strip():
        raise click.ClickException("Input is empty")

    analysis_input = parse_input(raw)
    result = analyze(analysis_input, min_tokens=min_tokens)

    if score_only:
        click.echo(str(result.cacheability_score))
        return

    if out_format.lower() == "json":
        click.echo(result.model_dump_json(indent=2))
        return

    # human
    click.echo("CacheLens Analysis\n══════════════════\n")
    click.echo(f"Score: {result.cacheability_score} / 100 ({result.cacheability_label})")
    click.echo(f"Total input tokens: {result.input_summary.total_input_tokens}")
    click.echo(
        f"Estimated waste: {result.waste_summary.total_waste_tokens} tokens ({result.waste_summary.waste_percentage:.1f}%)\n"
    )

    click.echo("Top Waste Sources\n─────────────────")
    for i, s in enumerate(result.waste_summary.sources[:5], start=1):
        click.echo(f" {i}. {s.description} .............. {s.waste_tokens} tokens")

    click.echo("\nSuggestions\n───────────")
    for sug in result.suggestions[:5]:
        click.echo(f" [{sug.priority.upper()}] {sug.title}")
        click.echo(f"        → Save ~{sug.estimated_savings_tokens} tokens")
        if suggestions:
            click.echo(f"        {sug.description}")
    click.echo("\nRun with --format json for machine-readable output.")


@main.command()
@click.option("--port", type=int, default=8420, show_default=True)
@click.option("--no-open", is_flag=True, help="Don't auto-open browser")
def ui(port: int, no_open: bool) -> None:
    """Launch the local web UI."""
    from .server import run

    run(port=port, open_browser=(not no_open))

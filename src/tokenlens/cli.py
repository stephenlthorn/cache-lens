import json
import sys
from pathlib import Path

import click

from .parser import parse_input
from .engine.analyzer import analyze


@click.group()
def main() -> None:
    """TokenLens CLI."""


@main.command()
@click.argument("file", type=str)
@click.option("--format", "out_format", type=click.Choice(["human", "json"], case_sensitive=False), default="human")
@click.option("--suggestions", is_flag=True, help="Show full suggestion details (human mode)")
@click.option("--score-only", is_flag=True, help="Print only the cacheability score")
@click.option("--min-tokens", type=int, default=50, show_default=True)
@click.option(
    "--sponsor-reminder/--no-sponsor-reminder",
    default=False,
    show_default=True,
    envvar="TOKENLENS_SPONSOR_REMINDER",
    help="Show a post-run sponsor reminder (human output only). Can also be enabled via TOKENLENS_SPONSOR_REMINDER=1.",
)
def analyze_cmd(file: str, out_format: str, suggestions: bool, score_only: bool, min_tokens: int, sponsor_reminder: bool) -> None:
    """Analyze a prompt/chain/trace from a file path or '-' for stdin."""

    raw: str
    if file == "-":
        raw = sys.stdin.read()
    else:
        p = Path(file)
        if not p.exists():
            raise click.ClickException(f"File not found: {file}")
        try:
            raw = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                raw = p.read_text(encoding="latin-1")
            except Exception:
                raise click.ClickException(f"Could not read file (unsupported encoding): {file}")

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
    click.echo("TokenLens Analysis\n══════════════════\n")
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

    # Sponsorship reminder (human mode only; opt-in)
    if sponsor_reminder:
        # Prefer waste_percentage (stable). If optimized_structure has a larger implied savings, use it.
        saved_pct = float(result.waste_summary.waste_percentage)
        if result.optimized_structure and result.optimized_structure.original_tokens_per_call and result.optimized_structure.savings_per_call is not None:
            denom = max(1, int(result.optimized_structure.original_tokens_per_call))
            opt_pct = (float(result.optimized_structure.savings_per_call) / denom) * 100.0
            saved_pct = max(saved_pct, opt_pct)

        saved_pct_int = int(round(saved_pct))

        click.echo("\n—")
        click.echo(f"TokenLens saved you ~{saved_pct_int}% tokens in this run.")
        click.echo("If this tool helps you, consider sponsoring:")
        click.echo("https://github.com/sponsors/stephenlthorn")


@main.command()
@click.option("--port", type=int, default=8420, show_default=True)
@click.option("--no-open", is_flag=True, help="Don't auto-open browser")
@click.option("--base-path", default="", help="URL base path (e.g. /tokenlens)")
def ui(port: int, no_open: bool, base_path: str) -> None:
    """Launch the local web UI."""
    from .server import run

    run(port=port, open_browser=(not no_open), base_path=base_path)


@main.command()
@click.option("--port", type=int, default=8420, show_default=True, help="Port to listen on")
@click.option("--base-path", default="", help="URL base path when behind a reverse proxy (e.g. /tokenlens)")
def daemon(port: int, base_path: str) -> None:
    """Start the TokenLens daemon."""
    from .installer import is_port_in_use

    if is_port_in_use(port):
        click.echo(
            f"Error: port {port} is already in use. Use --port N to specify a different port.",
            err=True,
        )
        raise SystemExit(1)
    from .server import run

    run(port=port, open_browser=False, base_path=base_path)


@main.command()
@click.option("--format", "fmt", default="human", type=click.Choice(["human", "json"]))
@click.option("--port", default=8420, show_default=True, help="Daemon port")
def status(fmt: str, port: int) -> None:
    """Show daemon status."""
    import httpx

    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/status", timeout=2.0)
        data = r.json()
        if fmt == "json":
            click.echo(json.dumps(data, indent=2))
        else:
            daemon_status = data.get("daemon", "unknown")
            pid = data.get("pid", "?")
            port = data.get("port", 8420)
            db_mb = data.get("db_size_bytes", 0) / 1_000_000
            raw_calls = data.get("raw_calls_today", 0)
            ret = data.get("retention", {})
            last = data.get("last_nightly_rollup") or "never"
            click.echo(f"TokenLens daemon: {daemon_status} (pid {pid}, port {port})")
            click.echo(f"DB size: {db_mb:.1f} MB")
            click.echo(f"Raw calls today: {raw_calls}")
            click.echo(
                f"Retention: raw={ret.get('raw_days', 1)}d, daily={ret.get('daily_days', 365)}d, aggregate={ret.get('aggregate', True)}"
            )
            click.echo(f"Last rollup: {last}")
    except Exception:
        click.echo("TokenLens daemon: stopped")


@main.command("install")
@click.option("--port", type=int, default=8420, show_default=True, help="Port for the daemon")
@click.option("--base-path", default="", help="URL base path when behind a reverse proxy (e.g. /tokenlens)")
def install_cmd(port: int, base_path: str) -> None:
    """Install TokenLens as a background service."""
    from .installer import install as _install

    _install(port=port, base_path=base_path)


@main.command("uninstall")
@click.option("--purge", is_flag=True, help="Also delete all usage data")
def uninstall_cmd(purge: bool) -> None:
    """Uninstall TokenLens."""
    from .installer import uninstall as _uninstall

    _uninstall(purge=purge)


@main.command()
@click.option("--port", default=8420, show_default=True, help="Daemon port")
def top(port: int) -> None:
    """Live terminal view of API traffic (htop-style)."""
    from tokenlens.top import run_top
    run_top(port=port)


@main.command("report")
@click.option("--days", default=7, show_default=True, help="Days to include")
@click.option("--format", "fmt", default="human", type=click.Choice(["human", "json"]))
@click.option("--port", default=8420, show_default=True)
def report_cmd(days: int, fmt: str, port: int) -> None:
    """Print a cost digest report."""
    import json as _json
    import httpx
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/usage/digest?days={days}", timeout=5.0)
        data = r.json()
        if fmt == "json":
            click.echo(_json.dumps(data, indent=2))
        else:
            from tokenlens.digest import format_digest_human
            click.echo(format_digest_human(data))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)

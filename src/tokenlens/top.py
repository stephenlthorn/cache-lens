"""Live terminal view for TokenLens — `tokenlens top`.

Connects to the WebSocket live feed and renders a rich terminal table
with the latest API calls, cost, and waste metrics.

Keyboard input runs in a separate thread to avoid blocking the WebSocket
event loop. Keys communicated to the main loop via queue.Queue.
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from collections import deque
from typing import Deque

import websockets
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

_MAX_ROWS = 50
_ROLLING_WINDOW = 60  # seconds


def _fmt_tokens(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def _fmt_cost(usd: float | None) -> str:
    if usd is None:
        return "—"
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"


def _build_table(calls: list[dict], stats: dict) -> Table:
    table = Table(
        title=(
            f"TokenLens top — {stats['calls_per_min']:.0f} calls/min | "
            f"${stats['cost_per_hr']:.2f}/hr | "
            f"Cache: {stats['cache_pct']:.0f}% | "
            f"Waste: {stats['waste_tok_per_min']:.0f} tok/min"
        ),
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    table.add_column("TIME", style="dim", width=9)
    table.add_column("SOURCE", min_width=12, max_width=20)
    table.add_column("MODEL", min_width=14, max_width=22)
    table.add_column("IN", justify="right", width=6)
    table.add_column("OUT", justify="right", width=5)
    table.add_column("CACHE", justify="right", width=6)
    table.add_column("COST", justify="right", width=7)
    table.add_column("WASTE", justify="right", width=6)

    for call in calls[:_MAX_ROWS]:
        ts = call.get("ts", 0)
        t_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "—"
        cost = call.get("cost_usd")
        cache_read = call.get("cache_read_tokens", 0) or 0
        total_in = call.get("input_tokens", 0) or 0
        waste = call.get("waste_tokens", 0) or 0

        cost_text = Text(_fmt_cost(cost))
        if cost and cost > 0.05:
            cost_text.stylize("bold red")
        elif cost and cost > 0.01:
            cost_text.stylize("yellow")

        cache_text = Text(_fmt_tokens(cache_read))
        if total_in > 0 and cache_read / total_in > 0.5:
            cache_text.stylize("green")

        waste_text = Text(_fmt_tokens(waste) if waste else "—")
        if waste and waste > 100:
            waste_text.stylize("yellow")

        table.add_row(
            t_str,
            call.get("source", "—")[:20],
            (call.get("model", "—") or "—")[:22],
            _fmt_tokens(total_in),
            _fmt_tokens(call.get("output_tokens")),
            cache_text,
            cost_text,
            waste_text,
        )
    return table


def _compute_stats(calls: list[dict], window_secs: int = _ROLLING_WINDOW) -> dict:
    now = time.time()
    recent = [c for c in calls if now - c.get("ts", 0) <= window_secs]
    n = len(recent)
    calls_per_min = n / (window_secs / 60) if window_secs > 0 else 0
    total_cost = sum(c.get("cost_usd") or 0 for c in recent)
    cost_per_hr = total_cost * (3600 / window_secs) if window_secs > 0 else 0
    total_in = sum(c.get("input_tokens") or 0 for c in recent)
    total_cache = sum(c.get("cache_read_tokens") or 0 for c in recent)
    cache_pct = (total_cache / total_in * 100) if total_in > 0 else 0
    waste_per_min = sum(c.get("waste_tokens") or 0 for c in recent) / (window_secs / 60) if n > 0 else 0
    return {
        "calls_per_min": calls_per_min,
        "cost_per_hr": cost_per_hr,
        "cache_pct": cache_pct,
        "waste_tok_per_min": waste_per_min,
    }


async def _run_async(port: int) -> None:
    url = f"ws://localhost:{port}/api/live"
    calls: Deque[dict] = deque(maxlen=_MAX_ROWS)
    key_q: queue.Queue[str] = queue.Queue()
    paused = False
    console = Console()

    def _keyboard_reader():
        """Read single keypresses in a separate thread."""
        import sys, tty, termios
        fd = sys.stdin.fileno()
        try:
            old = termios.tcgetattr(fd)
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                key_q.put(ch)
                if ch in ("q", "Q"):
                    break
        except Exception:
            pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass

    kb_thread = threading.Thread(target=_keyboard_reader, daemon=True)
    kb_thread.start()

    try:
        async with websockets.connect(url) as ws:
            with Live(console=console, refresh_per_second=2, screen=True) as live:
                while True:
                    # Check keyboard
                    try:
                        while True:
                            key = key_q.get_nowait()
                            if key in ("q", "Q"):
                                return
                            if key in ("p", "P"):
                                paused = not paused
                    except queue.Empty:
                        pass

                    # Receive message with timeout
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                        event = json.loads(msg)
                        if not paused:
                            calls.appendleft(event)
                    except asyncio.TimeoutError:
                        pass
                    except Exception:
                        break

                    stats = _compute_stats(list(calls))
                    live.update(_build_table(list(calls), stats))
    except Exception as e:
        console.print(f"[red]Could not connect to TokenLens at {url}[/red]")
        console.print(f"[dim]Is the daemon running? Try: tokenlens ui --port {port}[/dim]")


def run_top(port: int = 8420) -> None:
    """Entry point for `tokenlens top`."""
    asyncio.run(_run_async(port=port))

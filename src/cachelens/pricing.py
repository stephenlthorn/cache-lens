from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

logger = logging.getLogger(__name__)
_DATA_DIR = Path(__file__).parent / "data"


class PricingTable:
    def __init__(self, overrides_path: Optional[Path] = None) -> None:
        bundled = json.loads((_DATA_DIR / "pricing.json").read_text())
        self._prices: dict[str, dict[str, float]] = {
            k: v for k, v in bundled["models"].items()
        }
        if overrides_path and Path(overrides_path).exists():
            self._apply_overrides(Path(overrides_path))

    def _apply_overrides(self, path: Path) -> None:
        try:
            data = tomllib.loads(path.read_text())
        except Exception as e:
            logger.warning("pricing_overrides.toml failed to parse: %s", e)
            return
        for model, vals in (data.get("models") or {}).items():
            required = {"input_usd_per_mtok", "output_usd_per_mtok",
                        "cache_read_usd_per_mtok", "cache_write_usd_per_mtok"}
            try:
                if not required.issubset(vals.keys()):
                    raise ValueError(f"missing fields for {model}")
                self._prices[model] = {
                    "input":       float(vals["input_usd_per_mtok"]),
                    "output":      float(vals["output_usd_per_mtok"]),
                    "cache_read":  float(vals["cache_read_usd_per_mtok"]),
                    "cache_write": float(vals["cache_write_usd_per_mtok"]),
                }
            except Exception as e:
                logger.warning("Skipping malformed pricing override for %r: %s", model, e)

    def _row(self, provider: str, model: str) -> dict[str, float]:
        return (
            self._prices.get(model)
            or self._prices.get(f"{provider}/default")
            or {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}
        )

    def cost_usd(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
    ) -> float:
        r = self._row(provider, model)
        return (
            input_tokens       * r["input"]       / 1_000_000
            + output_tokens    * r["output"]      / 1_000_000
            + cache_read_tokens  * r["cache_read"]  / 1_000_000
            + cache_write_tokens * r["cache_write"] / 1_000_000
        )

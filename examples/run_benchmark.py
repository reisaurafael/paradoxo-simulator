"""
examples/run_benchmark.py
=========================
Reproducible balance benchmark: run a fixed-seed batch of full games with the
four bundled strategy profiles and print a statistical summary.

Usage:
    python -m examples.run_benchmark            # 1000 games, seed 7
    python -m examples.run_benchmark 200 42     # custom n_games and seed
    python -m examples.run_benchmark 1000 7 benchmark.json  # export outcomes

The seed makes every run identical, so the numbers quoted in the README can be
regenerated exactly.
"""

from __future__ import annotations

import io
import sys
import json
import platform
from dataclasses import asdict
from pathlib import Path

# Force UTF-8 output so Portuguese card names render on every platform.
if __name__ == "__main__" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from simulation.runner import simulate_n_games
from simulation.metrics import summarise, print_summary
from simulation.strategies.aggressive import AggressiveStrategy
from simulation.strategies.conservative import ConservativeStrategy
from simulation.strategies.smart import SmartStrategy
from simulation.strategies.collector import CollectorStrategy


def build_lineup() -> dict:
    return {
        "Aggressive":   AggressiveStrategy(),
        "Conservative": ConservativeStrategy(),
        "Smart":        SmartStrategy(),
        "Collector":    CollectorStrategy(),
    }


def main(n_games: int = 1000, seed: int = 7, output: str | None = None) -> None:
    if n_games < 1:
        raise ValueError("n_games must be positive")
    print(f"Running {n_games} games: 4 strategies, seed={seed}\n")
    results = simulate_n_games(build_lineup(), n=n_games, seed=seed)
    summary = summarise(results)
    print_summary(summary)
    if output:
        payload = {
            "python": platform.python_version(),
            "seed": seed,
            "lineup_order": list(build_lineup()),
            "max_hours": 200,
            "summary": asdict(summary),
            "games": [
                {"index": i, "winner": r.winner, "hours": r.hours_played,
                 "end_reason": r.end_reason}
                for i, r in enumerate(results)
            ],
        }
        Path(output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nSaved summary and per-game outcomes to {output}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    s = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    output = sys.argv[3] if len(sys.argv) > 3 else None
    main(n, s, output)

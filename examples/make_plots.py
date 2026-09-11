"""
examples/make_plots.py
======================
Generate the figures shown in the README from a fixed-seed batch.

Usage:
    python -m examples.make_plots

Writes two PNGs into docs/img/:
    - win_conditions.png   how games end (the four win conditions)
    - game_length.png      distribution of game length in hours
"""

from __future__ import annotations

from pathlib import Path
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from simulation.runner import simulate_n_games
from examples.run_benchmark import build_lineup

OUT = Path(__file__).resolve().parent.parent / "docs" / "img"
N_GAMES = 1000
SEED = 7

# Human-readable labels for the engine's end-reason keys.
REASON_LABELS = {
    "year_zero":      "Year Zero",
    "full_receptor":  "Full Receptor",
    "last_traveler":  "Last Traveler",
    "merchant_empty": "Merchant Empty",
    "all_terminated": "All Terminated",
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results = simulate_n_games(build_lineup(), n=N_GAMES, seed=SEED)

    # --- Figure 1: win-condition distribution ---
    reasons = Counter(r.end_reason for r in results)
    ordered = sorted(reasons.items(), key=lambda kv: -kv[1])
    labels = [REASON_LABELS.get(k, k) for k, _ in ordered]
    counts = [v for _, v in ordered]

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(labels, counts, color="#3b6ea5")
    ax.set_ylabel("Games")
    ax.set_title(f"How {N_GAMES} games end (4 strategies, seed {SEED})")
    ax.tick_params(axis="x", labelsize=9)
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right")
    for i, c in enumerate(counts):
        ax.text(i, c, f"{c/N_GAMES:.0%}", ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "win_conditions.png", dpi=120)

    # --- Figure 2: game-length distribution ---
    lengths = [r.hours_played for r in results]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(lengths, bins=range(min(lengths), max(lengths) + 2),
            color="#3b6ea5", edgecolor="white")
    ax.set_xlabel("Game length (hours)")
    ax.set_ylabel("Games")
    ax.set_title(f"Game-length distribution ({N_GAMES} games)")
    fig.tight_layout()
    fig.savefig(OUT / "game_length.png", dpi=120)

    print(f"Wrote {OUT / 'win_conditions.png'}")
    print(f"Wrote {OUT / 'game_length.png'}")


if __name__ == "__main__":
    main()

"""
rl/evaluate.py
==============
The benchmark: fixed tables of 2 to 6 players, one seat swapped.

For a table of n players the opponents are the first n - 1 of

    Aggressive, Conservative, Collector, Smart, Aggressive

so the 4 player table is Aggressive, Conservative and Collector, the table
every earlier result was measured on. The subject rotates through every seat
and every subject plays the same seeds.

Win rates are not comparable across table sizes (a fair share is 1/n), so
each size is also reported as an edge: win rate times n. An edge of 1.0 is a
fair share, 1.5 is winning half again as often as a fair share.

    python -m rl.evaluate --games 4000 --weights rl/results/runs/<run>/best.npz
"""

from __future__ import annotations
import argparse
import json
import math
import os
import random
from multiprocessing import Pool

import numpy as np

from simulation.runner import simulate_game
from simulation.strategies.aggressive import AggressiveStrategy
from simulation.strategies.conservative import ConservativeStrategy
from simulation.strategies.collector import CollectorStrategy
from simulation.strategies.smart import SmartStrategy

HEURISTICS = {
    "Smart": SmartStrategy,
    "Aggressive": AggressiveStrategy,
    "Conservative": ConservativeStrategy,
    "Collector": CollectorStrategy,
}
OPPONENT_ORDER = [AggressiveStrategy, ConservativeStrategy, CollectorStrategy,
                  SmartStrategy, AggressiveStrategy]
TABLE_SIZES = (2, 3, 4, 5, 6)
SUBJECT = "Subject"


def table(subject, game_index: int, n_players: int = 4, opponents: list | None = None) -> dict:
    """The subject plus n - 1 opponents, the subject in seat game_index % n."""
    seats = opponents if opponents is not None else [cls() for cls in OPPONENT_ORDER[:n_players - 1]]
    seats = list(seats)
    seats.insert(game_index % len(seats + [subject]), subject)
    names = [SUBJECT if s is subject else f"Rival{i}" for i, s in enumerate(seats)]
    return dict(zip(names, seats))


def play(subject, game_index: int, seed: int, n_players: int = 4):
    return simulate_game(table(subject, game_index, n_players), rng=random.Random(seed + game_index))


# ---------------------------------------------------------------------------
# Saving and loading the heads
# ---------------------------------------------------------------------------

def save_heads(path: str, base: str, heads: dict) -> None:
    arrays = {}
    for name, net in heads.items():
        arrays[f"{name}__sizes"] = np.array(net.sizes)
        for i, p in enumerate(net.params):
            arrays[f"{name}__p{i}"] = p
    np.savez(path, base=base, heads=np.array(list(heads)), **arrays)


def load_weights(path: str):
    """Return (base heuristic name, {head: (sizes, params)})."""
    data = np.load(path, allow_pickle=False)
    heads = {}
    for name in data["heads"]:
        name = str(name)
        sizes = [int(v) for v in data[f"{name}__sizes"]]
        n = len([k for k in data.files if k.startswith(f"{name}__p")])
        heads[name] = (sizes, [data[f"{name}__p{i}"] for i in range(n)])
    return str(data["base"]), heads


def build_heads(spec: dict):
    from rl.network import MLP
    heads = {}
    for name, (sizes, params) in spec.items():
        net = MLP(sizes)
        net.set(params)
        heads[name] = net
    return heads


# ---------------------------------------------------------------------------
# The benchmark
# ---------------------------------------------------------------------------

def _run_chunk(args):
    kind, payload, indices, seed, n_players = args
    if kind == "heuristic":
        factory = lambda: HEURISTICS[payload]()
    else:
        from rl.agent import make_learned_class
        base, spec = payload
        heads = build_heads(spec)
        cls = make_learned_class(HEURISTICS[base])
        factory = lambda: cls(heads, greedy=True)
    out = []
    for i in indices:
        r = play(factory(), i, seed, n_players)
        me = next(t for t in r.traveler_results if t.name == SUBJECT)
        best_rival = max(t.contract_points for t in r.traveler_results if t.name != SUBJECT)
        out.append((int(r.winner == SUBJECT), r.hours_played, me.contract_points,
                    me.contract_points - best_rival))
    return out


def benchmark(kind: str, payload, games: int, seed: int = 0, workers: int = 12,
              n_players: int = 4) -> dict:
    """Win rate of one subject at one table size, split across processes."""
    chunks = [list(range(w, games, workers)) for w in range(workers)]
    with Pool(workers) as pool:
        rows = [row for part in pool.map(_run_chunk,
                                         [(kind, payload, c, seed, n_players) for c in chunks])
                for row in part]
    wins = np.array([r[0] for r in rows], dtype=float)
    p = float(wins.mean())
    half = 1.96 * math.sqrt(p * (1 - p) / len(wins))
    return {
        "players": n_players,
        "games": len(rows),
        "win_rate": round(p, 4),
        "ci95": [round(p - half, 4), round(p + half, 4)],
        "edge": round(p * n_players, 3),
        "mean_hours": round(float(np.mean([r[1] for r in rows])), 2),
        "mean_cp": round(float(np.mean([r[2] for r in rows])), 3),
        "mean_cp_margin": round(float(np.mean([r[3] for r in rows])), 3),
    }


def benchmark_all_sizes(kind: str, payload, games: int, seed: int = 0, workers: int = 12,
                        sizes=TABLE_SIZES) -> dict:
    """The benchmark at every table size, plus the mean edge across sizes."""
    per_size = {n: benchmark(kind, payload, games, seed, workers, n) for n in sizes}
    return {"by_size": per_size,
            "mean_edge": round(float(np.mean([r["edge"] for r in per_size.values()])), 3)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=4000, help="matches per table size")
    ap.add_argument("--seed", type=int, default=10_000_000)
    ap.add_argument("--weights", default=None)
    ap.add_argument("--out", default="rl/results/benchmark.json")
    args = ap.parse_args()

    results = {}
    for name in HEURISTICS:
        results[name] = benchmark_all_sizes("heuristic", name, args.games, args.seed)
        print(name, results[name]["mean_edge"], flush=True)
    if args.weights and os.path.exists(args.weights):
        base, spec = load_weights(args.weights)
        label = f"Learned({base})"
        results[label] = benchmark_all_sizes("learned", (base, spec), args.games, args.seed)
        print(label, results[label]["mean_edge"], flush=True)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({"games_per_size": args.games, "seed": args.seed,
                   "opponent_order": [c.__name__ for c in OPPONENT_ORDER],
                   "results": results}, f, indent=2)


if __name__ == "__main__":
    main()

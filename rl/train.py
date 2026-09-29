"""
rl/train.py
===========
Policy gradient training: imitation first, then REINFORCE with a learned
value baseline, against a changing field that includes older copies of the
player itself.

Every match in training is drawn at random: 2 to 6 players, the subject in a
random seat, and each opponent either one of the four heuristics or, once
the league has members, a frozen earlier version of the learned player
(self-play). The dice, the Merchant's stock, the paradoxes and the rivals'
rolls all come from the match seed, so over millions of matches the player
meets every situation the game can produce.

Imitation. The player plays the heuristic's choices and each head learns to
predict them (cross-entropy), so reinforcement starts from a competent player.
It ends once the policy is confident enough (entropy below a threshold).

Reinforcement. The player samples its choices. At the end of the match the
reward is 1 for a win, 0 otherwise. Each choice is pushed up or down by how
much better the match went than the value network expected from that position:

    grad = sum over choices of (R - V(s)) * d log pi(choice | s) / d theta  +  beta * dH/d theta

Every few iterations the greedy player is measured on the fixed tables of
rl/evaluate.py, and rl/tracking.py records milestones, setbacks and recoveries.

    python -m rl.train --name full --iterations 3000
"""

from __future__ import annotations
import argparse
import json
import os
import random
import time
from multiprocessing import Pool

import numpy as np

from rl.agent import make_learned_class, softmax, HEADS
from rl.evaluate import (HEURISTICS, SUBJECT, table, benchmark_all_sizes, save_heads,
                         build_heads, load_weights, TABLE_SIZES)
from rl.features import STATE_SIZE, OPTION_SIZES
from rl.network import MLP, Adam
from rl.tracking import RunTracker
from simulation.runner import simulate_game

HEURISTIC_NAMES = list(HEURISTICS)


def new_heads(hidden: int, rng) -> dict[str, MLP]:
    return {h: MLP([STATE_SIZE + OPTION_SIZES[h], hidden, hidden, 1], rng) for h in HEADS}


def spec_of(heads: dict[str, MLP]) -> dict:
    return {h: (net.sizes, net.get()) for h, net in heads.items()}


def _episodes(args):
    """Play episodes with the sampling policy and return summed gradients per head."""
    (base, spec, v_spec, seeds, entropy_coef, imitate, league, self_play,
     min_players, max_players) = args
    heads = build_heads(spec)
    value = build_heads({"v": v_spec})["v"]
    cls = make_learned_class(HEURISTICS[base])
    league_heads = [build_heads(s) for s in league]

    grads = {h: [np.zeros_like(p) for p in heads[h].params] for h in heads}
    v_grads = [np.zeros_like(p) for p in value.params]
    stats = {"wins": 0, "decisions": 0, "entropy": 0.0, "by_size": {}}
    for seed in seeds:
        rng = np.random.default_rng(seed)
        n = int(rng.integers(min_players, max_players + 1))
        opponents = []
        for _ in range(n - 1):
            if league_heads and rng.random() < self_play:
                snap = league_heads[int(rng.integers(len(league_heads)))]
                opponents.append(cls(snap, greedy=False, rng=np.random.default_rng(rng.integers(1 << 31))))
            else:
                opponents.append(HEURISTICS[HEURISTIC_NAMES[int(rng.integers(4))]]())
        agent = cls(heads, greedy=False, rng=rng, record=True, follow_base=imitate)
        result = simulate_game(table(agent, seed, opponents=opponents), rng=random.Random(seed))
        reward = float(result.winner == SUBJECT)
        stats["wins"] += int(reward)
        size = stats["by_size"].setdefault(n, [0, 0])
        size[0] += int(reward)
        size[1] += 1
        if not agent.trace:
            continue

        states = np.stack([t[1] for t in agent.trace])
        v_out, v_acts = value.forward(states)
        v_pred = v_out[:, 0]
        for g, d in zip(v_grads, value.backward(v_acts, (-(v_pred - reward))[:, None])):
            g += d

        for (head, s, ox, choice, base_index), v in zip(agent.trace, v_pred):
            x = np.hstack([np.repeat(s[None, :], len(ox), axis=0), ox])
            scores, acts = heads[head].forward(x)
            p = softmax(scores[:, 0])
            logp = np.log(p + 1e-12)
            entropy = -(p * logp).sum()
            target = base_index if imitate else choice
            onehot = np.zeros_like(p)
            onehot[target] = 1.0
            if imitate:
                upstream = onehot - p
            else:
                upstream = (reward - v) * (onehot - p) - entropy_coef * p * (logp + entropy)
            for g, d in zip(grads[head], heads[head].backward(acts, upstream[:, None])):
                g += d
            stats["decisions"] += 1
            stats["entropy"] += entropy
    return grads, v_grads, stats


def reference_edges(games: int, seed: int, workers: int, path: str) -> dict:
    """Mean edge of each heuristic on the benchmark, cached per (games, seed)."""
    key = f"{games}_{seed}"
    cache = json.load(open(path)) if os.path.exists(path) else {}
    if key not in cache:
        cache[key] = {h: benchmark_all_sizes("heuristic", h, games, seed, workers)
                      for h in HEURISTICS}
        with open(path, "w") as f:
            json.dump(cache, f, indent=2)
    return {h: r["mean_edge"] for h, r in cache[key].items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="run")
    ap.add_argument("--note", default="", help="what this run is testing, for the run index")
    ap.add_argument("--base", default="Aggressive", choices=HEURISTIC_NAMES)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--imitation", type=int, default=60, help="most iterations of imitation")
    ap.add_argument("--imitation-entropy", type=float, default=0.6)
    ap.add_argument("--iterations", type=int, default=1000, help="iterations of reinforcement")
    ap.add_argument("--batch", type=int, default=384, help="matches per iteration")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--imitation-lr", type=float, default=3e-3)
    ap.add_argument("--value-lr", type=float, default=1e-3)
    ap.add_argument("--entropy", type=float, default=0.01)
    ap.add_argument("--self-play", type=float, default=0.3,
                    help="chance each opponent seat is a league snapshot")
    ap.add_argument("--league-every", type=int, default=50)
    ap.add_argument("--league-size", type=int, default=6)
    ap.add_argument("--min-players", type=int, default=2)
    ap.add_argument("--max-players", type=int, default=6)
    ap.add_argument("--eval-every", type=int, default=20)
    ap.add_argument("--eval-games", type=int, default=1000, help="matches per table size")
    ap.add_argument("--eval-seed", type=int, default=20_000_000)
    ap.add_argument("--results", default="rl/results")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--resume", default=None,
                    help="a run folder: continue it from its newest checkpoint")
    args = ap.parse_args()
    os.makedirs(args.results, exist_ok=True)

    refs = reference_edges(args.eval_games, args.eval_seed, args.workers,
                           os.path.join(args.results, "reference_edges.json"))
    tracker = RunTracker(args.results, args.name, vars(args), refs, resume_dir=args.resume)

    rng = np.random.default_rng(args.seed)
    heads = new_heads(args.hidden, rng)
    value = MLP([STATE_SIZE, args.hidden, 1], rng)
    league: list[dict] = []
    next_seed = 0
    imitating = args.imitation > 0
    it = 0
    if args.resume:
        it, next_seed, path = tracker.last_checkpoint()
        saved = build_heads(load_weights(path)[1])
        value_restored = "value" in saved
        value = saved.pop("value", value)
        heads = saved
        # The league is rebuilt from the newest checkpoints before this one.
        ckpts = sorted(f for f in os.listdir(os.path.join(args.resume, "checkpoints")))
        ckpts = [f for f in ckpts if int(f[2:7]) <= it][-args.league_size:]
        league = [{h: v for h, v in load_weights(os.path.join(args.resume, "checkpoints", f))[1].items()
                   if h != "value"} for f in ckpts]
        imitating = False
        tracker.event("resume", f"resumed from the checkpoint of iteration {it} ({next_seed:,} matches), "
                      f"league rebuilt from {len(league)} checkpoints; the optimiser state starts fresh"
                      + ("" if value_restored else " and so does the value network (not in this checkpoint)"),
                      iteration=it)
    opts = {h: Adam(heads[h].params, lr=args.lr) for h in heads}
    v_opt = Adam(value.params, lr=args.value_lr)
    reinforce_left = args.iterations
    start = time.time()
    if not args.resume:
        tracker.event("phase", "imitation" if imitating else "reinforcement")
    with Pool(args.workers) as pool:
        while reinforce_left > 0:
            it += 1
            imitate = imitating
            if not imitate:
                reinforce_left -= 1
            seeds = list(range(next_seed, next_seed + args.batch))
            next_seed += args.batch
            spec = spec_of(heads)
            v_spec = (value.sizes, value.get())
            jobs = [(args.base, spec, v_spec, seeds[w::args.workers], args.entropy, imitate,
                     league, args.self_play, args.min_players, args.max_players)
                    for w in range(args.workers)]
            parts = pool.map(_episodes, jobs)

            for h in heads:
                g = [sum(part[0][h][i] for part in parts) / args.batch
                     for i in range(len(heads[h].params))]
                opts[h].lr = args.imitation_lr if imitate else args.lr
                opts[h].step(heads[h].params, g, ascent=True)
            vg = [sum(part[1][i] for part in parts) / args.batch for i in range(len(value.params))]
            v_opt.step(value.params, vg, ascent=True)

            wins = sum(part[2]["wins"] for part in parts)
            decisions = sum(part[2]["decisions"] for part in parts)
            entropy = sum(part[2]["entropy"] for part in parts) / max(decisions, 1)
            row = {"iteration": it, "phase": "imitation" if imitate else "reinforce",
                   "matches": next_seed, "train_win_rate": round(wins / args.batch, 4),
                   "decisions": decisions, "entropy": round(entropy, 4),
                   "league": len(league), "minutes": round((time.time() - start) / 60, 2)}
            tracker.iteration(row)
            print(json.dumps(row), flush=True)

            if imitate and (entropy < args.imitation_entropy or it >= args.imitation):
                imitating = False
                tracker.event("phase", f"imitation ended at iteration {it}, entropy {entropy:.3f}; "
                              "reinforcement starts", iteration=it)
            if not imitate and args.self_play > 0 and (args.iterations - reinforce_left) % args.league_every == 0:
                league.append(spec_of(heads))
                league = league[-args.league_size:]
                tracker.event("league", f"snapshot {len(league)} added to the league at iteration {it}",
                              iteration=it)
            if it % args.eval_every == 0 or reinforce_left == 0:
                result = benchmark_all_sizes("learned", (args.base, spec_of(heads)),
                                             args.eval_games, args.eval_seed, args.workers)
                slim = {"mean_edge": result["mean_edge"],
                        "by_size": {n: {k: r[k] for k in ("win_rate", "ci95", "edge", "games")}
                                    for n, r in result["by_size"].items()}}
                tracker.evaluation(it, next_seed, slim,
                                   lambda p: save_heads(p, args.base, {**heads, "value": value}))
                print(json.dumps({"iteration": it, "eval_mean_edge": result["mean_edge"],
                                  "by_size": {n: r["win_rate"] for n, r in result["by_size"].items()}}),
                      flush=True)
    tracker.finish({"iterations": it, "matches": next_seed, "best_mean_edge": tracker.best_edge,
                    "minutes": round((time.time() - start) / 60, 1)})


if __name__ == "__main__":
    main()

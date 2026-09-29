"""
rl/lab.py
=========
Small tools for looking inside the learned player, used by walkthrough.ipynb.

    capture_position(seed, hour)   freeze a real match at one Hour's dice phase
    move_table(...)                every legal dice move, its features, its score
    market_table(...)              every market option at a frozen market call
    watch_match(...)               a whole match, every decision, next to the heuristic's
"""

from __future__ import annotations
import copy
import random

import numpy as np

from rl.actions import legal_moves, move_from_result, market_options
from rl.agent import make_learned_class, softmax
from rl.evaluate import HEURISTICS, SUBJECT, table, load_weights, build_heads
from simulation.runner import simulate_game
from rl.features import (state_features, move_features, market_features,
                         MOVE_NAMES, STATE_NAMES)

ROWS = ("Recharge", "Paradox", "Travel")


def describe(move) -> str:
    """A dice move in words: which functions get which value, the valve, the direction."""
    parts = []
    for r, row in enumerate(move.matrix):
        filled = [v for v in row if v]
        if filled:
            parts.append(f"{ROWS[r]} {len(filled)}x{filled[0]}")
    if move.valve:
        parts.append(f"valve {move.valve}")
    if move.travel_steps:
        way = "past" if move.direction < 0 else "future"
        parts.append(f"to the {way}" + (f", stop after {move.cap}" if move.cap is not None else ""))
    return ", ".join(parts)


def describe_market(option) -> str:
    where = " (Secret Market)" if option.secret else ""
    if option.card is None:
        return option.kind + where
    return f"{option.kind} {option.card.name} for {option.cost}{where}"


def load_heads(path: str):
    base, spec = load_weights(path)
    return base, build_heads(spec)


def _score(head, s, option_rows):
    x = np.hstack([np.repeat(s[None, :], len(option_rows), axis=0), np.stack(option_rows)])
    scores = head(x)[:, 0]
    return scores, softmax(scores)


def capture_position(seed: int, hour: int, base: str = "Aggressive", n_players: int = 4):
    """
    Play match `seed` with the heuristic in the subject seat and freeze it at
    `hour`'s dice phase. Returns (traveler, game, dice) as copies you can change.
    """
    captured = {}
    base_cls = HEURISTICS[base]

    class Recorder(base_cls):
        def choose_allocation(self, traveler, game, dice):
            if game.hour == hour and "game" not in captured:
                g = copy.deepcopy(game)
                captured["game"] = g
                captured["traveler"] = next(t for t in g.travelers if t.name == traveler.name)
                captured["dice"] = list(dice)
            return super().choose_allocation(traveler, game, dice)

    simulate_game(table(Recorder(), seed, n_players), rng=random.Random(seed))
    if "game" not in captured:
        raise ValueError(f"match {seed} ended before Hour {hour}")
    return captured["traveler"], captured["game"], captured["dice"]


def move_table(traveler, game, dice, heads=None, base: str = "Aggressive"):
    """Every legal dice move, its features, and (with heads) its score and probability."""
    import pandas as pd

    heuristic = HEURISTICS[base]()
    base_move = move_from_result(heuristic.choose_allocation(copy.deepcopy(traveler),
                                                             copy.deepcopy(game), dice))
    key = lambda m: (m.matrix, m.valve, m.direction, m.cap)
    moves = legal_moves(traveler, game, dice)
    if key(base_move) not in {key(m) for m in moves}:
        moves.append(base_move)
    s = state_features(traveler, game)
    feats = [move_features(traveler, game, m) for m in moves]
    rows = []
    for m, f in zip(moves, feats):
        row = {"move": describe(m)}
        row.update(dict(zip(MOVE_NAMES, np.round(f, 3))))
        row["heuristic's pick"] = key(m) == key(base_move)
        rows.append(row)
    df = pd.DataFrame(rows)
    if heads is not None:
        scores, p = _score(heads["dice"], s, feats)
        df.insert(1, "score", np.round(scores, 3))
        df.insert(2, "probability", np.round(p, 3))
        df = df.sort_values("score", ascending=False)
    return df


def market_table(traveler, game, revealed, renew_cost, heads=None, base: str = "Aggressive"):
    """Every market option at this call, with (given heads) the score of each."""
    import pandas as pd

    heuristic = HEURISTICS[base]()
    base_action = heuristic.choose_market_action(copy.deepcopy(traveler), copy.deepcopy(game),
                                                 revealed, renew_cost)
    options, chosen = market_options(traveler, game, revealed, renew_cost, base_action)
    s = state_features(traveler, game)
    feats = [market_features(traveler, game, o) for o in options]
    df = pd.DataFrame({"option": [describe_market(o) for o in options],
                       "heuristic's pick": [i == chosen for i in range(len(options))]})
    if heads is not None:
        scores, p = _score(heads["market"], s, feats)
        df.insert(1, "score", np.round(scores, 3))
        df.insert(2, "probability", np.round(p, 3))
        df = df.sort_values("score", ascending=False)
    return df


def state_table(traveler, game):
    import pandas as pd
    return pd.Series(np.round(state_features(traveler, game), 3), index=STATE_NAMES)


def watch_match(seed: int, heads, base: str = "Aggressive", n_players: int = 4):
    """
    One match with the learned player (greedy) in the subject seat. Every dice
    and market decision is logged next to what the heuristic would have done.
    """
    import pandas as pd

    base_cls = HEURISTICS[base]
    learned_cls = make_learned_class(base_cls)
    log = []

    class Watched(learned_cls):
        def choose_allocation(self, traveler, game, dice):
            shadow = base_cls()
            h = move_from_result(shadow.choose_allocation(copy.deepcopy(traveler),
                                                          copy.deepcopy(game), dice))
            before = (traveler.century, traveler.energy, traveler.gold, traveler.contract_points)
            result = super().choose_allocation(traveler, game, dice)
            chosen = move_from_result(result)
            log.append({"hour": game.hour, "decision": "dice", "situation": f"dice {dice}",
                        "century": before[0], "energy": before[1], "gold": before[2], "CP": before[3],
                        "learned": describe(chosen), "heuristic": describe(h),
                        "same": describe(chosen) == describe(h)})
            return result

        def choose_market_action(self, traveler, game, revealed, renew_cost):
            shadow = base_cls()
            h = shadow.choose_market_action(copy.deepcopy(traveler), copy.deepcopy(game),
                                            revealed, renew_cost)
            options, hi = market_options(traveler, game, revealed, renew_cost, h)
            action = super().choose_market_action(traveler, game, revealed, renew_cost)
            chosen = next((o for o in options if o.action == action), None)
            log.append({"hour": game.hour, "decision": "market",
                        "situation": ", ".join(c.name for c in revealed),
                        "century": traveler.century, "energy": traveler.energy,
                        "gold": traveler.gold, "CP": traveler.contract_points,
                        "learned": describe_market(chosen) if chosen else type(action).__name__,
                        "heuristic": describe_market(options[hi]),
                        "same": chosen is not None and chosen == options[hi]})
            return action

    result = simulate_game(table(Watched(heads, greedy=True), seed, n_players),
                           rng=random.Random(seed))
    final = {t.name: t.contract_points for t in result.traveler_results}
    return pd.DataFrame(log), result.winner, result.end_reason, final



def capture_market(seed: int, base: str = "Aggressive", n_players: int = 4, min_options: int = 3):
    """
    Play match `seed` and freeze the subject's first market call that offers at
    least `min_options` options. Returns (traveler, game, revealed, renew_cost).
    """
    captured = {}
    base_cls = HEURISTICS[base]

    class Recorder(base_cls):
        def choose_market_action(self, traveler, game, revealed, renew_cost):
            action = super().choose_market_action(traveler, game, revealed, renew_cost)
            if "game" not in captured:
                options, _ = market_options(traveler, game, revealed, renew_cost, action)
                if len(options) >= min_options:
                    g = copy.deepcopy(game)
                    captured["game"] = g
                    captured["traveler"] = next(t for t in g.travelers if t.name == traveler.name)
                    captured["revealed"] = [c for c in copy.deepcopy(revealed)]
                    captured["renew_cost"] = renew_cost
            return action

    simulate_game(table(Recorder(), seed, n_players), rng=random.Random(seed))
    if "game" not in captured:
        raise ValueError(f"no market call with {min_options} options in match {seed}")
    return captured["traveler"], captured["game"], captured["revealed"], captured["renew_cost"]


def latest_run(results: str = "rl/results/runs") -> str:
    """The newest run folder that has a best checkpoint."""
    import os
    runs = sorted(d for d in os.listdir(results)
                  if os.path.exists(os.path.join(results, d, "best.npz")))
    return os.path.join(results, runs[-1])

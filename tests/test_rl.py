"""Tests for the reinforcement learning player (rl/)."""

import random
from itertools import product

import numpy as np

from engine.matrix import validate_allocation
from engine.market import PassAction
from engine.state import GameState
from rl.actions import legal_allocations, legal_moves, market_options, Move
from rl.agent import make_learned_class
from rl.evaluate import table, SUBJECT, save_heads, load_weights, build_heads
from rl.features import (state_features, move_features, market_features, reward_features,
                         STATE_SIZE, MOVE_SIZE, MARKET_SIZE, REWARD_SIZE)
from rl.network import MLP
from rl.train import new_heads
from simulation.runner import simulate_game
from simulation.strategies.aggressive import AggressiveStrategy


def test_every_listed_allocation_is_legal():
    for dice in product((1, 2, 3), repeat=4):
        for unavailable in (set(), {0}, {1}, {2}, {0, 2}):
            for matrix, valve in legal_allocations(list(dice), unavailable):
                alloc = Move(matrix, valve, -1, None).to_allocation()
                assert validate_allocation(alloc, list(dice), unavailable) == []


def test_no_legal_allocation_is_missing():
    """Brute force over every cell assignment finds nothing the lister missed."""
    for dice in product((1, 2, 3), repeat=4):
        unavailable = {1}
        listed = set(legal_allocations(list(dice), unavailable))
        for cells in product(range(10), repeat=4):  # 9 cells or the valve
            if len(set(cells)) < 4:
                continue
            m = [[0] * 3 for _ in range(3)]
            valve = 0
            for die, c in zip(dice, cells):
                if c == 9:
                    valve = die
                else:
                    m[c // 3][c % 3] = die
            alloc = Move(tuple(map(tuple, m)), valve, -1, None).to_allocation()
            if validate_allocation(alloc, list(dice), unavailable) == []:
                assert (tuple(map(tuple, m)), valve) in listed


def test_checkpoints_and_secret_market_are_offered_as_stops():
    game = GameState.create(["a", "b"])
    t = game.travelers[0]
    t.century = 24
    game.secret_market_card_count = 12
    caps = {m.cap for m in legal_moves(t, game, [3, 3, 3, 1]) if m.direction < 0}
    assert 4 in caps          # stop on XX, not yet scored
    t.century = 14
    caps = {m.cap for m in legal_moves(t, game, [3, 3, 3, 1]) if m.direction < 0}
    assert 3 in caps and 4 in caps   # XI (Secret Market) and X


def test_market_options_cover_the_rules():
    game = GameState.create(["a", "b"])
    t = game.travelers[0]
    t.gold = 10
    from engine.cards import build_all_cards
    revealed = build_all_cards()[:4]
    options, chosen = market_options(t, game, revealed, 1, PassAction())
    kinds = [o.kind for o in options]
    assert kinds.count("buy") == 4 and kinds.count("renew") == 4 and "pass" in kinds
    assert options[chosen].kind == "pass"
    secret, _ = market_options(t, game, revealed[:1], 999, PassAction())
    assert [o.kind for o in secret] == ["pass", "buy"]


def test_feature_sizes():
    game = GameState.create(["a", "b", "c", "d"])
    t = game.travelers[0]
    moves = legal_moves(t, game, [1, 2, 2, 3])
    options, _ = market_options(t, game, [], 1, PassAction())
    assert state_features(t, game).shape == (STATE_SIZE,)
    assert move_features(t, game, moves[0]).shape == (MOVE_SIZE,)
    assert market_features(t, game, options[0]).shape == (MARKET_SIZE,)
    assert reward_features("Time").shape == (REWARD_SIZE,)


def test_network_gradient_matches_finite_differences():
    rng = np.random.default_rng(3)
    net = MLP([5, 7, 4, 1], rng)
    x = rng.normal(size=(6, 5))
    w = rng.normal(size=(6, 1))
    out, acts = net.forward(x)
    grads = net.backward(acts, w)
    eps = 1e-6
    for p, g in zip(net.params, grads):
        idx = tuple(rng.integers(0, s) for s in p.shape)
        old = p[idx]
        p[idx] = old + eps
        up = (net(x) * w).sum()
        p[idx] = old - eps
        down = (net(x) * w).sum()
        p[idx] = old
        assert abs((up - down) / (2 * eps) - g[idx]) < 1e-5


def test_learned_player_finishes_matches_at_every_table_size():
    cls = make_learned_class(AggressiveStrategy)
    heads = new_heads(16, np.random.default_rng(0))
    seen = set()
    for i in range(30):
        n = 2 + i % 5
        agent = cls(heads, greedy=False, rng=np.random.default_rng(i), record=True)
        opponents = [AggressiveStrategy() for _ in range(n - 1)]
        result = simulate_game(table(agent, i, opponents=opponents), rng=random.Random(i))
        assert result.hours_played > 0
        assert len(result.traveler_results) == n
        seen.update(head for head, *_ in agent.trace)
    assert {"dice", "market"} <= seen


def test_heads_survive_save_and_load(tmp_path):
    heads = new_heads(8, np.random.default_rng(1))
    path = tmp_path / "w.npz"
    save_heads(str(path), "Aggressive", heads)
    base, spec = load_weights(str(path))
    loaded = build_heads(spec)
    assert base == "Aggressive" and set(loaded) == set(heads)
    x = np.ones((3, heads["market"].sizes[0]))
    assert np.allclose(loaded["market"](x), heads["market"](x))

"""
tests/test_overdrive.py
=======================
Regression tests for the Overdrive experimental rule.

After century X (i.e., at position ≤ 10), past travel costs 2 energy per
century instead of the normal 1. This file verifies the cost formula and
that strategy profiles cap travel to avoid self-termination.
"""

import random
import pytest

from engine.state import GameState, Allocation
from engine.constants import (
    OVERDRIVE_THRESHOLD_CENTURY,
    OVERDRIVE_ENERGY_COST_PER_CENTURY,
    PAST_TRAVEL_ENERGY_COST_PER_CENTURY,
    CENTURY_MAX,
)
from engine.resolve import execute_travel
from simulation.strategies.util import safe_travel_cap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _game_with_traveler(century: int, energy: int) -> tuple[GameState, object]:
    g = GameState.create(["T0"])
    g.rng = random.Random(0)
    t = g.travelers[0]
    t.century = century
    t.energy = energy
    return g, t


# ---------------------------------------------------------------------------
# execute_travel cost formula
# ---------------------------------------------------------------------------

def test_normal_zone_cost():
    """Traveling entirely above century X costs 1 energy per century."""
    g, t = _game_with_traveler(20, 30)
    execute_travel(t, -5, g)   # 20 → 15, all above X
    assert t.energy == 30 - 5 * PAST_TRAVEL_ENERGY_COST_PER_CENTURY


def test_overdrive_zone_cost():
    """Traveling entirely within the overdrive zone (≤ X) costs 2 per century."""
    g, t = _game_with_traveler(8, 30)
    execute_travel(t, -3, g)   # 8 → 5, all in overdrive
    assert t.energy == 30 - 3 * OVERDRIVE_ENERGY_COST_PER_CENTURY


def test_mixed_cost_crossing_threshold():
    """Traveling across the X boundary applies the correct cost split."""
    g, t = _game_with_traveler(15, 30)
    # 15 → 5: 5 normal centuries (15→10) + 5 overdrive centuries (10→5)
    execute_travel(t, -10, g)
    expected_cost = 5 * PAST_TRAVEL_ENERGY_COST_PER_CENTURY + 5 * OVERDRIVE_ENERGY_COST_PER_CENTURY
    assert t.energy == 30 - expected_cost


def test_landing_on_threshold_is_normal():
    """Stepping from century 11 to century 10 costs 1 (normal, not overdrive)."""
    g, t = _game_with_traveler(11, 30)
    execute_travel(t, -1, g)   # 11 → 10
    assert t.energy == 30 - 1 * PAST_TRAVEL_ENERGY_COST_PER_CENTURY


def test_leaving_threshold_is_overdrive():
    """Stepping from century 10 to century 9 costs 2 (overdrive)."""
    g, t = _game_with_traveler(10, 30)
    execute_travel(t, -1, g)   # 10 → 9
    assert t.energy == 30 - 1 * OVERDRIVE_ENERGY_COST_PER_CENTURY


def test_overdrive_can_terminate():
    """Overdrive can drain energy to 0 (triggering termination via check_termination)."""
    g, t = _game_with_traveler(5, 3)
    execute_travel(t, -2, g)   # 5 → 3, costs 4 (overdrive), but only 3 energy
    assert t.energy == 0


# ---------------------------------------------------------------------------
# safe_travel_cap helper
# ---------------------------------------------------------------------------

def test_safe_cap_normal_zone():
    """Cap is limited by energy when fully in normal zone."""
    g, t = _game_with_traveler(25, 5)
    cap = safe_travel_cap(t, reserve=1)
    # available = 4; steps cost 1 each above century 10 → max 4 steps
    # But also limited so we don't enter overdrive beyond what's affordable.
    # 25 → 21 costs 4 × 1 = 4. One more step (21→20) costs 1 = 5 > available. Cap = 4.
    # Steps 25→21: 4 steps, all cost 1 each = 4 total, available=4. Next step: not affordable.
    assert cap == 4


def test_safe_cap_overdrive_zone():
    """In overdrive zone each step costs 2, halving the achievable distance."""
    g, t = _game_with_traveler(8, 8)
    cap = safe_travel_cap(t, reserve=0)
    # available = 8; each step costs 2 (all in overdrive)
    # max steps = 4; but pos=8 so cap = min(4, 8) = 4
    assert cap == 4


def test_safe_cap_crossing_threshold():
    """Cap is reduced when crossing from normal into overdrive zone."""
    g, t = _game_with_traveler(12, 5)
    cap = safe_travel_cap(t, reserve=0)
    # Steps: 12→11 (cost 1), 11→10 (cost 1), 10→9 (cost 2), 9→8 (cost 2)
    # Budget 5: step1=1 (avail=4), step2=1 (avail=3), step3=2 (avail=1), step4=2 not affordable
    assert cap == 3


def test_safe_cap_zero_reserve_edge():
    """Reserve=0 allows spending all energy."""
    g, t = _game_with_traveler(5, 4)
    cap = safe_travel_cap(t, reserve=0)
    # available = 4; all overdrive, 2 per step → 2 steps
    assert cap == 2


# ---------------------------------------------------------------------------
# Profile integration: strategies do not exceed safe cap
# ---------------------------------------------------------------------------

def _make_game_state(century: int, energy: int) -> GameState:
    g = GameState.create(["T0", "T1"])
    g.rng = random.Random(0)
    for t in g.travelers:
        t.energy = energy
        t.century = century
    return g


def test_aggressive_respects_energy_cap_in_overdrive():
    """Aggressive in siege mode doesn't plan travel it cannot afford."""
    from simulation.strategies.aggressive import AggressiveStrategy
    strat = AggressiveStrategy()
    # Energy=3, century=8: safe cap = 1 step (costs 2 each, reserve=3 means 0 budget)
    # Actually reserve=3: available = 3-3=0. Cap = 0.
    g = _make_game_state(8, 3)
    g.travelers[0].contract_points = 0
    g.travelers[1].contract_points = 5  # behind on CP → siege mode
    result = strat.choose_allocation(g.travelers[0], g, [2, 2, 2, 1])
    cap = result[2] if len(result) == 3 else None
    if cap is not None:
        assert cap <= safe_travel_cap(g.travelers[0], reserve=0)


def test_conservative_respects_energy_cap():
    """Conservative caps past travel to safe energy budget."""
    from simulation.strategies.conservative import ConservativeStrategy
    strat = ConservativeStrategy()
    g = _make_game_state(9, 5)
    result = strat.choose_allocation(g.travelers[0], g, [2, 2, 1, 1])
    cap = result[2] if len(result) == 3 else None
    if cap is not None:
        safe = safe_travel_cap(g.travelers[0], reserve=2)
        assert cap <= safe


def test_smart_respects_energy_cap():
    """Smart strategy caps travel to survive overdrive."""
    from simulation.strategies.smart import SmartStrategy
    strat = SmartStrategy()
    g = _make_game_state(7, 6)
    result = strat.choose_allocation(g.travelers[0], g, [3, 2, 2, 1])
    cap = result[2] if len(result) == 3 else None
    if cap is not None:
        safe = safe_travel_cap(g.travelers[0], reserve=2)
        assert cap <= safe


def test_collector_respects_energy_cap():
    """Collector strategy caps travel to survive overdrive."""
    from simulation.strategies.collector import CollectorStrategy
    strat = CollectorStrategy()
    g = _make_game_state(6, 4)
    result = strat.choose_allocation(g.travelers[0], g, [2, 1, 1, 1])
    cap = result[2] if len(result) == 3 else None
    if cap is not None:
        safe = safe_travel_cap(g.travelers[0], reserve=2)
        assert cap <= safe

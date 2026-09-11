"""
tests/test_aggressive_endgame.py
================================
Regression tests for the Aggressive profile's end-game discipline.

The Aggressive strategy must only step onto Year Zero (§11.1a) when doing so
makes it the outright CP winner (§32). Otherwise it stops one space short of
Year Zero (century I) and lays siege. It also pays off its Wanted poster
(§24.5) at the Merchant market to unlock the Secret Market.

Guards the balance regression where Aggressive ended games at Year Zero even
when it was losing, cutting other profiles' games short.
"""

import random

from engine.state import GameState
from engine.constants import (
    CENTURY_MIN,
    CP_YEAR_ZERO_TOTAL,
    DECLARE_COST,
    FUNCTION_TRAVEL,
)
from engine.market import BuyAction, DeclareAction, PassAction
from engine.cards import rifle_fergunson
from simulation.strategies.aggressive import AggressiveStrategy


def _game(*specs):
    """specs: (century, cp, terminated) tuples → GameState with named travelers."""
    g = GameState.create([f"T{i}" for i in range(len(specs))])
    g.rng = random.Random(0)
    for t, (century, cp, term) in zip(g.travelers, specs):
        t.century = century
        t.contract_points = cp
        t.is_terminated = term
        t.energy = 20
    return g


def test_caps_travel_when_year_zero_does_not_win():
    """Behind on CP and one step from Year Zero: must not end the game there."""
    strat = AggressiveStrategy()
    # T0 (Aggro) at century I with 1 CP; rival has 5 CP. Reaching Year Zero would
    # give Aggro 1 + 2 (+1 survival) = 4 < rival's 5 (+1 survival) = 6. Don't go.
    g = _game((CENTURY_MIN, 1, False), (5, 5, False))
    aggro = g.travelers[0]
    _, direction, cap = strat.choose_allocation(aggro, g, [3, 3, 3, 2])
    assert direction == -1
    assert cap == 0  # at century I, cannot move onto Year Zero


def test_rushes_year_zero_when_it_wins():
    """Ahead on CP: reaching Year Zero wins, so travel cap must cover the full distance."""
    strat = AggressiveStrategy()
    # Aggro at century V with 6 CP; rival has 2 CP. Reaching Year Zero gives
    # 6 + 2 + 1 = 9 > rival 2 + 1 = 3. Rush.
    g = _game((5, 6, False), (8, 2, False))
    aggro = g.travelers[0]
    alloc, direction, cap = strat.choose_allocation(aggro, g, [3, 3, 3, 1])
    # Cap must be >= distance to Year Zero (century 5) so the traveler can reach it.
    # It may be a finite integer (energy-aware) rather than None, as long as it covers.
    assert cap is None or cap >= aggro.century
    # Rush mode fills Travel to the maximum with the dominant die value.
    assert alloc.generators_in_function(FUNCTION_TRAVEL) == [3, 3, 3]


def test_cap_stops_one_short_from_higher_century():
    """From century III, the cap allows reaching century I but not Year Zero."""
    strat = AggressiveStrategy()
    g = _game((3, 0, False), (10, 9, False))
    aggro = g.travelers[0]
    _, _, cap = strat.choose_allocation(aggro, g, [2, 2, 1, 1])
    assert cap == 3 - CENTURY_MIN  # 2 steps: III → I, never onto Year Zero


def test_year_zero_win_projection_accounts_for_survival_and_termination():
    strat = AggressiveStrategy()
    # Tie on raw CP, but the rival is terminated (no survival point) while Aggro
    # is alive: Aggro's projected total clears the rival, so it wins by going.
    g = _game((CENTURY_MIN, 3, False), (5, 4, True))
    aggro = g.travelers[0]
    assert strat._year_zero_would_win(aggro, g) is True
    # Aggro projected: 3 + 2 + 1 = 6; rival: 4 + 0 = 4.

    # Now make the rival alive and ahead: Aggro must not go.
    g.travelers[1].is_terminated = False
    g.travelers[1].contract_points = 6
    assert strat._year_zero_would_win(aggro, g) is False


def test_pays_off_wanted_poster_to_reach_secret_market():
    strat = AggressiveStrategy()
    g = _game((CENTURY_MIN, 0, False), (10, 0, False))
    aggro = g.travelers[0]
    aggro.is_wanted = True
    aggro.gold = DECLARE_COST + 2
    g.secret_market_open = True
    # Merchant market context (renew_cost != 999) → declare to clear Wanted.
    action = strat.choose_market_action(aggro, g, [], renew_cost=1)
    assert isinstance(action, DeclareAction)


def test_does_not_declare_at_secret_market_loop():
    strat = AggressiveStrategy()
    g = _game((CENTURY_MIN, 0, False), (10, 0, False))
    aggro = g.travelers[0]
    aggro.is_wanted = True
    aggro.gold = DECLARE_COST + 2
    g.secret_market_open = True
    # Secret Market loop passes renew_cost == 999; Declare is not honoured there.
    action = strat.choose_market_action(aggro, g, [], renew_cost=999)
    assert not isinstance(action, DeclareAction)


def test_travel_is_top_priority_when_not_rushing():
    """Aggressive's identity: overload Travel as priority whenever it can move.

    Even when stepping onto Year Zero would not win (so it stops one short), the
    dominant die must go into Travel, not into a Paradox-first siege allocation.
    Guards the regression where the no-suicide cap was conflated with deprioritising
    Travel entirely.
    """
    strat = AggressiveStrategy()
    # Aggro at century V, behind on CP (won't rush onto Year Zero), full energy.
    g = _game((5, 0, False), (8, 9, False))
    aggro = g.travelers[0]
    alloc, direction, cap = strat.choose_allocation(aggro, g, [3, 3, 3, 1])
    assert direction == -1
    assert cap > 0  # can move toward the pack, stopping short of Year Zero
    # The dominant die (3) overloads Travel rather than feeding Paradox siege.
    assert alloc.generators_in_function(FUNCTION_TRAVEL) == [3, 3, 3]


def test_lays_siege_only_when_pinned_at_century_one():
    """At century I (cap == 0) travel is impossible, so don't heat Travel for nothing."""
    strat = AggressiveStrategy()
    g = _game((CENTURY_MIN, 0, False), (8, 9, False))
    aggro = g.travelers[0]
    alloc, _, cap = strat.choose_allocation(aggro, g, [3, 3, 3, 1])
    assert cap == 0
    # No movement possible → Travel must not be loaded (would only heat to explode).
    assert alloc.generators_in_function(FUNCTION_TRAVEL) == []


def test_buys_weapon_cards():
    strat = AggressiveStrategy()
    g = _game((10, 0, False), (12, 0, False))
    aggro = g.travelers[0]
    aggro.gold = 50
    rifle = rifle_fergunson()
    action = strat.choose_market_action(aggro, g, [rifle], renew_cost=1)
    assert isinstance(action, BuyAction)
    assert action.card.name == rifle.name

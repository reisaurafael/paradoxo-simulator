"""
tests/test_matrix_sequential_survival.py
=========================================
Regression tests for module-ordered, energy-aware Travel and the survival
mechanics around termination.

Because the matrix resolves module-by-module across the Hour (§12.1), in the
order Recharge (1-3), Paradox (4-6), Heating (7), Travel (8-9), a Travel
decision must read the energy left by the *earlier* modules, not the energy
the traveler had when the allocation was chosen. The engine therefore:

  * caps past-travel so a traveler never spends energy it no longer has and
    self-terminates mid-board (§30.1 travel is "up to" the value); the only
    exception is deliberately stepping onto Year Zero (§30.4);
  * credits a self-inflicted killing blow (past-travel cost §30.2, motor
    explosion §15.2) to nobody: the kill belongs to whichever instance
    landed the killing blow (§18, §28.1);
  * lets a traveler deny an *enemy* kill by recycling its least-important
    equipped object at instant speed for its recycle value (§3.3, §21.1),
    while self-inflicted deaths still terminate.
"""

import random

from engine.state import GameState, Allocation
from engine.constants import FUNCTION_TRAVEL, YEAR_ZERO
from engine.matrix import place
from engine.resolve import (
    survival_capped_steps,
    execute_travel,
    resolve_module_8,
    check_termination,
)
from engine import combat
from engine.cards import rifle_fergunson, toalha


def _game(*centuries, energy=10):
    g = GameState.create([f"T{i}" for i in range(len(centuries))])
    g.rng = random.Random(0)
    for t, c in zip(g.travelers, centuries):
        t.century = c
        t.energy = energy
    return g


# ---------------------------------------------------------------------------
# Energy-aware Travel: stop short instead of self-terminating
# ---------------------------------------------------------------------------

def test_survival_cap_stops_short_to_keep_energy():
    """From century 15 with 3 energy, a 5-step past plan is clamped to 2 steps."""
    g = _game(15, energy=3)
    t = g.travelers[0]
    # 1 energy/century above the overdrive zone: 2 steps cost 2, leaving 1 energy.
    assert survival_capped_steps(t, 5, direction=-1, game=g) == 2


def test_module_8_does_not_travel_into_self_termination():
    """A big Travel die is honoured only as far as energy allows (keep >= 1)."""
    g = _game(15, energy=3)
    t = g.travelers[0]
    alloc = Allocation.empty()
    place(alloc, FUNCTION_TRAVEL, 0, 3)  # heat
    place(alloc, FUNCTION_TRAVEL, 1, 3)  # module 8 = travel up to 3
    resolve_module_8(t, alloc, direction=-1, game=g, cap=None)
    assert t.century == 13   # moved only 2, not 3
    assert t.energy == 1     # survived


def test_survival_cap_allows_stepping_onto_year_zero():
    """Reaching Year Zero is a legal terminal move and is never clamped away."""
    g = _game(1, energy=1)
    t = g.travelers[0]
    # One step to Year Zero costs 2 (overdrive) > available energy, but it is
    # the game-ending destination, so it is allowed.
    assert survival_capped_steps(t, 3, direction=-1, game=g) == 1


# ---------------------------------------------------------------------------
# Self-inflicted killing blow credits nobody (§18, §28.1)
# ---------------------------------------------------------------------------

def test_self_travel_to_year_zero_credits_no_causer():
    g = _game(1, 1, energy=1)
    victim, other = g.travelers
    # Simulate earlier (non-lethal) Paradox damage this Hour from `other`.
    victim.eliminated_by = [other.name]
    cp_before = other.contract_points

    execute_travel(victim, -3, g)        # steps onto Year Zero, energy -> 0
    assert victim.century == YEAR_ZERO
    assert victim.energy == 0
    # The self-inflicted travel cost cleared the stale attribution.
    assert victim.eliminated_by == []

    assert check_termination(victim, g) is True
    # Nobody is credited for a self-termination.
    assert other.contract_points == cp_before
    assert other.is_wanted is False


# ---------------------------------------------------------------------------
# Instant Recycle denies an enemy kill (§3.3, §21.1), enemy hits only
# ---------------------------------------------------------------------------

def test_enemy_kill_is_denied_by_instant_recycle():
    g = _game(10, 10, energy=10)
    victim, causer = g.travelers
    victim.hand = [rifle_fergunson()]     # recycle value 3
    causer.energy = 10

    combat.lose_energy(g, victim, 10, source=causer, kind="paradox")
    assert victim.energy == 0
    cp_before = causer.contract_points

    # The enemy hit is denied: the victim recycles to survive, is not terminated,
    # and the causer earns nothing.
    assert check_termination(victim, g) is False
    assert victim.is_terminated is False
    assert victim.energy == 3              # recovered the rifle's recycle value
    assert victim.hand == []               # the rifle was recycled away
    assert causer.contract_points == cp_before
    assert causer.is_wanted is False


def test_instant_recycle_keeps_the_more_valuable_card():
    """When choosing, the least-important (lowest recycle value) card goes first."""
    g = _game(10, 10, energy=10)
    victim, causer = g.travelers
    rifle = rifle_fergunson()             # recycle value 3 (keep)
    towel = toalha()                      # recycle value 1 (recycle this one)
    victim.hand = [rifle, towel]

    combat.lose_energy(g, victim, 12, source=causer, kind="paradox")
    assert victim.energy == 0

    assert check_termination(victim, g) is False
    assert victim.energy == 1                       # towel's recycle value
    held = {c.name for c in victim.hand}
    assert rifle.name in held and towel.name not in held


def test_enemy_kill_terminates_when_no_recyclable_card():
    g = _game(10, 10, energy=10)
    victim, causer = g.travelers
    # No equipment to recycle: the enemy kill stands.
    combat.lose_energy(g, victim, 12, source=causer, kind="paradox")
    cp_before = causer.contract_points

    assert check_termination(victim, g) is True
    assert victim.is_terminated is True
    assert causer.contract_points == cp_before + 1
    assert causer.is_wanted is True

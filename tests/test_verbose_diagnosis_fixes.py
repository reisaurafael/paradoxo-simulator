"""
tests/test_verbose_diagnosis_fixes.py
=====================================
Regression tests for the batch of fixes that came out of a verbose single-game
diagnosis:

  * Porcelana market discount floors at 1 gold (never free), and the public
    ``effective_card_cost`` helper reflects the charge the engine actually makes.
  * Aggressive never travels itself to termination when it cannot reach Year Zero.
  * Collector buys the easiest-to-deliver reachable card (a nearby weapon beats a
    distant non-weapon) and prices cards with the Porcelana discount.
  * Conservative does not commit its dominant dice to a Travel that would only
    explode (booms already near the limit).
"""

import random

from engine.state import GameState, Allocation
from engine.constants import (
    FUNCTION_TRAVEL, FUNCTION_RECHARGE, BOOM_LIMIT, CENTURY_MIN,
)
from engine.market import effective_card_cost
from engine.cards import (
    porcelana, rifle_fergunson, espada_de_laser, maquina_voadora_da_vinci,
)
from simulation.strategies.aggressive import AggressiveStrategy
from simulation.strategies.collector import CollectorStrategy
from simulation.strategies.conservative import ConservativeStrategy


# ---------------------------------------------------------------------------
# Porcelana discount / effective_card_cost
# ---------------------------------------------------------------------------

def test_porcelana_discount_floors_at_one_gold():
    g = GameState.create(["T0"])
    t = g.travelers[0]
    t.hand = [porcelana()]
    rifle = rifle_fergunson()          # base 3g → 2g with Porcelana
    cheap = porcelana()                # base 1g → stays 1g (never free)
    assert effective_card_cost(rifle, t) == rifle.gold_cost - 1
    assert effective_card_cost(cheap, t) == 1


def test_no_discount_without_porcelana():
    g = GameState.create(["T0"])
    t = g.travelers[0]
    t.hand = []
    rifle = rifle_fergunson()
    assert effective_card_cost(rifle, t) == rifle.gold_cost


# ---------------------------------------------------------------------------
# Aggressive: no self-suicide travel
# ---------------------------------------------------------------------------

def test_aggressive_does_not_suicide_in_overdrive():
    """At low energy deep in the overdrive zone, with no rivals to make Year Zero
    a win, the cap must leave the traveler alive (it cannot reach Year Zero)."""
    g = GameState.create(["A", "B"])
    g.rng = random.Random(0)
    a, b = g.travelers
    a.century, a.energy = 7, 6          # overdrive zone: 2 energy/century
    b.century, b.energy, b.contract_points = 20, 20, 5  # B clearly ahead → no rush win

    alloc, direction, cap = AggressiveStrategy().choose_allocation(a, g, [1, 1, 1, 2])
    # Worst-case energy spent if it travels the full cap, charged at overdrive rate.
    spent = sum(2 if (a.century - s) <= 10 else 1 for s in range(cap))
    assert a.energy - spent >= 1, f"cap={cap} would terminate the traveler"


# ---------------------------------------------------------------------------
# Collector: easiest-to-deliver selection + effective pricing
# ---------------------------------------------------------------------------

def test_collector_prefers_nearby_deliverable_weapon():
    """A laser sword 3 centuries away beats a rifle 9 away, every delivery is +1 CP."""
    g = GameState.create(["K"])
    k = g.travelers[0]
    k.century, k.gold, k.energy = 27, 6, 15
    k.hand = []

    rifle = rifle_fergunson()             # delivery_century 18 → 9 steps
    sword = espada_de_laser()             # delivery_century 24 → 3 steps
    maquina = maquina_voadora_da_vinci()  # delivery_century 15 → 12 steps
    revealed = [maquina, rifle, sword]

    action = CollectorStrategy().choose_market_action(k, g, revealed, renew_cost=1)
    assert getattr(action, "card", None) is sword


def test_collector_buys_card_affordable_only_after_porcelana_discount():
    g = GameState.create(["K"])
    k = g.travelers[0]
    k.century, k.gold, k.energy = 27, 2, 15
    rifle = rifle_fergunson()             # base 3g, 2g with Porcelana
    k.hand = [porcelana()]
    # With only 2 gold the base-cost check would reject the 3g card; the
    # discounted 2g cost makes it affordable, so the Collector should buy it
    # rather than fall through to a pointless renew.
    action = CollectorStrategy().choose_market_action(k, g, [rifle], renew_cost=1)
    assert getattr(action, "card", None) is rifle


# ---------------------------------------------------------------------------
# Conservative: don't heat into a guaranteed explosion
# ---------------------------------------------------------------------------

def test_conservative_skips_travel_when_heating_would_explode():
    g = GameState.create(["C", "X"])
    c, x = g.travelers
    c.century, c.energy, c.booms = 15, 15, BOOM_LIMIT - 3   # +3 heat → explode
    x.century, x.energy = 5, 20

    alloc, direction, cap = ConservativeStrategy().choose_allocation(c, g, [2, 2, 3, 3])
    travel_dice = alloc.generators_in_function(FUNCTION_TRAVEL)
    assert travel_dice == [], "should not commit dice to a Travel that just explodes"
    # The freed dice should instead be earning energy/gold in Recharge.
    assert alloc.generators_in_function(FUNCTION_RECHARGE)

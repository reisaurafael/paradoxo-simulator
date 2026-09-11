"""
tests/test_termination.py
=========================
Regression tests for termination, respawn, and the Wanted consequences of a
kill (§28 Terminated Travelers, §33 Wanted, §24.5 Secret Market).

Guards:
    BUG-001: a terminated traveler respawns at XXX with 12 + Σ recycle value,
              keeps gold and booms, and acts again from the next Hour.
    BUG-002: the traveler responsible for a termination earns +1 CP and
              becomes Wanted; the terminated traveler loses Wanted (§28.5);
              re-terminating an already-terminated traveler yields no reward (§28.4).
    BUG-004: a Wanted traveler cannot Buy at the Secret Market.
"""

import random

from engine.state import GameState, Allocation
from engine.constants import (
    CENTURY_MAX,
    SECRET_MARKET_CENTURY,
    TERMINATION_RESPAWN_ENERGY_BASE,
    FUNCTION_RECHARGE,
)
from engine.resolve import check_termination, advance_overload
from engine import combat
from engine.cards import rifle_fergunson, toalha, build_secret_market
from engine.market import MerchantDeck, resolve_market_phase, BuyAction, PassAction
from simulation.runner import _check_win_conditions, _determine_cp_winner
from simulation.strategies.base import Strategy


def _game(*centuries, energy=10, gold=0):
    g = GameState.create([f"T{i}" for i in range(len(centuries))])
    g.rng = random.Random(0)
    for t, c in zip(g.travelers, centuries):
        t.century = c
        t.energy = energy
        t.gold = gold
    return g


# ---------------------------------------------------------------------------
# BUG-001: respawn (§28.2-28.3)
# ---------------------------------------------------------------------------

def test_termination_schedules_respawn_and_recycles_equipment():
    g = _game(10, 20)
    victim = g.travelers[0]
    rifle = rifle_fergunson()   # recycle_value 3
    towel = toalha()            # recycle_value 1
    victim.hand = [rifle, towel]
    victim.energy = 0

    assert check_termination(victim, g) is True
    # Marked terminated (permanent), energy zeroed, equipment recycled away.
    assert victim.is_terminated is True
    assert victim.survival_eligible is False
    assert victim.awaiting_respawn is True
    assert victim.energy == 0
    assert victim.hand == []
    # Respawn energy banked = 12 + (3 + 1).
    assert victim.respawn_energy == TERMINATION_RESPAWN_ENERGY_BASE + 4


def test_respawn_returns_traveler_to_xxx_next_hour_keeping_gold_and_booms():
    g = _game(5, 20)
    victim = g.travelers[0]
    victim.gold = 7
    victim.booms = 3
    victim.hand = [rifle_fergunson()]
    victim.energy = 0
    check_termination(victim, g)

    # Start of the next Hour: respawn fires.
    advance_overload(victim)

    # The Terminated condition is permanent: respawn does NOT clear it (§28.3).
    assert victim.is_terminated is True
    assert victim.survival_eligible is False
    # The transient out-this-Hour flag is cleared: they play normally now.
    assert victim.awaiting_respawn is False
    assert victim.is_active() is True
    assert victim.century == CENTURY_MAX
    assert victim.energy == TERMINATION_RESPAWN_ENERGY_BASE + 3
    # Gold and booms are preserved across termination (§28.2).
    assert victim.gold == 7
    assert victim.booms == 3
    # Stale damage attribution is cleared on respawn.
    assert victim.eliminated_by == []


def test_terminated_traveler_with_no_equipment_respawns_with_base_energy():
    g = _game(10, 20)
    victim = g.travelers[0]
    victim.energy = 0
    check_termination(victim, g)
    advance_overload(victim)
    assert victim.energy == TERMINATION_RESPAWN_ENERGY_BASE
    assert victim.century == CENTURY_MAX


# ---------------------------------------------------------------------------
# BUG-002: Wanted and CP for the responsible traveler (§33.1, §28.4-28.5)
# ---------------------------------------------------------------------------

def test_causer_earns_cp_and_becomes_wanted():
    g = _game(10, 12)
    victim, causer = g.travelers
    # The causer deals the lethal blow through the central pipeline.
    causer.energy = 10
    combat.lose_energy(g, victim, 20, source=causer, kind="paradox")
    assert victim.energy == 0

    cp_before = causer.contract_points
    assert check_termination(victim, g) is True
    assert causer.contract_points == cp_before + 1
    assert causer.is_wanted is True
    # §8.2: the termination CP queues a Reward for the causer.
    assert causer.name in g.cp_rewards_pending


def test_terminated_traveler_loses_wanted_status():
    g = _game(10, 20)
    victim = g.travelers[0]
    victim.is_wanted = True
    victim.energy = 0
    check_termination(victim, g)
    assert victim.is_wanted is False  # §28.5


def test_reterminating_an_already_terminated_traveler_gives_no_reward():
    g = _game(10, 12)
    victim, causer = g.travelers
    # Victim has already been terminated once this game (permanent condition),
    # and has since respawned (not awaiting respawn).
    victim.is_terminated = True
    victim.eliminated_by = [causer.name]
    victim.energy = 0

    cp_before = causer.contract_points
    check_termination(victim, g)
    # §28.4: no CP and no Wanted for re-terminating.
    assert causer.contract_points == cp_before
    assert causer.is_wanted is False


# ---------------------------------------------------------------------------
# BUG-004: Wanted cannot Buy at the Secret Market (§24.5)
# ---------------------------------------------------------------------------

class _AlwaysBuyFirst(Strategy):
    """Buys the first revealed card it can, then passes."""

    def choose_allocation(self, traveler, game, dice):
        return Allocation.empty(), 1

    def choose_market_action(self, traveler, game, revealed, renew_cost):
        for card in revealed:
            if traveler.gold >= card.gold_cost and traveler.can_hold(card):
                return BuyAction(card)
        return PassAction()


def test_wanted_traveler_cannot_buy_at_secret_market():
    g = _game(SECRET_MARKET_CENTURY, energy=10, gold=50)
    buyer = g.travelers[0]
    buyer.is_wanted = True

    deck = MerchantDeck(rng=random.Random(0))
    deck.secret_market.open()
    g.secret_market_open = True
    g.merchant_century = 1  # keep the buyer away from the Merchant market

    strategies = {buyer.name: _AlwaysBuyFirst()}
    resolve_market_phase(g, deck, strategies, random.Random(0))

    # Wanted: nothing acquired from the Secret Market.
    assert buyer.hand == []


def test_non_wanted_traveler_can_buy_at_secret_market():
    g = _game(SECRET_MARKET_CENTURY, energy=10, gold=50)
    buyer = g.travelers[0]
    buyer.is_wanted = False

    deck = MerchantDeck(rng=random.Random(0))
    deck.secret_market.open()
    g.secret_market_open = True
    g.merchant_century = 1

    strategies = {buyer.name: _AlwaysBuyFirst()}
    resolve_market_phase(g, deck, strategies, random.Random(0))

    # Not Wanted: the Secret Market card is bought and equipped.
    assert len(buyer.hand) == 1


# ---------------------------------------------------------------------------
# Game end: only one traveler not Terminated (§11.1c, §32.3, §32.2)
# ---------------------------------------------------------------------------

def test_game_ends_when_only_one_traveler_not_terminated():
    g = _game(10, 10)
    survivor, victim = g.travelers
    # victim carries the permanent Terminated condition (still technically playing).
    victim.is_terminated = True

    result = _check_win_conditions(g)
    assert result == (survivor.name, "last_traveler")


def test_terminated_but_respawned_traveler_still_keeps_game_going():
    """A terminated-but-respawned traveler is NOT 'out': as long as two
    travelers have never been terminated, the game continues (§11.1c)."""
    g = _game(10, 10, 10)
    a, b, c = g.travelers
    # c has been terminated before but has respawned and plays normally.
    c.is_terminated = True
    assert c.awaiting_respawn is False
    # a and b have never been terminated → two remain → game continues.
    assert _check_win_conditions(g) is None


def test_responsible_terminator_gets_termination_and_stabilisation_cp():
    """The traveler who terminates the last other traveler earns the
    termination contract CP (+1, at the kill) and the stabilisation bonus (+1,
    for ending the game), plus the survival bonus (+1, alive at game end)."""
    g = _game(10, 12)
    killer, victim = g.travelers
    combat.lose_energy(g, victim, 20, source=killer, kind="paradox")

    # The kill itself: termination contract CP + Wanted (§8.1b, §33.1).
    check_termination(victim, g)
    assert killer.contract_points == 1
    assert killer.is_wanted is True

    # Ending the game by being the last standing: +1 stabilisation, +1 survival.
    result = _check_win_conditions(g)
    assert result == (killer.name, "last_traveler")
    assert killer.contract_points == 3
    assert _determine_cp_winner(g) == killer.name


def test_survival_cp_only_for_non_terminated_players_at_game_end():
    g = _game(10, 10, 10)
    a, b, c = g.travelers
    # c was terminated earlier this game (and would respawn), not survival-eligible.
    c.is_terminated = True
    # a reaches Year Zero, ending the game.
    a.century = 0

    result = _check_win_conditions(g)
    assert result[1] == "year_zero"
    # a: Year Zero contract+stabilisation (CP_YEAR_ZERO_TOTAL) plus survival.
    assert a.contract_points >= 1
    # b is alive and never terminated → gets the survival bonus.
    assert b.contract_points == 1
    # c was terminated → no survival bonus.
    assert c.contract_points == 0


def test_terminated_traveler_can_still_win_on_cp():
    """§28.3: a traveler terminated at any point may still win if they hold the
    most CP when the game ends."""
    g = _game(10, 10)
    leader, survivor = g.travelers
    leader.contract_points = 5
    leader.is_terminated = True          # terminated, pending respawn

    # Survivor ends the game (last standing) and picks up stabilisation + survival.
    _check_win_conditions(g)
    # Even so, the terminated leader still has the most CP and wins.
    assert _determine_cp_winner(g) == leader.name

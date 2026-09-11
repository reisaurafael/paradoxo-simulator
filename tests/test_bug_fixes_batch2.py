"""
tests/test_bug_fixes_batch2.py
===============================
Regression tests for BUG-007 through BUG-015 (second audit batch).

Guards:
    BUG-007: Wanted bounty (4 gold) paid to killer (§33.2)
    BUG-008: Chaos I damage goes through combat pipeline (§23.3, §18)
    BUG-009: Market voucher grants Secret Market access (§23.3 Time II)
    BUG-010: Free-recycle fires Caldeirão da Agnes trigger (§21.3)
    BUG-011: Simulador does not block atemporal travelers (§42)
    BUG-012: Mona Lisa occupies the exact vacated slot in deck._revealed (§6.1)
    BUG-013: §10.3 linear progression is enforced by validate_allocation
    BUG-014: Janela do Tempo / Primeiro Smartphone grant Secret Market access (§24.4)
    BUG-015: Carretel de Pesca and Heliógrafo set Wanted after Merchant steal (§26.4)
"""

import random

from engine.state import GameState, Allocation
from engine.constants import (
    SECRET_MARKET_CENTURY,
    WANTED_BOUNTY,
    FUNCTION_TRAVEL,
)
from engine.resolve import check_termination
from engine import combat
from engine.cards import (
    escudo_viking,
    caldeirao_da_agnes,
    carretel_de_pesca,
    heliografo_de_niepce,
    mona_lisa,
    janela_do_tempo,
    primeiro_smartphone,
    simulador_da_realidade,
)
from engine.market import MerchantDeck, resolve_market_phase, BuyAction, PassAction
from engine.matrix import validate_allocation
from simulation.runner import _resolve_free_recycles
from simulation.strategies.base import Strategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _game(*centuries, energy=10, gold=0):
    g = GameState.create([f"T{i}" for i in range(len(centuries))])
    g.rng = random.Random(0)
    for t, c in zip(g.travelers, centuries):
        t.century = c
        t.energy = energy
        t.gold = gold
    return g


class _PassStrategy(Strategy):
    """Always passes on every decision."""
    def choose_allocation(self, traveler, game, dice):
        return Allocation.empty(), 1

    def choose_market_action(self, traveler, game, revealed, renew_cost):
        return PassAction()

    def choose_items_to_recycle(self, traveler, game):
        return list(traveler.hand)


class _BuyFirstStrategy(Strategy):
    """Buys the first card it can afford, then passes."""
    def choose_allocation(self, traveler, game, dice):
        return Allocation.empty(), 1

    def choose_market_action(self, traveler, game, revealed, renew_cost):
        for card in revealed:
            if traveler.gold >= card.gold_cost and traveler.can_hold(card):
                return BuyAction(card)
        return PassAction()


# ---------------------------------------------------------------------------
# BUG-007: Wanted bounty paid on kill (§33.2)
# ---------------------------------------------------------------------------

def test_wanted_bounty_paid_to_killer():
    g = _game(10, 20)
    victim, killer = g.travelers
    victim.is_wanted = True
    victim.eliminated_by = [killer.name]
    victim.energy = 0
    gold_before = killer.gold

    check_termination(victim, g)

    assert killer.gold == gold_before + WANTED_BOUNTY


def test_no_bounty_when_victim_not_wanted():
    g = _game(10, 20)
    victim, killer = g.travelers
    victim.is_wanted = False
    victim.eliminated_by = [killer.name]
    victim.energy = 0
    gold_before = killer.gold

    check_termination(victim, g)

    assert killer.gold == gold_before


def test_no_bounty_on_retermination_even_if_victim_was_wanted():
    """§28.4: re-termination suppresses reward; no bounty even if Wanted."""
    g = _game(10, 20)
    victim, killer = g.travelers
    victim.is_terminated = True   # already terminated before
    victim.is_wanted = True
    victim.eliminated_by = [killer.name]
    victim.energy = 0
    gold_before = killer.gold

    check_termination(victim, g)

    assert killer.gold == gold_before


# ---------------------------------------------------------------------------
# BUG-008: Chaos I uses combat pipeline (§23.3, §18)
# ---------------------------------------------------------------------------

def test_chaos_i_respects_escudo_viking():
    """Escudo Viking reduces the first enemy-caused hit by 2; Chaos I counts."""
    from engine.rewards import _chaos
    g = _game(10, 20)
    dealer, target = g.travelers
    shield = escudo_viking()
    target.hand = [shield]
    target.energy = 10

    _chaos(dealer, g, None, 1, None)  # die=1 → Chaos I

    # Escudo reduces 3 → 1; target loses 1, not 3.
    assert target.energy == 9


def test_chaos_i_does_not_affect_respawning_travelers():
    from engine.rewards import _chaos
    g = _game(10, 20)
    dealer, target = g.travelers
    target.awaiting_respawn = True
    target.energy = 10

    _chaos(dealer, g, None, 1, None)

    assert target.energy == 10  # untouched; respawning travelers skip Chaos I


# ---------------------------------------------------------------------------
# BUG-009: Market voucher grants Secret Market access (§23.3 Time II)
# ---------------------------------------------------------------------------

def test_voucher_grants_secret_market_access():
    g = _game(5, energy=10, gold=50)     # traveler is NOT on century XI
    buyer = g.travelers[0]
    buyer.market_voucher = 1

    deck = MerchantDeck(rng=random.Random(0))
    deck.secret_market.open()
    g.secret_market_open = True
    g.merchant_century = 99               # buyer is nowhere near the Merchant

    strategies = {buyer.name: _BuyFirstStrategy()}
    resolve_market_phase(g, deck, strategies, random.Random(0))

    assert len(buyer.hand) == 1           # voucher granted Secret Market access


def test_voucher_consumed_for_secret_market_when_sole_access_reason():
    g = _game(5, energy=10, gold=50)
    buyer = g.travelers[0]
    buyer.market_voucher = 2

    deck = MerchantDeck(rng=random.Random(0))
    deck.secret_market.open()
    g.secret_market_open = True
    g.merchant_century = 99

    strategies = {buyer.name: _BuyFirstStrategy()}
    resolve_market_phase(g, deck, strategies, random.Random(0))

    assert buyer.market_voucher == 1      # one voucher consumed


def test_voucher_not_consumed_when_synchronic_with_both_markets():
    """If the traveler is naturally synchronic with both markets, no voucher is consumed."""
    g = _game(SECRET_MARKET_CENTURY, energy=10, gold=50)
    buyer = g.travelers[0]
    buyer.market_voucher = 1

    deck = MerchantDeck(rng=random.Random(0))
    deck.secret_market.open()
    g.secret_market_open = True
    # Merchant also at XI → buyer is naturally synchronic everywhere.
    g.merchant_century = SECRET_MARKET_CENTURY

    strategies = {buyer.name: _BuyFirstStrategy()}
    resolve_market_phase(g, deck, strategies, random.Random(0))

    assert buyer.market_voucher == 1      # never needed the voucher


# ---------------------------------------------------------------------------
# BUG-010: Free-recycle fires Caldeirão da Agnes trigger (§21.3)
# ---------------------------------------------------------------------------

def test_free_recycle_fires_caldeirão_trigger():
    """Caldeirão da Agnes: when another traveler recycles a card, the holder
    may steal it. _resolve_free_recycles must call combat.recycle_card so the
    trigger fires."""
    from engine.cards import rifle_fergunson
    g = _game(10, 20, energy=20)
    recycler, watcher = g.travelers
    rifle = rifle_fergunson()
    recycler.hand = [rifle]

    cauldron = caldeirao_da_agnes()
    watcher.hand = [cauldron]

    # Strategy: recycler recycles the rifle; watcher always passes at market.
    class _RecycleOne(Strategy):
        def choose_allocation(self, t, game, dice): return Allocation.empty(), 1
        def choose_market_action(self, t, game, rev, cost): return PassAction()
        def choose_items_to_recycle(self, t, game):
            return list(t.hand) if t is recycler else []

    strats = {recycler.name: _RecycleOne(), watcher.name: _RecycleOne()}
    deck = MerchantDeck(rng=random.Random(0))

    _resolve_free_recycles(g, deck, strats)

    # Caldeirão trigger should have fired: watcher now holds the stolen rifle.
    assert rifle in watcher.hand
    assert rifle not in recycler.hand


def test_free_recycle_grants_energy_to_recycler():
    from engine.cards import rifle_fergunson
    g = _game(10, energy=5)
    recycler = g.travelers[0]
    rifle = rifle_fergunson()          # recycle_value = 3
    recycler.hand = [rifle]

    class _RecycleAll(Strategy):
        def choose_allocation(self, t, game, dice): return Allocation.empty(), 1
        def choose_market_action(self, t, game, rev, cost): return PassAction()
        def choose_items_to_recycle(self, t, game): return list(t.hand)

    strats = {recycler.name: _RecycleAll()}
    deck = MerchantDeck(rng=random.Random(0))

    _resolve_free_recycles(g, deck, strats)

    assert recycler.energy == 8        # 5 + recycle_value(3)


# ---------------------------------------------------------------------------
# BUG-011: Simulador does not block atemporal travelers (§42)
# ---------------------------------------------------------------------------

def test_simulador_does_not_block_janela_do_tempo_holder():
    g = _game(5, 20, energy=10, gold=50)
    janela_holder, simulador_holder = g.travelers
    janela_holder.hand = [janela_do_tempo()]
    simulador_holder.hand = [simulador_da_realidade()]

    merchant_pos = 20
    g.merchant_century = merchant_pos

    deck = MerchantDeck(rng=random.Random(0))
    g.secret_market_open = False

    strategies = {
        janela_holder.name: _BuyFirstStrategy(),
        simulador_holder.name: _PassStrategy(),
    }
    resolve_market_phase(g, deck, strategies, random.Random(0))

    # janela_holder is atemporal → not blocked by Simulador.
    assert len(janela_holder.hand) >= 2   # Janela + at least one purchase


def test_simulador_blocks_genuinely_non_synchronic_traveler():
    g = _game(5, 20, energy=10, gold=50)
    non_sync, simulador_holder = g.travelers
    # non_sync has no atemporal card, no voucher.
    simulador_holder.hand = [simulador_da_realidade()]

    merchant_pos = 20
    g.merchant_century = merchant_pos

    deck = MerchantDeck(rng=random.Random(0))
    g.secret_market_open = False

    strategies = {
        non_sync.name: _BuyFirstStrategy(),
        simulador_holder.name: _PassStrategy(),
    }
    resolve_market_phase(g, deck, strategies, random.Random(0))

    # non_sync is not at the Merchant and has no atemporal access → blocked.
    assert non_sync.hand == []


# ---------------------------------------------------------------------------
# BUG-012: Mona Lisa occupies the exact vacated slot (§6.1)
# ---------------------------------------------------------------------------

def test_mona_lisa_swaps_into_exact_slot():
    from engine.cards import toalha
    deck = MerchantDeck(rng=random.Random(42))
    # Snapshot the revealed list before the swap.
    revealed_before = list(deck._revealed)
    chosen = revealed_before[1]    # second slot

    g = _game(10, energy=10, gold=0)
    traveler = g.travelers[0]
    ml = mona_lisa()
    traveler.hand = [ml]
    traveler.gold = 0

    # Fire the Mona Lisa effect.
    ml.active_effect(traveler, g, (deck, chosen))

    # Mona Lisa is now at index 1 in the revealed list.
    assert deck._revealed[1] is ml
    # The chosen card is now in the traveler's hand.
    assert chosen in traveler.hand
    # Revealed count is still 4.
    assert len(deck._revealed) == 4


# ---------------------------------------------------------------------------
# BUG-013: §10.3 linear progression enforced by validate_allocation
# ---------------------------------------------------------------------------

def test_linear_progression_gap_in_col_1_is_rejected():
    alloc = Allocation.empty()
    alloc.set(FUNCTION_TRAVEL, 0, 2)     # module 7 filled (value=2)
    alloc.set(FUNCTION_TRAVEL, 2, 2)     # module 9 filled, gap at col 1!
    errors = validate_allocation(
        alloc,
        dice=[2, 2, 0, 0],
        unavailable_functions=set(),
    )
    assert any("10.3" in e for e in errors)


def test_valid_linear_progression_passes():
    alloc = Allocation.empty()
    alloc.set(FUNCTION_TRAVEL, 0, 1)
    alloc.set(FUNCTION_TRAVEL, 1, 1)
    errors = validate_allocation(
        alloc,
        dice=[1, 1, 0, 0],
        unavailable_functions=set(),
    )
    assert not any("10.3" in e for e in errors)


# ---------------------------------------------------------------------------
# BUG-014: Janela / Smartphone grant Secret Market access (§24.4)
# ---------------------------------------------------------------------------

def test_janela_do_tempo_grants_secret_market_access():
    g = _game(5, energy=10, gold=50)      # NOT on century XI
    buyer = g.travelers[0]
    buyer.hand = [janela_do_tempo()]

    deck = MerchantDeck(rng=random.Random(0))
    deck.secret_market.open()
    g.secret_market_open = True
    g.merchant_century = 99

    strategies = {buyer.name: _BuyFirstStrategy()}
    resolve_market_phase(g, deck, strategies, random.Random(0))

    # Janela grants Secret Market access; traveler acquired at least one card
    # (beyond Janela itself).
    non_janela = [c for c in buyer.hand if c.name != "Janela do Tempo"]
    assert len(non_janela) >= 1


def test_primeiro_smartphone_grants_secret_market_access():
    g = _game(5, energy=10, gold=50)
    buyer = g.travelers[0]
    buyer.hand = [primeiro_smartphone()]

    deck = MerchantDeck(rng=random.Random(0))
    deck.secret_market.open()
    g.secret_market_open = True
    g.merchant_century = 99

    strategies = {buyer.name: _BuyFirstStrategy()}
    resolve_market_phase(g, deck, strategies, random.Random(0))

    non_phone = [c for c in buyer.hand if c.name != "Primeiro Smartphone"]
    assert len(non_phone) >= 1


# ---------------------------------------------------------------------------
# BUG-015: Carretel de Pesca and Heliógrafo set Wanted after Merchant steal (§26.4)
# ---------------------------------------------------------------------------

def test_carretel_de_pesca_sets_wanted():
    deck = MerchantDeck(rng=random.Random(0))
    g = _game(10, energy=10, gold=0)
    traveler = g.travelers[0]
    carretel = carretel_de_pesca()
    traveler.hand = [carretel]
    traveler.is_wanted = False

    # Inject a deterministic RNG so carretel rolls 3 (can steal any ≤3 cost).
    g.rng = random.Random(0)

    carretel.active_effect(traveler, g, deck)

    # The traveler may or may not have stolen (depends on revealed cards and roll),
    # but IF a card was acquired, Wanted must be set.
    stolen = [c for c in traveler.hand if c is not carretel]
    if stolen:
        assert traveler.is_wanted is True


def test_carretel_de_pesca_sets_wanted_deterministic():
    """Force a steal by putting a 0-cost card in the revealed list."""
    from engine.cards import toalha
    deck = MerchantDeck(rng=random.Random(0))
    g = _game(10, energy=10, gold=0)
    traveler = g.travelers[0]
    carretel = carretel_de_pesca()
    traveler.hand = [carretel]

    # Inject a cheap card so the steal always succeeds.
    cheap = toalha()
    cheap.gold_cost = 0
    deck._revealed.insert(0, cheap)

    carretel.active_effect(traveler, g, deck)

    assert traveler.is_wanted is True
    assert cheap in traveler.hand


def test_heliografo_de_niepce_sets_wanted():
    from engine.cards import toalha
    deck = MerchantDeck(rng=random.Random(0))
    g = _game(10, energy=10, gold=0)
    traveler = g.travelers[0]
    helio = heliografo_de_niepce()
    traveler.hand = [helio]
    traveler.is_wanted = False

    chosen = deck._revealed[0]
    helio.active_effect(traveler, g, (deck, chosen))

    assert traveler.is_wanted is True
    assert chosen in traveler.hand

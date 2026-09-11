"""Tests for engine/market.py: Merchant movement, Market phase, and Delivery."""

import random
import pytest
from engine.state import GameState, TravelerState
from engine.market import (
    MerchantDeck,
    merchant_target,
    move_merchant,
    check_merchant_upgrades,
    resolve_deliveries,
    check_temporal_receptor_win,
    BuyAction, RenewAction, PassAction, UseCardAction,
)
from engine.cards import (
    sismografico, escudo_viking, maquina_de_alan_turing,
    mapa_de_geradus_mercator, toalha, bussola_de_navegacao,
    maquina_de_venda_automatica,
)


# ---------------------------------------------------------------------------
# MerchantDeck
# ---------------------------------------------------------------------------

def test_deck_starts_with_40_draw_and_4_revealed():
    deck = MerchantDeck(rng=random.Random(0))
    assert len(deck.revealed) == 4
    assert deck.draw_count == 36  # 40 - 4 revealed


def test_taking_a_card_refills_revealed():
    deck = MerchantDeck(rng=random.Random(0))
    card = deck.revealed[0]
    deck.take(card)
    assert len(deck.revealed) == 4
    assert card not in deck.revealed


def test_discarding_a_card_refills_revealed():
    deck = MerchantDeck(rng=random.Random(0))
    card = deck.revealed[0]
    deck.discard(card)
    assert len(deck.revealed) == 4


def test_deck_not_empty_initially():
    deck = MerchantDeck(rng=random.Random(0))
    assert not deck.is_empty


def test_deck_exhaustion():
    deck = MerchantDeck(rng=random.Random(0))
    while deck.revealed:
        deck.take(deck.revealed[0])
    assert deck.is_empty


# ---------------------------------------------------------------------------
# Merchant targeting
# ---------------------------------------------------------------------------

def _make_game(centuries: list[int], golds: list[int], merchant_century: int = 20) -> GameState:
    names = [f"T{i}" for i in range(len(centuries))]
    game = GameState.create(names)
    game.merchant_century = merchant_century
    for i, t in enumerate(game.travelers):
        t.century = centuries[i]
        t.gold = golds[i]
    return game


def test_merchant_targets_richest_non_synchronic():
    game = _make_game(centuries=[10, 15], golds=[5, 3], merchant_century=20)
    target = merchant_target(game)
    assert target == 10  # T0 has most gold and is not synchronic with merchant


def test_merchant_targets_secret_market_when_all_synchronic():
    game = _make_game(centuries=[20, 20], golds=[5, 3], merchant_century=20)
    from engine.constants import SECRET_MARKET_CENTURY
    target = merchant_target(game)
    assert target == SECRET_MARKET_CENTURY


def test_merchant_targets_xxx_when_also_synchronic_with_secret_market():
    from engine.constants import SECRET_MARKET_CENTURY, CENTURY_MAX
    game = _make_game(
        centuries=[SECRET_MARKET_CENTURY, SECRET_MARKET_CENTURY],
        golds=[5, 3],
        merchant_century=SECRET_MARKET_CENTURY,
    )
    target = merchant_target(game)
    assert target == CENTURY_MAX


# ---------------------------------------------------------------------------
# Merchant movement
# ---------------------------------------------------------------------------

def test_merchant_moves_toward_target():
    game = _make_game(centuries=[5], golds=[3], merchant_century=20)
    rng = random.Random(0)
    start = game.merchant_century
    move_merchant(game, rng)
    assert game.merchant_century < start


def test_merchant_never_enters_year_zero():
    game = _make_game(centuries=[1], golds=[10], merchant_century=2)
    rng = random.Random(0)
    for _ in range(20):
        move_merchant(game, rng)
    assert game.merchant_century >= 1


def test_merchant_does_not_exceed_xxx():
    from engine.constants import CENTURY_MAX
    game = _make_game(centuries=[CENTURY_MAX], golds=[10], merchant_century=CENTURY_MAX - 1)
    rng = random.Random(0)
    move_merchant(game, rng)
    assert game.merchant_century <= CENTURY_MAX


# ---------------------------------------------------------------------------
# Merchant speed upgrades
# ---------------------------------------------------------------------------

def test_upgrade_xx_triggers_on_century_20():
    game = _make_game(centuries=[20], golds=[0], merchant_century=25)
    assert not game.merchant_upgrade_xx_triggered
    check_merchant_upgrades(game)
    assert game.merchant_upgrade_xx_triggered


def test_upgrade_x_triggers_on_century_10():
    game = _make_game(centuries=[10], golds=[0], merchant_century=25)
    assert not game.merchant_upgrade_x_triggered
    check_merchant_upgrades(game)
    assert game.merchant_upgrade_x_triggered


def test_no_upgrade_without_milestone():
    game = _make_game(centuries=[25], golds=[0], merchant_century=20)
    check_merchant_upgrades(game)
    assert not game.merchant_upgrade_xx_triggered
    assert not game.merchant_upgrade_x_triggered


# ---------------------------------------------------------------------------
# Delivery phase: century-based
# ---------------------------------------------------------------------------

def test_delivery_at_matching_century_scores_cp():
    game = GameState.create(["T0"])
    game.travelers[0].century = 2   # Sismográfico delivers at II
    card = sismografico()
    game.travelers[0].hand.append(card)
    resolve_deliveries(game)
    assert game.travelers[0].contract_points == 1
    assert "Sismográfico" in game.travelers[0].temporal_receptor
    assert len(game.travelers[0].hand) == 0


def test_delivery_rejected_at_wrong_century():
    game = GameState.create(["T0"])
    game.travelers[0].century = 5   # wrong century for Sismográfico (delivers at 2)
    card = sismografico()
    game.travelers[0].hand.append(card)
    resolve_deliveries(game)
    assert game.travelers[0].contract_points == 0
    assert len(game.travelers[0].hand) == 1  # card still in hand


def test_delivery_fills_periods():
    game = GameState.create(["T0"])
    game.travelers[0].century = 9   # Escudo Viking delivers at IX (Origins period)
    card = escudo_viking()
    game.travelers[0].hand.append(card)
    resolve_deliveries(game)
    assert "Origins" in game.travelers[0].delivered_periods


def test_delivery_strategy_can_hold_card():
    """Strategy that returns empty list should cause no deliveries."""
    from simulation.strategies.base import Strategy
    from engine.state import Allocation

    class HoldAllStrategy(Strategy):
        def choose_allocation(self, t, g, d): return Allocation.empty(), -1
        def choose_cards_to_deliver(self, t, g, deliverable): return []  # hold all

    game = GameState.create(["T0"])
    game.travelers[0].century = 2
    game.travelers[0].hand.append(sismografico())
    resolve_deliveries(game, strategies={"T0": HoldAllStrategy()})
    assert game.travelers[0].contract_points == 0
    assert len(game.travelers[0].hand) == 1


def test_no_delivery_at_wrong_century_even_with_card():
    game = GameState.create(["T0"])
    game.travelers[0].century = 15  # Toalha delivers at I (century 1)
    game.travelers[0].hand.append(toalha())
    resolve_deliveries(game)
    assert game.travelers[0].contract_points == 0


# ---------------------------------------------------------------------------
# Temporal Receptor win condition
# ---------------------------------------------------------------------------

def test_full_receptor_triggers_win():
    game = GameState.create(["T0", "T1"])
    game.travelers[0].delivered_periods = {"Origins", "Ascension", "Singularity"}
    result = check_temporal_receptor_win(game)
    assert result is True
    assert game.game_over
    assert game.winner == "T0"
    assert game.game_over_reason == "full_receptor"


def test_partial_receptor_does_not_trigger_win():
    game = GameState.create(["T0"])
    game.travelers[0].delivered_periods = {"Origins", "Ascension"}
    result = check_temporal_receptor_win(game)
    assert result is False
    assert not game.game_over


# ---------------------------------------------------------------------------
# Market phase: buy action
# ---------------------------------------------------------------------------

def test_buy_action_adds_card_to_hand():
    from simulation.strategies.base import Strategy
    from engine.state import Allocation

    class BuyFirstStrategy(Strategy):
        def choose_allocation(self, t, g, d): return Allocation.empty(), -1
        def choose_market_action(self, t, g, revealed, renew_cost):
            if revealed and t.gold >= revealed[0].gold_cost and t.can_hold(revealed[0]):
                return BuyAction(revealed[0])
            return PassAction()

    game = GameState.create(["T0"])
    game.travelers[0].gold = 10
    game.travelers[0].century = 20
    game.merchant_century = 20  # synchronic, not XXX
    deck = MerchantDeck(rng=random.Random(42))
    strategies = {"T0": BuyFirstStrategy()}
    from engine.market import resolve_market_phase
    resolve_market_phase(game, deck, strategies, random.Random(0))
    assert len(game.travelers[0].hand) >= 1


def test_on_market_buy_other_hook_fires():
    """Máquina de Venda Automática: observer gains 1 gold when another traveler buys."""
    from simulation.strategies.base import Strategy
    from engine.state import Allocation

    class BuyFirstStrategy(Strategy):
        def choose_allocation(self, t, g, d): return Allocation.empty(), -1
        def choose_market_action(self, t, g, revealed, renew_cost):
            if t.name == "Buyer" and revealed and t.gold >= revealed[0].gold_cost:
                if t.can_hold(revealed[0]):
                    return BuyAction(revealed[0])
            return PassAction()

    game = GameState.create(["Buyer", "Observer"])
    game.travelers[0].gold = 10  # Buyer
    game.travelers[0].century = 20
    game.travelers[1].century = 5   # Observer elsewhere
    game.travelers[1].hand.append(maquina_de_venda_automatica())
    game.merchant_century = 20
    deck = MerchantDeck(rng=random.Random(42))
    strategies = {"Buyer": BuyFirstStrategy(), "Observer": BuyFirstStrategy()}
    initial_gold = game.travelers[1].gold
    from engine.market import resolve_market_phase
    resolve_market_phase(game, deck, strategies, random.Random(0))
    # Observer gains 1 gold per buy by Buyer
    buys = len(game.travelers[0].hand)
    assert game.travelers[1].gold == initial_gold + buys


# ---------------------------------------------------------------------------
# UseCardAction
# ---------------------------------------------------------------------------

def test_use_card_action_activates_effect():
    """Alan Turing: destroy all revealed cards."""
    from simulation.strategies.base import Strategy
    from engine.state import Allocation
    from engine.market import resolve_market_phase

    used = []

    class TuringStrategy(Strategy):
        def choose_allocation(self, t, g, d): return Allocation.empty(), -1
        def choose_market_action(self, t, g, revealed, renew_cost):
            turing = next((c for c in t.hand if c.name == "A Máquina de Alan Turing"), None)
            if turing and turing.active_effect and not used:
                used.append(True)
                return UseCardAction(turing, context=None)
            return PassAction()

    game = GameState.create(["T0"])
    game.travelers[0].century = 20
    game.merchant_century = 20
    deck = MerchantDeck(rng=random.Random(0))
    game.travelers[0].hand.append(maquina_de_alan_turing())
    strategies = {"T0": TuringStrategy()}
    resolve_market_phase(game, deck, strategies, random.Random(0))
    # After Alan Turing, market was cleared and refilled; card is still in hand (not recycles_on_use)
    assert any(c.name == "A Máquina de Alan Turing" for c in game.travelers[0].hand)

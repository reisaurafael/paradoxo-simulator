"""Tests for engine/cards.py: all 52 cards, and how the deck splits them."""

import random

from engine.cards import (
    ALL_CARDS, build_all_cards, Card,
    seismograph, viking_shield, da_vincis_flying_machine,
    joan_of_arcs_armor, super_motor, al_jazaris_automaton,
    teslas_ac_motor, towel, gerardus_mercators_map, attilas_sword,
    queen_annes_revenge_cannon, first_time_machine,
)
from engine.market import MerchantDeck


def _split(seed: int) -> tuple[set[str], set[str]]:
    """Card names in the Merchant stock and in the Secret Market for one seeded deck."""
    deck = MerchantDeck(rng=random.Random(seed))
    merchant = {c.name for c in deck._draw + deck._revealed}
    secret = {c.name for c in deck.secret_market._hidden}
    return merchant, secret


# ---------------------------------------------------------------------------
# Deck counts and structure
# ---------------------------------------------------------------------------

def test_all_cards_has_52_entries():
    assert len(ALL_CARDS) == 52


def test_all_cards_unique_names():
    names = [f().name for f in ALL_CARDS]
    assert len(names) == len(set(names)), "Duplicate card names found"


def test_merchant_deck_has_40_cards():
    merchant, _ = _split(0)
    assert len(merchant) == 40


def test_secret_market_has_12_cards_and_the_two_specials_under_them():
    deck = MerchantDeck(rng=random.Random(0))
    names = [c.name for c in deck.secret_market._hidden]
    assert len(names) == 14
    assert names[12:] == ["The Divine Comedy", "Oppenheimer's Trinity"]


def test_merchant_and_secret_market_are_disjoint():
    merchant, secret = _split(0)
    assert merchant & secret == set()


def test_merchant_plus_secret_equals_all():
    merchant, secret = _split(0)
    specials = {"The Divine Comedy", "Oppenheimer's Trinity"}
    assert merchant | (secret - specials) == {c.name for c in build_all_cards()}


def test_secret_market_split_is_random_per_game():
    assert _split(1)[1] != _split(2)[1]


def test_deck_contains_only_card_instances():
    for card in build_all_cards():
        assert isinstance(card, Card)


def test_each_factory_produces_distinct_instances():
    c1 = seismograph()
    c2 = seismograph()
    assert c1 is not c2
    assert c1 == c2  # same name → equal


def test_card_equality_by_name():
    assert seismograph() == seismograph()
    assert seismograph() != viking_shield()


# ---------------------------------------------------------------------------
# Card fields
# ---------------------------------------------------------------------------

def test_all_cards_have_delivery_century():
    for factory in ALL_CARDS:
        card = factory()
        assert isinstance(card.delivery_century, int)
        assert 1 <= card.delivery_century <= 30, f"{card.name} delivery_century out of range"


def test_all_cards_have_valid_ability_type():
    valid = {"passive", "active", "atemporal_passive", "atemporal_active"}
    for factory in ALL_CARDS:
        card = factory()
        assert card.ability_type in valid, f"{card.name} has invalid ability_type {card.ability_type!r}"


def test_large_item_flag():
    assert da_vincis_flying_machine().is_large_item is True
    assert queen_annes_revenge_cannon().is_large_item is True
    assert seismograph().is_large_item is False


def test_recycles_on_use_flag():
    assert super_motor().recycles_on_use is True
    assert towel().recycles_on_use is False


# ---------------------------------------------------------------------------
# Passive hooks
# ---------------------------------------------------------------------------

def test_seismograph_reduces_boom_gain_by_2():
    card = seismograph()
    assert card.on_boom_gain is not None
    assert card.on_boom_gain(None, 3) == 1


def test_seismograph_clamps_boom_gain_at_zero():
    card = seismograph()
    assert card.on_boom_gain(None, 1) == 0


def test_armor_reduces_energy_loss_by_1():
    card = joan_of_arcs_armor()
    assert card.on_energy_loss is not None
    assert card.on_energy_loss(None, 4) == 3


def test_armor_minimum_energy_loss_is_1():
    card = joan_of_arcs_armor()
    assert card.on_energy_loss(None, 1) == 1   # can't reduce below 1
    assert card.on_energy_loss(None, 5) == 4   # -1 applied


def test_flying_machine_zero_travel_cost():
    card = da_vincis_flying_machine()
    assert card.on_travel_cost is not None
    assert card.on_travel_cost(None, 10, -1) == 0


def test_tesla_gains_energy_on_boom():
    from engine.state import TravelerState
    traveler = TravelerState.create("T", 2)
    card = teslas_ac_motor()
    assert card.on_boom_gain is not None
    before = traveler.energy
    result = card.on_boom_gain(traveler, 2)
    assert result == 2  # booms unchanged
    assert traveler.energy == before + 1  # +1 energy side effect


def test_automaton_reduces_booms_on_overload():
    from engine.state import TravelerState, GameState
    traveler = TravelerState.create("T", 2)
    traveler.booms = 8
    card = al_jazaris_automaton()
    assert card.on_overload is not None
    game = GameState.create(["T"])
    card.on_overload(traveler, game)
    assert traveler.booms == 3  # 8 - 5


def test_super_motor_prevents_explosion():
    from engine.state import TravelerState, GameState
    traveler = TravelerState.create("T", 2)
    traveler.booms = 10
    card = super_motor()
    game = GameState.create(["T"])
    result = card.on_explosion_check(traveler, game)
    assert result is True
    assert traveler.booms == 4  # 10 - 6


# ---------------------------------------------------------------------------
# Active effects
# ---------------------------------------------------------------------------

def test_attilas_sword_damages_older_era_travelers():
    from engine.state import GameState
    game = GameState.create(["Attacker", "Target"])
    game.travelers[0].century = 20  # Contemporary
    game.travelers[1].century = 3   # Antiquity (older)
    card = attilas_sword()
    initial_energy = game.travelers[1].energy
    card.active_effect(game.travelers[0], game, None)
    assert game.travelers[1].energy == initial_energy - 3


def test_cannon_damages_same_era_travelers():
    from engine.state import GameState
    game = GameState.create(["Attacker", "Target"])
    game.travelers[0].century = 7   # High Middle Ages
    game.travelers[1].century = 9   # High Middle Ages (same era)
    card = queen_annes_revenge_cannon()
    initial_energy = game.travelers[1].energy
    card.active_effect(game.travelers[0], game, None)
    assert game.travelers[1].energy == initial_energy - 5


def test_mercators_map_moves_traveler_toward_past():
    from engine.state import GameState
    game = GameState.create(["T"])
    game.travelers[0].century = 20
    card = gerardus_mercators_map()
    card.active_effect(game.travelers[0], game, (3, -1))
    assert game.travelers[0].century == 17


def test_first_time_machine_teleports_to_xxx():
    from engine.state import GameState
    from engine.constants import CENTURY_MAX
    game = GameState.create(["T"])
    game.travelers[0].century = 5
    card = first_time_machine()
    card.active_effect(game.travelers[0], game, None)
    assert game.travelers[0].century == CENTURY_MAX

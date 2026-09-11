"""Tests for engine/cards.py: all 52 cards and deck builder."""

import pytest
from engine.cards import (
    ALL_CARDS, build_deck, build_merchant_deck, build_secret_market,
    SECRET_MARKET_NAMES, Card,
    sismografico, escudo_viking, maquina_voadora_da_vinci,
    armadura_da_joana_darc, super_motor, automato_de_ismail_al_jazari,
    motor_de_corrente_alternada_de_tesla, maquina_a_vapor_de_james_watt,
    toalha, mapa_de_geradus_mercator, espada_de_atila,
    canhao_de_vinganca_da_rainha_anne, primeira_maquina_do_tempo,
)


# ---------------------------------------------------------------------------
# Deck counts and structure
# ---------------------------------------------------------------------------

def test_all_cards_has_52_entries():
    assert len(ALL_CARDS) == 52


def test_all_cards_unique_names():
    names = [f().name for f in ALL_CARDS]
    assert len(names) == len(set(names)), "Duplicate card names found"


def test_merchant_deck_has_40_cards():
    assert len(build_merchant_deck()) == 40


def test_secret_market_has_12_cards():
    assert len(build_secret_market()) == 12


def test_merchant_and_secret_market_are_disjoint():
    merchant_names = {c.name for c in build_merchant_deck()}
    secret_names = {c.name for c in build_secret_market()}
    assert merchant_names & secret_names == set()


def test_merchant_plus_secret_equals_all():
    all_names = {f().name for f in ALL_CARDS}
    merchant_names = {c.name for c in build_merchant_deck()}
    secret_names = {c.name for c in build_secret_market()}
    assert merchant_names | secret_names == all_names


def test_build_deck_alias_returns_40():
    assert len(build_deck()) == 40


def test_deck_contains_only_card_instances():
    for card in build_deck():
        assert isinstance(card, Card)


def test_each_factory_produces_distinct_instances():
    c1 = sismografico()
    c2 = sismografico()
    assert c1 is not c2
    assert c1 == c2  # same name → equal


def test_card_equality_by_name():
    assert sismografico() == sismografico()
    assert sismografico() != escudo_viking()


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
    assert maquina_voadora_da_vinci().is_large_item is True
    assert canhao_de_vinganca_da_rainha_anne().is_large_item is True
    assert sismografico().is_large_item is False


def test_recycles_on_use_flag():
    assert super_motor().recycles_on_use is True
    assert toalha().recycles_on_use is False


# ---------------------------------------------------------------------------
# Passive hooks
# ---------------------------------------------------------------------------

def test_sismografico_reduces_boom_gain_by_2():
    card = sismografico()
    assert card.on_boom_gain is not None
    assert card.on_boom_gain(None, 3) == 1


def test_sismografico_clamps_boom_gain_at_zero():
    card = sismografico()
    assert card.on_boom_gain(None, 1) == 0


def test_armadura_reduces_energy_loss_by_1():
    card = armadura_da_joana_darc()
    assert card.on_energy_loss is not None
    assert card.on_energy_loss(None, 4) == 3


def test_armadura_minimum_energy_loss_is_1():
    card = armadura_da_joana_darc()
    assert card.on_energy_loss(None, 1) == 1   # can't reduce below 1
    assert card.on_energy_loss(None, 5) == 4   # -1 applied


def test_maquina_voadora_zero_travel_cost():
    card = maquina_voadora_da_vinci()
    assert card.on_travel_cost is not None
    assert card.on_travel_cost(None, 10, -1) == 0


def test_tesla_gains_energy_on_boom():
    from dataclasses import dataclass
    from engine.state import TravelerState
    traveler = TravelerState.create("T", 2)
    card = motor_de_corrente_alternada_de_tesla()
    assert card.on_boom_gain is not None
    before = traveler.energy
    result = card.on_boom_gain(traveler, 2)
    assert result == 2  # booms unchanged
    assert traveler.energy == before + 1  # +1 energy side effect


def test_automato_reduces_booms_on_overload():
    from engine.state import TravelerState, GameState
    traveler = TravelerState.create("T", 2)
    traveler.booms = 8
    card = automato_de_ismail_al_jazari()
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

def test_espada_de_atila_damages_older_era_travelers():
    from engine.state import TravelerState, GameState
    game = GameState.create(["Attacker", "Target"])
    game.travelers[0].century = 20  # Contemporary
    game.travelers[1].century = 3   # Antiquity (older)
    card = espada_de_atila()
    initial_energy = game.travelers[1].energy
    card.active_effect(game.travelers[0], game, None)
    assert game.travelers[1].energy == initial_energy - 3


def test_canhao_damages_same_era_travelers():
    from engine.state import TravelerState, GameState
    game = GameState.create(["Attacker", "Target"])
    game.travelers[0].century = 7   # High Middle Ages
    game.travelers[1].century = 9   # High Middle Ages (same era)
    card = canhao_de_vinganca_da_rainha_anne()
    initial_energy = game.travelers[1].energy
    card.active_effect(game.travelers[0], game, None)
    assert game.travelers[1].energy == initial_energy - 5


def test_mapa_moves_traveler_toward_past():
    from engine.state import TravelerState, GameState
    game = GameState.create(["T"])
    game.travelers[0].century = 20
    card = mapa_de_geradus_mercator()
    card.active_effect(game.travelers[0], game, (3, -1))
    assert game.travelers[0].century == 17


def test_primeira_maquina_teleports_to_xxx():
    from engine.state import TravelerState, GameState
    from engine.constants import CENTURY_MAX
    game = GameState.create(["T"])
    game.travelers[0].century = 5
    card = primeira_maquina_do_tempo()
    card.active_effect(game.travelers[0], game, None)
    assert game.travelers[0].century == CENTURY_MAX

"""
tests/test_card_effects.py
==========================
Behavioural tests for the card effects implemented on top of the central
combat pipeline (engine/combat.py), the paradox resolver, and the Phase 4
Item Activation step in the runner.
"""

import random
import pytest

from engine.state import GameState, Allocation
from engine.constants import FUNCTION_RECHARGE, FUNCTION_PARADOX, FUNCTION_TRAVEL
from engine import combat
from engine import cards
from engine.cards import (
    ALL_CARDS, Card,
    rifle_fergunson, bandeira_vermelha_da_ching_shih, revolver_de_polvora,
    arma_de_portais, arma_de_laser, espada_do_carlos_magno, excalibur,
    polvora, calice_do_principe_dracula, espada_de_laser, escudo_viking,
    armadura_da_joana_darc, carro, telescopio_de_galileu_galilei,
    computador_quantico, sismografico, geladeira, caldeirao_da_agnes,
    porcelana, lanca_do_destino, mapa_de_geradus_mercator, primeira_maquina_do_tempo,
    canhao_de_vinganca_da_rainha_anne,
)


def _game(*centuries, energy=10, gold=0):
    g = GameState.create([f"T{i}" for i in range(len(centuries))])
    g.rng = random.Random(0)
    for t, c in zip(g.travelers, centuries):
        t.century = c
        t.energy = energy
        t.gold = gold
    return g


# ---------------------------------------------------------------------------
# Recycle values present and sane for every card (§21, §28.2)
# ---------------------------------------------------------------------------

def test_all_cards_have_recycle_value():
    for f in ALL_CARDS:
        c = f()
        assert 1 <= c.recycle_value <= 4, f"{c.name} recycle_value {c.recycle_value} out of range"


def test_specific_recycle_values():
    by_name = {f().name: f().recycle_value for f in ALL_CARDS}
    assert by_name["Canhão de Vingança da Rainha Anne"] == 4
    assert by_name["Rifle Fergunson"] == 3
    assert by_name["Toalha"] == 1
    assert by_name["Carro"] == 3


# ---------------------------------------------------------------------------
# Carro generator buff
# ---------------------------------------------------------------------------

def test_carro_adds_one_to_generator_value():
    g = _game(30)
    t = g.travelers[0]
    alloc = Allocation.empty()
    alloc.set(FUNCTION_RECHARGE, 0, 2)
    assert combat.effective_generator(t, alloc, FUNCTION_RECHARGE, 0) == 2
    t.hand.append(carro())
    assert combat.effective_generator(t, alloc, FUNCTION_RECHARGE, 0) == 3


def test_carro_does_not_buff_empty_module():
    g = _game(30)
    t = g.travelers[0]
    t.hand.append(carro())
    alloc = Allocation.empty()
    assert combat.effective_generator(t, alloc, FUNCTION_RECHARGE, 0) == 0


# ---------------------------------------------------------------------------
# Central damage pipeline: Pólvora, Cálice, Espada de Laser, Escudo Viking, Armadura
# ---------------------------------------------------------------------------

def test_polvora_adds_one_damage():
    g = _game(10, 10)
    src, tgt = g.travelers
    src.hand.append(polvora())
    lost = combat.deal_energy(g, src, [tgt], 3, kind="weapon")
    assert tgt.energy == 10 - 4  # 3 + 1 from Pólvora


def test_calice_grants_two_energy_on_hit():
    g = _game(10, 10)
    src, tgt = g.travelers
    src.energy = 10
    src.hand.append(calice_do_principe_dracula())
    combat.deal_energy(g, src, [tgt], 3, kind="weapon")
    assert src.energy == 12  # +2 because a target lost energy


def test_calice_no_gain_when_no_target_loses():
    g = _game(10, 10)
    src, tgt = g.travelers
    src.energy = 10
    tgt.energy = 0
    tgt.awaiting_respawn = True   # out this Hour: cannot be targeted (§28.1)
    src.hand.append(calice_do_principe_dracula())
    combat.deal_energy(g, src, [tgt], 3, kind="weapon")
    assert src.energy == 10


def test_espada_de_laser_reflects_loss():
    g = _game(10, 10)
    src, tgt = g.travelers
    src.energy = 10
    tgt.hand.append(espada_de_laser())
    combat.deal_energy(g, src, [tgt], 4, kind="weapon")
    assert tgt.energy == 6
    assert src.energy == 6  # reflected the 4 the holder lost


def test_escudo_viking_first_hit_reduced_only_once():
    g = _game(10, 10)
    src, tgt = g.travelers
    tgt.hand.append(escudo_viking())
    combat.deal_energy(g, src, [tgt], 5, kind="weapon")
    assert tgt.energy == 10 - 3  # first hit -2
    combat.deal_energy(g, src, [tgt], 5, kind="weapon")
    assert tgt.energy == 7 - 5  # second hit full


def test_escudo_viking_not_triggered_by_self_loss():
    g = _game(10)
    t = g.travelers[0]
    t.hand.append(escudo_viking())
    # source=None means environmental/self loss; shield must not apply
    lost = combat.lose_energy(g, t, 5, source=None)
    assert lost == 5


def test_armadura_reduces_enemy_damage_via_pipeline():
    g = _game(10, 10)
    src, tgt = g.travelers
    tgt.hand.append(armadura_da_joana_darc())
    combat.deal_energy(g, src, [tgt], 4, kind="weapon")
    assert tgt.energy == 10 - 3  # -1 from Armadura


# ---------------------------------------------------------------------------
# Weapon actives
# ---------------------------------------------------------------------------

def test_rifle_damages_by_distance_same_era():
    g = _game(2, 4)  # both Antiquity (I-V); distance 2
    src, tgt = g.travelers
    rifle_fergunson().active_effect(src, g, tgt)
    assert tgt.energy == 10 - 2


def test_rifle_no_effect_across_eras():
    g = _game(2, 25)  # Antiquity vs Timeless
    src, tgt = g.travelers
    rifle_fergunson().active_effect(src, g, tgt)
    assert tgt.energy == 10


def test_bandeira_steals_one_gold():
    g = _game(2, 4, gold=3)
    src, tgt = g.travelers
    bandeira_vermelha_da_ching_shih().active_effect(src, g, tgt)
    assert tgt.energy == 7 and tgt.gold == 2 and src.gold == 4


def test_revolver_scales_with_equipped_items():
    g = _game(10, 10)
    src, tgt = g.travelers
    tgt.hand.extend([sismografico(), porcelana()])  # 2 items
    revolver_de_polvora().active_effect(src, g, tgt)
    assert tgt.energy == 10 - 6  # 3 * 2


def test_arma_de_portais_teleports_target():
    g = _game(3, 5)  # same era Antiquity
    src, tgt = g.travelers
    arma_de_portais().active_effect(src, g, tgt)
    assert tgt.century == 3


def test_espada_carlos_magno_damage_equals_gold():
    g = _game(10, 10, gold=5)
    src, tgt = g.travelers
    espada_do_carlos_magno().active_effect(src, g, tgt)
    assert tgt.energy == 10 - 5


def test_excalibur_hits_future_travelers():
    g = _game(10, 20, 5)  # T1 is future, T2 is past
    g.rng = random.Random(1)
    src = g.travelers[0]
    excalibur().active_effect(src, g, None)
    assert g.travelers[1].energy < 10   # future traveler hit
    assert g.travelers[2].energy == 10  # past traveler untouched


def test_canhao_aoe_same_era_only():
    g = _game(7, 9, 25)  # T0,T1 High Middle Ages; T2 Timeless
    src = g.travelers[0]
    canhao_de_vinganca_da_rainha_anne().active_effect(src, g, None)
    assert g.travelers[1].energy == 5
    assert g.travelers[2].energy == 10


# ---------------------------------------------------------------------------
# Telescópio passive (game-aware travel cost)
# ---------------------------------------------------------------------------

def test_telescopio_zeroes_travel_cost_when_older_traveler_exists():
    g = _game(20, 5)  # T1 in an older era than T0
    t = g.travelers[0]
    card = telescopio_de_galileu_galilei()
    assert card.on_travel_cost(t, 3, -1, g) == 0


def test_telescopio_keeps_cost_without_older_traveler():
    g = _game(20, 25)
    t = g.travelers[0]
    card = telescopio_de_galileu_galilei()
    assert card.on_travel_cost(t, 3, -1, g) == 3


# ---------------------------------------------------------------------------
# Meta cards
# ---------------------------------------------------------------------------

def test_computador_quantico_copies_receptor_passive():
    g = _game(10, 10)
    src, tgt = g.travelers
    tgt.hand.append(computador_quantico())
    tgt.receptor_cards.append(armadura_da_joana_darc())  # delivered Armadura
    combat.deal_energy(g, src, [tgt], 4, kind="weapon")
    assert tgt.energy == 10 - 3  # Armadura passive applied through Computador Quântico


def test_geladeira_activates_receptor_active():
    g = _game(2, 4)  # same era
    src, tgt = g.travelers
    delivered = rifle_fergunson()
    src.receptor_cards.append(delivered)
    geladeira().active_effect(src, g, (delivered, tgt))  # pass Rifle's target through
    assert tgt.energy == 10 - 2  # Rifle fired via Geladeira


# ---------------------------------------------------------------------------
# Recycle event: Caldeirão da Agnes, Porcelana
# ---------------------------------------------------------------------------

def test_caldeirao_steals_recycled_card():
    g = _game(10, 10)
    owner, agnes = g.travelers
    agnes.hand.append(caldeirao_da_agnes())
    victim_card = sismografico()
    owner.hand.append(victim_card)
    combat.recycle_card(g, owner, victim_card)
    assert victim_card not in owner.hand
    assert victim_card in agnes.hand


def test_porcelana_recycles_when_owner_loses_energy():
    g = _game(10, 10)
    src, tgt = g.travelers
    p = porcelana()
    tgt.hand.append(p)
    combat.deal_energy(g, src, [tgt], 2, kind="weapon")
    assert p not in tgt.hand  # Porcelana recycled itself on the loss


# ---------------------------------------------------------------------------
# Lança do Destino paradox mirror
# ---------------------------------------------------------------------------

def test_lanca_do_destino_mirrors_future_to_past():
    from engine.paradox import resolve_paradox_pool
    g = _game(15, 25, 5)  # causer middle, one future, one past
    causer = g.travelers[0]
    causer.hand.append(lanca_do_destino())
    alloc = Allocation.empty()
    alloc.set(FUNCTION_PARADOX, 0, 2)  # future paradox value 2
    allocs = {causer.name: alloc,
              g.travelers[1].name: Allocation.empty(),
              g.travelers[2].name: Allocation.empty()}
    resolve_paradox_pool(g, g.travelers, allocs, 0)
    assert g.travelers[1].energy == 8  # future hit
    assert g.travelers[2].energy == 8  # past mirror hit


# ---------------------------------------------------------------------------
# Mapa de Geradus Mercator uses real travel costs
# ---------------------------------------------------------------------------

def test_mapa_charges_energy_for_past_travel():
    g = _game(20)
    t = g.travelers[0]
    mapa_de_geradus_mercator().active_effect(t, g, (3, -1))
    assert t.century == 17
    assert t.energy == 10 - 3  # 1 energy per century to the past


def test_primeira_maquina_resets_to_xxx_free():
    g = _game(5)
    t = g.travelers[0]
    primeira_maquina_do_tempo().active_effect(t, g, None)
    assert t.century == 30 and t.energy == 10


# ---------------------------------------------------------------------------
# Phase 4 activation through the runner
# ---------------------------------------------------------------------------

def test_activation_phase_fires_weapon():
    from simulation.runner import resolve_activation_phase
    from simulation.strategies.base import Strategy

    g = _game(10, 10)
    src, tgt = g.travelers
    weapon = arma_de_laser()
    src.hand.append(weapon)

    class FireStrategy(Strategy):
        def choose_allocation(self, t, gg, dice):
            return Allocation.empty(), -1
        def choose_activations(self, t, gg):
            if t is src:
                return [(weapon, tgt)]
            return []

    strategies = {src.name: FireStrategy(), tgt.name: FireStrategy()}
    resolve_activation_phase(g, strategies)
    assert tgt.energy == 10 - 6


def test_activation_recycles_single_use_active():
    from simulation.runner import resolve_activation_phase
    from simulation.strategies.base import Strategy
    from engine.cards import lanca_de_fogo

    g = _game(10, 10)
    src, tgt = g.travelers
    weapon = lanca_de_fogo()  # recycles_on_use
    src.hand.append(weapon)

    class FireStrategy(Strategy):
        def choose_allocation(self, t, gg, dice):
            return Allocation.empty(), -1
        def choose_activations(self, t, gg):
            return [(weapon, tgt)] if t is src else []

    strategies = {src.name: FireStrategy(), tgt.name: FireStrategy()}
    resolve_activation_phase(g, strategies)
    assert tgt.energy == 10 - 6
    assert weapon not in src.hand  # self-recycled

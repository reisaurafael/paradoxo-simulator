"""
tests/test_card_effects.py
==========================
Behavioural tests for the card effects implemented on top of the central
combat pipeline (engine/combat.py), the paradox resolver, and the Phase 4
Item Activation step in the runner.
"""

import random

from engine.state import GameState, Allocation
from engine.constants import FUNCTION_RECHARGE, FUNCTION_PARADOX
from engine import combat
from engine.cards import (
    ALL_CARDS, ferguson_rifle, ching_shihs_red_flag, gunpowder_revolver,
    wormhole_pistol, laser_gun, charlemagnes_sword, excalibur,
    gunpowder, prince_draculas_chalice, laser_sword, viking_shield,
    joan_of_arcs_armor, automobile, galileos_telescope,
    quantum_computer, seismograph, refrigerator, agnes_cauldron,
    porcelain, spear_of_destiny, gerardus_mercators_map, first_time_machine,
    queen_annes_revenge_cannon,
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
    assert by_name["Queen Anne's Revenge Cannon"] == 4
    assert by_name["Ferguson Rifle"] == 3
    assert by_name["Towel"] == 1
    assert by_name["Automobile"] == 3


# ---------------------------------------------------------------------------
# Automobile generator buff
# ---------------------------------------------------------------------------

def test_automobile_adds_one_to_generator_value():
    g = _game(30)
    t = g.travelers[0]
    alloc = Allocation.empty()
    alloc.set(FUNCTION_RECHARGE, 0, 2)
    assert combat.effective_generator(t, alloc, FUNCTION_RECHARGE, 0) == 2
    t.hand.append(automobile())
    assert combat.effective_generator(t, alloc, FUNCTION_RECHARGE, 0) == 3


def test_automobile_does_not_buff_empty_module():
    g = _game(30)
    t = g.travelers[0]
    t.hand.append(automobile())
    alloc = Allocation.empty()
    assert combat.effective_generator(t, alloc, FUNCTION_RECHARGE, 0) == 0


# ---------------------------------------------------------------------------
# Central damage pipeline: Gunpowder, Chalice, Laser Sword, Viking Shield, Joan of Arc's Armor
# ---------------------------------------------------------------------------

def test_gunpowder_adds_one_damage():
    g = _game(10, 10)
    src, tgt = g.travelers
    src.hand.append(gunpowder())
    combat.deal_energy(g, src, [tgt], 3, kind="weapon")
    assert tgt.energy == 10 - 4  # 3 + 1 from Gunpowder


def test_chalice_grants_two_energy_on_hit():
    g = _game(10, 10)
    src, tgt = g.travelers
    src.energy = 10
    src.hand.append(prince_draculas_chalice())
    combat.deal_energy(g, src, [tgt], 3, kind="weapon")
    assert src.energy == 12  # +2 because a target lost energy


def test_chalice_no_gain_when_no_target_loses():
    g = _game(10, 10)
    src, tgt = g.travelers
    src.energy = 10
    tgt.energy = 0
    tgt.awaiting_respawn = True   # out this Hour: cannot be targeted (§28.1)
    src.hand.append(prince_draculas_chalice())
    combat.deal_energy(g, src, [tgt], 3, kind="weapon")
    assert src.energy == 10


def test_laser_sword_reflects_loss():
    g = _game(10, 10)
    src, tgt = g.travelers
    src.energy = 10
    tgt.hand.append(laser_sword())
    combat.deal_energy(g, src, [tgt], 4, kind="weapon")
    assert tgt.energy == 6
    assert src.energy == 6  # reflected the 4 the holder lost


def test_viking_shield_first_hit_reduced_only_once():
    g = _game(10, 10)
    src, tgt = g.travelers
    tgt.hand.append(viking_shield())
    combat.deal_energy(g, src, [tgt], 5, kind="weapon")
    assert tgt.energy == 10 - 3  # first hit -2
    combat.deal_energy(g, src, [tgt], 5, kind="weapon")
    assert tgt.energy == 7 - 5  # second hit full


def test_viking_shield_not_triggered_by_self_loss():
    g = _game(10)
    t = g.travelers[0]
    t.hand.append(viking_shield())
    # source=None means environmental/self loss; shield must not apply
    lost = combat.lose_energy(g, t, 5, source=None)
    assert lost == 5


def test_armor_reduces_enemy_damage_via_pipeline():
    g = _game(10, 10)
    src, tgt = g.travelers
    tgt.hand.append(joan_of_arcs_armor())
    combat.deal_energy(g, src, [tgt], 4, kind="weapon")
    assert tgt.energy == 10 - 3  # -1 from Joan of Arc's Armor


# ---------------------------------------------------------------------------
# Weapon actives
# ---------------------------------------------------------------------------

def test_rifle_damages_by_distance_same_era():
    g = _game(2, 4)  # both Antiquity (I-V); distance 2
    src, tgt = g.travelers
    ferguson_rifle().active_effect(src, g, tgt)
    assert tgt.energy == 10 - 2


def test_rifle_no_effect_across_eras():
    g = _game(2, 25)  # Antiquity vs Timeless
    src, tgt = g.travelers
    ferguson_rifle().active_effect(src, g, tgt)
    assert tgt.energy == 10


def test_red_flag_steals_one_gold():
    g = _game(2, 4, gold=3)
    src, tgt = g.travelers
    ching_shihs_red_flag().active_effect(src, g, tgt)
    assert tgt.energy == 7 and tgt.gold == 2 and src.gold == 4


def test_revolver_scales_with_equipped_items():
    g = _game(10, 10)
    src, tgt = g.travelers
    tgt.hand.extend([seismograph(), porcelain()])  # 2 items
    gunpowder_revolver().active_effect(src, g, tgt)
    assert tgt.energy == 10 - 6  # 3 * 2


def test_wormhole_pistol_teleports_target():
    g = _game(3, 5)  # same era Antiquity
    src, tgt = g.travelers
    wormhole_pistol().active_effect(src, g, tgt)
    assert tgt.century == 3


def test_charlemagnes_sword_damage_equals_gold():
    g = _game(10, 10, gold=5)
    src, tgt = g.travelers
    charlemagnes_sword().active_effect(src, g, tgt)
    assert tgt.energy == 10 - 5


def test_excalibur_hits_future_travelers():
    g = _game(10, 20, 5)  # T1 is future, T2 is past
    g.rng = random.Random(1)
    src = g.travelers[0]
    excalibur().active_effect(src, g, None)
    assert g.travelers[1].energy < 10   # future traveler hit
    assert g.travelers[2].energy == 10  # past traveler untouched


def test_cannon_aoe_same_era_only():
    g = _game(7, 9, 25)  # T0,T1 High Middle Ages; T2 Timeless
    src = g.travelers[0]
    queen_annes_revenge_cannon().active_effect(src, g, None)
    assert g.travelers[1].energy == 5
    assert g.travelers[2].energy == 10


# ---------------------------------------------------------------------------
# Galileo's Telescope passive (game-aware travel cost)
# ---------------------------------------------------------------------------

def test_telescope_zeroes_travel_cost_when_older_traveler_exists():
    g = _game(20, 5)  # T1 in an older era than T0
    t = g.travelers[0]
    card = galileos_telescope()
    assert card.on_travel_cost(t, 3, -1, g) == 0


def test_telescope_keeps_cost_without_older_traveler():
    g = _game(20, 25)
    t = g.travelers[0]
    card = galileos_telescope()
    assert card.on_travel_cost(t, 3, -1, g) == 3


# ---------------------------------------------------------------------------
# Meta cards
# ---------------------------------------------------------------------------

def test_quantum_computer_copies_receptor_passive():
    g = _game(10, 10)
    src, tgt = g.travelers
    tgt.hand.append(quantum_computer())
    tgt.receptor_cards.append(joan_of_arcs_armor())  # delivered Joan of Arc's Armor
    combat.deal_energy(g, src, [tgt], 4, kind="weapon")
    assert tgt.energy == 10 - 3  # Joan of Arc's Armor passive applied through Quantum Computer


def test_refrigerator_activates_receptor_active():
    g = _game(2, 4)  # same era
    src, tgt = g.travelers
    delivered = ferguson_rifle()
    src.receptor_cards.append(delivered)
    refrigerator().active_effect(src, g, (delivered, tgt))  # pass Rifle's target through
    assert tgt.energy == 10 - 2  # Rifle fired via Refrigerator


# ---------------------------------------------------------------------------
# Recycle event: Agnes's Cauldron, Porcelain
# ---------------------------------------------------------------------------

def test_cauldron_steals_recycled_card():
    g = _game(10, 10)
    owner, agnes = g.travelers
    agnes.hand.append(agnes_cauldron())
    victim_card = seismograph()
    owner.hand.append(victim_card)
    combat.recycle_card(g, owner, victim_card)
    assert victim_card not in owner.hand
    assert victim_card in agnes.hand


def test_porcelain_recycles_when_owner_loses_energy():
    g = _game(10, 10)
    src, tgt = g.travelers
    p = porcelain()
    tgt.hand.append(p)
    combat.deal_energy(g, src, [tgt], 2, kind="weapon")
    assert p not in tgt.hand  # Porcelain recycled itself on the loss


# ---------------------------------------------------------------------------
# Spear of Destiny paradox mirror
# ---------------------------------------------------------------------------

def test_spear_of_destiny_mirrors_future_to_past():
    from engine.paradox import resolve_paradox_pool
    g = _game(15, 25, 5)  # causer middle, one future, one past
    causer = g.travelers[0]
    causer.hand.append(spear_of_destiny())
    alloc = Allocation.empty()
    alloc.set(FUNCTION_PARADOX, 0, 2)  # future paradox value 2
    allocs = {causer.name: alloc,
              g.travelers[1].name: Allocation.empty(),
              g.travelers[2].name: Allocation.empty()}
    resolve_paradox_pool(g, g.travelers, allocs, 0)
    assert g.travelers[1].energy == 8  # future hit
    assert g.travelers[2].energy == 8  # past mirror hit


# ---------------------------------------------------------------------------
# Gerardus Mercator's Map uses real travel costs
# ---------------------------------------------------------------------------

def test_mercators_map_charges_energy_for_past_travel():
    g = _game(20)
    t = g.travelers[0]
    gerardus_mercators_map().active_effect(t, g, (3, -1))
    assert t.century == 17
    assert t.energy == 10 - 3  # 1 energy per century to the past


def test_first_time_machine_resets_to_xxx_free():
    g = _game(5)
    t = g.travelers[0]
    first_time_machine().active_effect(t, g, None)
    assert t.century == 30 and t.energy == 10


# ---------------------------------------------------------------------------
# Phase 4 activation through the runner
# ---------------------------------------------------------------------------

def test_activation_phase_fires_weapon():
    from simulation.runner import resolve_activation_phase
    from simulation.strategies.base import Strategy

    g = _game(10, 10)
    src, tgt = g.travelers
    weapon = laser_gun()
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
    from engine.cards import fire_lance

    g = _game(10, 10)
    src, tgt = g.travelers
    weapon = fire_lance()  # recycles_on_use
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

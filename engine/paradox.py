"""
engine/paradox.py
=================
Paradox damage resolution for Paradoxo.

§18 defines a pool mechanic with distance-ordered application:

    18.1  A paradox damages every traveler in a portion of the timeline
          relative to the causer's century:
            - module 4 (col 0): future, higher centuries than the causer
            - module 5 (col 1): present, the causer's own century
            - module 6 (col 2): past, lower centuries than the causer
          The causer is NEVER affected by their own paradox.

    18.2  Each affected traveler loses energy equal to the generator value.

    18.3  Paradoxes that resolve at the same instant form one pool. Within
          the pool, damage is applied from smallest emitter-to-target
          distance to largest (to assign elimination responsibility).

    18.4  Emitter-to-target pairs of equal distance resolve together.

Every hit is applied through engine/combat.lose_energy, so the source-side
amplifier (Pólvora), target shields (Escudo Viking, Armadura), reflection
(Espada de Laser) and the Carro generator buff all apply consistently. Two
card behaviours live here because they are paradox-specific:

    - Lança do Destino: a future paradox also emits a past paradox of equal value.
    - Simulador da Realidade: causing a present paradox recycles the card.
    - Cálice do Príncipe Drácula: causing 1+ travelers to lose energy grants +2.
"""

from __future__ import annotations
from dataclasses import dataclass

from engine.state import TravelerState, GameState, Allocation
from engine.timeline import distance
from engine.constants import FUNCTION_PARADOX
from engine import combat


@dataclass
class ParadoxHit:
    """One emitter-to-target damage event in the pool."""
    causer_name: str
    target_name: str
    damage: int
    dist: int
    hour: int


def _targets_for_module(
    causer: TravelerState,
    col: int,               # 0=future, 1=present, 2=past
    candidates: list[TravelerState],
) -> list[TravelerState]:
    """Valid targets for a paradox from `causer` in the given direction (§18.1)."""
    pos = causer.century
    result = []
    for t in candidates:
        if t is causer or t.awaiting_respawn:
            continue
        if col == 0 and t.century > pos:
            result.append(t)
        elif col == 1 and t.century == pos:
            result.append(t)
        elif col == 2 and t.century < pos:
            result.append(t)
    return result


def resolve_paradox_pool(
    game: GameState,
    active_travelers: list[TravelerState],
    allocations: dict[str, Allocation],
    paradox_col: int,
) -> list[ParadoxHit]:
    """
    Resolve one paradox module (col 0, 1, or 2) across all travelers (§18.3).

    Builds the full pool of (causer, target, damage, distance) tuples, sorts by
    distance ascending, applies damage in that order via the combat pipeline,
    then resolves the paradox-specific card effects.
    """
    hour = game.hour
    pool: list[ParadoxHit] = []
    traveler_map = {t.name: t for t in active_travelers}

    def add_hits(causer: TravelerState, col: int, damage: int) -> None:
        for target in _targets_for_module(causer, col, active_travelers):
            pool.append(ParadoxHit(
                causer_name=causer.name,
                target_name=target.name,
                damage=damage,
                dist=distance(causer.century, target.century),
                hour=hour,
            ))

    for causer in active_travelers:
        alloc = allocations.get(causer.name)
        if alloc is None:
            continue
        damage = combat.effective_generator(causer, alloc, FUNCTION_PARADOX, paradox_col)
        if damage == 0:
            continue

        add_hits(causer, paradox_col, damage)

        # Lança do Destino: a future paradox also fires a past paradox of equal value.
        if paradox_col == 0 and any(c.name == "Lança do Destino" for c in causer.hand):
            add_hits(causer, 2, damage)

        # Simulador da Realidade recycles itself when its holder causes a present paradox.
        if paradox_col == 1:
            sim = next((c for c in causer.hand if c.name == "Simulador da Realidade"), None)
            if sim is not None:
                combat.recycle_card(game, causer, sim)

    if not pool:
        return []

    # Smallest distance first; stable sort keeps equal-distance pairs together (§18.4).
    pool.sort(key=lambda h: h.dist)

    causer_hits: dict[str, int] = {}
    applied: list[ParadoxHit] = []
    for hit in pool:
        target = traveler_map.get(hit.target_name)
        causer = traveler_map.get(hit.causer_name)
        if target is None or target.awaiting_respawn or causer is None:
            applied.append(hit)
            continue
        lost = combat.lose_energy(game, target, hit.damage, source=causer, kind="paradox")
        if lost > 0:
            causer_hits[hit.causer_name] = causer_hits.get(hit.causer_name, 0) + 1
        applied.append(hit)

    # Cálice do Príncipe Drácula: +2 energy to each causer that made someone lose energy.
    for name, hits in causer_hits.items():
        if hits and any(c.name == "Cálice do Príncipe Drácula" for c in traveler_map[name].hand):
            traveler_map[name].energy += 2

    return applied

"""
engine/paradox.py
=================
Paradox damage resolution.

§18 defines a pool mechanic with distance-ordered application:

    18.1  A paradox damages every traveler in a portion of the timeline
          relative to the causer's century:
            - module 4 (col 0): future, higher centuries than the causer
            - module 5 (col 1): present, the causer's own century
            - module 6 (col 2): past, lower centuries than the causer
          The causer is NEVER affected by their own paradox.

    18.2  Each affected traveler loses energy equal to the generator value.
          The PAST module (6) deals DOUBLE its value:
          it only takes a die when the Paradox row holds three, an overload, and
          like module 9's double travel it counts twice. The function still shuts
          for the next Hour. Future (4) and Present (5) are unchanged.

    18.3  Paradoxes that resolve at the same instant form one pool. Within
          the pool, damage is applied from smallest emitter-to-target
          distance to largest (to assign elimination responsibility).

    18.4  Emitter-to-target pairs of equal distance resolve together.

Every hit goes through engine/combat.lose_energy, so Gunpowder, Viking Shield,
Joan of Arc's Armor, Laser Sword and the Automobile's generator bonus apply the
same way as for any other damage. Three card behaviours live here because they
only concern paradoxes:

    - Spear of Destiny: a future paradox also emits a past paradox of equal value
      (the future die's value: it is not module 6's die, so it is not doubled).
    - Reality Simulator: causing a present paradox recycles the card.
    - Prince Dracula's Chalice: +2 energy once per module for a causer who made
      at least one traveler lose energy.
"""

from __future__ import annotations
from dataclasses import dataclass

from engine.state import TravelerState, GameState, Allocation
from engine.timeline import distance
from engine.constants import FUNCTION_PARADOX, PARADOX_PAST_MULTIPLIER
from engine import constants as _C
from engine import combat


@dataclass
class ParadoxHit:
    """One emitter-to-target damage event in the pool."""
    causer_name: str
    target_name: str
    damage: int
    dist: int
    hour: int


def past_is_dead(traveler: TravelerState) -> bool:
    """Terminated travelers lose the PAST module: a die there overloads
    the row but deals nothing. constants.TERMINATED_PAST_MODE picks the reading."""
    mode = _C.TERMINATED_PAST_MODE
    if mode == "ever":
        return bool(getattr(traveler, "is_terminated", False))
    if mode == "grace":
        immune = getattr(traveler, "is_atemporal_immune", None)
        return bool(getattr(traveler, "is_terminated", False)) and bool(immune and immune())
    return False


def module_damage(causer: TravelerState, alloc: Allocation, col: int) -> int:
    """The energy one Paradox module deals to each traveler it reaches, before the
    target's own modifiers (Gunpowder, shields, armor, reflection in combat.py).

    The die's value as the module reads it (Automobile and matrix buffs included,
    combat.effective_generator), doubled on the PAST module (col 2): that module
    only holds a die when the row is overloaded, and it counts twice, like module
    9's double travel."""
    if col == 2 and past_is_dead(causer):
        return 0
    v = combat.effective_generator(causer, alloc, FUNCTION_PARADOX, col)
    return v * PARADOX_PAST_MULTIPLIER if col == 2 else v


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
        damage = module_damage(causer, alloc, paradox_col)
        if damage == 0:
            continue

        add_hits(causer, paradox_col, damage)

        # Spear of Destiny: a future paradox also fires a past paradox of equal value
        # (the future die's value, never doubled: it is not module 6's die).
        if paradox_col == 0 and any(c.name == "Spear of Destiny" for c in causer.hand):
            add_hits(causer, 2, damage)

        # Reality Simulator recycles itself when its holder causes a present paradox.
        if paradox_col == 1:
            sim = next((c for c in causer.hand if c.name == "Reality Simulator"), None)
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

    # Prince Dracula's Chalice: +2 energy to each causer that made someone lose energy.
    for name, hits in causer_hits.items():
        if hits and any(c.name == "Prince Dracula's Chalice" for c in traveler_map[name].hand):
            traveler_map[name].energy += 2

    return applied

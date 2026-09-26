"""
simulation/strategies/util.py
==============================
Helpers shared by the strategy profiles.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from engine.constants import CENTURY_MAX, ERAS, OVERDRIVE_THRESHOLD_CENTURY
from engine.timeline import eras_for_century, same_era

if TYPE_CHECKING:
    from engine.state import TravelerState, GameState
    from engine.cards import Card


# Cards whose active effect needs the Merchant deck, which Phase 4 does not
# hand to the strategies.
_MARKET_DECK_CARDS = {
    "Alan Turing's Machine",
    "Fishing Reel",
    "Mona Lisa",
    "Eyeglasses",
    "Book of Mysteries of Alexandria",
    "Niépce's Heliograph",
    "Thomas Edison's Lamp",
    "Woodblock Print",
}

# Cards whose active effect needs no context (area effects, or the user alone).
_NO_CONTEXT_CARDS = {
    "Queen Anne's Revenge Cannon",
    "Excalibur",
    "Attila's Sword",
    "The First Time Machine",
}

# Cards whose active effect needs a target traveler.
_TARGET_TRAVELER_CARDS = {
    "Ferguson Rifle",
    "Fire Lance",
    "Ching Shih's Red Flag",
    "Laser Gun",
    "Portal Gun",
    "Gunpowder Revolver",
    "Charlemagne's Sword",
}


def paradox_can_terminate(
    traveler: "TravelerState",
    game: "GameState",
    dice: list[int],
) -> bool:
    """True if using the best available die in Paradox could drop any rival to 0 energy.

    The non-Aggressive profiles use this to decide when Paradox deserves more
    dice than Travel: if a kill is on the table, commit to it; otherwise keep
    the dice in Recharge. It checks every active rival regardless of direction.
    Actual reach depends on how many columns get filled, but "could the best
    die kill anyone?" is the right threshold for deciding whether to invest at all.
    """
    if not dice:
        return False
    best_val = max(dice)
    return any(
        t.energy <= best_val
        for t in game.travelers
        if t is not traveler and not t.awaiting_respawn
    )


def astrolabe_destination(traveler: "TravelerState") -> int | None:
    """Where an Astrolabe should take the traveler: the oldest century of their
    current era, or None if they are already there (or on Year Zero)."""
    eras = eras_for_century(traveler.century)
    if not eras:
        return None
    era_start = min(ERAS[e][0] for e in eras)
    if era_start >= traveler.century:
        return None
    return era_start


def refrigerator_context(
    receptor_card: "Card",
    traveler: "TravelerState",
    game: "GameState",
) -> tuple[bool, object]:
    """
    Decide whether a receptor card can be used through the Refrigerator during
    Phase 4, and return the context to pass to its effect.

    Returns (can_use, sub_context). Cards that need the Merchant deck, or have
    no useful target right now, return (False, None).
    """
    name = receptor_card.name

    if name in _MARKET_DECK_CARDS:
        return False, None

    if name in _NO_CONTEXT_CARDS:
        return True, None

    if name in _TARGET_TRAVELER_CARDS:
        # Aim at the weakest rival: synchronic first, then anyone in the era.
        era_enemies = [
            t for t in game.travelers
            if t is not traveler and not t.awaiting_respawn
            and same_era(traveler.century, t.century)
        ]
        sync_enemies = [t for t in era_enemies if t.century == traveler.century]
        target = (
            min(sync_enemies, key=lambda t: t.energy, default=None)
            or min(era_enemies, key=lambda t: t.energy, default=None)
        )
        if target is None:
            return False, None
        return True, target

    if name == "Gerardus Mercator's Map":
        if traveler.century <= 3:
            return False, None
        return True, (3, -1)

    if name == "Astrolabe":
        destination = astrolabe_destination(traveler)
        if destination is None:
            return False, None
        return True, destination

    # The Refrigerator itself (no chains) and any card not handled above.
    return False, None


def refrigerator_choice(traveler: "TravelerState", game: "GameState") -> tuple | None:
    """The (receptor card, sub_context) a Refrigerator should use, or None.

    Takes the first active card in the Temporal Receptor that can be used now.
    """
    receptor_actives = [c for c in getattr(traveler, "receptor_cards", [])
                        if c.ability_type in ("active", "atemporal_active")
                        and c.active_effect is not None]
    return next(
        ((rc, sub) for rc in receptor_actives
         for ok, sub in [refrigerator_context(rc, traveler, game)] if ok),
        None,
    )


def century_farthest_from_rivals(traveler: "TravelerState", game: "GameState") -> int:
    """The century (never Year Zero) as far as possible from every rival in
    play, where a Chaos III Merchant move hurts their market access most."""
    others = [t for t in game.travelers if t is not traveler and not t.awaiting_respawn]
    if not others:
        return CENTURY_MAX
    best, best_dist = 1, -1
    for c in range(1, CENTURY_MAX + 1):
        min_dist = min(abs(c - t.century) for t in others)
        if min_dist > best_dist:
            best_dist = min_dist
            best = c
    return best


def safe_travel_cap(traveler: "TravelerState", reserve: int = 2) -> int:
    """Max past-travel steps before energy drops to or below `reserve`.

    Accounts for the overdrive rule (centuries at or below X cost 2 each
    instead of 1). Ignores card discounts, so it errs on the safe side.
    """
    available = max(0, traveler.energy - reserve)
    pos = traveler.century
    steps = 0
    while steps < pos:
        current_pos = pos - steps
        cost = 2 if current_pos <= OVERDRIVE_THRESHOLD_CENTURY else 1
        if available < cost:
            break
        available -= cost
        steps += 1
    return steps

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
    "Wormhole Pistol",
    "Gunpowder Revolver",
    "Charlemagne's Sword",
}


# The bots read the doubled PAST of an overloaded Paradox.
# A switch only so an experiment can measure the rule with bots that ignore it.
PAST_OVERLOAD_AWARE = True

# Smart's threshold for bleeding the race leader with the doubled Past (energy dealt).
PAST_BLEED_MIN = 4


def paradox_triple_damage(traveler: "TravelerState", game: "GameState", v: int) -> dict:
    """What an overloaded Paradox row (v, v, v) would take from each rival, by name:
    module 4 hits every rival ahead, 5 every rival in my century, 6 every rival behind
    for DOUBLE (engine.paradox.module_damage, Automobile and matrix buffs included).
    Cards on the rivals' side (shields, armor) are ignored, as everywhere in the bots."""
    from engine.state import Allocation
    from engine.constants import FUNCTION_PARADOX
    from engine.paradox import module_damage
    alloc = Allocation.empty()
    for col in range(3):
        alloc.set(FUNCTION_PARADOX, col, v)
    per_col = [module_damage(traveler, alloc, col) for col in range(3)]
    out = {}
    for t in game.travelers:
        if t is traveler or t.awaiting_respawn:
            continue
        col = 0 if t.century > traveler.century else 1 if t.century == traveler.century else 2
        out[t.name] = per_col[col]
    return out


def past_overload_value(
    traveler: "TravelerState",
    game: "GameState",
    dice: list[int],
    *,
    bleed_leader: bool = False,
) -> int | None:
    """The die value to OVERLOAD Paradox with (three of them), or None.

    Chosen when three equal dice make the doubled Past strike TERMINATE a rival
    standing behind (every profile takes a kill), or, with ``bleed_leader``, when it
    takes at least PAST_BLEED_MIN energy from the race leader (the rival nearest Year
    Zero) standing behind. Never while Paradox is shut. The highest such value wins."""
    from engine.constants import FUNCTION_PARADOX
    from engine.paradox import past_is_dead
    if not PAST_OVERLOAD_AWARE or FUNCTION_PARADOX in traveler.overloaded_functions:
        return None
    if past_is_dead(traveler):          # terminated: the Past module deals nothing (28/09)
        return None
    counts = {v: dice.count(v) for v in set(dice)}
    rivals = [t for t in game.travelers if t is not traveler and not t.awaiting_respawn]
    behind = [t for t in rivals if t.century < traveler.century and not getattr(t, "is_atemporal_immune", lambda: False)()]
    if not behind:
        return None
    leader = min(rivals, key=lambda t: (t.century, -t.contract_points))
    for v in sorted((v for v, n in counts.items() if n >= 3), reverse=True):
        dmg = paradox_triple_damage(traveler, game, v)
        if any(dmg[t.name] >= t.energy for t in behind):
            return v
        if bleed_leader and leader in behind and dmg[leader.name] >= PAST_BLEED_MIN:
            return v
    return None


def past_overload_allocation(
    traveler: "TravelerState",
    dice: list[int],
    v: int,
) -> "Allocation":
    """Paradox (v, v, v), overloaded; the fourth die to Recharge 1, or to the escape
    valve when Recharge is shut (the valve is always a legal home for one die)."""
    from engine.state import Allocation
    from engine.constants import FUNCTION_PARADOX, FUNCTION_RECHARGE
    alloc = Allocation.empty()
    rest = list(dice)
    for col in range(3):
        alloc.set(FUNCTION_PARADOX, col, v)
        rest.remove(v)
    for d in rest:
        if FUNCTION_RECHARGE not in traveler.overloaded_functions and alloc.get(FUNCTION_RECHARGE, 0) == 0:
            alloc.set(FUNCTION_RECHARGE, 0, d)
        else:
            alloc.escape_valve = d
    return alloc


# ---------------------------------------------------------------------------
# The two special cards (28/09): The Divine Comedy and Oppenheimer's Trinity
# ---------------------------------------------------------------------------

TRINITY = "Oppenheimer's Trinity"
DIVINE_COMEDY = "The Divine Comedy"
# Where the specials sit on every profile's shopping list: the Comedy (all dice 3)
# is the strongest passive of the game for any plan; Trinity is a finisher.
SPECIAL_WANTS = (DIVINE_COMEDY, TRINITY)


def trinity_era(traveler: "TravelerState", game: "GameState") -> str | None:
    """The era to detonate Oppenheimer's Trinity on, or None to hold it.

    Every traveler in the era loses 20 energy (the user too) and takes 5 booms.
    Score: 3 for a rival it terminates (energy 20 or less), 1 for any other rival
    it hits; an era with the user in it only when he survives with room (energy
    over 25) and it costs him 2. Fired only for a score of 3 or more (a kill, or
    three rivals mauled)."""
    from engine.constants import ERAS
    from engine.timeline import eras_for_century
    best, best_score = None, 2
    for era in ERAS:
        inside = [t for t in game.travelers
                  if not t.awaiting_respawn and era in eras_for_century(t.century)]
        score = 0
        for t in inside:
            if t is traveler:
                if traveler.energy <= 25:
                    score = -99
                    break
                score -= 2
            elif not getattr(t, "is_atemporal_immune", lambda: False)():
                score += 3 if t.energy <= 20 else 1
        if score > best_score:
            best, best_score = era, score
    return best


def special_buy(traveler: "TravelerState", revealed: list, *, trinity: bool = True):
    """The special card to buy from ``revealed``, or None. Every profile takes The
    Divine Comedy; Trinity (a Large Item, both slots) only when ``trinity``, or
    when the pack is empty so it displaces nothing."""
    held = {c.name for c in traveler.hand}
    for want in SPECIAL_WANTS:
        if want in held:
            continue
        if want == TRINITY and not trinity and traveler.hand:
            continue
        card = next((c for c in revealed if c.name == want), None)
        if card is not None and traveler.gold >= card.gold_cost and traveler.can_hold(card):
            return card
    return None


def special_activations(traveler: "TravelerState", game: "GameState") -> list:
    """(card, context) for the special cards a profile holds: Trinity on its best era."""
    out = []
    card = next((c for c in traveler.hand if c.name == TRINITY), None)
    if card is not None:
        era = trinity_era(traveler, game)
        if era is not None:
            out.append((card, era))
    return out


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

"""
engine/combat.py
================
The shared plumbing for energy loss and recycling.

Several cards change how energy is lost (Gunpowder, Prince Dracula's Chalice,
Laser Sword, Viking Shield, Joan of Arc's Armor, and more). Keeping those rules
here means each one is written once and holds for every damage source:
paradoxes, weapon actives and rewards alike.

Three things live here:

1. ``effective_generator``: the value a placed generator actually reads, with
   the Automobile's +1 and any Resource III matrix buff.

2. ``lose_energy`` / ``deal_energy``: the single channel through which a
   traveler (or the game itself) takes energy from a traveler. It applies, in
   order: the attacker's Gunpowder, the target's Viking Shield, the target's
   standing reductions (Joan of Arc's Armor and any other ``on_energy_loss``
   passive, including ones copied by the Quantum Computer), then the Laser
   Sword reflection and the attacker's Prince Dracula's Chalice.

3. ``recycle_card``: moving an equipped card to the recycling pile, firing the
   Agnes's Cauldron steal and, for the free Recycle action, granting the
   recycle value as energy.

engine.cards imports this module for its active effects, so every reference
back into engine.cards is imported inside the function that needs it.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Iterable, Optional

from engine.state import ItemEvent

if TYPE_CHECKING:
    from engine.state import TravelerState, GameState, Allocation
    from engine.cards import Card


# ---------------------------------------------------------------------------
# Generator value
# ---------------------------------------------------------------------------

def _has(traveler: "TravelerState", name: str) -> bool:
    return any(c.name == name for c in traveler.hand)


def effective_generator(
    traveler: "TravelerState",
    allocation: "Allocation",
    row: int,
    col: int,
) -> int:
    """
    Return the value a generator placed at (row, col) reads during resolution.

    An empty module reads 0. A placed generator reads its face value, +1 while
    the traveler equips the Automobile, plus any Resource III buff on that module.
    """
    v = allocation.get(row, col)
    if v <= 0:
        return 0
    if _has(traveler, "Automobile"):
        v += 1
    module_index = row * 3 + col
    if hasattr(traveler, "matrix_buffs") and module_index in traveler.matrix_buffs:
        v += traveler.matrix_buffs[module_index]
    return v


# ---------------------------------------------------------------------------
# Energy loss pipeline
# ---------------------------------------------------------------------------

def register_loss(game: "GameState", traveler: "TravelerState", actual: int) -> None:
    """
    Fire any "when you lose energy" triggers after a confirmed loss.

    Today that is only Porcelain, which recycles itself the moment its holder
    loses any energy. Called from ``lose_energy`` and from the self-inflicted
    loss sites in resolve.py (escape valve, explosion, past travel), so the
    trigger fires whatever caused the loss.
    """
    if actual <= 0 or game is None:
        return
    porcelain = next((c for c in traveler.hand if c.name == "Porcelain"), None)
    if porcelain is not None:
        recycle_card(game, traveler, porcelain)


def lose_energy(
    game: "GameState",
    target: "TravelerState",
    amount: int,
    *,
    source: Optional["TravelerState"] = None,
    kind: str = "effect",
    reflect: bool = True,
) -> int:
    """
    Reduce ``target``'s energy by ``amount`` (before modifiers), applying every
    interacting card. Returns the energy actually lost.

    Args:
        game:    Current game (needed for reflection / cross-traveler effects).
        target:  Traveler losing energy.
        amount:  Raw amount before modifiers.
        source:  Traveler causing the loss, or None for environmental loss
                 (escape valve, explosion, self-inflicted): modifiers that
                 only apply to "another traveler causing the loss" are skipped.
        kind:    Free-text tag for the cause ("paradox", "weapon", "reflect").
        reflect: Whether Laser Sword may reflect this loss (set False on
                 the reflected hit itself to prevent an infinite bounce).
    """
    if amount <= 0 or target.awaiting_respawn:
        return 0

    by_other = source is not None and source is not target

    # Source amplifier (Gunpowder): the target loses 1 more.
    if by_other and _has(source, "Gunpowder"):
        amount += 1

    # Target first-hit shield (Viking Shield): the first enemy-caused loss
    # each Hour is reduced by 2.
    if by_other and _has(target, "Viking Shield") \
            and "Viking Shield" not in target.cards_used_this_hour:
        target.cards_used_this_hour.add("Viking Shield")
        amount = max(0, amount - 2)

    # Standing reductions: Joan of Arc's Armor and any other
    # on_energy_loss passive (including those copied by Quantum Computer).
    from engine.cards import passive_source_cards
    for card in passive_source_cards(target, game):
        if card.on_energy_loss:
            amount = card.on_energy_loss(target, amount)
    amount = max(0, amount)

    actual = min(target.energy, amount)
    target.energy -= actual
    if target.energy < 0:
        target.energy = 0

    if by_other and actual > 0:
        target.eliminated_by.append(source.name)

    # Reflection (Laser Sword): the attacker loses what the holder lost.
    if reflect and by_other and actual > 0 and _has(target, "Laser Sword"):
        lose_energy(game, source, actual, source=None, kind="reflect", reflect=False)

    # "When you lose energy" triggers (Porcelain).
    register_loss(game, target, actual)

    return actual


def deal_energy(
    game: "GameState",
    source: Optional["TravelerState"],
    targets: Iterable["TravelerState"],
    amount: int,
    *,
    kind: str = "effect",
) -> int:
    """
    Make a batch of targets lose ``amount`` energy from one source effect.

    Applies ``lose_energy`` per target, then the attacker's Prince Dracula's
    Chalice once if at least one target actually lost energy. Returns the
    number of targets that lost energy.
    """
    hits = 0
    for t in targets:
        if t is source:
            continue
        if lose_energy(game, t, amount, source=source, kind=kind) > 0:
            hits += 1

    if hits and source is not None and _has(source, "Prince Dracula's Chalice"):
        source.energy += 2

    return hits


# ---------------------------------------------------------------------------
# Recycling with the Agnes's Cauldron trigger
# ---------------------------------------------------------------------------

def recycle_card(
    game: "GameState",
    owner: "TravelerState",
    card: "Card",
    *,
    grant_energy: bool = False,
) -> None:
    """
    Move ``card`` out of ``owner``'s equipment to the recycling pile (§21).

    - With ``grant_energy`` (the free Recycle action) the owner gains the card's
      recycle value, the number printed bottom-right.
    - Agnes's Cauldron: when another traveler recycles a card, a Cauldron
      holder may steal it. I model that as the first holder with room taking
      the card before it reaches the pile.

    The recycling pile itself is not tracked, so a card nobody steals simply
    leaves play. For the analysis that is the same thing.
    """
    if card in owner.hand:
        owner.hand.remove(card)

    if grant_energy:
        owner.energy += getattr(card, "recycle_value", 0)

    for t in game.travelers:
        if t is owner or t.awaiting_respawn:
            continue
        if any(c.name == "Agnes's Cauldron" for c in t.hand) and t.can_hold(card):
            t.hand.append(card)
            game.item_events.append(ItemEvent(
                hour=game.hour,
                traveler=owner.name,
                event_type="recycled",
                card_name=card.name,
                century=owner.century,
            ))
            game.item_events.append(ItemEvent(
                hour=game.hour,
                traveler=t.name,
                event_type="bought",
                card_name=card.name,
                century=t.century,
            ))
            return
    game.item_events.append(ItemEvent(
        hour=game.hour,
        traveler=owner.name,
        event_type="recycled",
        card_name=card.name,
        century=owner.century,
    ))

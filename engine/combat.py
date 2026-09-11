"""
engine/combat.py
================
Shared effect plumbing that several cards and the paradox/travel resolvers
all depend on. Keeping it in one place means a damage rule (Pólvora, Cálice,
Espada de Laser, Escudo Viking, Armadura, …) is written once and obeyed
everywhere: paradoxes, weapon actives, and reward effects alike.

Three concerns live here:

1. ``effective_generator``: the value a placed generator actually reads,
   accounting for the Carro passive (+1 to every causality generator, §card).

2. ``lose_energy`` / ``deal_energy``: the single channel through which one
   traveler (or the environment) reduces another traveler's energy. It applies,
   in order: the source-side amplifier (Pólvora), the target-side first-hit
   shield (Escudo Viking), the target's standing reductions (Armadura and any
   ``on_energy_loss`` passive, including those copied by Computador Quântico),
   then the reflection passive (Espada de Laser) and the source-side reward
   (Cálice do Príncipe Drácula).

3. ``recycle_card``: moving an equipped card to the recycling pile, firing the
   Caldeirão da Agnes steal trigger and (optionally) granting the recycle
   value as energy for the free Recycle action.

To avoid an import cycle (cards.py imports this module for its active effects)
every reference back into engine.cards is a function-local import.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Iterable, Optional

from engine.constants import FUNCTION_TRAVEL

if TYPE_CHECKING:
    from engine.state import TravelerState, GameState, Allocation
    from engine.cards import Card


# ---------------------------------------------------------------------------
# Carro: +1 to every causality generator value (§card "Carro")
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

    An empty module reads 0. A placed generator reads its face value, plus 1
    for every level of the Carro passive the traveler currently equips
    (Carro adds +1 to the value of all common causality generators).
    """
    v = allocation.get(row, col)
    if v <= 0:
        return 0
    if _has(traveler, "Carro"):
        v += 1
    module_index = row * 3 + col
    if hasattr(traveler, "matrix_buffs") and module_index in traveler.matrix_buffs:
        v += traveler.matrix_buffs[module_index]
    return v


# ---------------------------------------------------------------------------
# Energy loss pipeline
# ---------------------------------------------------------------------------

def _mitigation_cards(target: "TravelerState", game: "GameState | None") -> list["Card"]:
    """Cards whose ``on_energy_loss`` passive applies to this target."""
    from engine.cards import passive_source_cards
    return passive_source_cards(target, game)


def register_loss(game: "GameState", traveler: "TravelerState", actual: int) -> None:
    """
    Fire any "when you lose energy" triggers after a confirmed loss.

    Currently: Porcelana recycles itself the moment its holder loses any energy
    (§card). Called from ``lose_energy`` and from the self-inflicted loss sites
    in resolve.py (escape valve, explosion, past-travel) so the trigger fires
    regardless of the loss source.
    """
    if actual <= 0 or game is None:
        return
    porcelana = next((c for c in traveler.hand if c.name == "Porcelana"), None)
    if porcelana is not None:
        recycle_card(game, traveler, porcelana)


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
        kind:    Free-text tag for the cause ("paradox", "weapon", "reflect"…).
        reflect: Whether Espada de Laser may reflect this loss (set False on
                 the reflected hit itself to prevent an infinite bounce).
    """
    if amount <= 0 or target.awaiting_respawn:
        return 0

    by_other = source is not None and source is not target

    # Source amplifier (Pólvora): the target loses 1 more.
    if by_other and _has(source, "Pólvora"):
        amount += 1

    # Target first-hit shield (Escudo Viking): the first enemy-caused loss
    # each Hour is reduced by 2.
    if by_other and _has(target, "Escudo Viking") \
            and "Escudo Viking" not in target.cards_used_this_hour:
        target.cards_used_this_hour.add("Escudo Viking")
        amount = max(0, amount - 2)

    # Standing reductions: Armadura da Joana d'Arc and any other
    # on_energy_loss passive (including those copied by Computador Quântico).
    for card in _mitigation_cards(target, game):
        if card.on_energy_loss:
            amount = card.on_energy_loss(target, amount)
    amount = max(0, amount)

    actual = min(target.energy, amount)
    target.energy -= actual
    if target.energy < 0:
        target.energy = 0

    if by_other and actual > 0:
        target.eliminated_by.append(source.name)

    # Reflection (Espada de Laser): the attacker loses what the holder lost.
    if reflect and by_other and actual > 0 and _has(target, "Espada de Laser"):
        lose_energy(game, source, actual, source=None, kind="reflect", reflect=False)

    # "When you lose energy" triggers (Porcelana).
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

    Applies ``lose_energy`` per target, then the source-side Cálice do Príncipe
    Drácula reward once if at least one target actually lost energy. Returns the
    number of targets that lost energy.
    """
    hits = 0
    for t in targets:
        if t is source:
            continue
        if lose_energy(game, t, amount, source=source, kind=kind) > 0:
            hits += 1

    if hits and source is not None and _has(source, "Cálice do Príncipe Drácula"):
        source.energy += 2

    return hits


# ---------------------------------------------------------------------------
# Recycling with the Caldeirão da Agnes trigger
# ---------------------------------------------------------------------------

def recycle_card(
    game: "GameState",
    owner: "TravelerState",
    card: "Card",
    *,
    grant_energy: bool = False,
) -> None:
    """
    Move ``card`` out of ``owner``'s equipment to the recycling pile (§Recycle).

    - If ``grant_energy`` (the free Recycle action), the owner gains the card's
      recycle value (§Recycling: energy = recycle value printed bottom-right).
    - Caldeirão da Agnes: when another traveler recycles a card, a holder of
      Caldeirão may steal it, modelled here as: the first such holder with room
      takes the card instead of it reaching the pile.

    The recycling pile itself is not otherwise tracked in game state, so a card
    that is not stolen simply leaves play (functionally equivalent for the
    analysis engine).
    """
    if card in owner.hand:
        owner.hand.remove(card)

    if grant_energy:
        owner.energy += getattr(card, "recycle_value", 0)

    for t in game.travelers:
        if t is owner or t.awaiting_respawn:
            continue
        if any(c.name == "Caldeirão da Agnes" for c in t.hand) and t.can_hold(card):
            t.hand.append(card)
            from engine.state import ItemEvent
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
    from engine.state import ItemEvent
    game.item_events.append(ItemEvent(
        hour=game.hour,
        traveler=owner.name,
        event_type="recycled",
        card_name=card.name,
        century=owner.century,
    ))

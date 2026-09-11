"""
simulation/strategies/util.py
==============================
Shared helpers used by all strategy agents.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.state import TravelerState, GameState
    from engine.cards import Card


# Cards whose active_effect needs a MerchantDeck (or similar market object),
# which is not available during Phase 4 activation.
_MARKET_DECK_CARDS = {
    "A Máquina de Alan Turing",
    "Carretel de Pesca",
    "Mona Lisa",
    "Óculos",
    "Livro de Mistérios de Alexandria",
    "Heliógrafo de Niépce",
    "A Lâmpada de Thomas Edison",
    "Xilogravura",
}

# Cards whose active_effect operates with no external context (AoE / self).
_NO_CONTEXT_CARDS = {
    "Canhão de Vingança da Rainha Anne",
    "Excalibur",
    "A Espada de Átila",
    "Primeira Maquina do Tempo",
}

# Cards that teleport/swap and need a TravelerState target.
_TARGET_TRAVELER_CARDS = {
    "Rifle Fergunson",
    "Lança de Fogo",
    "Bandeira Vermelha da Ching Shih",
    "Arma de Laser",
    "Arma de Portais",
    "Revolver de Pólvora",
    "Espada do Carlos Magno",
}


def paradox_can_terminate(
    traveler: "TravelerState",
    game: "GameState",
    dice: list[int],
) -> bool:
    """True if using the best available die in Paradox could drop any rival to 0 energy.

    Used by non-Aggressive profiles to decide when Paradox deserves higher priority
    than Travel: if a kill shot is on the table, commit to it; otherwise, keep dice
    in Recharge/Gold. Checks all active rivals regardless of direction, the caller
    fills Paradox from col 0 (future) onward, so actual reach depends on how many
    dice go in, but the simple "can any rival be killed with max die?" is the right
    threshold for deciding whether to invest at all.
    """
    if not dice:
        return False
    best_val = max(dice)
    return any(
        t.energy <= best_val
        for t in game.travelers
        if t is not traveler and not t.awaiting_respawn
    )


def paradox_kill_direction(
    traveler: "TravelerState",
    game: "GameState",
    dice: list[int],
) -> int | None:
    """Return the Paradox column (0=future, 1=present, 2=past) that covers the most
    killable rivals, weighted by how many fit under max(dice). Returns None if no
    rivals can be killed.

    Used when a strategy wants to place just one Paradox column optimally rather
    than filling all three.
    """
    if not dice:
        return None
    best_val = max(dice)
    pos = traveler.century
    buckets = {0: 0, 1: 0, 2: 0}  # col → count of killable rivals
    for t in game.travelers:
        if t is traveler or t.awaiting_respawn:
            continue
        if t.energy > best_val:
            continue
        if t.century > pos:
            buckets[0] += 1
        elif t.century == pos:
            buckets[1] += 1
        else:
            buckets[2] += 1
    best_col = max(buckets, key=lambda c: buckets[c])
    return best_col if buckets[best_col] > 0 else None


def geladeira_context(
    receptor_card: "Card",
    traveler: "TravelerState",
    game: "GameState",
) -> tuple[bool, object]:
    """
    Determine whether a receptor card can be activated via Geladeira during
    Phase 4, and return the sub-context to pass to its effect.

    Returns (can_use: bool, sub_context: object).
    Returns (False, None) for cards that need a market deck or have no
    useful target in Phase 4.
    """
    name = receptor_card.name

    if name in _MARKET_DECK_CARDS:
        return False, None

    if name in _NO_CONTEXT_CARDS:
        return True, None

    if name in _TARGET_TRAVELER_CARDS:
        # Find the weakest enemy in era as target.
        era_enemies = [
            t for t in game.travelers
            if t is not traveler and not t.awaiting_respawn
            and _same_era(traveler.century, t.century)
        ]
        sync_enemies = [t for t in era_enemies if t.century == traveler.century]
        target = (
            min(sync_enemies, key=lambda t: t.energy, default=None)
            or min(era_enemies, key=lambda t: t.energy, default=None)
        )
        if target is None:
            return False, None
        return True, target

    if name == "Mapa de Geradus Mercator":
        if traveler.century <= 3:
            return False, None
        return True, (3, -1)

    if name == "Astrolábio":
        from engine.timeline import eras_for_century
        from engine.constants import ERAS
        eras = eras_for_century(traveler.century)
        if not eras:
            return False, None
        era_start = min(ERAS[e][0] for e in eras)
        if era_start >= traveler.century:
            return False, None
        return True, era_start

    if name == "Geladeira":
        return False, None  # no recursive Geladeira chains

    # Unknown / unhandled card: skip safely.
    return False, None


def safe_travel_cap(traveler: "TravelerState", reserve: int = 2) -> int:
    """Max past-travel steps before energy drops to or below `reserve`.

    Accounts for the overdrive rule (centuries ≤ 10 cost 2 each instead of 1).
    Ignores card-hook cost reductions: conservative estimate for strategy use.
    """
    available = max(0, traveler.energy - reserve)
    pos = traveler.century
    steps = 0
    while steps < pos:
        current_pos = pos - steps
        cost = 2 if current_pos <= 10 else 1  # overdrive zone
        if available < cost:
            break
        available -= cost
        steps += 1
    return steps


def _same_era(a: int, b: int) -> bool:
    from engine.timeline import eras_for_century
    return bool(set(eras_for_century(a)) & set(eras_for_century(b)))

"""
simulation/strategies/base.py
==============================
Abstract base class for all Paradoxo strategy agents.

Every strategy, whether a hand-crafted heuristic, a Monte Carlo agent,
or eventually a learned AI, implements this interface. The simulation
runner calls it without knowing which concrete strategy it is talking to.

The interface is deliberately minimal:
    - `choose_allocation` is the only required method.
    - It receives the full game state and the dice rolled this Hour.
    - It returns a completed Allocation and a travel direction.

Strategies must not mutate game state. The runner owns state mutation.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from engine.state import TravelerState, GameState, Allocation
from engine.dice import count_faces
from engine.market import MarketAction, PassAction, UseCardAction
from engine.cards import Card


class Strategy(ABC):
    """
    Abstract base for all strategy agents.

    Subclasses implement `choose_allocation` and may optionally override
    `name` to provide a human-readable label for reports.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name, used in reports and graph labels."""
        return self.__class__.__name__

    @abstractmethod
    def choose_allocation(
        self,
        traveler: TravelerState,
        game: GameState,
        dice: list[int],
    ) -> tuple:
        """
        Decide how to allocate the rolled generators this Hour.

        Args:
            traveler: This traveler's current state (read-only, do not mutate).
            game:     Full game state (read-only, do not mutate).
            dice:     Sorted list of 4 generator values for this Hour.

        Returns:
            A 2- or 3-tuple:
                allocation:   a fully populated Allocation object
                direction:    +1 (toward future/XXX) or -1 (toward past/Year Zero)
                travel_cap:   (optional) maximum centuries to move this Hour.
                              If omitted or None, the full dice value is used.
                              Enables the "up to rolled value" movement rule (§14.1).

        The returned Allocation must be valid under engine/matrix.py rules.
        """
        ...

    def choose_market_action(
        self,
        traveler: TravelerState,
        game: GameState,
        revealed: list[Card],
        renew_cost: int,
    ) -> MarketAction:
        """
        Decide one Market action during this traveler's priority.

        Called repeatedly until PassAction is returned. The runner calls
        this in a loop, so strategies that want to buy then renew should
        return each action on successive calls, then PassAction.

        Default: always Pass. Subclasses override to buy/renew/declare.
        """
        return PassAction()

    def choose_activations(
        self,
        traveler: TravelerState,
        game: GameState,
    ) -> list[tuple[Card, object]]:
        """
        Decide which Active abilities to use during Phase 4, Item Activation.

        Return a list of ``(card, context)`` pairs (up to one per equipped object,
        §2.3). Each card must be in hand and have an Active ability. ``context``
        is the card-specific argument (a target traveler for weapons, a
        ``(centuries, direction)`` tuple for Mapa, a receptor card for Geladeira,
        …). The runner resolves them in this traveler's priority window.

        Default: activate nothing. Subclasses override to use weapons/utilities.
        """
        return []

    def choose_chaos_destroy_target(
        self,
        traveler: TravelerState,
        game: GameState,
        candidates: list,
    ) -> object:
        """
        Chaos II: choose one card to destroy from `candidates`.

        Candidates are Card objects drawn from: revealed Merchant cards,
        the open Secret Market card, or other travelers' equipped cards
        (never from a Temporal Receptor). Default: destroy the cheapest.
        """
        return min(candidates, key=lambda c: c.gold_cost)

    def choose_chaos_merchant_century(
        self,
        traveler: TravelerState,
        game: GameState,
    ) -> int:
        """
        Chaos III: choose the century to teleport the Merchant to.
        Must not be Year Zero (century 0). Default: move to century farthest
        from all travelers (disrupts market access most).
        """
        from engine.constants import CENTURY_MAX
        active_centuries = {t.century for t in game.travelers if not t.awaiting_respawn}
        best = 1
        best_min_dist = -1
        for c in range(1, CENTURY_MAX + 1):
            min_dist = min(abs(c - t) for t in active_centuries) if active_centuries else 0
            if min_dist > best_min_dist:
                best_min_dist = min_dist
                best = c
        return best

    def choose_resource_steal_target(
        self,
        traveler: TravelerState,
        game: GameState,
        revealed: list,
    ) -> object:
        """
        Resource II: choose one card to steal from the Merchant's revealed stock.
        Default: take the most expensive affordable card.
        """
        affordable = [c for c in revealed if traveler.can_hold(c)]
        if not affordable:
            return None
        return max(affordable, key=lambda c: c.gold_cost)

    def choose_matrix_buff_module(
        self,
        traveler: TravelerState,
        game: GameState,
    ) -> int:
        """
        Resource III: choose which module (0-8) to permanently buff by +1.
        Cannot stack on an already-buffed module. Default: prefer module 8
        (Travel col 1 = module 8), then any unbuffed module.
        """
        already_buffed = set(getattr(traveler, "matrix_buffs", {}).keys())
        preference = [7, 8, 6, 0, 1, 2, 3, 4, 5]  # module indices in preference order
        for m in preference:
            if m not in already_buffed:
                return m
        return next((m for m in range(9) if m not in already_buffed), 0)

    def choose_reward_category(
        self,
        traveler: TravelerState,
        game: GameState,
        available: list[str],
    ) -> str:
        """
        Choose a reward category after earning a CP (§26).

        `available` is the list of categories that may be chosen this turn
        (excludes the last category chosen, per §26.2).

        Default: prefer Resource; fall back to first available.
        Subclasses override for profile-specific reward priorities.
        """
        for preferred in ("Resource", "Time", "Chaos"):
            if preferred in available:
                return preferred
        return available[0]

    def choose_items_to_recycle(
        self,
        traveler: TravelerState,
        game: GameState,
    ) -> list[Card]:
        """
        Called before Phase 2 (Market) to allow free recycling of held items.

        Return the subset of `traveler.hand` to recycle; each recycled card
        grants energy equal to its recycle_value (§21.1). Default: recycle nothing.
        """
        return []

    def choose_cards_to_deliver(
        self,
        traveler: TravelerState,
        game: GameState,
        deliverable: list[Card],
    ) -> list[Card]:
        """
        Decide which deliverable cards to actually deliver this phase.

        `deliverable` is the subset of cards in hand whose delivery_century
        matches the traveler's current century. Return the subset to deliver
        (empty list = hold all). Default: deliver all.
        """
        return deliverable

    def __repr__(self) -> str:
        return f"<Strategy: {self.name}>"

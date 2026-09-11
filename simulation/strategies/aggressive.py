"""
simulation/strategies/aggressive.py
=====================================
Aggressive strategy: overload Travel whenever dice allow it.

Translated from the MS529 Julia prototype `model_agressive`, corrected
against the Rules Reference where the prototype diverged.

Core intent: win the game, not merely end it. Reaching Year Zero (§11.1a)
immediately ends the game, but a traveler standing on Year Zero sits on the
lowest possible board position and so loses every CP tiebreak (§32.4). The
Aggressive profile therefore only steps onto Year Zero when it has computed,
in advance, that doing so makes it the outright CP winner. Otherwise it stops
one space short (century I) and lays siege: it overloads Paradox to bleed the
field, buys and fires weapons to terminate rivals (aiming for the §11.1c
"last traveler" win), pays off its Wanted poster (§24.5) to reach the Secret
Market, and still delivers in the low centuries.

When Travel is the right tool (a winning rush, or repositioning toward the
pack) it pushes the Travel function to maximum output, accepting the overload
penalty as the price of speed.

Allocation logic follows §10 (one value per function, linear progression).
Escape valve used when a function is unavailable and dice cannot be
legally placed elsewhere (§11.2).
"""

from __future__ import annotations
from engine.state import TravelerState, GameState, Allocation
from engine.constants import (
    FUNCTION_RECHARGE,
    FUNCTION_PARADOX,
    FUNCTION_TRAVEL,
    CENTURY_MIN,
    CP_YEAR_ZERO_TOTAL,
    CP_SURVIVAL_BONUS,
    DECLARE_COST,
)
from engine.matrix import place, send_to_escape_valve
from engine.dice import count_faces
from engine.cards import Card
from engine.market import MarketAction, BuyAction, DeclareAction, PassAction
from simulation.strategies.base import Strategy
from simulation.strategies.util import safe_travel_cap

# Cards that directly help reach Year Zero faster
_TRAVEL_CARDS = {
    "Mapa de Geradus Mercator",          # active: travel 3 toward past
    "Bússola de Navegação",              # passive: bonus free travel at hour-end
    "Colar de Cavalo",                   # passive: +1 energy per travel module
    "Motor de Corrente Alternada de Tesla", # passive: +1 energy on boom gain
    "Astrolábio",                        # active: free in-era travel (recycles)
    "Porcelana",                         # passive: market cards -1g
}

_WEAPON_NAMES = {
    "Rifle Fergunson", "Lança de Fogo", "Bandeira Vermelha da Ching Shih",
    "Canhão de Vingança da Rainha Anne", "Arma de Laser", "Arma de Portais",
    "Revolver de Pólvora", "Excalibur", "Espada do Carlos Magno",
    "A Espada de Átila",
}


def _same_era(a: int, b: int) -> bool:
    from engine.timeline import eras_for_century
    return bool(set(eras_for_century(a)) & set(eras_for_century(b)))


class AggressiveStrategy(Strategy):
    """
    Always overload the Travel function when three or more matching dice
    are available. Otherwise maximise travel distance with available dice.

    When Travel is overloaded (unavailable), uses Recharge and Paradox
    and sends leftovers to the escape valve.
    """

    @property
    def name(self) -> str:
        return "Aggressive"

    def choose_allocation(
        self,
        traveler: TravelerState,
        game: GameState,
        dice: list[int],
    ) -> tuple[Allocation, int, int | None]:
        alloc = Allocation.empty()
        unavailable = traveler.overloaded_functions

        # Always travel toward the past (toward Year Zero).
        direction = -1

        # Decide, in advance, whether stepping onto Year Zero this Hour would
        # actually win the game (§11.1a + §32). If not, we must not end the game
        # there: cap travel so we stop one space short (century I) and lay
        # siege instead (overload Paradox, hunt with weapons, §11.1c).
        rush = self._year_zero_would_win(traveler, game)

        if rush:
            # Spending to empty is only justified if Year Zero is actually
            # reachable this Hour: stepping onto it ends the game on our terms.
            # If our energy cannot carry us all the way to Year Zero, burning it
            # to 0 mid-board is pure self-termination (§28.1) with no payoff, so
            # fall back to a safe siege cap instead. (Fixes self-suicide travel.)
            burn_cap = safe_travel_cap(traveler, reserve=0)
            if burn_cap >= traveler.century:        # reaches Year Zero
                cap = burn_cap
                priority = (FUNCTION_TRAVEL, FUNCTION_RECHARGE, FUNCTION_PARADOX)
                rush = True
            else:
                rush = False
        if not rush:
            # Max steps that keep us at or above century I (never onto Year Zero).
            # Also cap by energy so overdrive doesn't kill us en route.
            cap = min(max(0, traveler.century - CENTURY_MIN),
                      safe_travel_cap(traveler, reserve=3))
            # Travel is still the Aggressive profile's identity: whenever there is
            # any room to move (cap > 0), overload Travel as the top priority and
            # rush toward the pack / Year Zero, accepting the overload penalty as
            # the price of speed. The caps above are the only restraint, they keep
            # us one space short of Year Zero (so we don't end a game we'd lose,
            # §32.4) and hold an energy reserve (so overdrive doesn't self-terminate
            # us, §28.1). With Travel capped, leftover dice recover energy (Recharge)
            # then bleed the field (Paradox).
            #
            # Only when we are pinned at century I (cap == 0) is travel physically
            # impossible: there, filling Travel would merely heat module 7 toward
            # an explosion (§15.2) for no movement, so we lay siege instead:
            # Paradox to bleed the field, Recharge to recover.
            if cap > 0:
                priority = (FUNCTION_TRAVEL, FUNCTION_RECHARGE, FUNCTION_PARADOX)
            else:
                priority = (FUNCTION_PARADOX, FUNCTION_RECHARGE, FUNCTION_TRAVEL)

        self._allocate_by_priority(alloc, dice, unavailable, priority)
        return alloc, direction, cap

    def _allocate_by_priority(
        self,
        alloc: Allocation,
        dice: list[int],
        unavailable: set[int],
        priority: tuple[int, ...],
    ) -> None:
        """
        Fill functions in the given priority order, one die value per function
        (§10 linear progression). Each function takes the value with the highest
        count among the still-unplaced dice (ties broken by higher value). Any
        die that cannot be legally placed is sent to the escape valve, which is
        only legal when at least one function is unavailable (§11.2).
        """
        remaining = list(dice)

        for fn in priority:
            if fn in unavailable:
                continue
            existing = alloc.generators_in_function(fn)
            if len(existing) >= 3:
                continue
            existing_val = existing[0] if existing else None

            counts = count_faces(remaining)
            candidates = [
                (v, c) for v, c in counts.items()
                if c > 0 and (existing_val is None or v == existing_val)
            ]
            if not candidates:
                continue
            val = max(candidates, key=lambda x: (x[1], x[0]))[0]

            slots_used = len(existing)
            for _ in range(counts[val]):
                if slots_used >= 3:
                    break
                place(alloc, fn, slots_used, val)
                slots_used += 1
                remaining.remove(val)

        # Anything left goes to the escape valve (only legal when a function is
        # unavailable, §11.2). With no unavailable function a 4th die may be left
        # unplaced; the runner's validator (when enabled) catches that.
        for val in list(remaining):
            if len(unavailable) > 0:
                send_to_escape_valve(alloc, val)
                remaining.remove(val)

    def _year_zero_would_win(self, traveler: TravelerState, game: GameState) -> bool:
        """
        True if stepping onto Year Zero right now would make this traveler the
        outright CP winner (§11.1a end, §32 scoring).

        On a Year Zero end the reacher gains the Year Zero contract + stabilisation
        bundle (``CP_YEAR_ZERO_TOTAL``) plus the survival point if still alive;
        every other never-terminated traveler gains only their survival point
        (§32.2). Because the reacher ends on the lowest board position it loses
        all CP tiebreaks (§32.4: century, then gold, then energy), so it must
        finish with *strictly* more CP than everyone else.
        """
        my_final = traveler.contract_points + CP_YEAR_ZERO_TOTAL
        if not traveler.is_terminated:
            my_final += CP_SURVIVAL_BONUS

        for other in game.travelers:
            if other is traveler:
                continue
            their_final = other.contract_points
            if not other.is_terminated:
                their_final += CP_SURVIVAL_BONUS
            if their_final >= my_final:
                return False
        return True

    def choose_market_action(
        self,
        traveler: TravelerState,
        game: GameState,
        revealed: list[Card],
        renew_cost: int,
    ) -> MarketAction:
        """Pay off the Wanted poster for Secret Market access, then buy weapons
        and travel-speed cards.

        Declaring (paying off the Wanted poster, §24.5) is only honoured at the
        Merchant market; the Secret Market loop passes a sentinel ``renew_cost``
        of 999 and skips Wanted travelers entirely, so we gate the Declare on the
        Merchant context. We pay it off only once the Secret Market is open and
        we can still afford it: clearing Wanted unlocks Secret Market buys next
        phase (§33.3)."""
        held_names = {c.name for c in traveler.hand}

        if (traveler.is_wanted
                and renew_cost != 999
                and game.secret_market_open
                and traveler.gold >= DECLARE_COST):
            return DeclareAction()

        # Weapons first (terminate rivals → §11.1c), then travel-speed cards.
        for wishlist in (_WEAPON_NAMES, _TRAVEL_CARDS):
            for card in revealed:
                if card.name not in wishlist:
                    continue
                if card.name in held_names:
                    continue
                if traveler.gold < card.gold_cost:
                    continue
                if not traveler.can_hold(card):
                    continue
                return BuyAction(card)
        return PassAction()

    def choose_activations(
        self,
        traveler: TravelerState,
        game: GameState,
    ) -> list[tuple[Card, object]]:
        """Use travel cards to rush Year Zero; fire weapons against nearby enemies."""
        activations = []
        sync_enemies = [t for t in game.travelers
                        if t is not traveler and not t.awaiting_respawn
                        and t.century == traveler.century]
        era_enemies = [t for t in game.travelers
                       if t is not traveler and not t.awaiting_respawn
                       and _same_era(traveler.century, t.century)]
        weakest_sync = min(sync_enemies, key=lambda t: t.energy, default=None)
        weakest_era = min(era_enemies, key=lambda t: t.energy, default=None)

        for card in list(traveler.hand):
            name = card.name
            ctx: object = None

            if name == "Mapa de Geradus Mercator":
                if traveler.century <= 3:
                    continue
                ctx = (3, -1)

            elif name == "Astrolábio":
                from engine.timeline import eras_for_century
                from engine.constants import ERAS
                eras = eras_for_century(traveler.century)
                if not eras:
                    continue
                era_start = min(ERAS[e][0] for e in eras)
                if era_start >= traveler.century:
                    continue
                ctx = era_start

            elif name == "Canhão de Vingança da Rainha Anne":
                if not era_enemies:
                    continue
                ctx = None

            elif name in ("Arma de Laser", "Lança de Fogo"):
                if not weakest_sync:
                    continue
                ctx = weakest_sync

            elif name == "Bandeira Vermelha da Ching Shih":
                if not era_enemies:
                    continue
                ctx = max(era_enemies, key=lambda t: t.gold, default=weakest_era)

            elif name == "Rifle Fergunson":
                if not era_enemies:
                    continue
                ctx = weakest_era

            elif name == "Revolver de Pólvora":
                if not sync_enemies:
                    continue
                ctx = max(sync_enemies, key=lambda t: len(t.hand))

            elif name == "Excalibur":
                future = [t for t in game.travelers
                          if not t.awaiting_respawn and t.century > traveler.century]
                if not future:
                    continue
                ctx = None

            elif name == "Espada do Carlos Magno":
                if not weakest_sync or traveler.gold == 0:
                    continue
                ctx = weakest_sync

            elif name == "A Espada de Átila":
                from engine.timeline import eras_for_century
                from engine.constants import ERAS
                order = list(ERAS.keys())
                my_eras = eras_for_century(traveler.century)
                my_oldest = min(order.index(e) for e in my_eras) if my_eras else 0
                older = [t for t in game.travelers
                         if not t.awaiting_respawn and t is not traveler
                         and eras_for_century(t.century)
                         and max(order.index(e) for e in eras_for_century(t.century)) < my_oldest]
                if not older:
                    continue
                ctx = None

            elif name == "Arma de Portais":
                if not era_enemies:
                    continue
                ctx = weakest_era

            elif name == "Geladeira":
                from simulation.strategies.util import geladeira_context
                receptor_actives = [c for c in getattr(traveler, "receptor_cards", [])
                                    if c.ability_type in ("active", "atemporal_active")
                                    and c.active_effect is not None]
                chosen = next(
                    ((rc, sub) for rc in receptor_actives
                     for ok, sub in [geladeira_context(rc, traveler, game)] if ok),
                    None,
                )
                if chosen is None:
                    continue
                ctx = (chosen[0], chosen[1])

            else:
                continue

            activations.append((card, ctx))

        return activations

    def choose_cards_to_deliver(
        self,
        traveler: TravelerState,
        game: GameState,
        deliverable: list[Card],
    ) -> list[Card]:
        """Always deliver: CP helps in tiebreaks, and speed cards are used before delivery."""
        return deliverable

    def choose_chaos_destroy_target(self, traveler, game, candidates):
        """Destroy the most expensive equipped card from a rival, or cheapest market card."""
        from engine.cards import Card
        equipped = [c for t in game.travelers
                    if t is not traveler and not t.awaiting_respawn
                    for c in t.hand
                    if c in candidates]
        if equipped:
            return max(equipped, key=lambda c: c.gold_cost)
        return min(candidates, key=lambda c: c.gold_cost)

    def choose_chaos_merchant_century(self, traveler, game):
        """Push Merchant far from all other travelers to block their market access."""
        from engine.constants import CENTURY_MAX
        others = [t for t in game.travelers if t is not traveler and not t.awaiting_respawn]
        if not others:
            return CENTURY_MAX
        # Choose the century maximally far from all rivals
        best, best_dist = 1, -1
        for c in range(1, CENTURY_MAX + 1):
            min_dist = min(abs(c - t.century) for t in others)
            if min_dist > best_dist:
                best_dist = min_dist
                best = c
        return best

    def choose_resource_steal_target(self, traveler, game, revealed):
        """Steal the most valuable affordable card."""
        affordable = [c for c in revealed if traveler.can_hold(c)]
        return max(affordable, key=lambda c: c.gold_cost) if affordable else None

    def choose_matrix_buff_module(self, traveler, game):
        """Buff travel modules first (modules 7, 8, 9 = indices 6, 7, 8)."""
        already = set(traveler.matrix_buffs.keys())
        for m in [7, 8, 6, 0, 1, 2, 3, 4, 5]:
            if m not in already:
                return m
        return next((m for m in range(9) if m not in already), 0)

    def choose_reward_category(
        self,
        traveler: TravelerState,
        game: GameState,
        available: list[str],
    ) -> str:
        """Primary: Chaos. Secondary: Resource. Last resort: Time."""
        for preferred in ("Chaos", "Resource", "Time"):
            if preferred in available:
                return preferred
        return available[0]

"""
simulation/strategies/conservative.py
=======================================
Conservative strategy: steady travel while keeping energy and gold high.

Core intent: never overload any function. Prioritise Energy and Gold (Recharge)
above everything. Use Paradox only when it can plausibly terminate a rival
(see util.paradox_can_terminate). Travel takes whatever slots remain.

Card preferences target survival, market access, and delivery CP, in that
order of urgency. The Merchant market is treated as a resource: Declare away
the Wanted poster to restore Secret Market access, renew the market when
nothing useful is visible, and never let money sit idle when good cards are
available.
"""

from __future__ import annotations
from engine.state import TravelerState, GameState, Allocation
from engine.constants import (
    FUNCTION_RECHARGE,
    FUNCTION_PARADOX,
    FUNCTION_TRAVEL,
    DECLARE_COST,
    BOOM_LIMIT,
)
from engine.matrix import place, send_to_escape_valve
from engine.dice import count_faces
from engine.cards import Card, TIEBREAK_RANK
from engine.market import (
    MarketAction, BuyAction, RenewAction, DeclareAction, PassAction, SECRET_MARKET_RENEW_COST,
)
from engine.timeline import periods_for_century, same_era
from simulation.strategies.base import Strategy
from simulation.strategies.util import (
    past_overload_value, past_overload_allocation, special_activations, special_buy,
    astrolabe_destination, century_farthest_from_rivals, paradox_can_terminate,
    refrigerator_choice, safe_travel_cap,
)


# ---------------------------------------------------------------------------
# Card wish-lists
# ---------------------------------------------------------------------------

# Tier 1: immediate survival value; buy even with low gold.
_SURVIVAL_CARDS = {
    "Towel",                  # 1g: Travel and Paradox never overload
    "Al-Jazari's Automaton",  # 1g: -5 booms on overload
    "Tesla's AC Motor",       # 1g: +1 energy on boom gain
    "Viking Shield",          # 2g: first energy loss -2/hour
    "Seismograph",            # 2g: -2 booms on gain
    "Super Motor",            # 2g: prevent first explosion
    "Holy Grail",             # 3g: prevent first death
    "Joan of Arc's Armor",    # 4g: -1 to every energy loss
}

# Tier 2: market efficiency; cheap discounts and access.
_MARKET_CARDS = {
    "Porcelain",             # 1g: cards cost 1g less
    "Harald's Blue Tooth",    # 1g: atemporal market access
    "The First Smartphone",  # 1g: atemporal market access
    "Vending Machine",       # 2g: +1 gold when others buy
    "Window of Time",        # 4g: always synchronic
}

# Tier 3: travel and delivery acceleration.
_TRAVEL_CARDS = {
    "Gerardus Mercator's Map",    # 2g: active: 3-century move
    "Horse Collar",               # 3g: +1 energy per travel module
    "Navigation Compass",         # 3g: passive bonus travel each hour
    "Galileo's Telescope",        # 3g: reduces past-travel cost
    "da Vinci's Flying Machine",  # 4g: large item, reduces travel cost
    "Astrolabe",                  # 3g: free in-era travel (recycles)
}


class ConservativeStrategy(Strategy):
    """
    Spread dice across functions: at most 2 per function, to avoid overloading.
    Prioritises Recharge (energy+gold), adds Paradox only when it can kill,
    and fills Travel last.
    """

    ENERGY_SAFETY = 6     # Below this: prefer Recharge over everything
    ENERGY_LOW    = 4     # Below this: skip Travel entirely

    @property
    def name(self) -> str:
        return "Conservative"

    # ------------------------------------------------------------------
    # choose_allocation
    # ------------------------------------------------------------------

    def choose_allocation(
        self,
        traveler: TravelerState,
        game: GameState,
        dice: list[int],
    ) -> tuple:
        alloc = Allocation.empty()
        unavailable = traveler.overloaded_functions
        direction = -1  # always travel toward Year Zero

        # Park at the Merchant to shop this Hour.
        if (game.merchant_century == traveler.century
                and not traveler.equipment_full()
                and traveler.gold >= 1):
            return self._park_alloc(dice, unavailable), direction, 0

        # The overloaded Paradox hits the Past for DOUBLE (28/09): a kill it makes
        # is a kill shot like any other.
        v3 = past_overload_value(traveler, game, dice)
        if v3 is not None:
            return past_overload_allocation(traveler, dice, v3), direction, 0

        kill_threat = paradox_can_terminate(traveler, game, dice)
        energy_ok   = traveler.energy >= self.ENERGY_SAFETY
        # Heating to travel (module 7) adds booms; if it would tip us over
        # BOOM_LIMIT the motor explodes and we don't move at all (§15.2). Treat
        # that as "can't travel this Hour" so the good dice farm instead.
        boom_safe   = (traveler.booms + max(dice, default=0)) < BOOM_LIMIT
        can_travel  = (traveler.energy >= self.ENERGY_LOW
                       and FUNCTION_TRAVEL not in unavailable
                       and boom_safe)

        remaining = list(dice)

        if kill_threat:
            # Kill shot available: Recharge, then Paradox(2), then Travel with leftovers.
            remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2)
            remaining = self._fill_max(alloc, FUNCTION_PARADOX,  remaining, unavailable, max_slots=2)
            if can_travel:
                remaining = self._fill_max(alloc, FUNCTION_TRAVEL, remaining, unavailable, max_slots=2)
        elif not energy_ok:
            # Energy low: Recharge, Travel, Paradox (survival first, still move).
            remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2)
            if can_travel:
                remaining = self._fill_max(alloc, FUNCTION_TRAVEL, remaining, unavailable, max_slots=2)
            remaining = self._fill_max(alloc, FUNCTION_PARADOX, remaining, unavailable, max_slots=1)
        else:
            # Normal: Travel first to preserve the dominant-value die for movement,
            # then Recharge fills remaining dice, Paradox takes 1 leftover.
            if can_travel:
                remaining = self._fill_max(alloc, FUNCTION_TRAVEL,   remaining, unavailable, max_slots=2)
            remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2)
            remaining = self._fill_max(alloc, FUNCTION_PARADOX,  remaining, unavailable, max_slots=1)

        # Mop-up: place any leftover dice rather than waste them. Recharge and
        # Paradox are tried before Travel, so a die only lands in Travel (module 7
        # heating) when §10.2 leaves it no other legal home, an unavoidable heat,
        # not a chosen one.
        for fn in (FUNCTION_RECHARGE, FUNCTION_PARADOX, FUNCTION_TRAVEL):
            if fn in unavailable:
                continue
            remaining = self._fill_max(alloc, fn, remaining, unavailable, max_slots=2)

        for val in remaining:
            if unavailable:
                send_to_escape_valve(alloc, val)

        energy_cap = safe_travel_cap(traveler, reserve=2)
        return alloc, direction, energy_cap

    def _park_alloc(self, dice: list[int], unavailable: set[int]) -> Allocation:
        """No travel: Recharge, then Paradox, then the escape valve."""
        alloc = Allocation.empty()
        remaining = list(dice)
        remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2)
        remaining = self._fill_max(alloc, FUNCTION_PARADOX, remaining, unavailable, max_slots=1)
        for val in remaining:
            if unavailable:
                send_to_escape_valve(alloc, val)
        return alloc

    # ------------------------------------------------------------------
    # choose_market_action
    # ------------------------------------------------------------------

    def choose_market_action(
        self,
        traveler: TravelerState,
        game: GameState,
        revealed: list[Card],
        renew_cost: int,
    ) -> MarketAction:
        # The two special cards under the Secret Market's twelve (28/09).
        _special = special_buy(traveler, revealed, trinity=False)
        if _special is not None:
            return BuyAction(_special)
        held_names = {c.name for c in traveler.hand}
        missing_periods = {"Origins", "Ascension", "Singularity"} - traveler.delivered_periods

        # Clear the Wanted poster at the Merchant to use the Secret Market next phase.
        if (traveler.is_wanted
                and renew_cost != SECRET_MARKET_RENEW_COST
                and game.secret_market_open
                and traveler.gold >= DECLARE_COST):
            return DeclareAction()

        # 1. Delivery cards for still-missing periods (cheapest first; equal
        #    prices fall back on the fixed card ranking).
        if missing_periods:
            candidates = []
            for card in revealed:
                if card.delivery_century is None or card.delivery_century > traveler.century:
                    continue
                card_periods = set(periods_for_century(card.delivery_century))
                new = card_periods & missing_periods
                if not new:
                    continue
                if card.name in held_names:
                    continue
                if traveler.gold < card.gold_cost or not traveler.can_hold(card):
                    continue
                candidates.append((card.gold_cost, TIEBREAK_RANK[card.name], card))
            if candidates:
                candidates.sort()
                return BuyAction(candidates[0][2])

        # 2. Survival cards when energy is low.
        if traveler.energy < self.ENERGY_SAFETY:
            for card in sorted(revealed, key=lambda c: c.gold_cost):
                if card.name not in _SURVIVAL_CARDS or card.name in held_names:
                    continue
                if traveler.gold >= card.gold_cost and traveler.can_hold(card):
                    return BuyAction(card)

        # 3. Market-efficiency cards (cheap; buy whenever affordable).
        for card in sorted(revealed, key=lambda c: c.gold_cost):
            if card.name not in _MARKET_CARDS or card.name in held_names:
                continue
            if traveler.gold >= card.gold_cost and traveler.can_hold(card):
                return BuyAction(card)

        # 4. Survival cards (any energy level: still valuable).
        for card in sorted(revealed, key=lambda c: c.gold_cost):
            if card.name not in _SURVIVAL_CARDS or card.name in held_names:
                continue
            if traveler.gold >= card.gold_cost and traveler.can_hold(card):
                return BuyAction(card)

        # 5. Travel / speed cards when we have gold to spare.
        if traveler.gold >= 2:
            for card in sorted(revealed, key=lambda c: c.gold_cost):
                if card.name not in _TRAVEL_CARDS or card.name in held_names:
                    continue
                if traveler.gold >= card.gold_cost and traveler.can_hold(card):
                    return BuyAction(card)

        # 6. Renew once cheaply to cycle the market.
        if renew_cost <= 1 and traveler.gold >= renew_cost and revealed:
            return RenewAction(revealed[0])

        return PassAction()

    # ------------------------------------------------------------------
    # choose_activations
    # ------------------------------------------------------------------

    def choose_activations(
        self,
        traveler: TravelerState,
        game: GameState,
    ) -> list[tuple[Card, object]]:
        activations = []
        era_enemies = [
            t for t in game.travelers
            if t is not traveler and not t.awaiting_respawn
            and same_era(traveler.century, t.century)
        ]
        sync_enemies = [t for t in era_enemies if t.century == traveler.century]
        weakest_sync = min(sync_enemies, key=lambda t: t.energy, default=None)
        weakest_era  = min(era_enemies,  key=lambda t: t.energy, default=None)

        # Only use weapons when we have energy to spare.
        use_weapons = traveler.energy >= self.ENERGY_SAFETY

        for card in list(traveler.hand):
            name = card.name
            ctx: object = None

            if name == "Gerardus Mercator's Map":
                if traveler.century <= 3:
                    continue
                ctx = (min(3, traveler.century - 1), -1)

            elif name == "Astrolabe":
                ctx = astrolabe_destination(traveler)
                if ctx is None:
                    continue

            elif name == "Refrigerator":
                ctx = refrigerator_choice(traveler, game)
                if ctx is None:
                    continue

            # Weapons: only while energy is comfortable.
            elif name in ("Laser Gun", "Fire Lance"):
                if not use_weapons or weakest_sync is None:
                    continue
                ctx = weakest_sync

            elif name == "Ferguson Rifle":
                if not use_weapons or weakest_era is None:
                    continue
                ctx = weakest_era

            elif name == "Wormhole Pistol":
                if not use_weapons or weakest_era is None:
                    continue
                ctx = weakest_era

            else:
                continue

            activations.append((card, ctx))

        return activations + special_activations(traveler, game)

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    def choose_cards_to_deliver(
        self,
        traveler: TravelerState,
        game: GameState,
        deliverable: list[Card],
    ) -> list[Card]:
        """Hold key passives while they're actively helping; deliver the rest."""
        to_keep: set[str] = set()
        for card in deliverable:
            if card.name == "Seismograph" and traveler.booms >= 7:
                to_keep.add(card.name)
            elif card.name == "Super Motor" and traveler.booms >= 8:
                to_keep.add(card.name)
            elif card.name == "Holy Grail" and traveler.energy <= 4:
                to_keep.add(card.name)
            elif card.name == "Towel":
                to_keep.add(card.name)
        return [c for c in deliverable if c.name not in to_keep]

    # ------------------------------------------------------------------
    # Reward sub-choices
    # ------------------------------------------------------------------

    def choose_reward_category(
        self,
        traveler: TravelerState,
        game: GameState,
        available: list[str],
    ) -> str:
        """Resource first (gold, matrix), then Time (market, travel), then Chaos."""
        for preferred in ("Resource", "Time", "Chaos"):
            if preferred in available:
                return preferred
        return available[0]

    def choose_chaos_destroy_target(self, traveler, game, candidates):
        """Destroy the most expensive equipped card from a rival; else cheapest market card."""
        equipped = [c for t in game.travelers
                    if t is not traveler and not t.awaiting_respawn
                    for c in t.hand if c in candidates]
        if equipped:
            return max(equipped, key=lambda c: c.gold_cost)
        return min(candidates, key=lambda c: c.gold_cost)

    def choose_chaos_merchant_century(self, traveler, game):
        """Push the Merchant away from rivals to block their market access."""
        return century_farthest_from_rivals(traveler, game)

    def choose_matrix_buff_module(self, traveler, game):
        """Buff Recharge modules first (energy/gold income), then Travel."""
        already = set(traveler.matrix_buffs.keys())
        for m in [0, 1, 2, 7, 8, 3, 4, 5, 6]:
            if m not in already:
                return m
        return next((m for m in range(9) if m not in already), 0)

    def choose_resource_steal_target(self, traveler, game, revealed):
        """Steal the most valuable card we can hold."""
        affordable = [c for c in revealed if traveler.can_hold(c)]
        return max(affordable, key=lambda c: c.gold_cost) if affordable else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fill_max(
        self,
        alloc: Allocation,
        fn: int,
        remaining: list[int],
        unavailable: set[int],
        max_slots: int = 2,
    ) -> list[int]:
        if fn in unavailable or not remaining:
            return remaining
        existing = alloc.generators_in_function(fn)
        req_val   = existing[0] if existing else None
        slots_used = len(existing)

        counts = count_faces(remaining)
        if req_val is not None:
            val = req_val if counts.get(req_val, 0) > 0 else None
        else:
            candidates = [(v, c) for v, c in counts.items() if c > 0]
            val = max(candidates, key=lambda x: (x[1], x[0]))[0] if candidates else None

        if val is None:
            return remaining

        to_place = min(counts[val], max_slots - slots_used)
        for _ in range(to_place):
            place(alloc, fn, slots_used, val)
            slots_used += 1
            remaining = list(remaining)
            remaining.remove(val)
        return remaining

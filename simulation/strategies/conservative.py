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
from engine.cards import Card
from engine.market import MarketAction, BuyAction, RenewAction, DeclareAction, PassAction
from engine.timeline import periods_for_century
from simulation.strategies.base import Strategy
from simulation.strategies.util import paradox_can_terminate, safe_travel_cap


# ---------------------------------------------------------------------------
# Card wish-lists (ordered by value within each tier)
# ---------------------------------------------------------------------------

# Tier 1: immediate survival value; buy even with low gold.
_SURVIVAL_CARDS = {
    "Toalha",                               # 1g: no overload ever
    "Autômato de Ismail Al-Jazari",         # 1g: -5 booms on overload
    "Motor de Corrente Alternada de Tesla", # 1g: +1 energy on boom gain
    "Escudo Viking",                        # 2g: first energy loss -2/hour
    "Sismográfico",                         # 2g: -2 booms on gain
    "Super Motor",                          # 2g: prevent first explosion
    "Santo Graal",                          # 3g: prevent first death
    "Armadura da Joana d'Arc",              # 4g: −1 to every energy loss
}

# Tier 2: market efficiency; cheap discounts and access.
_MARKET_CARDS = {
    "Porcelana",                            # 1g: cards cost 1g less
    "Dente Azul do Harald",                 # 1g: atemporal market access
    "Primeiro Smartphone",                  # 1g: atemporal market access
    "Máquina de Venda Automática",          # 2g: +1 gold when others buy
    "Janela do Tempo",                      # 4g: always synchronic
}

# Tier 3: travel and delivery acceleration.
_TRAVEL_CARDS = {
    "Mapa de Geradus Mercator",             # 2g: active: free 3-century move
    "Colar de Cavalo",                      # 3g: +1 energy per travel module
    "Bússola de Navegação",                 # 3g: passive bonus travel each hour
    "Telescópio de Galileu Galilei",        # 3g: reduces past-travel cost
    "Máquina Voadora da da Vinci",          # 4g: large item, reduces travel cost
    "Astrolábio",                           # 3g: free in-era travel (recycles)
}


class ConservativeStrategy(Strategy):
    """
    Spread dice across functions: at most 2 per function, to avoid overloading.
    Prioritises Recharge (energy+gold), adds Paradox only when it can kill,
    and fills Travel last.
    """

    ENERGY_SAFETY = 6     # Below this: prefer Recharge over everything
    ENERGY_LOW    = 4     # Below this: skip Travel entirely
    BOOM_DANGER   = 9     # At/above this, heating to travel would explode (§15.2):
                          #   don't waste the dominant dice on a trip that never
                          #   happens: route them to Recharge instead.

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
            # Kill shot available: Recharge → Paradox(2) → Travel with leftovers.
            remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2)
            remaining = self._fill_max(alloc, FUNCTION_PARADOX,  remaining, unavailable, max_slots=2)
            if can_travel:
                remaining = self._fill_max(alloc, FUNCTION_TRAVEL, remaining, unavailable, max_slots=2)
        elif not energy_ok:
            # Energy low: Recharge → Travel → Paradox (survival first, still move).
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
        """No travel: Recharge → Paradox → escape valve."""
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
        held_names = {c.name for c in traveler.hand}
        missing_periods = {"Origins", "Ascension", "Singularity"} - traveler.delivered_periods

        # Clear Wanted poster at the Merchant so we can use the Secret Market next phase.
        if (traveler.is_wanted
                and renew_cost != 999          # not inside Secret Market loop
                and game.secret_market_open
                and traveler.gold >= DECLARE_COST):
            return DeclareAction()

        # 1. Delivery cards for still-missing periods (cheapest first).
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
                candidates.append((card.gold_cost, card.name, card))
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
            and _same_era(traveler.century, t.century)
        ]
        sync_enemies = [t for t in era_enemies if t.century == traveler.century]
        weakest_sync = min(sync_enemies, key=lambda t: t.energy, default=None)
        weakest_era  = min(era_enemies,  key=lambda t: t.energy, default=None)

        # Only use weapons when we have energy to spare.
        use_weapons = traveler.energy >= self.ENERGY_SAFETY

        for card in list(traveler.hand):
            name = card.name
            ctx: object = None

            if name == "Mapa de Geradus Mercator":
                if traveler.century <= 3:
                    continue
                ctx = (min(3, traveler.century - 1), -1)

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

            # Weapons: only if energy is comfortable AND the target is low enough to kill.
            elif name in ("Arma de Laser", "Lança de Fogo"):
                if not use_weapons or weakest_sync is None:
                    continue
                ctx = weakest_sync

            elif name == "Rifle Fergunson":
                if not use_weapons or weakest_era is None:
                    continue
                ctx = weakest_era

            elif name == "Arma de Portais":
                if not use_weapons or weakest_era is None:
                    continue
                ctx = weakest_era

            else:
                continue

            activations.append((card, ctx))

        return activations

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
            if card.name == "Sismográfico" and traveler.booms >= 7:
                to_keep.add(card.name)
            elif card.name == "Super Motor" and traveler.booms >= 8:
                to_keep.add(card.name)
            elif card.name == "Santo Graal" and traveler.energy <= 4:
                to_keep.add(card.name)
            elif card.name == "Toalha":
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
        """Resource first (gold/matrix), then Time (market/travel), then Chaos."""
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
        """Push Merchant away from rivals to block their market access."""
        from engine.constants import CENTURY_MAX
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


def _same_era(a: int, b: int) -> bool:
    from engine.timeline import eras_for_century
    return bool(set(eras_for_century(a)) & set(eras_for_century(b)))

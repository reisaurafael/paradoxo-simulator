"""
simulation/strategies/collector.py
====================================
Collector strategy: optimised for completing the Temporal Receptor.

Decision priority (in order):

  1. DELIVER: if standing exactly on a held card's delivery century, park and
     let Phase 1 handle it.

  2. TRAVEL TO DELIVER: if holding any card whose delivery century is still
     reachable (≤ current century), travel to land on it (precision landing or
     partial approach).

  3. PARK NEAR DELIVERY: if Travel is overloaded and a delivery target is
     within 6 centuries, stay put to avoid overshooting next Hour.

  4. APPROACH DELIVERY: if holding a reachable card but too far for precision
     landing, travel toward it conservatively.

  5. NO DELIVERY CARDS: use gold state to decide movement:
       Richest traveler: move toward Merchant, then park and farm gold.
       Not richest:      farm gold (Recharge) until becoming the richest.

Allocation priority (outside delivery targeting):
  Energy/Gold (Recharge) is always first. Paradox is included only when it
  can plausibly terminate a rival (util.paradox_can_terminate). Travel fills
  whatever slots remain.

Market phase:
  • Clear Wanted status (DeclareAction) to restore Secret Market access.
  • Buy delivery cards for missing periods first, then energy/survival cards,
    then market-access helpers. Renew the market when nothing useful is visible.

Energy management:
  • Before Phase 2, recycle held cards whose delivery century has been passed
    when energy is low (converts now-useless items into energy, §21.1).
"""

from __future__ import annotations
from engine.state import TravelerState, GameState, Allocation
from engine.constants import (
    FUNCTION_RECHARGE,
    FUNCTION_PARADOX,
    FUNCTION_TRAVEL,
    BOOM_LIMIT,
    RENEW_COST_BASE,
    DECLARE_COST,
)
from engine.matrix import place, send_to_escape_valve
from engine.dice import count_faces
from engine.cards import Card
from engine.market import (
    MarketAction, BuyAction, RenewAction, DeclareAction, PassAction,
    effective_card_cost,
)
from engine.timeline import periods_for_century
from simulation.strategies.base import Strategy
from simulation.strategies.util import paradox_can_terminate, safe_travel_cap


# Energy/survival cards worth buying even for a delivery-focused traveler.
_ENERGY_CARDS = {
    "Toalha",                               # 1g: no overload
    "Autômato de Ismail Al-Jazari",         # 1g: -5 booms on overload
    "Motor de Corrente Alternada de Tesla", # 1g: +1 energy on boom
    "Escudo Viking",                        # 2g: first energy loss -2/hour
    "Sismográfico",                         # 2g: -2 booms
    "Super Motor",                          # 2g: prevent first explosion
    "Colar de Cavalo",                      # 3g: +1 energy per travel module
    "Santo Graal",                          # 3g: prevent first death
}

# Market helpers that make buying delivery cards cheaper/easier.
_MARKET_HELPERS = {
    "Porcelana",            # 1g: market cards cost 1 less
    "Dente Azul do Harald", # 1g: atemporal market access
    "Primeiro Smartphone",  # 1g: atemporal market access
}

class CollectorStrategy(Strategy):
    """
    Maximises deliveries by buying and delivering any reachable card.
    Prioritises energy/gold over Paradox, adding Paradox only when a
    kill shot is available. Uses gold accumulation to attract the Merchant.
    """

    ENERGY_CRITICAL = 3    # prioritise Recharge over Travel below this
    BOOM_DANGER     = 9    # avoid travel when booms are this high
    ENERGY_LOW      = 11   # recycle non-reachable items below this threshold
    ENERGY_SAFE     = 8    # minimum to consider any weapon use

    @property
    def name(self) -> str:
        return "Collector"

    # ------------------------------------------------------------------
    # choose_allocation
    # ------------------------------------------------------------------

    def choose_allocation(
        self,
        traveler: TravelerState,
        game: GameState,
        dice: list[int],
    ) -> tuple:
        unavailable = traveler.overloaded_functions

        # Priority 1: already on a delivery century, park and deliver next Phase 1.
        if self._should_stay_to_deliver(traveler):
            return self._farm_alloc(dice, unavailable, game, traveler), -1, 0

        # Priority 2: precision travel to land on a reachable delivery century.
        delivery_result = self._try_target_delivery(traveler, dice, unavailable)
        if delivery_result is not None:
            alloc, cap = delivery_result
            energy_cap = safe_travel_cap(traveler, reserve=2)
            return alloc, -1, min(cap, energy_cap)

        # Priority 3: Travel overloaded and a target is within overshoot range, park.
        if (FUNCTION_TRAVEL in unavailable and
                any(1 <= traveler.century - c.delivery_century <= 6
                    for c in traveler.hand if c.delivery_century is not None)):
            return self._farm_alloc(dice, unavailable, game, traveler), -1, 0

        # Priority 4: reachable delivery card but too far, travel toward it.
        if self._has_reachable_delivery(traveler):
            energy_cap = safe_travel_cap(traveler, reserve=2)
            return self._travel_toward_delivery_alloc(traveler, dice, unavailable, game), -1, energy_cap

        # Priority 5: no deliverable card, use gold state to decide.
        merchant = game.merchant_century
        if self._is_richest(traveler, game):
            if traveler.century == merchant:
                return self._farm_alloc(dice, unavailable, game, traveler), -1, 0
            direction = -1 if traveler.century > merchant else 1
            energy_cap = safe_travel_cap(traveler, reserve=2) if direction == -1 else None
            return self._travel_toward_delivery_alloc(traveler, dice, unavailable, game), direction, energy_cap
        else:
            return self._farm_alloc(dice, unavailable, game, traveler), -1, 0

    def _farm_alloc(
        self,
        dice: list[int],
        unavailable: set[int],
        game: GameState | None = None,
        traveler: TravelerState | None = None,
    ) -> Allocation:
        """No travel: Recharge(2) → Paradox(2 if kill, else 1) → escape valve."""
        alloc = Allocation.empty()
        remaining = list(dice)
        # High dice into Recharge: energy/gold gained scales with the die value.
        remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable,
                                   max_slots=2, prefer="high")
        if FUNCTION_PARADOX not in unavailable:
            best = max(dice) if dice else 0
            kill = game is not None and any(
                t.energy <= best
                for t in game.travelers
                if not t.awaiting_respawn and t is not traveler
            )
            # Commit high dice to Paradox only for a kill shot; otherwise it is a
            # filler for the leftover low die (§10.2 forces one value per function).
            paradox_slots = 2 if kill else 1
            remaining = self._fill_max(alloc, FUNCTION_PARADOX, remaining, unavailable,
                                       max_slots=paradox_slots,
                                       prefer="count" if kill else "low")
        for val in remaining:
            if unavailable:
                send_to_escape_valve(alloc, val)
        return alloc

    def _travel_toward_delivery_alloc(
        self,
        traveler: TravelerState,
        dice: list[int],
        unavailable: set[int],
        game: GameState | None = None,
    ) -> Allocation:
        """Conservative 2-column travel + Recharge; prioritise Recharge when energy is low."""
        alloc = Allocation.empty()
        remaining = list(dice)
        kill = game is not None and paradox_can_terminate(traveler, game, dice)

        if FUNCTION_TRAVEL not in unavailable and traveler.booms < self.BOOM_DANGER:
            if traveler.energy < self.ENERGY_CRITICAL:
                remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2, prefer="high")
                remaining = self._fill_max(alloc, FUNCTION_TRAVEL,   remaining, unavailable, max_slots=2)
            else:
                remaining = self._fill_max(alloc, FUNCTION_TRAVEL,   remaining, unavailable, max_slots=2)
                remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2, prefer="high")
        else:
            remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2, prefer="high")

        paradox_slots = 2 if kill else 1
        remaining = self._fill_max(alloc, FUNCTION_PARADOX, remaining, unavailable,
                                   max_slots=paradox_slots,
                                   prefer="count" if kill else "low")
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

        # Clear Wanted poster for Secret Market access.
        if (traveler.is_wanted
                and renew_cost != 999
                and game.secret_market_open
                and traveler.gold >= DECLARE_COST):
            return DeclareAction()

        # 1. Market helpers (cheap; enable better buying).
        for card in sorted(revealed, key=lambda c: effective_card_cost(c, traveler)):
            if card.name not in _MARKET_HELPERS or card.name in held_names:
                continue
            if traveler.gold >= effective_card_cost(card, traveler) and traveler.can_hold(card):
                return BuyAction(card)

        # 2. Energy cards only when critically low (don't let survival displace deliveries).
        if traveler.energy < self.ENERGY_CRITICAL:
            for card in sorted(revealed, key=lambda c: effective_card_cost(c, traveler)):
                if card.name not in _ENERGY_CARDS or card.name in held_names:
                    continue
                if traveler.gold >= effective_card_cost(card, traveler) and traveler.can_hold(card):
                    return BuyAction(card)

        # 3. Delivery cards: every reachable card is worth +1 CP on delivery, so a
        #    nearby weapon beats a distant non-weapon. Buy the easiest to deliver:
        #    cover a missing period first, then the shortest trip, then the cheapest.
        candidates = []
        for card in revealed:
            if not card.delivery_century or card.delivery_century > traveler.century:
                continue
            if card.name in held_names:
                continue
            cost = effective_card_cost(card, traveler)
            if traveler.gold < cost or not traveler.can_hold(card):
                continue
            card_periods = set(periods_for_century(card.delivery_century))
            new_periods = len(card_periods & missing_periods)
            steps = traveler.century - card.delivery_century
            candidates.append((-new_periods, steps, cost, card.name, card))
        if candidates:
            candidates.sort(key=lambda x: x[:3])
            return BuyAction(candidates[0][4])

        # 4. Renew (cost 1) only while still hunting deliveries: there must be a
        #    missing period left to chase and room to hold what we'd find. Renewing
        #    with a full receptor goal or no capacity is just throwing gold away.
        if (renew_cost == RENEW_COST_BASE
                and traveler.gold > renew_cost          # keep a gold buffer
                and revealed
                and missing_periods
                and any(traveler.can_hold(c) for c in revealed)):
            return RenewAction(revealed[0])

        return PassAction()

    # ------------------------------------------------------------------
    # choose_items_to_recycle  (pre-Phase 2 energy recovery)
    # ------------------------------------------------------------------

    def choose_items_to_recycle(
        self,
        traveler: TravelerState,
        game: GameState,
    ) -> list[Card]:
        if traveler.energy >= self.ENERGY_LOW:
            return []
        return [
            c for c in traveler.hand
            if c.delivery_century is not None
            and c.delivery_century > traveler.century
        ]

    # ------------------------------------------------------------------
    # choose_activations
    # ------------------------------------------------------------------

    def choose_activations(
        self,
        traveler: TravelerState,
        game: GameState,
    ) -> list[tuple[Card, object]]:
        activations = []
        for card in list(traveler.hand):
            if card.name == "Mapa de Geradus Mercator":
                # Travel back toward future to recover an overshot delivery century.
                overshot = [
                    c for c in traveler.hand
                    if c.delivery_century is not None
                    and 1 <= c.delivery_century - traveler.century <= 3
                ]
                if overshot:
                    nearest = min(overshot, key=lambda c: c.delivery_century - traveler.century)
                    steps = nearest.delivery_century - traveler.century
                    activations.append((card, (steps, 1)))
                elif traveler.century > 4:
                    activations.append((card, (2, -1)))

            elif card.name == "Geladeira":
                from simulation.strategies.util import geladeira_context
                receptor_actives = [c for c in getattr(traveler, "receptor_cards", [])
                                    if c.ability_type in ("active", "atemporal_active")
                                    and c.active_effect is not None]
                chosen = next(
                    ((rc, sub) for rc in receptor_actives
                     for ok, sub in [geladeira_context(rc, traveler, game)] if ok),
                    None,
                )
                if chosen is not None:
                    activations.append((card, (chosen[0], chosen[1])))

        return activations

    # ------------------------------------------------------------------
    # Reward sub-choices
    # ------------------------------------------------------------------

    def choose_resource_steal_target(self, traveler, game, revealed):
        """Steal the nearest reachable delivery card, else the most valuable."""
        reachable = [
            c for c in revealed
            if c.delivery_century is not None
            and c.delivery_century <= traveler.century
            and traveler.can_hold(c)
        ]
        if reachable:
            return min(reachable, key=lambda c: traveler.century - c.delivery_century)
        affordable = [c for c in revealed if traveler.can_hold(c)]
        return max(affordable, key=lambda c: c.gold_cost) if affordable else None

    def choose_matrix_buff_module(self, traveler, game):
        """Buff Recharge modules first (sustain energy/gold), then Travel."""
        already = set(traveler.matrix_buffs.keys())
        for m in [1, 2, 0, 7, 8, 6, 3, 4, 5]:
            if m not in already:
                return m
        return next((m for m in range(9) if m not in already), 0)

    def choose_chaos_destroy_target(self, traveler, game, candidates):
        """Destroy a rival's delivery card if visible, else cheapest market card."""
        rival_delivery = [
            c for t in game.travelers
            if t is not traveler and not t.awaiting_respawn
            for c in t.hand
            if c in candidates and c.delivery_century is not None
        ]
        if rival_delivery:
            return min(rival_delivery, key=lambda c: abs(c.delivery_century - traveler.century))
        market_cards = [c for c in candidates
                        if not any(c in t.hand for t in game.travelers)]
        return min(market_cards or candidates, key=lambda c: c.gold_cost)

    def choose_reward_category(self, traveler, game, available):
        """Resource (gold/matrix) → Time (market/travel) → Chaos."""
        for preferred in ("Resource", "Time", "Chaos"):
            if preferred in available:
                return preferred
        return available[0]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_richest(self, traveler: TravelerState, game: GameState) -> bool:
        others = [t for t in game.travelers if t is not traveler and not t.awaiting_respawn]
        return not others or traveler.gold >= max(t.gold for t in others)

    def _has_reachable_delivery(self, traveler: TravelerState) -> bool:
        return any(
            c.delivery_century is not None and 0 < c.delivery_century <= traveler.century
            for c in traveler.hand
        )

    def _should_stay_to_deliver(self, traveler: TravelerState) -> bool:
        return any(
            c.delivery_century == traveler.century
            for c in traveler.hand
            if c.delivery_century is not None
        )

    def _try_target_delivery(
        self,
        traveler: TravelerState,
        dice: list[int],
        unavailable: set[int],
    ) -> tuple[Allocation, int] | None:
        if FUNCTION_TRAVEL in unavailable:
            return None
        targets = sorted(
            {c.delivery_century for c in traveler.hand
             if c.delivery_century is not None and 0 < c.delivery_century < traveler.century},
            reverse=True,
        )
        counts = count_faces(dice)
        for target_century in targets:
            steps = traveler.century - target_century
            if steps < 1 or steps > 6:
                continue
            for v in (1, 2, 3):
                if counts.get(v, 0) >= 2 and v >= steps:
                    return self._travel_alloc(dice, v, cols=2, unavailable=unavailable), steps
            if steps in (4, 5, 6) and traveler.booms + 2 < BOOM_LIMIT:
                for v in (2, 3):
                    if counts.get(v, 0) >= 3:
                        return self._travel_alloc(dice, v, cols=3, unavailable=unavailable), steps
        # Partial approach.
        nearest_steps = min(
            (traveler.century - c.delivery_century
             for c in traveler.hand
             if c.delivery_century is not None
             and 1 <= traveler.century - c.delivery_century <= 6),
            default=None,
        )
        if nearest_steps is not None and nearest_steps > 1:
            for v in (1, 2, 3):
                if counts.get(v, 0) >= 2 and v < nearest_steps:
                    return self._travel_alloc(dice, v, cols=2, unavailable=unavailable), v
        return None

    def _travel_alloc(
        self,
        dice: list[int],
        travel_val: int,
        cols: int,
        unavailable: set[int],
    ) -> Allocation:
        alloc = Allocation.empty()
        remaining = list(dice)
        for col in range(cols):
            place(alloc, FUNCTION_TRAVEL, col, travel_val)
            remaining.remove(travel_val)
        remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2, prefer="high")
        remaining = self._fill_max(alloc, FUNCTION_PARADOX,  remaining, unavailable, max_slots=1, prefer="low")
        for val in remaining:
            if unavailable:
                send_to_escape_valve(alloc, val)
        return alloc

    def _fill_max(
        self,
        alloc: Allocation,
        fn: int,
        remaining: list[int],
        unavailable: set[int],
        max_slots: int = 3,
        prefer: str = "count",
    ) -> list[int]:
        """Fill a function with the best single die value (§10.2: one value per
        function). ``prefer`` breaks ties between equally-frequent values:
        ``"high"`` keeps high dice in value-scaling functions (Recharge energy/gold),
        ``"low"`` dumps the cheapest die into a low-value function (Paradox filler),
        ``"count"`` (default) is purely frequency-based.
        """
        if fn in unavailable or not remaining:
            return remaining
        existing = alloc.generators_in_function(fn)
        req_val    = existing[0] if existing else None
        slots_used = len(existing)
        if req_val is not None:
            if remaining.count(req_val) == 0:
                return remaining
            val = req_val
        elif prefer == "high":
            val = max(remaining, key=lambda v: (remaining.count(v), v))
        elif prefer == "low":
            val = min(remaining, key=lambda v: (-remaining.count(v), v))
        else:
            val = max(set(remaining), key=remaining.count)
        to_place = min(remaining.count(val), max_slots - slots_used)
        for _ in range(to_place):
            place(alloc, fn, slots_used, val)
            remaining = remaining[:]
            remaining.remove(val)
            slots_used += 1
        return remaining

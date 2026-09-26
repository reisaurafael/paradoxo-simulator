"""
simulation/strategies/smart.py
================================
Smart strategy: adaptive allocation based on current game state.

Core intent: win by CP, not by being the first to reach Year Zero. Each Hour
the strategy evaluates three levers in order:

  1. ENERGY/GOLD first: Recharge fills before Travel unless the board state
     clearly calls for speed (deliver timing, Merchant convergence, low travel
     budget remaining).

  2. PARADOX as a weapon: Paradox gets real investment (2 columns) only when
     it can plausibly terminate a rival (energy ≤ best available die). Otherwise
     a single Paradox column keeps mild pressure without sacrificing Recharge.

  3. TRAVEL to close distance: overload Travel only when dice are high (≥ 2)
     AND energy is comfortable enough to absorb the next-Hour escape-valve penalty.

Market behaviour:
  - DeclareAction to clear Wanted before entering the Secret Market loop.
  - Wide card wishlist: delivery periods, energy/survival, market access, travel,
    Prince Dracula's Chalice (energy on paradox hits), Quantum Computer,
    Joan of Arc's Armor.
  - Renew up to cost 2 when the revealed set has nothing useful.
"""

from __future__ import annotations
from engine.state import TravelerState, GameState, Allocation
from engine.constants import (
    FUNCTION_RECHARGE,
    FUNCTION_PARADOX,
    FUNCTION_TRAVEL,
    BOOM_LIMIT,
    DECLARE_COST,
)
from engine.matrix import place, send_to_escape_valve
from engine.dice import count_faces
from engine.cards import Card, TIEBREAK_RANK
from engine.market import (
    MarketAction, BuyAction, RenewAction, DeclareAction, PassAction, SECRET_MARKET_RENEW_COST,
)
from engine.timeline import in_older_era, periods_for_century, same_era
from simulation.strategies.base import Strategy
from simulation.strategies.util import (
    astrolabe_destination, paradox_can_terminate, refrigerator_choice, safe_travel_cap,
)


class SmartStrategy(Strategy):
    """
    Adaptive strategy. Prioritises Energy/Gold over Paradox over Travel in the
    baseline; escalates Paradox when a kill shot is available; escalates Travel
    only when energy is comfortable and dice are strong.
    """

    OVERLOAD_MIN_VALUE = 2     # Only overload Travel if dominant die is ≥ this
    ENERGY_CRITICAL    = 4     # Below this: Recharge first, no overloading
    ENERGY_SAFE        = 6     # Above this: can consider weapons
    BOOM_DANGER        = 9     # Above this: skip heating module (no Travel)

    # ------------------------------------------------------------------
    # Card wish-lists
    # ------------------------------------------------------------------

    # Tier A: cheap, high-value survivability.
    _ENERGY_CARDS = {
        "Towel",                     # 1g: Travel and Paradox never overload
        "Al-Jazari's Automaton",     # 1g: -5 booms on overload
        "Tesla's AC Motor",          # 1g: +1 energy on boom gain
        "Viking Shield",             # 2g: first energy loss -2/hour
        "Seismograph",               # 2g: -2 booms on gain
        "Super Motor",               # 2g: prevent first explosion
        "Horse Collar",              # 3g: +1 energy per travel module
        "Holy Grail",                # 3g: prevent first death
        "Joan of Arc's Armor",       # 4g: -1 to every energy loss
        "Prince Dracula's Chalice",  # 3g: +2 energy when paradox lands
    }

    # Tier B: market efficiency / access.
    _MARKET_CARDS = {
        "Porcelain",             # 1g: market cards cost 1 less
        "Harald's Bluetooth",    # 1g: atemporal market access
        "The First Smartphone",  # 1g: atemporal market access
        "Vending Machine",       # 2g: +1 gold when others buy
        "Quantum Computer",      # 2g: inherits receptor passives
        "Window of Time",        # 4g: always synchronic
    }

    # Tier C: travel and positioning.
    _TRAVEL_CARDS = {
        "Gerardus Mercator's Map",    # 2g: free 3-step active move
        "Navigation Compass",         # 3g: passive bonus travel
        "Horse Collar",               # (also in Tier A)
        "Galileo's Telescope",        # 3g: reduces past-travel cost
        "da Vinci's Flying Machine",  # 4g: large item, reduces travel cost
    }

    @property
    def name(self) -> str:
        return "Smart"

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
        counts = count_faces(dice)
        remaining = list(dice)
        direction = -1

        # Park to deliver next Phase 1.
        if self._should_stay_to_deliver(traveler):
            return self._park_alloc(dice, unavailable), -1, 0

        # Park to shop at the Merchant.
        if (game.merchant_century == traveler.century
                and not traveler.equipment_full()
                and traveler.gold >= 1):
            return self._park_alloc(dice, unavailable), -1, 0

        # Precision-land on a delivery century.
        delivery_result = self._try_target_delivery(traveler, dice, unavailable)
        if delivery_result is not None:
            alloc, cap = delivery_result
            energy_cap = safe_travel_cap(traveler, reserve=2)
            return alloc, -1, min(cap, energy_cap)

        # Near delivery but Travel overloaded: park to avoid overshoot.
        if (FUNCTION_TRAVEL in unavailable and
                any(1 <= traveler.century - c.delivery_century <= 6
                    for c in traveler.hand
                    if c.delivery_century is not None)):
            return self._park_alloc(dice, unavailable), -1, 0

        # Wait for Merchant before descending past Singularity zone.
        if self._should_wait_for_merchant(traveler, game):
            return self._park_alloc(dice, unavailable), -1, 0

        # --- Decide allocation mode ---
        kill_threat = paradox_can_terminate(traveler, game, dice)

        if traveler.energy < self.ENERGY_CRITICAL:
            remaining = self._recharge_first(alloc, counts, remaining, unavailable)
        elif traveler.booms >= self.BOOM_DANGER:
            remaining = self._avoid_heating(alloc, counts, remaining, unavailable, kill_threat)
        elif self._should_overload_travel(traveler, counts, unavailable):
            remaining = self._aggressive_travel(alloc, counts, remaining, unavailable)
        else:
            remaining = self._balanced(alloc, counts, remaining, unavailable, kill_threat)

        for val in remaining:
            if unavailable:
                send_to_escape_valve(alloc, val)

        energy_cap = safe_travel_cap(traveler, reserve=2) if direction == -1 else None
        return alloc, direction, energy_cap

    def _park_alloc(self, dice: list[int], unavailable: set[int]) -> Allocation:
        """No travel: Recharge(2), then Paradox(1), then the escape valve."""
        alloc = Allocation.empty()
        remaining = list(dice)
        remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2)
        remaining = self._fill_max(alloc, FUNCTION_PARADOX,  remaining, unavailable, max_slots=1)
        for val in remaining:
            if unavailable:
                send_to_escape_valve(alloc, val)
        return alloc

    # ------------------------------------------------------------------
    # Allocation modes
    # ------------------------------------------------------------------

    def _recharge_first(self, alloc, counts, remaining, unavailable):
        """Energy critical: Recharge(2), then Travel(2), then Paradox(1).
        Recharge before Travel to stabilise; Paradox last with leftovers."""
        remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2)
        remaining = self._fill_max(alloc, FUNCTION_TRAVEL,   remaining, unavailable, max_slots=2)
        remaining = self._fill_max(alloc, FUNCTION_PARADOX,  remaining, unavailable, max_slots=1)
        return remaining

    def _avoid_heating(self, alloc, counts, remaining, unavailable, kill_threat: bool):
        """Booms dangerously high: skip Travel (heating module is col 0).
        Recharge(2), then Paradox (2 for a kill, else 1)."""
        remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2)
        paradox_slots = 2 if kill_threat else 1
        remaining = self._fill_max(alloc, FUNCTION_PARADOX, remaining, unavailable,
                                   max_slots=paradox_slots)
        return remaining

    def _aggressive_travel(self, alloc, counts, remaining, unavailable):
        """Overload Travel with dominant die; Recharge fills the rest."""
        travel_val = self._dominant_value(counts)
        n = counts[travel_val]
        for col in range(min(n, 3)):
            place(alloc, FUNCTION_TRAVEL, col, travel_val)
            remaining.remove(travel_val)
        return self._fill_rest(alloc, remaining, unavailable, skip={FUNCTION_TRAVEL})

    def _balanced(self, alloc, counts, remaining, unavailable, kill_threat: bool):
        """Default: Travel(2), then Recharge(2), then Paradox(1 or 2).

        Travel fills first so the dominant die value goes into movement, not into
        Recharge. Recharge then gets the next-best value. Paradox gets 2 cols when
        a kill shot is available, otherwise 1 col for mild pressure. This order
        satisfies 'Energy/Gold over Paradox': Recharge beats Paradox in the
        remaining-dice priority, while keeping steady board advancement.
        """
        if kill_threat:
            # Sacrifice some movement for a kill: Recharge first, then Paradox(2), Travel last.
            remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2)
            remaining = self._fill_max(alloc, FUNCTION_PARADOX,  remaining, unavailable, max_slots=2)
            remaining = self._fill_max(alloc, FUNCTION_TRAVEL,   remaining, unavailable, max_slots=2)
        else:
            remaining = self._fill_max(alloc, FUNCTION_TRAVEL,   remaining, unavailable, max_slots=2)
            remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2)
            remaining = self._fill_max(alloc, FUNCTION_PARADOX,  remaining, unavailable, max_slots=1)
        return remaining

    def _fill_rest(self, alloc, remaining, unavailable, skip=frozenset()):
        for fn in (FUNCTION_RECHARGE, FUNCTION_PARADOX, FUNCTION_TRAVEL):
            if fn in unavailable or fn in skip:
                continue
            remaining = self._fill_max(alloc, fn, remaining, unavailable, max_slots=2)
        return remaining

    # ------------------------------------------------------------------
    # Mode guards
    # ------------------------------------------------------------------

    def _should_overload_travel(self, traveler, counts, unavailable) -> bool:
        if FUNCTION_TRAVEL in unavailable:
            return False
        if traveler.energy < self.ENERGY_CRITICAL:
            return False
        dominant_val = self._dominant_value(counts)
        if dominant_val < self.OVERLOAD_MIN_VALUE:
            return False
        if counts[dominant_val] < 3:
            return False
        if traveler.booms + dominant_val >= BOOM_LIMIT:
            return False
        return True

    def _should_stay_to_deliver(self, traveler: TravelerState) -> bool:
        return any(c.delivery_century == traveler.century for c in traveler.hand
                   if c.delivery_century is not None)

    def _should_wait_for_merchant(self, traveler: TravelerState, game: GameState) -> bool:
        if traveler.century <= 19:
            return False
        if "Singularity" in traveler.delivered_periods:
            return False
        sing_held = any(
            "Singularity" in periods_for_century(c.delivery_century)
            for c in traveler.hand
            if c.delivery_century is not None
        )
        if sing_held or len(traveler.hand) >= traveler.equipment_capacity:
            return False
        dist = traveler.century - game.merchant_century
        return 0 < dist <= 6

    # ------------------------------------------------------------------
    # Delivery targeting (close to the Collector's version, with its own thresholds)
    # ------------------------------------------------------------------

    def _try_target_delivery(self, traveler, dice, unavailable):
        if FUNCTION_TRAVEL in unavailable:
            return None
        if traveler.energy < self.ENERGY_CRITICAL:
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

    def _travel_alloc(self, dice, travel_val, cols, unavailable):
        alloc = Allocation.empty()
        remaining = list(dice)
        for col in range(cols):
            place(alloc, FUNCTION_TRAVEL, col, travel_val)
            remaining.remove(travel_val)
        remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2)
        remaining = self._fill_max(alloc, FUNCTION_PARADOX,  remaining, unavailable, max_slots=1)
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

        # Clear the Wanted poster for Secret Market access.
        if (traveler.is_wanted
                and renew_cost != SECRET_MARKET_RENEW_COST
                and game.secret_market_open
                and traveler.gold >= DECLARE_COST):
            return DeclareAction()

        # 1. Cheap market-efficiency cards (unlock better buying).
        for card in sorted(revealed, key=lambda c: c.gold_cost):
            if card.name not in self._MARKET_CARDS or card.name in held_names:
                continue
            if card.gold_cost > 2:
                break  # only grab the cheap ones opportunistically
            if traveler.gold >= card.gold_cost and traveler.can_hold(card):
                return BuyAction(card)

        # 2. Delivery cards for still-missing periods.
        held_delivery: set[str] = set()
        for c in traveler.hand:
            if c.delivery_century is not None and c.delivery_century <= traveler.century:
                held_delivery.update(periods_for_century(c.delivery_century))

        if missing_periods:
            candidates = []
            for card in revealed:
                if card.delivery_century is None or card.delivery_century > traveler.century:
                    continue
                card_periods = set(periods_for_century(card.delivery_century))
                new_p = card_periods & missing_periods
                if not new_p or card_periods <= held_delivery:
                    continue
                if card.name in held_names:
                    continue
                if traveler.gold < card.gold_cost or not traveler.can_hold(card):
                    continue
                steps = traveler.century - card.delivery_century
                candidates.append((-len(new_p), steps, card.gold_cost, TIEBREAK_RANK[card.name], card))
            candidates.sort(key=lambda x: x[:4])
            if candidates:
                return BuyAction(candidates[0][4])

        # 3. Energy/survival cards when energy is below safety threshold.
        if traveler.energy < self.ENERGY_SAFE:
            for card in sorted(revealed, key=lambda c: c.gold_cost):
                if card.name not in self._ENERGY_CARDS or card.name in held_names:
                    continue
                if traveler.gold >= card.gold_cost and traveler.can_hold(card):
                    return BuyAction(card)

        # 4. Travel cards when in good shape and still far from Year Zero.
        if traveler.energy >= self.ENERGY_SAFE and traveler.century > 8:
            for card in sorted(revealed, key=lambda c: c.gold_cost):
                if card.name not in self._TRAVEL_CARDS or card.name in held_names:
                    continue
                if traveler.gold >= card.gold_cost and traveler.can_hold(card):
                    return BuyAction(card)

        # 5. Remaining energy cards and market cards at any energy level.
        for wishlist in (self._ENERGY_CARDS, self._MARKET_CARDS):
            for card in sorted(revealed, key=lambda c: c.gold_cost):
                if card.name not in wishlist or card.name in held_names:
                    continue
                if traveler.gold >= card.gold_cost and traveler.can_hold(card):
                    return BuyAction(card)

        # 6. Renew up to cost 2 to cycle the market.
        if renew_cost <= 2 and traveler.gold >= renew_cost and revealed:
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
        sync_enemies = [t for t in game.travelers
                        if t is not traveler and not t.awaiting_respawn
                        and t.century == traveler.century]
        era_enemies  = [t for t in game.travelers
                        if t is not traveler and not t.awaiting_respawn
                        and same_era(traveler.century, t.century)]
        weakest_sync = min(sync_enemies, key=lambda t: t.energy, default=None)
        weakest_era  = min(era_enemies,  key=lambda t: t.energy, default=None)
        use_weapons  = traveler.energy >= self.ENERGY_SAFE

        for card in list(traveler.hand):
            name = card.name
            ctx: object = None

            if name == "Gerardus Mercator's Map":
                overshot = [c for c in traveler.hand
                            if c.delivery_century is not None
                            and 1 <= c.delivery_century - traveler.century <= 3]
                if overshot:
                    nearest = min(overshot, key=lambda c: c.delivery_century - traveler.century)
                    ctx = (nearest.delivery_century - traveler.century, 1)
                elif traveler.century > 3:
                    ctx = (3, -1)
                else:
                    continue

            elif name == "Astrolabe":
                ctx = astrolabe_destination(traveler)
                if ctx is None:
                    continue

            elif name == "The First Time Machine":
                if traveler.century >= 20 and traveler.gold <= 1:
                    ctx = None
                else:
                    continue

            elif name == "Queen Anne's Revenge Cannon":
                if not use_weapons or not era_enemies:
                    continue
                ctx = None

            elif name in ("Laser Gun", "Fire Lance"):
                if not use_weapons or weakest_sync is None:
                    continue
                ctx = weakest_sync

            elif name == "Ching Shih's Red Flag":
                if not use_weapons or not era_enemies:
                    continue
                ctx = max(era_enemies, key=lambda t: t.gold, default=weakest_era)

            elif name == "Ferguson Rifle":
                if not use_weapons or weakest_era is None:
                    continue
                ctx = weakest_era

            elif name == "Gunpowder Revolver":
                if not use_weapons or not sync_enemies:
                    continue
                ctx = max(sync_enemies, key=lambda t: len(t.hand))

            elif name == "Excalibur":
                if not use_weapons:
                    continue
                future = [t for t in game.travelers
                          if not t.awaiting_respawn and t.century > traveler.century]
                if not future:
                    continue
                ctx = None

            elif name == "Charlemagne's Sword":
                if not use_weapons or weakest_sync is None or traveler.gold == 0:
                    continue
                ctx = weakest_sync

            elif name == "Attila's Sword":
                if not use_weapons:
                    continue
                if not any(not t.awaiting_respawn and t is not traveler
                           and in_older_era(t.century, traveler.century)
                           for t in game.travelers):
                    continue
                ctx = None

            elif name == "Portal Gun":
                if not use_weapons or weakest_era is None:
                    continue
                ctx = weakest_era

            elif name == "Refrigerator":
                ctx = refrigerator_choice(traveler, game)
                if ctx is None:
                    continue

            else:
                continue

            activations.append((card, ctx))

        return activations

    # ------------------------------------------------------------------
    # Reward sub-choices
    # ------------------------------------------------------------------

    def choose_resource_steal_target(self, traveler, game, revealed):
        affordable = [c for c in revealed if traveler.can_hold(c)]
        return max(affordable, key=lambda c: c.gold_cost) if affordable else None

    def choose_matrix_buff_module(self, traveler, game):
        """Buff Recharge modules first, then Travel."""
        already = set(traveler.matrix_buffs.keys())
        for m in [0, 1, 2, 7, 8, 6, 3, 4, 5]:
            if m not in already:
                return m
        return next((m for m in range(9) if m not in already), 0)

    def choose_chaos_merchant_century(self, traveler, game):
        """Move Merchant to our century for free market access."""
        return traveler.century

    def choose_reward_category(self, traveler, game, available):
        """Time (vouchers, market), then Resource (gold, matrix), then Chaos."""
        for preferred in ("Time", "Resource", "Chaos"):
            if preferred in available:
                return preferred
        return available[0]

    def choose_cards_to_deliver(self, traveler, game, deliverable):
        to_keep: set[str] = set()
        for card in deliverable:
            if card.name == "Seismograph" and traveler.booms >= 6:
                to_keep.add(card.name)
            elif card.name == "Viking Shield":
                same_era = any(
                    t for t in game.travelers
                    if t is not traveler and not t.awaiting_respawn
                    and abs(t.century - traveler.century) <= 5
                )
                if same_era:
                    to_keep.add(card.name)
            elif card.name == "Towel":
                to_keep.add(card.name)
            elif card.name == "Holy Grail" and traveler.energy <= 6:
                to_keep.add(card.name)
        return [c for c in deliverable if c.name not in to_keep]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fill_max(self, alloc, fn, remaining, unavailable, max_slots=3):
        if fn in unavailable or not remaining:
            return remaining
        existing = alloc.generators_in_function(fn)
        req_val    = existing[0] if existing else None
        slots_used = len(existing)
        val = self._pick_value(remaining, req_val)
        if val is None:
            return remaining
        to_place = min(remaining.count(val), max_slots - slots_used)
        for _ in range(to_place):
            place(alloc, fn, slots_used, val)
            slots_used += 1
            remaining.remove(val)
        return remaining

    def _dominant_value(self, counts):
        return max(counts.items(), key=lambda x: (x[1], x[0]))[0]

    def _pick_value(self, remaining, required):
        if not remaining:
            return None
        if required is not None:
            return required if required in remaining else None
        return max(set(remaining), key=lambda v: (remaining.count(v), v))

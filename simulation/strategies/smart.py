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
  • DeclareAction to clear Wanted before entering the Secret Market loop.
  • Wide card wishlist: delivery periods, energy/survival, market access, travel,
    Cálice do Príncipe Drácula (energy on paradox hits), Computador Quântico,
    Armadura da Joana d'Arc.
  • Renew up to cost 2 when the revealed set has nothing useful.
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
from engine.cards import Card
from engine.market import MarketAction, BuyAction, RenewAction, DeclareAction, PassAction
from engine.timeline import periods_for_century
from simulation.strategies.base import Strategy
from simulation.strategies.util import paradox_can_terminate, safe_travel_cap


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

    # ---------------------------------------------------------------------------
    # Card wish-lists
    # ---------------------------------------------------------------------------

    # Tier A: cheap, high-value survivability.
    _ENERGY_CARDS = {
        "Toalha",                               # 1g: no overload
        "Autômato de Ismail Al-Jazari",         # 1g: -5 booms on overload
        "Motor de Corrente Alternada de Tesla", # 1g: +1 energy on boom gain
        "Escudo Viking",                        # 2g: first energy loss -2/hour
        "Sismográfico",                         # 2g: -2 booms on gain
        "Super Motor",                          # 2g: prevent first explosion
        "Colar de Cavalo",                      # 3g: +1 energy per travel module
        "Santo Graal",                          # 3g: prevent first death
        "Armadura da Joana d'Arc",              # 4g: -1 to every energy loss
        "Cálice do Príncipe Drácula",           # 3g: +2 energy when paradox lands
    }

    # Tier B: market efficiency / access.
    _MARKET_CARDS = {
        "Porcelana",                            # 1g: market cards cost 1 less
        "Dente Azul do Harald",                 # 1g: atemporal market access
        "Primeiro Smartphone",                  # 1g: atemporal market access
        "Máquina de Venda Automática",          # 2g: +1 gold when others buy
        "Computador Quântico",                  # 2g: inherits receptor passives
        "Janela do Tempo",                      # 4g: always synchronic
    }

    # Tier C: travel and positioning.
    _TRAVEL_CARDS = {
        "Mapa de Geradus Mercator",              # 2g: free 3-step active move
        "Bússola de Navegação",                  # 3g: passive bonus travel
        "Colar de Cavalo",                       # (also in Tier A)
        "Telescópio de Galileu Galilei",         # 3g: reduces past-travel cost
        "Máquina Voadora da da Vinci",           # 4g: large item, reduces travel cost
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
        """No travel: Recharge(2) → Paradox(1) → escape valve."""
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
        """Energy critical: Recharge(2) → Travel(2) → Paradox(1).
        Recharge before Travel to stabilise; Paradox last with leftovers."""
        remaining = self._fill_max(alloc, FUNCTION_RECHARGE, remaining, unavailable, max_slots=2)
        remaining = self._fill_max(alloc, FUNCTION_TRAVEL,   remaining, unavailable, max_slots=2)
        remaining = self._fill_max(alloc, FUNCTION_PARADOX,  remaining, unavailable, max_slots=1)
        return remaining

    def _avoid_heating(self, alloc, counts, remaining, unavailable, kill_threat: bool):
        """Booms dangerously high: skip Travel (heating module is col 0).
        Recharge(2) → Paradox(2 if kill, else 1)."""
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
        """Default: Travel(2) → Recharge(2) → Paradox(1 or 2).

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
    # Delivery targeting (shared with Collector)
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

        # Clear Wanted poster for Secret Market access.
        if (traveler.is_wanted
                and renew_cost != 999
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
                candidates.append((-len(new_p), steps, card.gold_cost, card.name, card))
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
                        and self._same_era(traveler.century, t.century)]
        weakest_sync = min(sync_enemies, key=lambda t: t.energy, default=None)
        weakest_era  = min(era_enemies,  key=lambda t: t.energy, default=None)
        use_weapons  = traveler.energy >= self.ENERGY_SAFE

        for card in list(traveler.hand):
            name = card.name
            ctx: object = None

            if name == "Mapa de Geradus Mercator":
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

            elif name == "Primeira Maquina do Tempo":
                if traveler.century >= 20 and traveler.gold <= 1:
                    ctx = None
                else:
                    continue

            elif name == "Canhão de Vingança da Rainha Anne":
                if not use_weapons or not era_enemies:
                    continue
                ctx = None

            elif name in ("Arma de Laser", "Lança de Fogo"):
                if not use_weapons or weakest_sync is None:
                    continue
                ctx = weakest_sync

            elif name == "Bandeira Vermelha da Ching Shih":
                if not use_weapons or not era_enemies:
                    continue
                ctx = max(era_enemies, key=lambda t: t.gold, default=weakest_era)

            elif name == "Rifle Fergunson":
                if not use_weapons or weakest_era is None:
                    continue
                ctx = weakest_era

            elif name == "Revolver de Pólvora":
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

            elif name == "Espada do Carlos Magno":
                if not use_weapons or weakest_sync is None or traveler.gold == 0:
                    continue
                ctx = weakest_sync

            elif name == "A Espada de Átila":
                if not use_weapons:
                    continue
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
                if not use_weapons or weakest_era is None:
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
        """Time (voucher/market) → Resource (gold/matrix) → Chaos."""
        for preferred in ("Time", "Resource", "Chaos"):
            if preferred in available:
                return preferred
        return available[0]

    def choose_cards_to_deliver(self, traveler, game, deliverable):
        to_keep: set[str] = set()
        for card in deliverable:
            if card.name == "Sismográfico" and traveler.booms >= 6:
                to_keep.add(card.name)
            elif card.name == "Escudo Viking":
                same_era = any(
                    t for t in game.travelers
                    if t is not traveler and not t.awaiting_respawn
                    and abs(t.century - traveler.century) <= 5
                )
                if same_era:
                    to_keep.add(card.name)
            elif card.name == "Toalha":
                to_keep.add(card.name)
            elif card.name == "Santo Graal" and traveler.energy <= 6:
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

    def _same_era(self, a: int, b: int) -> bool:
        from engine.timeline import eras_for_century
        return bool(set(eras_for_century(a)) & set(eras_for_century(b)))

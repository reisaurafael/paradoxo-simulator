"""
simulation/verbose_run.py
=========================
Single-game verbose trace for Paradoxo.

Runs one game (no seed: fully random) with four traveler profiles and prints
every decision, dice roll, market state, resource change, card event, CP reward,
and category choice in a structured, hour-by-hour format.

Usage:
    python -m simulation.verbose_run
    python simulation/verbose_run.py
"""

from __future__ import annotations
import copy
import random
import sys
import io
from dataclasses import dataclass
from typing import Any

from engine.state import GameState, Allocation, GameResult, HourSnapshot, ItemEvent
from engine.dice import roll_generators
from engine.resolve import resolve_hour, advance_overload
from engine.timeline import reached_year_zero
from engine.matrix import validate_allocation
from engine.resolve import priority_order
from engine.constants import (
    CP_YEAR_ZERO_TOTAL,
    CP_SURVIVAL_BONUS,
    CP_STABILISATION_BONUS,
)
from engine.rewards import process_pending_rewards
from engine.market import (
    MerchantDeck,
    resolve_market_phase,
    resolve_deliveries,
    check_merchant_upgrades,
    check_temporal_receptor_win,
    BuyAction, RenewAction, DeclareAction, UseCardAction, PassAction,
)
from engine import combat
from simulation.strategies.base import Strategy
from simulation.runner import (
    resolve_activation_phase,
    _resolve_free_recycles,
    _resolve_solo_phases,
    _check_win_conditions,
    _determine_cp_winner,
    _award_survival_bonus,
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

DIVIDER   = "=" * 72
SUBDIV    = "-" * 72
THIN      = "·" * 72

_ROMAN = {1: "I", 2: "II", 3: "III"}
_MODULE_LABEL = {
    (0, 0): "Rchrg-Future", (0, 1): "Rchrg-Present", (0, 2): "Rchrg-Past",
    (1, 0): "Prdx-Future",  (1, 1): "Prdx-Present",  (1, 2): "Prdx-Past",
    (2, 0): "Trvl-Future",  (2, 1): "Trvl-Present",  (2, 2): "Trvl-Past",
}
_FUNC = {0: "Recharge", 1: "Paradox", 2: "Travel"}
_COL  = {0: "Future(mod4)", 1: "Present(mod5)", 2: "Past(mod6)"}
_ROW  = {0: "Recharge", 1: "Paradox", 2: "Travel"}


def _century_str(c: int) -> str:
    if c == 0:
        return "Yr0"
    if c == 10:
        return " X "
    if c == 20:
        return " XX"
    if c == 30:
        return "XXX"
    return f" {c:2}"


def _hand_str(hand) -> str:
    if not hand:
        return "(empty)"
    return ", ".join(c.name for c in hand)


def _overload_str(overloaded: set) -> str:
    if not overloaded:
        return "none"
    return "+".join(_FUNC[f] for f in sorted(overloaded))


def _alloc_str(alloc: Allocation, overloaded: set | None = None) -> str:
    """Compact single-line allocation: Rc[F·P·Pa] Px[F·P·Pa] Tr[F·P·Pa] EV=x"""
    parts = []
    short = ["Rc", "Px", "Tr"]
    for r in range(3):
        cells = "".join(str(alloc.get(r, c)) if alloc.get(r, c) else "·" for c in range(3))
        ovl = "*" if (overloaded and r in overloaded) else ""
        parts.append(f"{short[r]}{ovl}[{cells}]")
    ev = alloc.escape_valve
    parts.append(f"EV={ev if ev else '·'}")
    return "    " + " ".join(parts)


def _snap(game: GameState) -> dict:
    """Snapshot traveler resources for before/after diffs."""
    return {
        t.name: {
            "energy": t.energy,
            "gold": t.gold,
            "booms": t.booms,
            "century": t.century,
            "cp": t.contract_points,
            "exploded": t.exploded_this_hour,
            "hand": [c.name for c in t.hand],
            "terminated": t.is_terminated,
            "wanted": t.is_wanted,
            "vouchers_market": t.market_voucher,
            "vouchers_item": t.item_voucher,
            "matrix_buffs": dict(t.matrix_buffs),
            "receptor": list(t.temporal_receptor),
        }
        for t in game.travelers
    }


def _diff_snap(before: dict, after: dict, label: str = "") -> None:
    """Print only travelers with actual changes; skip entirely if nothing changed."""
    any_change = False
    for name in before:
        b, a = before[name], after[name]
        parts = []
        for key in ("energy", "gold", "booms", "century", "cp"):
            bv, av = b[key], a[key]
            if bv != av:
                parts.append(f"{key}:{bv}→{av}({av-bv:+d})")
        if not b.get("exploded") and a.get("exploded"):
            parts.append("EXPLODED(motor:-2 NRG, booms reset, no travel)")
        if b["terminated"] != a["terminated"] and a["terminated"]:
            parts.append("TERMINATED")
        if b["wanted"] != a["wanted"]:
            parts.append(f"wanted:{b['wanted']}→{a['wanted']}")
        if b["vouchers_market"] != a["vouchers_market"]:
            parts.append(f"mktV:{b['vouchers_market']}→{a['vouchers_market']}")
        if b["vouchers_item"] != a["vouchers_item"]:
            parts.append(f"itmV:{b['vouchers_item']}→{a['vouchers_item']}")
        if b["matrix_buffs"] != a["matrix_buffs"]:
            new_mods = [k for k in a["matrix_buffs"] if k not in b["matrix_buffs"]]
            parts.append(f"buff+{new_mods}")
        if b["hand"] != a["hand"]:
            added   = [x for x in a["hand"] if x not in b["hand"]]
            removed = [x for x in b["hand"] if x not in a["hand"]]
            if added:   parts.append(f"hand+[{','.join(added)}]")
            if removed: parts.append(f"hand-[{','.join(removed)}]")
        if b["receptor"] != a["receptor"]:
            added = [x for x in a["receptor"] if x not in b["receptor"]]
            parts.append(f"rcpt+[{','.join(added)}]")
        if parts:
            any_change = True
            prefix = f"    [{name}]" + (f" ({label})" if label else "")
            print(f"{prefix}  {' | '.join(parts)}")


def _print_traveler_table(game: GameState) -> None:
    """Print a compact table of all traveler states."""
    print(f"  {'Traveler':<20} {'Cen':>4} {'NRG':>5} {'GLD':>5} {'BMB':>5} "
          f"{'CP':>4} {'Term':>5} {'Wnt':>4} {'MktV':>5} {'ItmV':>5} {'Hand'}")
    print(f"  {'-'*20} {'-'*4} {'-'*5} {'-'*5} {'-'*5} {'-'*4} {'-'*5} {'-'*4} {'-'*5} {'-'*5} {'-'*40}")
    for t in game.travelers:
        status = " [RES]" if t.awaiting_respawn else ""
        term = "YES" if t.is_terminated else "no"
        wnt  = "YES" if t.is_wanted    else "no"
        print(
            f"  {t.name:<20} {_century_str(t.century)} {t.energy:>5} {t.gold:>5} {t.booms:>5} "
            f"{t.contract_points:>4} {term:>5} {wnt:>4} "
            f"{t.market_voucher:>5} {t.item_voucher:>5}  "
            f"{_hand_str(t.hand)[:50]}{status}"
        )
    print()


def _item_events_since(events: list[ItemEvent], since_count: int) -> list[ItemEvent]:
    return events[since_count:]


def _print_item_events(events: list[ItemEvent], label: str = "") -> None:
    if not events:
        return
    if label:
        print(f"  [{label} item events]")
    for e in events:
        print(f"    Hr{e.hour:>3} | {e.traveler:<20} | {e.event_type:<20} | {e.card_name} (cen {e.century})")


# ---------------------------------------------------------------------------
# Logging strategy wrapper
# ---------------------------------------------------------------------------

class LoggingStrategy(Strategy):
    """
    Wraps any Strategy and prints every decision it makes.
    The runner sees this as a normal Strategy; the user sees everything.
    """

    def __init__(self, inner: Strategy) -> None:
        self._inner = inner
        self._traveler_name: str | None = None

    @property
    def name(self) -> str:
        return self._inner.name

    def _tag(self, traveler_name: str) -> str:
        return f"  [DECISION] {traveler_name:<20} ({self._inner.name})"

    def choose_allocation(self, traveler, game, dice):
        result = self._inner.choose_allocation(traveler, game, dice)
        if len(result) == 3:
            alloc, direction, cap = result
        else:
            alloc, direction = result
            cap = None

        dir_str = "→ FUTURE (+1)" if direction > 0 else "← PAST (−1)"
        cap_str = f"  travel_cap={cap}" if cap is not None else ""
        overloaded = traveler.overloaded_functions
        print(f"{self._tag(traveler.name)}  chose allocation  direction={dir_str}{cap_str}")
        print(_alloc_str(alloc, overloaded))

        if len(result) == 3:
            return alloc, direction, cap
        return alloc, direction

    def choose_market_action(self, traveler, game, revealed, renew_cost):
        action = self._inner.choose_market_action(traveler, game, revealed, renew_cost)
        if isinstance(action, PassAction):
            print(f"{self._tag(traveler.name)}  market → PASS")
        elif isinstance(action, BuyAction):
            from engine.market import effective_card_cost
            paid = effective_card_cost(action.card, traveler)
            base = action.card.gold_cost
            cost_str = f"{paid}g" if paid == base else f"{paid}g (base {base}g)"
            print(f"{self._tag(traveler.name)}  market → BUY  '{action.card.name}' (cost {cost_str})")
        elif isinstance(action, RenewAction):
            print(f"{self._tag(traveler.name)}  market → RENEW '{action.card.name}' (cost {renew_cost}g)")
        elif isinstance(action, DeclareAction):
            print(f"{self._tag(traveler.name)}  market → DECLARE (clear Wanted, cost 2g)")
        elif isinstance(action, UseCardAction):
            print(f"{self._tag(traveler.name)}  market → USE_CARD '{action.card.name}'")
        return action

    def choose_activations(self, traveler, game):
        result = self._inner.choose_activations(traveler, game)
        if result:
            for card, ctx in result:
                print(f"{self._tag(traveler.name)}  Phase4 → ACTIVATE '{card.name}' ctx={ctx}")
        else:
            print(f"{self._tag(traveler.name)}  Phase4 → no activations")
        return result

    def choose_reward_category(self, traveler, game, available):
        choice = self._inner.choose_reward_category(traveler, game, available)
        print(f"{self._tag(traveler.name)}  reward_category → {choice}  (available: {available})")
        return choice

    def choose_items_to_recycle(self, traveler, game):
        result = self._inner.choose_items_to_recycle(traveler, game)
        if result:
            print(f"{self._tag(traveler.name)}  pre-market RECYCLE: {[c.name for c in result]}")
        return result

    def choose_cards_to_deliver(self, traveler, game, deliverable):
        result = self._inner.choose_cards_to_deliver(traveler, game, deliverable)
        held = [c.name for c in deliverable if c not in result]
        if held:
            print(f"{self._tag(traveler.name)}  delivery → HOLD BACK: {held}")
        return result

    def choose_chaos_destroy_target(self, traveler, game, candidates):
        result = self._inner.choose_chaos_destroy_target(traveler, game, candidates)
        print(f"{self._tag(traveler.name)}  Chaos-II destroy → '{result.name if result else None}'")
        return result

    def choose_chaos_merchant_century(self, traveler, game):
        result = self._inner.choose_chaos_merchant_century(traveler, game)
        print(f"{self._tag(traveler.name)}  Chaos-III merchant century → {result}")
        return result

    def choose_resource_steal_target(self, traveler, game, revealed):
        result = self._inner.choose_resource_steal_target(traveler, game, revealed)
        print(f"{self._tag(traveler.name)}  Resource-II steal → '{result.name if result else None}'")
        return result

    def choose_matrix_buff_module(self, traveler, game):
        result = self._inner.choose_matrix_buff_module(traveler, game)
        row, col = divmod(result, 3)
        print(f"{self._tag(traveler.name)}  Resource-III matrix_buff → module {result} ({_ROW[row]}, col {col})")
        return result

    # Pass-through for interface completeness
    def __repr__(self):
        return f"<LoggingStrategy wrapping {self._inner!r}>"


# ---------------------------------------------------------------------------
# Reward-aware process_pending_rewards wrapper
# ---------------------------------------------------------------------------

def _process_rewards_verbose(game: GameState, deck, strategies: dict, rng: random.Random) -> None:
    """Wrap process_pending_rewards with before/after state diffs per reward."""
    while game.cp_rewards_pending:
        name = game.cp_rewards_pending[0]
        before = _snap(game)
        process_pending_rewards.__wrapped__(game, deck, strategies, rng)  # type: ignore[attr-defined]
        after = _snap(game)
        print(f"  [REWARD] {name}")
        _diff_snap(before, after, f"reward for {name}")
        # process_pending_rewards pops one at a time inside; we just drained them all
        break
    # Drain any remaining the above missed (shouldn't happen but be safe)
    if game.cp_rewards_pending:
        process_pending_rewards(game, deck, strategies, rng)


# ---------------------------------------------------------------------------
# Verbose game simulator
# ---------------------------------------------------------------------------

def verbose_simulate_game(
    strategies: dict[str, Strategy],
    max_hours: int = 200,
) -> GameResult:
    """
    Full-trace single game. No seed: random every run.
    All decisions, dice, market states, resource changes, and rewards are printed.
    """
    rng = random.Random()   # no seed
    traveler_names = list(strategies.keys())
    game = GameState.create(traveler_names)
    deck = MerchantDeck(rng=rng)
    game.rng = rng
    game.market_revealed = deck.snapshot_revealed()
    history: list[HourSnapshot] = []

    print(DIVIDER)
    print("  PARADOXO: VERBOSE TRACE")
    print(f"  Travelers: {', '.join(f'{n} ({strategies[n].name})' for n in traveler_names)}")
    print(DIVIDER)
    print()
    print("  INITIAL STATE")
    _print_traveler_table(game)
    print(f"  Merchant: century {game.merchant_century}  |  deck {deck.draw_count} draw + {len(deck.revealed)} revealed")
    _print_merchant_stock(deck, game)
    print()

    for hour_idx in range(max_hours):

        # ---------------------------------------------------------------
        print(f"\n{DIVIDER}")
        print(f"  HOUR {game.hour}")
        print(DIVIDER)

        # --- Advance overload markers ---
        for t in game.travelers:
            advance_overload(t)

        overloaded_travelers = [t for t in game.travelers if t.overloaded_functions]
        if overloaded_travelers:
            print(f"  Overloaded: " + ", ".join(
                f"{t.name}({_overload_str(t.overloaded_functions)})"
                for t in overloaded_travelers))

        # ---------------------------------------------------------------
        print(f"\n  Ph1 DELIVERY")
        _print_deliverable_summary(game)

        snap_before = _snap(game)
        events_before = len(game.item_events)
        resolve_deliveries(game, strategies)
        snap_after = _snap(game)

        new_events = _item_events_since(game.item_events, events_before)
        if new_events:
            _print_item_events(new_events, "deliveries")

        _diff_snap(snap_before, snap_after, "delivery")

        # Temporal receptor win check
        if check_temporal_receptor_win(game):
            stabiliser = next(t for t in game.travelers if t.name == game.winner)
            stabiliser.contract_points += CP_STABILISATION_BONUS
            game.cp_rewards_pending.append(stabiliser.name)
            for s in game.travelers:
                if not s.is_terminated:
                    s.contract_points += CP_SURVIVAL_BONUS
                    game.cp_rewards_pending.append(s.name)
            _process_rewards_all_verbose(game, deck, strategies, rng)
            return _end_game(game, history, "full_receptor")

        _process_rewards_all_verbose(game, deck, strategies, rng)

        # ---------------------------------------------------------------
        snap_before = _snap(game)
        events_before = len(game.item_events)
        _resolve_free_recycles(game, deck, strategies)
        snap_after = _snap(game)
        new_events = _item_events_since(game.item_events, events_before)
        if new_events:
            print(f"\n  Pre-Ph2 FREE RECYCLES")
            _print_item_events(new_events, "free recycles")
            _diff_snap(snap_before, snap_after, "free recycles")

        # ---------------------------------------------------------------
        print(f"\n  Ph2 MARKET  (merchant @ century {game.merchant_century})")
        _print_merchant_stock(deck, game)

        snap_before = _snap(game)
        events_before = len(game.item_events)
        game_over = resolve_market_phase(game, deck, strategies, rng)
        snap_after = _snap(game)
        new_events = _item_events_since(game.item_events, events_before)
        if new_events:
            _print_item_events(new_events, "market")
        _diff_snap(snap_before, snap_after, "market")

        if game_over:
            for s in game.travelers:
                if not s.is_terminated:
                    s.contract_points += CP_SURVIVAL_BONUS
            return _end_game(game, history, game.game_over_reason)

        # ---------------------------------------------------------------
        print(f"\n  Ph3 GENERATORS")

        allocations: dict[str, Allocation] = {}
        travel_directions: dict[str, int] = {}
        travel_caps: dict[str, int | None] = {}

        active = [t for t in game.travelers if not t.awaiting_respawn]
        for t in active:
            dice = roll_generators(rng=rng)
            ovl = _overload_str(t.overloaded_functions)
            strategy = strategies[t.name]
            result = strategy.choose_allocation(t, game, dice)
            if len(result) == 3:
                alloc, direction, cap = result
            else:
                alloc, direction = result
                cap = None
            allocations[t.name] = alloc
            travel_directions[t.name] = direction
            travel_caps[t.name] = cap
            dir_s = "→FUT" if direction > 0 else "←PST"
            cap_s = f" cap={cap}" if cap is not None else ""
            ovl_s = f" ovl={ovl}" if ovl != "none" else ""
            print(f"  {t.name:<18} dice={dice}{ovl_s}  {dir_s}{cap_s}")
            print(_alloc_str(alloc))

        snap_before = _snap(game)
        snapshots = resolve_hour(game, allocations, travel_directions, travel_caps)
        history.extend(snapshots)
        snap_after = _snap(game)
        _diff_snap(snap_before, snap_after, "Ph3")

        _process_rewards_all_verbose(game, deck, strategies, rng)

        # ---------------------------------------------------------------
        snap_before = _snap(game)
        resolve_activation_phase(game, strategies)
        snap_after = _snap(game)
        if snap_before != snap_after:
            print(f"\n  Ph4 ITEM ACTIVATION")
            _diff_snap(snap_before, snap_after, "Ph4")

        # ---------------------------------------------------------------
        if game.solo_phases_pending:
            pending = list(game.solo_phases_pending)
            print(f"\n  SOLO PHASES: {pending}")
            snap_before = _snap(game)
            _resolve_solo_phases(game, deck, strategies, rng)
            snap_after = _snap(game)
            _diff_snap(snap_before, snap_after, "solo")
        else:
            _resolve_solo_phases(game, deck, strategies, rng)

        check_merchant_upgrades(game)
        if game.merchant_upgrade_xx_triggered or game.merchant_upgrade_x_triggered:
            dice_n = 3 if game.merchant_upgrade_x_triggered else 2
            print(f"  [Merchant speed upgraded: {dice_n}d3]")

        # ---------------------------------------------------------------
        result = _check_win_conditions(game)
        if result is not None:
            _process_rewards_all_verbose(game, deck, strategies, rng)
            print(f"\n  *** WIN CONDITION TRIGGERED: {result[1]} ***")
            return _end_game(game, history, result[1])

        # ---------------------------------------------------------------
        print(f"\n  END Hr{game.hour}")
        _print_traveler_table(game)

    # Hit max_hours
    print(f"\n  [MAX HOURS ({max_hours}) REACHED: GAME FORCE-ENDED]")
    return GameResult(
        winner=None,
        hours_played=max_hours,
        end_reason="max_hours_reached",
        traveler_results=list(game.travelers),
        history=history,
        strategy_names={n: s.name for n, s in strategies.items()},
        item_events=list(game.item_events),
    )


# ---------------------------------------------------------------------------
# Auxiliary print helpers
# ---------------------------------------------------------------------------

def _print_merchant_stock(deck: MerchantDeck, game: GameState) -> None:
    sm = deck.secret_market
    sm_s = f"  SM:{'OPEN['+sm.current_card.name+']' if sm.is_open and sm.current_card else 'OPEN(empty)' if sm.is_open else 'closed'}"
    print(f"  draw={deck.draw_count} revealed={len(deck.revealed)}{sm_s}")
    for i, c in enumerate(deck.revealed):
        dc = c.delivery_century or "--"
        print(f"    {i+1}. {c.name:<32} {c.gold_cost}g  rv={c.recycle_value}  dc={dc}")


def _print_deliverable_summary(game: GameState) -> None:
    any_deliverable = False
    for t in game.travelers:
        if t.awaiting_respawn:
            continue
        deliverable = [c for c in t.hand if c.delivery_century == t.century]
        if deliverable:
            any_deliverable = True
            print(f"  {t.name} at century {t.century}, deliverable: {[c.name for c in deliverable]}")
    if not any_deliverable:
        print("  (no traveler has deliverable cards this hour)")


def _process_rewards_all_verbose(
    game: GameState,
    deck: MerchantDeck,
    strategies: dict,
    rng: random.Random,
) -> None:
    if not game.cp_rewards_pending:
        return

    queue = list(game.cp_rewards_pending)
    print(f"  [REWARDS: {queue}]")
    while game.cp_rewards_pending:
        name = game.cp_rewards_pending[0]
        snap_before = _snap(game)
        process_pending_rewards(game, deck, strategies, rng)
        snap_after = _snap(game)
        _diff_snap(snap_before, snap_after, f"reward→{name}")


def _end_game(game: GameState, history: list[HourSnapshot], reason: str) -> GameResult:
    winner = _determine_cp_winner(game)
    print()
    print(DIVIDER)
    print("  GAME OVER")
    print(DIVIDER)
    print(f"  End reason : {reason}")
    print(f"  Hours played: {game.hour - 1}")
    print(f"  CP winner   : {winner}")
    print()
    print("  FINAL STANDINGS:")
    ranked = sorted(game.travelers, key=lambda t: (-t.contract_points, -t.century, -t.gold, -t.energy))
    for i, t in enumerate(ranked):
        win_mark = " ← WINNER" if t.name == winner else ""
        print(f"  {i+1}. {t.name:<20} CP={t.contract_points}  NRG={t.energy}  GLD={t.gold}  "
              f"cen={t.century}  terminated={'YES' if t.is_terminated else 'no'}  "
              f"receptor={t.temporal_receptor}{win_mark}")
    print()
    print("  FULL ITEM EVENT LOG:")
    for e in game.item_events:
        print(f"    Hr{e.hour:>3} | {e.traveler:<20} | {e.event_type:<20} | {e.card_name} (cen {e.century})")
    print()
    return GameResult(
        winner=winner,
        hours_played=game.hour - 1,
        end_reason=reason,
        traveler_results=list(game.travelers),
        history=history,
        strategy_names={n: s.name for n, s in strategies.items()},
        item_events=list(game.item_events),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_to_report(out_dir: str = "sim_output", seed: int | None = None) -> str:
    """Run one full game and export a single self-contained HTML match report.

    The report (player dashboards, the generator matrix drawn as a 3x3 grid, the
    Market as card panels, and a colour-coded event timeline) is written into
    ``out_dir`` (created if needed) with a timestamped filename so successive runs
    are kept side by side. Returns the path written.

    Passing ``--console`` on the command line instead streams the legacy text trace.
    """
    import os
    from datetime import datetime
    from simulation.report import record_game, render_html
    from simulation.strategies.aggressive import AggressiveStrategy
    from simulation.strategies.collector import CollectorStrategy
    from simulation.strategies.smart import SmartStrategy
    from simulation.strategies.conservative import ConservativeStrategy

    strategies = {
        "Traveler_A": AggressiveStrategy(),
        "Traveler_C": ConservativeStrategy(),
        "Traveler_S": SmartStrategy(),
        "Traveler_K": CollectorStrategy(),
    }

    report = record_game(strategies, random.Random(seed))
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.abspath(os.path.join(out_dir, f"match_{stamp}.html"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html(report))
    return path


if __name__ == "__main__":
    # Default: write the visual HTML match report into sim_output/.
    # Use `--console` for the legacy live text trace.
    if "--console" in sys.argv:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        from simulation.strategies.aggressive import AggressiveStrategy
        from simulation.strategies.collector import CollectorStrategy
        from simulation.strategies.smart import SmartStrategy
        from simulation.strategies.conservative import ConservativeStrategy

        raw_strategies = {
            "Traveler_A": AggressiveStrategy(),
            "Traveler_C": ConservativeStrategy(),
            "Traveler_S": SmartStrategy(),
            "Traveler_K": CollectorStrategy(),
        }
        strategies = {name: LoggingStrategy(strat) for name, strat in raw_strategies.items()}
        verbose_simulate_game(strategies)
    else:
        seed_arg = next((a for a in sys.argv[1:] if a.isdigit()), None)
        path = run_to_report(seed=int(seed_arg) if seed_arg else None)
        print(f"Match report written -> {path}")
        print("Open it in a browser. (Use --console for the old text trace.)")

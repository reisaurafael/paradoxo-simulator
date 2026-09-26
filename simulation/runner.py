"""
simulation/runner.py
====================
The game loop: plays single games or batches with any mix of strategies.

The runner owns every state change. Strategies only advise: the runner asks
them for decisions and applies the answers through the engine.

Win conditions (§31.1):
    (a) A traveler reaches Year Zero
    (b) A traveler completes their Temporal Receptor (all 3 periods)
    (c) Only one traveler is not terminated
    (d) The Merchant's stock empties

The phase helpers here are also used by simulation/report.py and
simulation/verbose_run.py, which replay the same loop while recording it.
"""

from __future__ import annotations
import random
from engine.state import GameState, Allocation, GameResult, HourSnapshot, TravelerState
from engine.dice import roll_generators
from engine.resolve import (
    resolve_hour, advance_overload, priority_order,
    apply_escape_valve, resolve_module_1, resolve_module_2, resolve_module_3,
    resolve_module_7, resolve_module_8, resolve_module_9,
    apply_overload_markers, check_termination, check_millennium_milestones,
)
from engine.paradox import resolve_paradox_pool
from engine.timeline import reached_year_zero
from engine.matrix import validate_allocation
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
)
from engine import combat
from simulation.strategies.base import Strategy


MAX_HOURS_DEFAULT = 200  # Safety limit so a stalled game still ends


# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------

def unpack_allocation(result: tuple) -> tuple[Allocation, int, int | None]:
    """Normalise a strategy's choose_allocation() answer to (allocation, direction, cap)."""
    if len(result) == 3:
        return result
    alloc, direction = result
    return alloc, direction, None


def _activation_window(traveler: TravelerState, game: GameState, strategy: Strategy) -> None:
    """One traveler's Item Activation window (§15.4, §2.3).

    A Time III item voucher, if held, is spent here and marks the window in
    game.item_voucher_active_for. The caller clears that mark afterwards.
    """
    if traveler.item_voucher > 0:
        traveler.item_voucher -= 1
        game.item_voucher_active_for = traveler.name
    else:
        game.item_voucher_active_for = None

    for card, context in strategy.choose_activations(traveler, game):
        if card not in traveler.hand:
            continue
        if card.ability_type not in ("active", "atemporal_active"):
            continue
        if card.active_effect is None:
            continue
        card.active_effect(traveler, game, context)
        if card.recycles_on_use and card in traveler.hand:
            combat.recycle_card(game, traveler, card)


def resolve_activation_phase(
    game: GameState,
    strategies: dict[str, Strategy],
) -> None:
    """
    Phase 4: Item Activation, in Priority order (§15.4, §2.3).

    Every traveler in play who did not explode this Hour may use each equipped
    Active ability once.
    """
    for traveler in priority_order([t for t in game.travelers if not t.awaiting_respawn]):
        if traveler.exploded_this_hour:
            continue  # cannot act for the rest of this Hour (§5.2)
        _activation_window(traveler, game, strategies[traveler.name])

    game.item_voucher_active_for = None


def resolve_free_recycles(
    game: GameState,
    deck,
    strategies: dict,
) -> None:
    """
    Before Phase 2, let each strategy recycle cards it no longer needs.
    Each recycled card grants energy equal to its recycle_value (§21.1).
    """
    for traveler in game.travelers:
        if traveler.awaiting_respawn:
            continue
        cards = strategies[traveler.name].choose_items_to_recycle(traveler, game)
        for card in list(cards):
            if card not in traveler.hand:
                continue
            # recycle_card also logs the "recycled" item event.
            combat.recycle_card(game, traveler, card, grant_energy=True)


def resolve_solo_phases(
    game: GameState,
    deck,
    strategies: dict,
    rng: random.Random | None,
) -> None:
    """
    Resolve the extra solo generator phases earned with the Time I reward (§26.3).

    Each pending traveler gets a full Phase 3 on their own (roll, allocate, all
    nine modules), then their own Phase 4 window, then their rewards. A solo
    phase that earns another Time I queues one more, resolved in the same loop.
    """
    rng_used = rng or random.Random()

    while game.solo_phases_pending:
        name = game.solo_phases_pending.pop(0)
        traveler = next((t for t in game.travelers if t.name == name), None)
        if traveler is None or traveler.awaiting_respawn:
            continue
        strategy = strategies.get(name)
        if strategy is None:
            continue

        # Solo Phase 3: roll, allocate, and resolve all 9 modules for this traveler.
        dice = roll_generators(rng=rng_used)
        alloc, direction, cap = unpack_allocation(strategy.choose_allocation(traveler, game, dice))

        apply_escape_valve(traveler, alloc, game)
        resolve_module_1(traveler, alloc)
        resolve_module_2(traveler, alloc)
        resolve_module_3(traveler, alloc)
        resolve_paradox_pool(game, [traveler], {name: alloc}, 0)
        resolve_paradox_pool(game, [traveler], {name: alloc}, 1)
        resolve_paradox_pool(game, [traveler], {name: alloc}, 2)
        resolve_module_7(traveler, alloc, game)
        check_termination(traveler, game)
        if not traveler.awaiting_respawn:
            century_before = traveler.century
            resolve_module_8(traveler, alloc, direction, game, cap=cap)
            m8_moved = abs(traveler.century - century_before)
            remaining_cap = (cap - m8_moved) if cap is not None else None
            resolve_module_9(traveler, alloc, direction, game, cap=remaining_cap)
            # §8.1c: milestone evaluated against the final position of this phase. (BUG-003)
            check_millennium_milestones(traveler, game)
        apply_overload_markers(traveler, alloc, game)

        # Solo Phase 4: this traveler's activation window only.
        if not traveler.awaiting_respawn and not traveler.exploded_this_hour:
            _activation_window(traveler, game, strategy)
            game.item_voucher_active_for = None

        process_pending_rewards(game, deck, strategies, rng_used)


# ---------------------------------------------------------------------------
# Scoring and game end
# ---------------------------------------------------------------------------

def determine_cp_winner(game: GameState) -> str | None:
    """
    Return the name of the traveler with the most CP (§32.4).
    Ties break by Priority: highest century, then gold, then energy.
    """
    if not game.travelers:
        return None
    winner = max(
        game.travelers,
        key=lambda t: (t.contract_points, t.century, t.gold, t.energy),
    )
    return winner.name


def award_survival_bonus(game: GameState, queue_rewards: bool = True) -> None:
    """§32.2: +1 CP at game end for each traveler that was never terminated."""
    for s in game.travelers:
        if not s.is_terminated:
            s.contract_points += CP_SURVIVAL_BONUS
            if queue_rewards:
                game.cp_rewards_pending.append(s.name)


def award_full_receptor_bonuses(game: GameState) -> None:
    """End bonuses when a traveler completes the Temporal Receptor: the
    stabilisation point for the winner (§32.3), then the survival points."""
    stabiliser = next(t for t in game.travelers if t.name == game.winner)
    stabiliser.contract_points += CP_STABILISATION_BONUS
    game.cp_rewards_pending.append(stabiliser.name)
    award_survival_bonus(game)


def check_win_conditions(game: GameState) -> tuple[str | None, str] | None:
    """
    Check the §11.1 win conditions at the end of an Hour.

    Returns (winner_name_or_None, reason_string) or None if the game continues.

    Termination is permanent (§28.1), but a terminated traveler keeps playing
    after respawning. So §11.1c counts the travelers who have *never* been
    terminated, not who is currently in play, and the game ends as soon as one
    or none of them remain.
    """
    # Anyone on the timeline can reach Year Zero, including respawned travelers.
    in_play = [t for t in game.travelers if not t.awaiting_respawn]
    never_terminated = [t for t in game.travelers if not t.is_terminated]

    # (a) A traveler reaches Year Zero (§11.1a)
    for t in in_play:
        if reached_year_zero(t.century):
            t.contract_points += CP_YEAR_ZERO_TOTAL
            for _ in range(CP_YEAR_ZERO_TOTAL):
                game.cp_rewards_pending.append(t.name)
            award_survival_bonus(game)
            return (t.name, "year_zero")

    # (c) All but one traveler carry the Terminated condition (§11.1c).
    if len(never_terminated) <= 1:
        # §11.3 / §32.3: ending the game this way is a Stabilisation. The
        # stabiliser is whoever terminated the last not-yet-terminated traveler
        # (game.last_termination_causer) and gets +1 for ending the game. Their
        # termination CP and Wanted status were already given at the kill, in
        # resolve.check_termination(). The stabiliser may be terminated too:
        # terminated travelers still act.
        survivor = never_terminated[0] if never_terminated else None
        stabiliser = None
        if game.last_termination_causer is not None:
            stabiliser = next(
                (t for t in game.travelers if t.name == game.last_termination_causer),
                None,
            )
        if stabiliser is None:
            stabiliser = survivor
        if stabiliser is not None:
            stabiliser.contract_points += CP_STABILISATION_BONUS
            game.cp_rewards_pending.append(stabiliser.name)
        # §32.2: survival point for the lone never-terminated traveler (if any).
        award_survival_bonus(game)
        reason = "last_traveler" if survivor is not None else "all_terminated"
        return (survivor.name if survivor is not None else None, reason)

    return None


def _result(
    game: GameState,
    history: list[HourSnapshot],
    strategies: dict[str, Strategy],
    winner: str | None,
    end_reason: str,
    hours_played: int,
) -> GameResult:
    return GameResult(
        winner=winner,
        hours_played=hours_played,
        end_reason=end_reason,
        traveler_results=list(game.travelers),
        history=history,
        strategy_names={n: s.name for n, s in strategies.items()},
        item_events=list(game.item_events),
    )


# ---------------------------------------------------------------------------
# One game
# ---------------------------------------------------------------------------

def simulate_game(
    strategies: dict[str, Strategy],
    rng: random.Random | None = None,
    max_hours: int = MAX_HOURS_DEFAULT,
    validate: bool = False,
) -> GameResult:
    """
    Simulate one complete game.

    Args:
        strategies: traveler_name → Strategy. The names become the travelers'
                    names, and the order is the seating order.
        rng:        Seeded Random for a reproducible game, or None.
        max_hours:  Hard limit on Hours, so a stalled game still ends.
        validate:   Check every allocation against §10-11 before resolving it.
                    Useful while writing a strategy; slower in large batches.

    Returns:
        A GameResult with the winner, the number of Hours, the end reason and
        the full per-Hour history.
    """
    traveler_names = list(strategies.keys())
    game = GameState.create(traveler_names)
    deck = MerchantDeck(rng=rng)
    # Card effects that roll their own generator share the game's rng, and
    # passives that read the Merchant's stock (Movable-Type Press) need a
    # first snapshot of it.
    game.rng = rng or random.Random()
    game.market_revealed = deck.snapshot_revealed()
    history: list[HourSnapshot] = []

    def finish(winner: str | None, reason: str) -> GameResult:
        return _result(game, history, strategies, winner, reason, game.hour - 1)

    for _ in range(max_hours):
        # --- Start of Hour: respawns and overload markers (§11.1) ---
        for t in game.travelers:
            advance_overload(t)

        # --- Phase 1: Delivery ---
        resolve_deliveries(game, strategies)
        if check_temporal_receptor_win(game):
            award_full_receptor_bonuses(game)
            process_pending_rewards(game, deck, strategies, rng or random.Random())
            return finish(determine_cp_winner(game), game.game_over_reason)

        # Rewards earned by deliveries resolve before Phase 2.
        process_pending_rewards(game, deck, strategies, rng or random.Random())

        # --- Before Phase 2: free recycles (unreachable cards into energy) ---
        resolve_free_recycles(game, deck, strategies)

        # --- Phase 2: Market ---
        if resolve_market_phase(game, deck, strategies, rng or random.Random()):
            # The Merchant ran out of cards: the game ends on the spot, so the
            # survival points here trigger no Reward.
            award_survival_bonus(game, queue_rewards=False)
            return finish(determine_cp_winner(game), game.game_over_reason)

        # A Reward is queued the instant its CP is earned but only runs once
        # the current phase is over (§26), so drain the Market's before Phase 3.
        process_pending_rewards(game, deck, strategies, rng or random.Random())

        # --- Phase 3: Generators, roll and allocate ---
        allocations: dict[str, Allocation] = {}
        travel_directions: dict[str, int] = {}
        travel_caps: dict[str, int | None] = {}

        for t in [t for t in game.travelers if not t.awaiting_respawn]:
            dice = roll_generators(rng=rng)
            strategy = strategies[t.name]
            alloc, direction, cap = unpack_allocation(strategy.choose_allocation(t, game, dice))

            if validate:
                errors = validate_allocation(alloc, dice, t.overloaded_functions)
                if errors:
                    raise ValueError(
                        f"Strategy '{strategy.name}' produced invalid allocation "
                        f"for '{t.name}': {errors}"
                    )

            allocations[t.name] = alloc
            travel_directions[t.name] = direction
            travel_caps[t.name] = cap

        # --- Resolve all 9 modules (§12) ---
        history.extend(resolve_hour(game, allocations, travel_directions, travel_caps))

        # Rewards from Phase 3 (terminations, millennium milestones).
        process_pending_rewards(game, deck, strategies, rng or random.Random())

        # --- Phase 4: Item Activation (§15.4) ---
        resolve_activation_phase(game, strategies)

        # Rewards earned during Item Activation (a delivery through the
        # Refrigerator, say) resolve before the Time I solo phases (§26).
        process_pending_rewards(game, deck, strategies, rng or random.Random())

        # --- Solo generator phases from Time I rewards (§26.3) ---
        resolve_solo_phases(game, deck, strategies, rng)

        # --- Merchant speed upgrades (§17.6) ---
        check_merchant_upgrades(game)

        # --- Win conditions at the end of the Hour ---
        outcome = check_win_conditions(game)
        if outcome is not None:
            process_pending_rewards(game, deck, strategies, rng or random.Random())
            return finish(determine_cp_winner(game), outcome[1])

    return _result(game, history, strategies, None, "max_hours_reached", max_hours)


# ---------------------------------------------------------------------------
# Batch simulation
# ---------------------------------------------------------------------------

def simulate_n_games(
    strategies: dict[str, Strategy],
    n: int,
    seed: int | None = None,
    max_hours: int = MAX_HOURS_DEFAULT,
    validate: bool = False,
) -> list[GameResult]:
    """
    Run n games with the same lineup.

    Args:
        strategies: traveler_name → Strategy, reused for every game.
        n:          Number of games to simulate.
        seed:       Master seed. Each game gets its own seed drawn from it, so
                    a batch is fully reproducible.
        max_hours:  Per-game Hour limit.
        validate:   Validate allocations (slow; for strategy development).
    """
    results: list[GameResult] = []
    master_rng = random.Random(seed)

    for _ in range(n):
        game_seed = master_rng.randint(0, 2**32 - 1) if seed is not None else None
        game_rng = random.Random(game_seed) if game_seed is not None else None
        results.append(simulate_game(
            strategies=strategies,
            rng=game_rng,
            max_hours=max_hours,
            validate=validate,
        ))

    return results


# ---------------------------------------------------------------------------
# Quick smoke test: 200 games and a compact summary.
# For the interactive menu with charts, run python -m simulation.sim_menu
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    from simulation.strategies.aggressive import AggressiveStrategy
    from simulation.strategies.conservative import ConservativeStrategy
    from simulation.strategies.smart import SmartStrategy
    from simulation.strategies.collector import CollectorStrategy
    from simulation.metrics import summarise, print_summary

    lineup = {
        "Traveler_A": AggressiveStrategy(),
        "Traveler_C": ConservativeStrategy(),
        "Traveler_S": SmartStrategy(),
        "Traveler_K": CollectorStrategy(),
    }

    n = 200
    print(f"Running {n} games (Aggressive, Conservative, Smart, Collector)...")
    print_summary(summarise(simulate_n_games(lineup, n=n, seed=42)))

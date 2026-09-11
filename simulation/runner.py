"""
simulation/runner.py
====================
Core simulation loop for Paradoxo.

Runs single games or batches of games with any combination of strategy
agents. The runner owns all state mutation; strategies are read-only
advisors called once per traveler per Hour.

Win conditions (§31.1):
    (a) A traveler reaches Year Zero
    (b) A traveler completes their Temporal Receptor (all 3 periods)
    (c) Only one traveler is not terminated
    (d) The Merchant's stock empties
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field
from engine.state import GameState, Allocation, GameResult, HourSnapshot
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
)
from engine import combat
from simulation.strategies.base import Strategy


# ---------------------------------------------------------------------------
# Phase 4: Item Activation (§15.4)
# ---------------------------------------------------------------------------

def resolve_activation_phase(
    game: GameState,
    strategies: dict[str, Strategy],
) -> None:
    """
    Run Phase 4: Item Activation, in Priority order (§15.4, §2.3).

    Each non-terminated, non-exploded traveler may activate up to one Active
    ability per equipped object. If the traveler holds an item_voucher (Time III
    reward), it is consumed for their window and game.item_voucher_active_for is
    set so card effects know to treat all other travelers as synchronic.
    """
    for traveler in priority_order([t for t in game.travelers if not t.awaiting_respawn]):
        if traveler.exploded_this_hour:
            continue  # cannot act for the rest of this Hour (§5.2)

        # Time III: consume one item voucher for this activation window.
        if traveler.item_voucher > 0:
            traveler.item_voucher -= 1
            game.item_voucher_active_for = traveler.name
        else:
            game.item_voucher_active_for = None

        activations = strategies[traveler.name].choose_activations(traveler, game)
        for card, context in activations:
            if card not in traveler.hand:
                continue
            if card.ability_type not in ("active", "atemporal_active"):
                continue
            if card.active_effect is None:
                continue
            card.active_effect(traveler, game, context)
            if card.recycles_on_use and card in traveler.hand:
                combat.recycle_card(game, traveler, card)

    game.item_voucher_active_for = None  # clear after all windows close


# ---------------------------------------------------------------------------
# Game simulation
# ---------------------------------------------------------------------------

MAX_HOURS_DEFAULT = 200  # Safety limit to prevent infinite loops


def simulate_game(
    strategies: dict[str, Strategy],
    rng: random.Random | None = None,
    max_hours: int = MAX_HOURS_DEFAULT,
    validate: bool = False,
) -> GameResult:
    """
    Simulate one complete game of Paradoxo.

    Args:
        strategies: Mapping of traveler_name → Strategy instance.
                    The names become the traveler names in game state.
        rng:        Optional seeded Random for reproducibility.
        max_hours:  Hard limit on Hours to prevent runaway games.
        validate:   If True, validate each allocation against §10-11
                    before resolving. Useful during strategy development;
                    adds overhead in large batch runs.

    Returns:
        A GameResult with the winner, turn count, end reason, and
        full trajectory history for analysis.
    """
    traveler_names = list(strategies.keys())
    game = GameState.create(traveler_names)
    deck = MerchantDeck(rng=rng)
    # Shared rng for card effects that roll an independent generator, and an
    # initial revealed-stock snapshot for resolution-time passives (Prensa Móvel).
    game.rng = rng or random.Random()
    game.market_revealed = deck.snapshot_revealed()
    history: list[HourSnapshot] = []

    for _ in range(max_hours):
        # --- Start of Hour: advance overload markers (§11.1) ---
        for t in game.travelers:
            advance_overload(t)

        # --- Phase 1: Delivery ---
        resolve_deliveries(game, strategies)
        if check_temporal_receptor_win(game):
            stabiliser = next(t for t in game.travelers if t.name == game.winner)
            stabiliser.contract_points += CP_STABILISATION_BONUS  # §32.3
            game.cp_rewards_pending.append(stabiliser.name)
            for s in game.travelers:
                if not s.is_terminated:        # §32.2 survival point
                    s.contract_points += CP_SURVIVAL_BONUS
                    game.cp_rewards_pending.append(s.name)
            process_pending_rewards(game, deck, strategies, rng or random.Random())
            return GameResult(
                winner=_determine_cp_winner(game),
                hours_played=game.hour - 1,
                end_reason=game.game_over_reason,
                traveler_results=list(game.travelers),
                history=history,
                strategy_names={n: s.name for n, s in strategies.items()},
                item_events=list(game.item_events),
            )

        # Process rewards queued by Phase 1 (deliveries) before Phase 2.
        process_pending_rewards(game, deck, strategies, rng or random.Random())

        # --- Pre-Phase 2: free recycles (non-reachable items for energy) ---
        _resolve_free_recycles(game, deck, strategies)

        # --- Phase 2: Market ---
        game_over = resolve_market_phase(game, deck, strategies, rng or random.Random())
        if game_over:
            for s in game.travelers:
                if not s.is_terminated:        # §32.2 survival point
                    s.contract_points += CP_SURVIVAL_BONUS
            return GameResult(
                winner=_determine_cp_winner(game),
                hours_played=game.hour - 1,
                end_reason=game.game_over_reason,
                traveler_results=list(game.travelers),
                history=history,
                strategy_names={n: s.name for n, s in strategies.items()},
                item_events=list(game.item_events),
            )

        # A Reward Phase is queued the instant its CP is earned but only runs
        # once the current phase has completely finished (§26): drain anything
        # the Market phase earned before Phase 3 begins.
        process_pending_rewards(game, deck, strategies, rng or random.Random())

        # --- Phase 3: Generators, roll and allocate ---
        allocations: dict[str, Allocation] = {}
        travel_directions: dict[str, int] = {}
        travel_caps: dict[str, int | None] = {}

        active = [t for t in game.travelers if not t.awaiting_respawn]
        for t in active:
            dice = roll_generators(rng=rng)
            strategy = strategies[t.name]
            result = strategy.choose_allocation(t, game, dice)
            if len(result) == 3:
                alloc, direction, cap = result
            else:
                alloc, direction = result
                cap = None

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
        snapshots = resolve_hour(game, allocations, travel_directions, travel_caps)
        history.extend(snapshots)

        # Process any rewards queued by Phase 3 (millennium crossings).
        process_pending_rewards(game, deck, strategies, rng or random.Random())

        # --- Phase 4: Item Activation (§15.4) ---
        resolve_activation_phase(game, strategies)

        # Reward Phase for any CP earned during Item Activation (e.g. a delivery
        # via Geladeira), run before the Time I solo phases are resolved (§26).
        process_pending_rewards(game, deck, strategies, rng or random.Random())

        # --- Solo generators phases from Time I rewards (§26.3 Time I) ---
        # Each solo phase is a full Phase 3 for that traveler alone, followed
        # by their own Phase 4 window, then immediate reward processing.
        _resolve_solo_phases(game, deck, strategies, rng)

        # --- Update Merchant speed upgrades (§17.6) ---
        check_merchant_upgrades(game)

        # --- Check win conditions after each Hour ---
        result = _check_win_conditions(game)
        if result is not None:
            process_pending_rewards(game, deck, strategies, rng or random.Random())
            return GameResult(
                winner=_determine_cp_winner(game),
                hours_played=game.hour - 1,
                end_reason=result[1],
                traveler_results=list(game.travelers),
                history=history,
                strategy_names={n: s.name for n, s in strategies.items()},
                item_events=list(game.item_events),
            )

    # Hit max_hours without a natural end
    return GameResult(
        winner=None,
        hours_played=max_hours,
        end_reason="max_hours_reached",
        traveler_results=list(game.travelers),
        history=history,
        strategy_names={n: s.name for n, s in strategies.items()},
        item_events=list(game.item_events),
    )


def _determine_cp_winner(game: GameState) -> str | None:
    """
    Return the name of the traveler with the most CP (§32.4).
    Tiebreaker: Priority cascade, highest century, then gold, then energy.
    """
    if not game.travelers:
        return None
    winner = max(
        game.travelers,
        key=lambda t: (t.contract_points, t.century, t.gold, t.energy),
    )
    return winner.name


def _resolve_free_recycles(
    game: GameState,
    deck,
    strategies: dict,
) -> None:
    """
    Pre-Phase 2 hook: let strategies recycle items they no longer need.
    Each recycled card grants energy equal to its recycle_value (§21.1).
    """
    from engine.state import ItemEvent
    from engine import combat
    for traveler in game.travelers:
        if traveler.awaiting_respawn:
            continue
        cards = strategies[traveler.name].choose_items_to_recycle(traveler, game)
        for card in list(cards):
            if card not in traveler.hand:
                continue
            combat.recycle_card(game, traveler, card, grant_energy=True)
            game.item_events.append(ItemEvent(
                hour=game.hour,
                traveler=traveler.name,
                event_type="recycled",
                card_name=card.name,
                century=traveler.century,
            ))


def _award_survival_bonus(game: GameState) -> None:
    """§32.2: +1 for each traveler that has never been terminated, at game end."""
    for s in game.travelers:
        if not s.is_terminated:
            s.contract_points += CP_SURVIVAL_BONUS
            game.cp_rewards_pending.append(s.name)


def _check_win_conditions(game: GameState) -> tuple[str | None, str] | None:
    """
    Check §11.1 win conditions after each Hour.

    Returns (winner_name_or_None, reason_string) or None if the game continues.

    Note on termination: the Terminated condition is permanent (§28.1) but a
    terminated traveler keeps playing after respawning. So §11.1c counts how many
    travelers have *never* been terminated (``not is_terminated``), not who is
    currently in play. The game ends the moment only one, or none, remain
    never-terminated, even though every traveler is technically still playing.
    """
    # Travelers in play this Hour (used for the Year Zero check, anyone present
    # on the timeline can reach it, including respawned terminated travelers).
    in_play = [t for t in game.travelers if not t.awaiting_respawn]
    never_terminated = [t for t in game.travelers if not t.is_terminated]

    # (a) A traveler reaches Year Zero (§11.1a)
    for t in in_play:
        if reached_year_zero(t.century):
            t.contract_points += CP_YEAR_ZERO_TOTAL
            for _ in range(CP_YEAR_ZERO_TOTAL):
                game.cp_rewards_pending.append(t.name)
            _award_survival_bonus(game)
            return (t.name, "year_zero")

    # (c) All but one traveler carry the Terminated condition (§11.1c).
    if len(never_terminated) <= 1:
        # §11.3 / §32.3: ending the game this way is a Stabilisation. The
        # stabiliser is the traveler responsible for terminating the last
        # not-yet-terminated traveler: recorded in game.last_termination_causer.
        # They get the +1 stabilisation bonus for ending the game (their
        # termination contract's own +1 and Wanted were already awarded at the
        # kill, in resolve.check_termination()). The stabiliser may themselves
        # carry the Terminated condition: terminated travelers still act.
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
        _award_survival_bonus(game)
        reason = "last_traveler" if survivor is not None else "all_terminated"
        return (survivor.name if survivor is not None else None, reason)

    return None


# ---------------------------------------------------------------------------
# Solo generators phase (Time I reward: §26.3)
# ---------------------------------------------------------------------------

def _resolve_solo_phases(
    game: GameState,
    deck,
    strategies: dict,
    rng,
) -> None:
    """
    Resolve extra solo generators phases earned via the Time I reward.

    Each pending traveler gets a full Phase 3 (dice roll + full matrix, solo)
    followed by their own Phase 4 activation window. CP rewards from milestones
    hit during the solo phase are processed immediately after.

    Solo phases that themselves yield a Time I reward queue another solo phase,
    resolved in the same pass (appended to pending before the loop ends).
    """
    rng_used = rng or random.Random()

    from engine.resolve import (
        apply_escape_valve, resolve_module_1, resolve_module_2, resolve_module_3,
        resolve_module_7, resolve_module_8, resolve_module_9,
        apply_overload_markers, check_termination, check_millennium_milestones,
    )
    from engine.paradox import resolve_paradox_pool

    while game.solo_phases_pending:
        name = game.solo_phases_pending.pop(0)
        traveler = next((t for t in game.travelers if t.name == name), None)
        if traveler is None or traveler.awaiting_respawn:
            continue
        strategy = strategies.get(name)
        if strategy is None:
            continue

        # Solo Phase 3: roll and allocate.
        dice = roll_generators(rng=rng_used)
        result = strategy.choose_allocation(traveler, game, dice)
        if len(result) == 3:
            alloc, direction, cap = result
        else:
            alloc, direction = result
            cap = None

        # Resolve all 9 modules for this traveler only.
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
            if traveler.item_voucher > 0:
                traveler.item_voucher -= 1
                game.item_voucher_active_for = name
            else:
                game.item_voucher_active_for = None

            activations = strategy.choose_activations(traveler, game)
            for card, context in activations:
                if card not in traveler.hand:
                    continue
                if card.ability_type not in ("active", "atemporal_active"):
                    continue
                if card.active_effect is None:
                    continue
                card.active_effect(traveler, game, context)
                if card.recycles_on_use and card in traveler.hand:
                    combat.recycle_card(game, traveler, card)

            game.item_voucher_active_for = None

        # Process CP rewards triggered by this solo phase immediately.
        process_pending_rewards(game, deck, strategies, rng_used)


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
    Run n games with the same strategy lineup.

    Args:
        strategies: traveler_name → Strategy (same for all games).
        n:          Number of games to simulate.
        seed:       Optional master seed. Each game gets a deterministic
                    sub-seed derived from this for full reproducibility.
        max_hours:  Per-game hour limit.
        validate:   Validate allocations (slow; use only during development).

    Returns:
        List of n GameResult objects.
    """
    results: list[GameResult] = []
    master_rng = random.Random(seed)

    for i in range(n):
        game_seed = master_rng.randint(0, 2**32 - 1) if seed is not None else None
        game_rng = random.Random(game_seed) if game_seed is not None else None
        result = simulate_game(
            strategies=strategies,
            rng=game_rng,
            max_hours=max_hours,
            validate=validate,
        )
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Quick CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # For the interactive simulation menu with charts and full reporting, run:
    #   python -m simulation.sim_menu
    #
    # This quick smoke-test runs 200 games and prints a compact summary.
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    from simulation.strategies.aggressive import AggressiveStrategy
    from simulation.strategies.conservative import ConservativeStrategy
    from simulation.strategies.smart import SmartStrategy
    from simulation.strategies.collector import CollectorStrategy
    from simulation.metrics import summarise, print_summary

    strategies = {
        "Traveler_A": AggressiveStrategy(),
        "Traveler_C": ConservativeStrategy(),
        "Traveler_S": SmartStrategy(),
        "Traveler_K": CollectorStrategy(),
    }

    n = 200
    print(f"Running {n} games (Aggressive, Conservative, Smart, Collector)...")
    results = simulate_n_games(strategies, n=n, seed=42)
    print_summary(summarise(results))

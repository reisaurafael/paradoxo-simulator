"""
engine/resolve.py
=================
Module-by-module resolution of the matrix for all travelers in an Hour.

The Rules Reference §12 is explicit and unusual: resolution is NOT
per-traveler. It is per-module, across ALL travelers simultaneously:
    module 1 of all travelers → module 2 of all travelers → … → module 9.

This file implements that ordering and every downstream effect:
    §13: Recharge (modules 1, 2, 3): energy and gold
    §15: Heating (module 7): booms and explosion
    §14: Travel (modules 8, 9): movement along the timeline
    §16: Paradox (modules 4, 5, 6): damage pool (see engine/paradox.py)

Priority (§27) is needed for Travel (§12.2 / §14.5). Where order between
travelers matters for travel, higher century = higher priority. Ties break
by gold, then energy, then a generator duel (not simulated here, we use
a deterministic index-based tiebreak in the simulation layer).

Escape valve energy loss is applied before Phase 3 resolution begins (§11.2).
"""

from __future__ import annotations
import copy
from engine.state import TravelerState, GameState, Allocation, HourSnapshot
from engine.constants import (
    BOOM_LIMIT,
    EXPLOSION_ENERGY_LOSS,
    EXPLOSION_BOOM_RESET,
    PAST_TRAVEL_ENERGY_COST_PER_CENTURY,
    DOUBLE_TRAVEL_MULTIPLIER,
    YEAR_ZERO,
    CENTURY_MAX,
    FUNCTION_RECHARGE,
    FUNCTION_PARADOX,
    FUNCTION_TRAVEL,
    MILLENNIUM_CENTURIES,
    CP_PER_MILLENNIUM,
    CP_PER_TERMINATION,
    WANTED_BOUNTY,
    OVERDRIVE_THRESHOLD_CENTURY,
    OVERDRIVE_ENERGY_COST_PER_CENTURY,
)
from engine.timeline import reached_year_zero, clamp_to_board
from engine.paradox import resolve_paradox_pool
from engine import combat
from engine.cards import passive_source_cards


# ---------------------------------------------------------------------------
# Priority ordering (§27)
# ---------------------------------------------------------------------------

def priority_order(travelers: list[TravelerState]) -> list[TravelerState]:
    """
    Sort travelers by Priority for cases where order matters (§27).

    Priority cascade (highest priority first):
        1. Closest to the future (highest century number) (§27.1)
        2. Most gold
        3. Most energy
        4. Generator duel: approximated here by stable index order
           (true duel is interactive; simulation uses insertion order as
           a deterministic tiebreak that introduces no systematic bias).
    """
    return sorted(
        travelers,
        key=lambda t: (t.century, t.gold, t.energy),
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Escape valve pre-processing (§11.2)
# ---------------------------------------------------------------------------

def apply_escape_valve(traveler: TravelerState, allocation: Allocation,
                       game: GameState | None = None) -> None:
    """
    Apply escape valve energy loss before matrix resolution begins.

    The escape valve loses energy equal to the generator value sent there (§29.9).
    Energy cannot go below 0 here: termination is checked after resolution.
    Mutates traveler in place.
    """
    if allocation.escape_valve > 0:
        actual = min(traveler.energy, allocation.escape_valve)
        traveler.energy = max(0, traveler.energy - allocation.escape_valve)
        combat.register_loss(game, traveler, actual)


# ---------------------------------------------------------------------------
# Recharge: modules 1, 2, 3 (§13)
# ---------------------------------------------------------------------------

def resolve_module_1(traveler: TravelerState, allocation: Allocation) -> None:
    """Module 1: energy equal to generator value (§20.1). Carro adds +1."""
    v = combat.effective_generator(traveler, allocation, FUNCTION_RECHARGE, 0)
    if v:
        traveler.energy += v


def resolve_module_2(traveler: TravelerState, allocation: Allocation) -> None:
    """Module 2: gold equal to generator value (§20.1). Carro adds +1."""
    v = combat.effective_generator(traveler, allocation, FUNCTION_RECHARGE, 1)
    if v:
        traveler.gold += v


def resolve_module_3(traveler: TravelerState, allocation: Allocation) -> None:
    """Module 3: both energy and gold equal to generator value (§20.1). Carro adds +1."""
    v = combat.effective_generator(traveler, allocation, FUNCTION_RECHARGE, 2)
    if v:
        traveler.energy += v
        traveler.gold += v


# ---------------------------------------------------------------------------
# Paradox: modules 4, 5, 6 (§16), dispatched to engine/paradox.py
# ---------------------------------------------------------------------------
# Paradox resolution is handled by resolve_paradox_pool() in paradox.py.
# It is called once for each of modules 4, 5, 6 in the per-module loop below.


# ---------------------------------------------------------------------------
# Heating: module 7 (§15)
# ---------------------------------------------------------------------------

def _apply_boom_hooks(traveler: TravelerState, booms: int,
                      game: GameState | None = None) -> int:
    """Apply on_boom_gain hooks from all source cards. Returns modified boom count."""
    for card in passive_source_cards(traveler, game):
        if card.on_boom_gain:
            booms = card.on_boom_gain(traveler, booms)
    return max(0, booms)


def _apply_travel_cost_hooks(traveler: TravelerState, cost: int, direction: int,
                             game: GameState | None = None) -> int:
    """Apply on_travel_cost hooks (Máquina Voadora, Telescópio, Armadura). Returns cost."""
    for card in passive_source_cards(traveler, game):
        if card.on_travel_cost:
            cost = card.on_travel_cost(traveler, cost, direction, game)
    return max(0, cost)


def _apply_overload_hooks(traveler: TravelerState, game: GameState) -> None:
    """Call on_overload hooks after overload markers are set."""
    # Toalha prevents overload entirely: checked in apply_overload_markers.
    for card in passive_source_cards(traveler, game):
        if card.on_overload:
            card.on_overload(traveler, game)


def _colar_de_cavalo_bonus(traveler: TravelerState, allocation: Allocation) -> None:
    """Colar de Cavalo: +1 energy for each travel module with a placed generator."""
    has_colar = any(c.name == "Colar de Cavalo" for c in traveler.hand)
    if not has_colar:
        return
    for col in range(3):
        if allocation.get(FUNCTION_TRAVEL, col) > 0:
            traveler.energy += 1


def resolve_module_7(traveler: TravelerState, allocation: Allocation,
                     game: GameState | None = None) -> None:
    """
    Module 7: heating, add booms equal to generator value (§15.1).

    If the total reaches BOOM_LIMIT (12), the motor explodes:
        - traveler loses EXPLOSION_ENERGY_LOSS (2) energy (§15.2)
        - 12 booms are discarded (§15.2)
        - traveler cannot travel this Hour (§15.2)

    Card hooks: on_boom_gain modifies boom count; on_explosion_check can prevent explosion.
    Mutates traveler in place.
    """
    v = combat.effective_generator(traveler, allocation, FUNCTION_TRAVEL, 0)
    if not v:
        return

    booms_gained = _apply_boom_hooks(traveler, v, game)
    traveler.booms += booms_gained

    if traveler.booms >= BOOM_LIMIT:
        # Check if any card prevents the explosion (e.g. Super Motor)
        prevented = False
        for card in list(traveler.hand):
            if card.on_explosion_check:
                if card.on_explosion_check(traveler, game):
                    prevented = True
                    if card.recycles_on_use:
                        traveler.hand.remove(card)
                    break
        if not prevented:
            traveler.booms -= EXPLOSION_BOOM_RESET
            actual = min(traveler.energy, EXPLOSION_ENERGY_LOSS)
            traveler.energy = max(0, traveler.energy - EXPLOSION_ENERGY_LOSS)
            traveler.exploded_this_hour = True
            combat.register_loss(game, traveler, actual)
            # §28.1 + §18: a motor explosion is self-inflicted. If it lands the
            # killing blow, no other traveler is responsible, drop any stale
            # Paradox attribution from earlier this Hour.
            if traveler.energy <= 0:
                traveler.eliminated_by.clear()


# ---------------------------------------------------------------------------
# Travel: modules 8, 9 (§14)
# ---------------------------------------------------------------------------

def _past_travel_cost(
    traveler: TravelerState,
    prev_century: int,
    new_century: int,
    game: GameState | None = None,
) -> int:
    """Energy cost of moving from ``prev_century`` to ``new_century`` in the past.

    Overdrive rule: every century traveled at a position ≤ OVERDRIVE_THRESHOLD_CENTURY
    costs OVERDRIVE_ENERGY_COST_PER_CENTURY instead of PAST_TRAVEL_ENERGY_COST_PER_CENTURY.
    Card cost-reduction passives (Máquina Voadora, Telescópio, Armadura) apply to
    the combined total. Returns 0 for non-past movement.
    """
    if new_century >= prev_century:
        return 0
    normal_cents = max(0, min(prev_century, CENTURY_MAX)
                      - max(new_century, OVERDRIVE_THRESHOLD_CENTURY))
    overdrive_cents = max(0, min(prev_century, OVERDRIVE_THRESHOLD_CENTURY) - new_century)
    cost = (normal_cents * PAST_TRAVEL_ENERGY_COST_PER_CENTURY
            + overdrive_cents * OVERDRIVE_ENERGY_COST_PER_CENTURY)
    return _apply_travel_cost_hooks(traveler, cost, -1, game)


def survival_capped_steps(
    traveler: TravelerState,
    steps: int,
    direction: int,
    game: GameState | None = None,
) -> int:
    """Reduce a planned past-travel distance so the traveler does not self-terminate.

    Travel distance is "up to" the resolved value (§30.1): a traveler may stop
    short. Because the matrix is resolved module-by-module (§12.1), a Travel
    decision must read the energy state left by the *earlier* modules this Hour:
    Recharge (1-3) and Paradox (4-6) have already landed by the time Travel
    (8-9) resolves. A traveler therefore only travels as far as it can while
    keeping at least 1 energy, rather than burning energy it no longer has and
    terminating itself mid-board.

    The one exception is Year Zero (§30.4): stepping onto Year Zero ends the game
    and is a legitimate terminal move, so it is never clamped away.

    Future travel is free (§30.2) and is returned unchanged.
    """
    if direction >= 0 or steps <= 0:
        return steps
    prev_century = traveler.century
    allowed = 0
    for s in range(1, steps + 1):
        new_century = clamp_to_board(prev_century - s)
        if new_century <= YEAR_ZERO:
            # Reaching Year Zero is a legal, game-ending destination, allow it.
            return s
        if _past_travel_cost(traveler, prev_century, new_century, game) <= traveler.energy - 1:
            allowed = s
        else:
            break
    return allowed


def execute_travel(traveler: TravelerState, centuries: int,
                   game: GameState | None = None) -> None:
    """
    Move a traveler along the timeline by `centuries` (positive = future,
    negative = past).

    Positive movement (toward future, toward XXX) costs 0 energy (§30.2).
    Negative movement (toward past, toward Year Zero) costs 1 energy per
    century traveled (§30.2), modified by travel-cost passives (Máquina Voadora,
    Telescópio, Armadura).

    The traveler stops at XXX and cannot pass it (§30.4).
    Reaching Year Zero is a legal terminal position that ends the game (§30.4).

    Public so card actives that grant movement (Mapa de Geradus Mercator) reuse
    the exact same cost/passive handling. Mutates traveler in place.
    """
    if centuries == 0:
        return

    prev_century = traveler.century
    new_century = clamp_to_board(prev_century + centuries)
    actual_movement = new_century - prev_century

    # Energy cost for past travel (card hooks may reduce to 0).
    if actual_movement < 0:
        cost = _past_travel_cost(traveler, prev_century, new_century, game)
        actual = min(traveler.energy, cost)
        traveler.energy = max(0, traveler.energy - cost)
        combat.register_loss(game, traveler, actual)
        # §28.1 + §18 ("responsibility for a kill goes to whichever instance
        # landed the killing blow"): past-travel cost is self-inflicted. If it
        # takes the traveler's last energy, the killing blow is their own, no
        # other traveler is responsible. Clear any stale attribution from earlier
        # (non-lethal) Paradox damage this Hour so a self-termination credits
        # nobody. (Survival is normally preserved by the caller's clamp; this
        # only fires when the traveler deliberately steps onto Year Zero, §30.4.)
        if traveler.energy <= 0:
            traveler.eliminated_by.clear()

    traveler.century = new_century

    # Millennium CP is not awarded here. Per §8.1c the milestone fires only when
    # a traveler ends an Hour on X/XX, not when they pass through mid-travel.
    # It is evaluated once, against the final position, after all travel resolves.

    # Missed delivery diagnostic: any card whose delivery_century falls in the
    # path of this move but is NOT the landing century is a missed delivery.
    if game is not None and traveler.hand:
        lo = min(prev_century, new_century)
        hi = max(prev_century, new_century)
        from engine.state import ItemEvent
        for card in traveler.hand:
            dc = getattr(card, "delivery_century", None)
            if dc is not None and lo <= dc <= hi and dc != new_century:
                game.item_events.append(ItemEvent(
                    hour=game.hour,
                    traveler=traveler.name,
                    event_type="missed_delivery",
                    card_name=card.name,
                    century=dc,
                ))


def _award_millennium_crossings(
    traveler: TravelerState,
    game: "GameState | None" = None,
) -> None:
    """Award milestone CP when the traveler ends an Hour on century X or XX (§8.1c).

    Must be called after all travel for the Hour has resolved so that
    ``traveler.century`` reflects the final position. Idempotent.
    """
    for milestone, scored_attr in (
        (10, "scored_century_x"),
        (20, "scored_century_xx"),
    ):
        if not getattr(traveler, scored_attr) and traveler.century == milestone:
            traveler.contract_points += CP_PER_MILLENNIUM
            setattr(traveler, scored_attr, True)
            if game is not None:
                game.cp_rewards_pending.append(traveler.name)


def resolve_module_8(
    traveler: TravelerState,
    allocation: Allocation,
    direction: int,
    game: GameState | None = None,
    cap: int | None = None,
) -> None:
    """
    Module 8: travel by up to generator value in `direction` (§14.1, §17.5).

    Per the rules a traveler may stop before their full rolled distance.
    `cap` (if provided) limits movement to at most that many centuries.
    Skipped if traveler exploded this Hour (§5.4).
    """
    if traveler.exploded_this_hour:
        return
    v = combat.effective_generator(traveler, allocation, FUNCTION_TRAVEL, 1)
    if v:
        steps = min(v, cap) if cap is not None else v
        steps = survival_capped_steps(traveler, steps, direction, game)
        if steps > 0:
            execute_travel(traveler, steps * direction, game)


def resolve_module_9(
    traveler: TravelerState,
    allocation: Allocation,
    direction: int,
    game: GameState | None = None,
    cap: int | None = None,
) -> None:
    """
    Module 9: double travel, travel by up to 2× generator value (§14.1).

    `cap` limits movement to at most that many remaining centuries.
    Skipped if traveler exploded this Hour (§5.4).
    """
    if traveler.exploded_this_hour:
        return
    v = combat.effective_generator(traveler, allocation, FUNCTION_TRAVEL, 2)
    if v:
        steps = min(v * DOUBLE_TRAVEL_MULTIPLIER, cap) if cap is not None else v * DOUBLE_TRAVEL_MULTIPLIER
        steps = survival_capped_steps(traveler, steps, direction, game)
        if steps > 0:
            execute_travel(traveler, steps * direction, game)


# ---------------------------------------------------------------------------
# Milestone CP check (§25.3)
# ---------------------------------------------------------------------------

def check_millennium_milestones(
    traveler: TravelerState,
    game: "GameState | None" = None,
) -> None:
    """Evaluate the end-of-Hour millennium milestone for X/XX (§8.1c).

    Called after all travel for the Hour has resolved; reads the final position. Idempotent.
    """
    _award_millennium_crossings(traveler, game)


# ---------------------------------------------------------------------------
# Termination check (§30)
# ---------------------------------------------------------------------------

def check_termination(
    traveler: TravelerState,
    game: "GameState | None" = None,
) -> bool:
    """
    A traveler whose energy reaches 0 is terminated (§28.1).

    Termination is a **permanent** condition: once taken it is never removed, and
    respawning does not clear it (§28.3). What it costs the traveler is the
    end-of-game survival point (§32.2) and it is what the §11.1c game-end
    condition counts. A terminated traveler otherwise plays normally once they
    have respawned.

    Santo Graal prevents the first termination (restores 1 energy, then recycles).

    On termination (§28.2-28.3, §33.1, §28.4-28.5):
        - the traveler's equipped objects are recycled, and respawn energy is
          recorded as 12 + the total recycle value of those objects;
        - `awaiting_respawn` is set: they are inactive and untargetable for the
          rest of this Hour and return to XXX with that energy at the start of
          the next Hour, keeping their gold and booms (applied by advance_overload);
        - they lose their Wanted status (a terminated traveler cannot hold it);
        - the traveler responsible for the killing blow earns +1 CP (which
          triggers a Reward) and becomes Wanted, but only for the victim's
          *first* termination; re-terminating an already-terminated traveler
          yields no reward (§28.4).

    Returns True if the traveler was terminated by this check.
    """
    if traveler.energy <= 0 and not traveler.awaiting_respawn:
        # Santo Graal: prevent the death (restore energy; not a termination).
        graal = next((c for c in traveler.hand if c.name == "Santo Graal"), None)
        if graal is not None:
            traveler.energy = 1
            traveler.hand.remove(graal)
            return False

        # Instant Recycle to survive *enemy-caused* lethal damage (§3.3 Recycle is
        # a free action announceable during resolution; §21.1 recycling an equipped
        # object as the Recycle action grants energy equal to its recycle value).
        # A traveler about to be terminated by another traveler's hit (Paradox,
        # weapon, reflection) recycles its *least important* equipped object(s) at
        # instant speed to climb back above 0 and deny the kill. "Least important"
        # is approximated by the lowest recycle value, so the more valuable cards
        # are kept; recycle one at a time, stopping as soon as the traveler
        # survives, and only when it can actually save them (a doomed traveler
        # keeps its objects so they convert to respawn energy, §28.2).
        #
        # This does NOT apply to self-inflicted deaths, past-travel cost (§30.2),
        # a motor explosion (§15.2), or the escape valve (§11.2). Those clear
        # ``eliminated_by`` at the loss site, so a non-empty list here is exactly
        # the signal that an enemy landed the killing blow.
        if game is not None and traveler.hand and traveler.eliminated_by:
            recyclable = sorted(
                (c for c in traveler.hand if getattr(c, "recycle_value", 0) > 0),
                key=lambda c: c.recycle_value,
            )
            if traveler.energy + sum(c.recycle_value for c in recyclable) > 0:
                for card in recyclable:
                    if traveler.energy > 0:
                        break
                    combat.recycle_card(game, traveler, card, grant_energy=True)
                if traveler.energy > 0:
                    return False  # survived; not terminated, nobody credited

        # First termination iff the traveler has never carried the condition
        # before; this gates the §28.4 "no reward on re-termination".
        first_termination = not traveler.is_terminated

        # §33.1 + §8.1b: the traveler who took the last energy earns the
        # termination reward and becomes Wanted, unless §28.4 suppresses it.
        if game is not None and traveler.eliminated_by:
            causer_name = traveler.eliminated_by[-1]
            causer = next((t for t in game.travelers if t.name == causer_name), None)
            if causer is not None and causer is not traveler and not causer.awaiting_respawn:
                # Record responsibility for the §32.3 stabilisation bonus even
                # when §28.4 suppresses the contract reward.
                game.last_termination_causer = causer.name
                if first_termination:
                    causer.contract_points += CP_PER_TERMINATION
                    causer.is_wanted = True               # §33.1
                    game.cp_rewards_pending.append(causer.name)  # §8.2 reward trigger
                    if traveler.is_wanted:                # §33.2: bounty for killing Wanted
                        causer.gold += WANTED_BOUNTY

        traveler.is_terminated = True                 # permanent (§28.1)
        traveler.awaiting_respawn = True              # transient (§28.3)
        traveler.energy = 0
        traveler.is_wanted = False                    # §28.5

        # §28.2 / §12.5: recycle equipment and bank the respawn energy.
        from engine.constants import TERMINATION_RESPAWN_ENERGY_BASE
        respawn_energy = TERMINATION_RESPAWN_ENERGY_BASE
        for card in list(traveler.hand):
            respawn_energy += getattr(card, "recycle_value", 0)
            if game is not None:
                combat.recycle_card(game, traveler, card)
            elif card in traveler.hand:
                traveler.hand.remove(card)
        traveler.respawn_energy = respawn_energy
        return True
    return False


# ---------------------------------------------------------------------------
# End-of-Hour overload housekeeping (§11.1, §8.3)
# ---------------------------------------------------------------------------

def apply_overload_markers(
    traveler: TravelerState,
    allocation: Allocation,
    game: GameState | None = None,
) -> None:
    """
    At the end of Phase 3, place overload markers on functions that were
    overloaded this Hour (§8.3, §11.1).

    Toalha: travel and paradox functions cannot be overloaded.
    Relógio Mecânico / Autômato: fire on_overload hooks when overload occurs.
    Mutates traveler in place.
    """
    from engine.matrix import functions_overloaded_by
    from engine.constants import FUNCTION_PARADOX, FUNCTION_TRAVEL
    overloaded = functions_overloaded_by(allocation)

    # Toalha prevents travel and paradox overloads
    has_toalha = any(c.name == "Toalha" for c in traveler.hand)
    if has_toalha:
        overloaded.discard(FUNCTION_TRAVEL)
        overloaded.discard(FUNCTION_PARADOX)

    if overloaded:
        traveler.overloaded_next = overloaded
        if game is not None:
            _apply_overload_hooks(traveler, game)
    else:
        traveler.overloaded_next = set()


def advance_overload(traveler: TravelerState) -> None:
    """
    Start-of-Hour housekeeping (§28.3, §11.1).

    First, respawn a traveler terminated last Hour: they return to XXX with the
    banked respawn energy (12 + Σ recycle value), keep their gold and booms, and
    act normally from this Hour (§28.2-28.3). The permanent Terminated condition
    (`is_terminated`) is NOT cleared: only the transient `awaiting_respawn` is.
    Termination resets their machine, so any pending overload is cleared along
    with stale damage attribution.

    Then rotate overload markers:
        - overloaded_functions ← overloaded_next (these are now in effect)
        - overloaded_next ← empty set
    Mutates traveler in place.
    """
    if traveler.awaiting_respawn:
        traveler.energy = traveler.respawn_energy
        traveler.century = CENTURY_MAX
        traveler.awaiting_respawn = False          # is_terminated stays True (§28.3)
        traveler.respawn_energy = 0
        traveler.eliminated_by = []
        traveler.overloaded_next = set()

    traveler.overloaded_functions = set(traveler.overloaded_next)
    traveler.overloaded_next = set()
    traveler.exploded_this_hour = False
    # Per-hour "first time this Hour" trackers reset at the start of the Hour so
    # they cover every phase, including Item Activation (§Escudo Viking).
    traveler.cards_used_this_hour = set()


# ---------------------------------------------------------------------------
# Full Hour resolution (§8.3, §12)
# ---------------------------------------------------------------------------

def resolve_hour(
    game: GameState,
    allocations: dict[str, Allocation],
    travel_directions: dict[str, int],
    travel_caps: dict[str, int | None] | None = None,
) -> list[HourSnapshot]:
    """
    Resolve Phase 3 of one Hour: all 9 modules, in order, across all travelers.

    This is the core simulation step. It mutates `game` in place and
    returns a list of HourSnapshot records (one per traveler) for the
    metrics and graph modules.

    Args:
        game:              Current game state (mutated in place).
        allocations:       traveler_name → Allocation for this Hour.
        travel_directions: traveler_name → +1 (future) or -1 (past).
                           Only consulted for modules 8 and 9.

    Returns:
        List of HourSnapshot objects, one per traveler.

    Resolution order (§12.1):
        Module 1 all → Module 2 all → … → Module 9 all
    Within a module:
        Recharge (modules 1-3): simultaneous (§12.2)
        Paradox  (modules 4-6): simultaneous among travelers (§12.2)
        Travel   (modules 7-9): Priority order where order matters (§12.2)
    """
    # Travelers in play this Hour. Includes travelers carrying the permanent
    # Terminated condition who have already respawned: they act normally (§28.3).
    active = [t for t in game.travelers if not t.awaiting_respawn]

    # --- Pre-resolution: escape valve energy loss ---
    for t in active:
        apply_escape_valve(t, allocations[t.name], game)

    # --- Modules 1-3: Recharge (simultaneous) ---
    for t in active:
        alloc = allocations[t.name]
        resolve_module_1(t, alloc)
        resolve_module_2(t, alloc)
        resolve_module_3(t, alloc)

    # --- Modules 4-6: Paradox ---
    # Each module is resolved as a pool across all causers (§16.3).
    # resolve_paradox_pool handles ordering by distance within the module.
    for paradox_col in range(3):  # col 0=future, 1=present, 2=past
        resolve_paradox_pool(game, active, allocations, paradox_col)

    # --- Module 7: Heating (Priority order) ---
    for t in priority_order(active):
        resolve_module_7(t, allocations[t.name], game)

    # Termination checks after Paradox and Heating, before travel (§30.1)
    for t in active:
        check_termination(t, game)

    # --- Modules 8-9: Travel (Priority order) ---
    still_active = [t for t in active if not t.awaiting_respawn]
    for t in priority_order(still_active):
        alloc = allocations[t.name]
        direction = travel_directions.get(t.name, -1)  # default: toward past
        cap = travel_caps.get(t.name) if travel_caps else None
        century_before_m8 = t.century
        resolve_module_8(t, alloc, direction, game, cap=cap)
        m8_moved = abs(t.century - century_before_m8)
        remaining_cap = (cap - m8_moved) if cap is not None else None
        resolve_module_9(t, alloc, direction, game, cap=remaining_cap)

    # Termination check after travel: past travel costs energy and can reach 0 (§28.1).
    for t in still_active:
        check_termination(t, game)

    # --- Post-travel: Colar de Cavalo bonus, milestone CP, overload markers, hour-end hooks ---
    for t in game.travelers:
        if not t.awaiting_respawn:
            _colar_de_cavalo_bonus(t, allocations.get(t.name, Allocation.empty()))
            check_millennium_milestones(t, game)
        apply_overload_markers(t, allocations.get(t.name, Allocation.empty()), game)

    # Hour-end card hooks (James Watt, Bússola, etc.)
    for t in game.travelers:
        if not t.awaiting_respawn:
            for card in t.hand:
                if card.on_hour_end:
                    card.on_hour_end(t, game)

    # Per-hour "first time this Hour" trackers are reset at the start of the next
    # Hour (advance_overload), so Item Activation in Phase 4 still sees this
    # Hour's flags.

    # --- Snapshots ---
    snapshots = []
    for t in game.travelers:
        snapshots.append(HourSnapshot(
            hour=game.hour,
            traveler_name=t.name,
            energy=t.energy,
            gold=t.gold,
            booms=t.booms,
            century=t.century,
            contract_points=t.contract_points,
            allocation=copy.deepcopy(allocations.get(t.name, Allocation.empty())),
            hand_size=len(t.hand),
            exploded=t.exploded_this_hour,
            terminated=t.is_terminated,
        ))

    game.hour += 1
    return snapshots

"""
rl/actions.py
=============
Every legal option of every decision the learned player makes.

    legal_moves       the dice: where the generators go, which way, how far
    market_options    the market: buy a card, renew one, pay off Wanted, pass
    REWARD_CATEGORIES the reward: Chaos, Time or Resource

The dice come first below.

A move is where the four generators go (the Allocation), which way to
travel, and how far at most. The learned player scores every legal move and
picks one, so this module has to write them all out.

The rules of §10 and §11 cut the space down a lot: every generator in a
function shows the same value, modules fill left to right, and the escape
valve only exists while a function is overloaded. With four dice and three
functions there are rarely more than a few dozen moves.
"""

from __future__ import annotations
from dataclasses import dataclass
from itertools import product

from engine.state import Allocation, TravelerState, GameState
from engine.constants import (
    FUNCTION_TRAVEL,
    CENTURY_MAX,
    DOUBLE_TRAVEL_MULTIPLIER,
    MILLENNIUM_CENTURIES,
    SECRET_MARKET_CENTURY,
    DECLARE_COST,
    PERIODS,
)
from engine.market import (
    BuyAction, RenewAction, DeclareAction, UseCardAction, PassAction, effective_card_cost,
)

VALVE = -1   # target index for the escape valve
PAST = -1
FUTURE = 1   # where we're going, we don't need roads

PERIOD_NAMES = list(PERIODS)
REWARD_CATEGORIES = ["Chaos", "Time", "Resource"]
MARKET_KINDS = ["pass", "buy", "renew", "declare", "use"]
SECRET_RENEW_COST = 999  # the runner's marker for a Secret Market call


@dataclass(frozen=True)
class Move:
    """One legal move: a filled matrix, the escape valve, direction and cap."""
    matrix: tuple[tuple[int, int, int], ...]
    valve: int
    direction: int          # -1 toward the past, +1 toward the future
    cap: int | None         # most centuries to travel this Hour, None = all
    from_base: bool = False  # True if this is the heuristic's own choice

    def to_allocation(self) -> Allocation:
        alloc = Allocation.empty()
        for row in range(3):
            for col in range(3):
                alloc.set(row, col, self.matrix[row][col])
        alloc.escape_valve = self.valve
        return alloc

    @property
    def travel_steps(self) -> int:
        """Most centuries this move can travel before the cap (§14.1)."""
        return max_travel(self.matrix)


def max_travel(matrix: tuple[tuple[int, int, int], ...]) -> int:
    """Module 7 only heats, module 8 travels v, module 9 travels 2v."""
    row = matrix[FUNCTION_TRAVEL]
    steps = 0
    if row[1]:
        steps += row[1]
    if row[2]:
        steps += row[2] * DOUBLE_TRAVEL_MULTIPLIER
    return steps


def legal_allocations(
    dice: list[int],
    unavailable: set[int],
) -> list[tuple[tuple[tuple[int, int, int], ...], int]]:
    """
    Every distinct legal placement of `dice` under §10-11.

    Each die goes to an available function or, while a function is
    overloaded, to the escape valve (one slot). Dice that share a function
    must share a value, and at most three fit in a function. Placements that
    fill the same cells with the same values are the same move.
    """
    functions = [f for f in (0, 1, 2) if f not in unavailable]
    targets = functions + ([VALVE] if unavailable else [])
    seen: set = set()
    result = []
    for assign in product(targets, repeat=len(dice)):
        if assign.count(VALVE) > 1:
            continue
        rows: dict[int, list[int]] = {0: [], 1: [], 2: []}
        valve = 0
        for die, target in zip(dice, assign):
            if target == VALVE:
                valve = die
            else:
                rows[target].append(die)
        if any(len(v) > 3 or len(set(v)) > 1 for v in rows.values()):
            continue
        matrix = tuple(
            tuple(rows[r] + [0] * (3 - len(rows[r]))) for r in (0, 1, 2)
        )
        key = (matrix, valve)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def interesting_centuries(traveler: TravelerState, game: GameState) -> set[int]:
    """
    Centuries worth stopping on exactly: deliveries, the Merchant, the
    millennium checkpoints not yet scored (§8.1c) and the Secret Market on XI.
    """
    marks = {c.delivery_century for c in traveler.hand if c.delivery_century is not None}
    marks.add(game.merchant_century)
    for century, scored in zip(MILLENNIUM_CENTURIES,
                               (traveler.scored_century_x, traveler.scored_century_xx)):
        if not scored:
            marks.add(century)
    if game.secret_market_card_count > 0:
        marks.add(SECRET_MARKET_CENTURY)
    return marks


def legal_moves(
    traveler: TravelerState,
    game: GameState,
    dice: list[int],
) -> list[Move]:
    """
    Every legal move for this Hour.

    For a placement that travels, both directions are offered, each with no
    cap and with a cap that stops exactly on a century worth stopping on.
    """
    marks = interesting_centuries(traveler, game)
    moves: list[Move] = []
    for matrix, valve in legal_allocations(dice, traveler.overloaded_functions):
        steps = max_travel(matrix)
        if steps == 0:
            moves.append(Move(matrix, valve, PAST, 0))
            continue
        for direction in (PAST, FUTURE):
            if direction == FUTURE and traveler.century >= CENTURY_MAX:
                continue
            caps: set[int | None] = {None}
            for c in marks:
                distance = (c - traveler.century) * direction
                if 0 < distance < steps:
                    caps.add(distance)
            for cap in caps:
                moves.append(Move(matrix, valve, direction, cap))
    return moves


def move_from_result(result: tuple) -> Move:
    """Turn a heuristic's (allocation, direction[, cap]) into a Move."""
    alloc, direction = result[0], result[1]
    cap = result[2] if len(result) == 3 else None
    matrix = tuple(tuple(alloc.matrix[r]) for r in range(3))
    return Move(matrix, alloc.escape_valve, direction, cap, from_base=True)


# ---------------------------------------------------------------------------
# The market
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketOption:
    """One thing the traveler can do at the market right now."""
    kind: str                 # one of MARKET_KINDS
    card: object = None       # the Card bought, renewed or used
    cost: int = 0
    secret: bool = False      # True at the Secret Market
    action: object = None     # the engine action this option stands for


def market_options(traveler: TravelerState, game: GameState, revealed: list,
                   renew_cost: int, heuristic_action) -> tuple[list[MarketOption], int]:
    """
    Every legal market option, and the index of the one the heuristic chose.

    At the Merchant: buy any revealed card the traveler can pay for and carry,
    renew any revealed card, pay off Wanted (§18.3), or pass. At the Secret
    Market (§19) there is only the one card on top: buy it or pass. A card the
    heuristic wants to use from hand is offered too.
    """
    secret = renew_cost >= SECRET_RENEW_COST
    options = [MarketOption("pass", secret=secret, action=PassAction())]
    for card in revealed:
        cost = effective_card_cost(card, traveler)
        if traveler.gold >= cost and traveler.can_hold(card):
            options.append(MarketOption("buy", card, cost, secret, BuyAction(card)))
    if not secret:
        if traveler.gold >= renew_cost:
            for card in revealed:
                options.append(MarketOption("renew", card, renew_cost, False, RenewAction(card)))
        if traveler.is_wanted and traveler.gold >= DECLARE_COST:
            options.append(MarketOption("declare", None, DECLARE_COST, False, DeclareAction()))

    chosen = 0
    h = heuristic_action
    for i, o in enumerate(options):
        if type(o.action) is type(h) and getattr(o.action, "card", None) is getattr(h, "card", None):
            chosen = i
            break
    else:
        if isinstance(h, UseCardAction):
            options.append(MarketOption("use", h.card, 0, secret, h))
            chosen = len(options) - 1
    return options, chosen

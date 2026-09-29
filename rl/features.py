"""
rl/features.py
==============
What the networks see.

Every decision is scored the same way: the position, which is the same for
every option, next to the option itself. All numbers are scaled to roughly
[-1, 1].

    state_features(traveler, game)            the table from this seat
    move_features(traveler, game, move)       a dice move, its outcome estimated
    market_features(traveler, game, option)   a market option: buy, renew, pass...
    reward_features(category)                 a reward category

The dice estimate follows the resolution order of §12 for the traveler's own
modules (Recharge, the escape valve, heating, travel) without touching the
game. It ignores card hooks and the paradoxes other travelers throw this Hour,
which is part of what the network has to learn to live with.

Nothing here says which option the heuristic would pick. The network has to
judge an option by what it does, not by who suggested it.
"""

from __future__ import annotations
import numpy as np

from engine.cards import TIEBREAK_ORDER
from engine.state import TravelerState, GameState
from engine.timeline import periods_for_century
from engine.constants import (
    BOOM_LIMIT,
    CENTURY_MAX,
    CP_YEAR_ZERO_TOTAL,
    OVERDRIVE_THRESHOLD_CENTURY,
    OVERDRIVE_ENERGY_COST_PER_CENTURY,
    PAST_TRAVEL_ENERGY_COST_PER_CENTURY,
    FUNCTION_RECHARGE,
    FUNCTION_TRAVEL,
    SECRET_MARKET_CENTURY,
)
from rl.actions import Move, MarketOption, PERIOD_NAMES, REWARD_CATEGORIES, MARKET_KINDS

# The card inputs keep the order the trained weights were fitted to (the old
# alphabetical order of the original card names), whatever the names are now.
CARD_NAMES = list(TIEBREAK_ORDER)
CARD_INDEX = {n: i for i, n in enumerate(CARD_NAMES)}
ABILITY_TYPES = ["active", "passive", "atemporal_active", "atemporal_passive"]

STATE_NAMES = [
    "players at the table", "energy", "gold", "century", "contract points", "booms",
    "hand size", "equipment full", "Recharge overloaded", "Paradox overloaded",
    "Travel overloaded", "Terminated", "Wanted", "hour", "Merchant offset",
    "Merchant cards left", "deliverable here", "nearest delivery below",
    "nearest delivery above", "Origins delivered", "Ascension delivered",
    "Singularity delivered", "best rival CP", "my CP margin", "rivals mean energy",
    "lowest rival century", "share of rivals below", "share of rivals above",
    "share of rivals on my century", "Secret Market open", "Secret Market cards left",
    "market voucher", "scored XX", "scored X",
]
MOVE_NAMES = [
    "m1 energy", "m2 gold", "m3 energy+gold", "m4 paradox future", "m5 paradox present",
    "m6 paradox past", "m7 heating", "m8 travel", "m9 double travel",
    "escape valve", "direction", "centuries travelled", "landing century",
    "lands on Year Zero", "Year Zero wins", "deliveries on landing", "Merchant on landing",
    "nearest delivery from landing", "energy after", "gold gained", "booms after",
    "motor explodes", "overloads Recharge", "overloads Paradox", "overloads Travel",
    "lands on XX unscored", "lands on X unscored", "lands on XI",
]
MARKET_NAMES = (
    [f"kind {k}" for k in MARKET_KINDS]
    + [f"card {n}" for n in CARD_NAMES]
    + ["cost", "gold after", "delivery offset", "delivery distance", "delivery period open",
       "recycle value", "large item"]
    + [f"ability {a}" for a in ABILITY_TYPES]
    + ["Secret Market", "slots left after"]
)
REWARD_NAMES = [f"category {c}" for c in REWARD_CATEGORIES]

STATE_SIZE = len(STATE_NAMES)
MOVE_SIZE = len(MOVE_NAMES)
MARKET_SIZE = len(MARKET_NAMES)
REWARD_SIZE = len(REWARD_NAMES)
OPTION_SIZES = {"dice": MOVE_SIZE, "market": MARKET_SIZE, "reward": REWARD_SIZE}


def _nearest(distances: list[int]) -> float:
    return min(distances) / CENTURY_MAX if distances else 1.0


def _rivals(traveler: TravelerState, game: GameState) -> list[TravelerState]:
    return [t for t in game.travelers if t.name != traveler.name]


def state_features(traveler: TravelerState, game: GameState) -> np.ndarray:
    rivals = _rivals(traveler, game)
    n_rivals = max(len(rivals), 1)
    here = traveler.century
    deliveries = [c.delivery_century for c in traveler.hand if c.delivery_century is not None]
    rival_cp = max((r.contract_points for r in rivals), default=0)
    f = [
        len(game.travelers) / 6,
        traveler.energy / 20,
        traveler.gold / 10,
        here / CENTURY_MAX,
        traveler.contract_points / 10,
        traveler.booms / BOOM_LIMIT,
        len(traveler.hand) / 5,
        float(traveler.equipment_full()),
        float(0 in traveler.overloaded_functions),
        float(1 in traveler.overloaded_functions),
        float(2 in traveler.overloaded_functions),
        float(traveler.is_terminated),
        float(traveler.is_wanted),
        game.hour / 60,
        (game.merchant_century - here) / CENTURY_MAX,
        game.merchant_card_count / 40,
        sum(1 for d in deliveries if d == here) / 3,
        _nearest([here - d for d in deliveries if d < here]),
        _nearest([d - here for d in deliveries if d > here]),
    ] + [float(p in traveler.delivered_periods) for p in PERIOD_NAMES] + [
        rival_cp / 10,
        (traveler.contract_points - rival_cp) / 10,
        np.mean([r.energy for r in rivals]) / 20 if rivals else 0.0,
        min((r.century for r in rivals), default=CENTURY_MAX) / CENTURY_MAX,
        sum(1 for r in rivals if r.century < here) / n_rivals,
        sum(1 for r in rivals if r.century > here) / n_rivals,
        sum(1 for r in rivals if r.century == here) / n_rivals,
        float(game.secret_market_open),
        game.secret_market_card_count / 12,
        float(traveler.market_voucher > 0),
        float(traveler.scored_century_xx),
        float(traveler.scored_century_x),
    ]
    return np.asarray(f, dtype=np.float64)


def _past_travel_cost(start: int, steps: int) -> int:
    cost = 0
    for i in range(steps):
        pos = start - i
        cost += (OVERDRIVE_ENERGY_COST_PER_CENTURY if pos <= OVERDRIVE_THRESHOLD_CENTURY
                 else PAST_TRAVEL_ENERGY_COST_PER_CENTURY)
    return cost


def move_features(traveler: TravelerState, game: GameState, move: Move) -> np.ndarray:
    m = move.matrix
    rec = m[FUNCTION_RECHARGE]
    energy = traveler.energy + rec[0] + rec[2] - move.valve
    gold_gain = rec[1] + rec[2]

    heat = m[FUNCTION_TRAVEL][0]
    booms = traveler.booms + heat
    explodes = booms >= BOOM_LIMIT
    if explodes:
        energy -= 2
        booms -= BOOM_LIMIT

    steps = 0 if explodes else move.travel_steps
    if move.cap is not None:
        steps = min(steps, move.cap)
    here = traveler.century
    if move.direction < 0:
        steps = min(steps, here)
        # §30.1: past travel stops short rather than spend the last energy,
        # except onto Year Zero, which ends the game.
        while steps > 0 and steps < here and _past_travel_cost(here, steps) >= energy:
            steps -= 1
        energy -= _past_travel_cost(here, steps)
        landing = here - steps
    else:
        steps = min(steps, CENTURY_MAX - here)
        landing = here + steps

    deliveries = [c.delivery_century for c in traveler.hand if c.delivery_century is not None]
    rival_cp = max((r.contract_points for r in _rivals(traveler, game)), default=0)
    year_zero = landing <= 0
    f = [v / 3 for row in m for v in row] + [
        move.valve / 3,
        float(move.direction),
        steps / 12,
        landing / CENTURY_MAX,
        float(year_zero),
        float(year_zero and traveler.contract_points + CP_YEAR_ZERO_TOTAL > rival_cp),
        sum(1 for d in deliveries if d == landing) / 3,
        float(landing == game.merchant_century),
        _nearest([abs(landing - d) for d in deliveries if d != landing]),
        energy / 20,
        gold_gain / 6,
        booms / BOOM_LIMIT,
        float(explodes),
        float(all(m[0])),
        float(all(m[1])),
        float(all(m[2])),
        float(landing == 20 and not traveler.scored_century_xx),
        float(landing == 10 and not traveler.scored_century_x),
        float(landing == SECRET_MARKET_CENTURY),
    ]
    return np.asarray(f, dtype=np.float64)


def market_features(traveler: TravelerState, game: GameState, option: MarketOption) -> np.ndarray:
    kind = [float(option.kind == k) for k in MARKET_KINDS]
    card_vec = [0.0] * len(CARD_NAMES)
    numbers = [0.0] * 7
    ability = [0.0] * len(ABILITY_TYPES)
    card = option.card
    numbers[0] = option.cost / 5
    numbers[1] = (traveler.gold - option.cost) / 10
    if card is not None:
        if card.name in CARD_INDEX:
            card_vec[CARD_INDEX[card.name]] = 1.0
        delivery = card.delivery_century or 0
        periods = periods_for_century(delivery) if delivery else []
        numbers[2:] = [
            (delivery - traveler.century) / CENTURY_MAX,
            abs(delivery - traveler.century) / CENTURY_MAX,
            float(any(p not in traveler.delivered_periods for p in periods)),
            card.recycle_value / 3,
            float(card.is_large_item),
        ]
        if card.ability_type in ABILITY_TYPES:
            ability[ABILITY_TYPES.index(card.ability_type)] = 1.0
    slots_after = (0.0 if traveler.equipment_full() else 1.0) - float(option.kind == "buy")
    f = kind + card_vec + numbers + ability + [float(option.secret), slots_after]
    return np.asarray(f, dtype=np.float64)


def reward_features(category: str) -> np.ndarray:
    return np.asarray([float(category == c) for c in REWARD_CATEGORIES], dtype=np.float64)

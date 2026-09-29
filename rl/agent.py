"""
rl/agent.py
===========
The learned player.

It is built on a heuristic player and takes over three of its decisions,
each with its own network (a "head"):

    dice     every Hour: where the four generators go, which way, how far
    market   every market call: which card to buy, renew one, pay off Wanted, pass
    reward   every Reward Phase: Chaos, Time or Resource

For each decision it lists every legal option (rl/actions.py), describes each
one next to the position (rl/features.py), scores them all with that head and
picks one: the best in play, a sample from the probabilities in training.
Card activations and the targets of rewards stay with the heuristic.

The heuristic's own choice is always one of the options. With follow_base the
player plays it and records it, which is how the imitation phase collects its
examples.
"""

from __future__ import annotations
import numpy as np

from simulation.strategies.base import Strategy
from rl.actions import legal_moves, move_from_result, market_options, REWARD_CATEGORIES
from rl.features import state_features, move_features, market_features, reward_features
from rl.network import MLP

HEADS = ("dice", "market", "reward")
MAX_MARKET_STEPS = 12  # a market call that goes on longer than this passes


def softmax(x: np.ndarray) -> np.ndarray:
    z = x - x.max()
    e = np.exp(z)
    return e / e.sum()


def make_learned_class(base_cls: type[Strategy]) -> type[Strategy]:
    """A subclass of `base_cls` whose dice, market and reward come from the heads."""

    class Learned(base_cls):  # type: ignore[misc, valid-type]
        def __init__(self, heads: dict[str, MLP], greedy: bool = True,
                     rng: np.random.Generator | None = None, record: bool = False,
                     follow_base: bool = False):
            super().__init__()
            self.heads = heads
            self.greedy = greedy
            self.np_rng = rng or np.random.default_rng()
            self.record = record
            self.follow_base = follow_base
            self.trace: list[tuple[str, np.ndarray, np.ndarray, int, int]] = []
            self._market_hour = -1
            self._market_steps = 0
            self._used_this_market: set[str] = set()

        @property
        def name(self) -> str:
            return f"Learned({base_cls.__name__.replace('Strategy', '')})"

        # -- one decision -------------------------------------------------

        def _decide(self, head: str, s: np.ndarray, options_x: np.ndarray, base_index: int) -> int:
            if self.follow_base or head not in self.heads:
                choice = base_index
            else:
                x = np.hstack([np.repeat(s[None, :], len(options_x), axis=0), options_x])
                scores = self.heads[head](x)[:, 0]
                if self.greedy:
                    choice = int(np.argmax(scores))
                else:
                    choice = int(self.np_rng.choice(len(scores), p=softmax(scores)))
            if self.record and len(options_x) > 1:
                self.trace.append((head, s, options_x, choice, base_index))
            return choice

        # -- the dice -----------------------------------------------------

        def choose_allocation(self, traveler, game, dice):
            base_result = super().choose_allocation(traveler, game, dice)
            base_move = move_from_result(base_result)
            key = lambda m: (m.matrix, m.valve, m.direction, m.cap)

            moves = legal_moves(traveler, game, dice)
            index = {key(m): i for i, m in enumerate(moves)}
            if key(base_move) in index:
                base_index = index[key(base_move)]
                moves[base_index] = base_move
            else:
                moves.append(base_move)
                base_index = len(moves) - 1

            s = state_features(traveler, game)
            ox = np.stack([move_features(traveler, game, m) for m in moves])
            move = moves[self._decide("dice", s, ox, base_index)]
            if move.from_base:
                return base_result
            return move.to_allocation(), move.direction, move.cap

        # -- the market ---------------------------------------------------

        def choose_market_action(self, traveler, game, revealed, renew_cost):
            if game.hour != self._market_hour:
                self._market_hour = game.hour
                self._market_steps = 0
                self._used_this_market = set()
            self._market_steps += 1
            base_action = super().choose_market_action(traveler, game, revealed, renew_cost)
            if self._market_steps > MAX_MARKET_STEPS:
                from engine.market import PassAction
                return PassAction()

            options, base_index = market_options(traveler, game, revealed, renew_cost, base_action)
            base_option = options[base_index]
            # A card is used at most once per market phase, so a reusable
            # active can never keep the phase going forever.
            options = [o for o in options
                       if not (o.kind == "use" and o.card.name in self._used_this_market)]
            base_index = options.index(base_option) if base_option in options else 0
            s = state_features(traveler, game)
            ox = np.stack([market_features(traveler, game, o) for o in options])
            option = options[self._decide("market", s, ox, base_index)]
            if option.kind == "use":
                self._used_this_market.add(option.card.name)
            return option.action

        # -- the reward ---------------------------------------------------

        def choose_reward_category(self, traveler, game, available):
            base_choice = super().choose_reward_category(traveler, game, available)
            options = [c for c in REWARD_CATEGORIES if c in available]
            base_index = options.index(base_choice) if base_choice in options else 0
            s = state_features(traveler, game)
            ox = np.stack([reward_features(c) for c in options])
            return options[self._decide("reward", s, ox, base_index)]

    Learned.__name__ = f"Learned{base_cls.__name__}"
    return Learned

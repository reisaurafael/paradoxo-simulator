"""The two special cards: under the Secret Market's
twelve, The Divine Comedy 13th and Oppenheimer's Trinity always last."""
import random

from engine.cards import card_by_name, rolls_threes, holder_dice
from engine.market import MerchantDeck
from engine.state import GameState
from simulation.strategies import util as bot_util

TRINITY, COMEDY = "Oppenheimer's Trinity", "The Divine Comedy"


def _table(*centuries, energy=24):
    game = GameState.create([chr(65 + i) for i in range(len(centuries))])
    for t, c in zip(game.travelers, centuries):
        t.century, t.energy, t.booms = c, energy, 0
        t.hand.clear()
    return game, game.travelers


def test_order_trinity_last():
    for seed in range(10):
        names = [c.name for c in MerchantDeck(rng=random.Random(seed)).secret_market._hidden]
        assert names[12:] == [COMEDY, TRINITY] and len(names) == 14


def test_trinity_era_blast():
    game, (a, b, c) = _table(28, 12, 20)
    card_by_name(TRINITY).active_effect(a, game, "Low Middle Ages")
    assert (b.energy, b.booms) == (4, 5) and (c.energy, a.energy) == (24, 24)


def test_trinity_hits_its_user_and_explodes_at_twelve():
    game, (a, b) = _table(21, 22, energy=30)
    b.booms = 8
    card_by_name(TRINITY).active_effect(a, game, "Contemporary")
    assert a.energy == 10 and a.booms == 5
    assert b.energy == 8 and b.booms == 1 and b.exploded_this_hour


def test_comedy_dice():
    game, (a, b) = _table(20, 20)
    a.hand.append(card_by_name(COMEDY))
    assert rolls_threes(a, game) and not rolls_threes(b, game)
    assert holder_dice(a, [1, 2, 1, 2], game) == [3, 3, 3, 3]
    assert holder_dice(b, [1, 2, 1, 2], game) == [1, 2, 1, 2]


def test_bots_aim_and_buy():
    game, (a, b, c) = _table(22, 12, 13, energy=15)
    assert bot_util.trinity_era(a, game) == "Low Middle Ages"
    a.gold = 5
    assert bot_util.special_buy(a, [card_by_name(COMEDY)]).name == COMEDY

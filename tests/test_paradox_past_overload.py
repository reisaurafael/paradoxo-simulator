"""An overloaded Paradox (three dice in the row) makes its
PAST module (6) deal DOUBLE its die, like module 9's double travel. The function still
shuts for the next Hour; Future and Present are unchanged; damage modifiers apply to
the doubled hit. The profiles read the new value (simulation/strategies/util.py)."""
import pytest

from engine.cards import ALL_CARDS
from engine.constants import FUNCTION_PARADOX, PARADOX_PAST_MULTIPLIER
from engine.paradox import resolve_paradox_pool, module_damage
from engine.resolve import apply_overload_markers
from engine.state import Allocation, GameState
from engine.matrix import validate_allocation
from simulation.strategies import util as bot_util
from simulation.strategies.aggressive import AggressiveStrategy
from simulation.strategies.conservative import ConservativeStrategy
from simulation.strategies.collector import CollectorStrategy
from simulation.strategies.smart import SmartStrategy


def _card(name):
    return next(c for c in (f() for f in ALL_CARDS) if c.name == name)


def _table():
    game = GameState.create(["A", "Ahead", "Beside", "Behind"])
    a, ahead, beside, behind = game.travelers
    a.century, ahead.century, beside.century, behind.century = 20, 25, 20, 15
    for t in game.travelers:
        t.energy = 20
        t.hand.clear()
    return game, a, ahead, beside, behind


def _row(*dice):
    alloc = Allocation.empty()
    for col, v in enumerate(dice):
        alloc.set(FUNCTION_PARADOX, col, v)
    return alloc


def _resolve(game, allocs, cols=(0, 1, 2)):
    for col in cols:
        resolve_paradox_pool(game, list(game.travelers), allocs, col)


def test_the_multiplier_is_two():
    assert PARADOX_PAST_MULTIPLIER == 2


@pytest.mark.parametrize("v", [1, 2, 3])
def test_an_overloaded_paradox_hits_the_past_for_double(v):
    game, a, ahead, beside, behind = _table()
    _resolve(game, {"A": _row(v, v, v)})
    assert behind.energy == 20 - 2 * v
    assert ahead.energy == 20 - v and beside.energy == 20 - v and a.energy == 20


@pytest.mark.parametrize("v", [1, 2, 3])
def test_without_the_third_die_nothing_changes(v):
    game, a, ahead, beside, behind = _table()
    _resolve(game, {"A": _row(v, v)})
    assert behind.energy == 20 and ahead.energy == 20 - v


def test_the_function_still_shuts():
    game, a, *_ = _table()
    alloc = _row(3, 3, 3)
    apply_overload_markers(a, alloc, game)
    assert FUNCTION_PARADOX in a.overloaded_next


def test_buffs_then_double():
    game, a, *_ = _table()
    alloc = _row(2, 2, 2)
    assert module_damage(a, alloc, 2) == 4
    a.hand.append(_card("Automobile"))
    assert module_damage(a, alloc, 2) == 6 and module_damage(a, alloc, 0) == 3


def test_modifiers_apply_to_the_doubled_hit():
    game, a, ahead, beside, behind = _table()
    a.hand.append(_card("Gunpowder"))
    _resolve(game, {"A": _row(3, 3, 3)}, cols=(2,))
    assert behind.energy == 13
    game, a, ahead, beside, behind = _table()
    behind.hand.append(_card("Laser Sword"))
    _resolve(game, {"A": _row(3, 3, 3)}, cols=(2,))
    assert behind.energy == 14 and a.energy == 14


def test_spear_of_destiny_is_not_doubled():
    game, a, ahead, beside, behind = _table()
    a.hand.append(_card("Spear of Destiny"))
    _resolve(game, {"A": _row(2, 2)}, cols=(0,))
    assert behind.energy == 18


@pytest.mark.parametrize("strategy", [AggressiveStrategy, ConservativeStrategy,
                                      CollectorStrategy, SmartStrategy])
def test_every_profile_takes_a_kill_the_doubled_past_makes(strategy):
    game, a, ahead, beside, behind = _table()
    behind.energy, a.energy = 6, 15
    dice = [3, 3, 3, 1]
    alloc = strategy().choose_allocation(a, game, dice)[0]
    assert alloc.matrix[FUNCTION_PARADOX] == [3, 3, 3]
    assert not validate_allocation(alloc, dice, a.overloaded_functions)


def test_smart_bleeds_the_leader_and_the_switch_turns_it_off(monkeypatch):
    game, a, ahead, beside, behind = _table()
    behind.energy = a.energy = 15
    assert bot_util.past_overload_value(a, game, [2, 2, 2, 3]) is None
    assert bot_util.past_overload_value(a, game, [2, 2, 2, 3], bleed_leader=True) == 2
    monkeypatch.setattr(bot_util, "PAST_OVERLOAD_AWARE", False)
    assert bot_util.past_overload_value(a, game, [2, 2, 2, 3], bleed_leader=True) is None


def test_a_terminated_travelers_past_is_dead():
    game, a, ahead, beside, behind = _table()
    a.is_terminated = True
    _resolve(game, {"A": _row(3, 3, 3)})
    assert behind.energy == 20 and ahead.energy == 17
    behind.energy = 4
    assert bot_util.past_overload_value(a, game, [3, 3, 3, 1]) is None

"""
tests/test_matrix.py
=====================
Tests for engine/matrix.py: allocation rules §10-11.

Each test is named for the rule section it validates.
"""

import pytest
from engine.state import Allocation
from engine.matrix import can_place, validate_allocation, functions_overloaded_by
from engine.constants import FUNCTION_RECHARGE, FUNCTION_PARADOX, FUNCTION_TRAVEL


# ---------------------------------------------------------------------------
# can_place: individual placement checks
# ---------------------------------------------------------------------------

def test_first_module_always_valid():
    """§10.3: col 0 is always legal (no predecessor required)."""
    alloc = Allocation.empty()
    ok, msg = can_place(FUNCTION_TRAVEL, 0, 2, alloc, set())
    assert ok, msg

def test_linear_progression_enforced():
    """§10.3: col 1 requires col 0 to be filled."""
    alloc = Allocation.empty()
    ok, msg = can_place(FUNCTION_TRAVEL, 1, 2, alloc, set())
    assert not ok
    assert "§10.3" in msg

def test_linear_progression_satisfied():
    """§10.3: col 1 is legal when col 0 is filled with same value."""
    alloc = Allocation.empty()
    alloc.set(FUNCTION_TRAVEL, 0, 2)
    ok, msg = can_place(FUNCTION_TRAVEL, 1, 2, alloc, set())
    assert ok, msg

def test_one_value_per_function_enforced():
    """§10.2: cannot place value 3 in a function that has value 2."""
    alloc = Allocation.empty()
    alloc.set(FUNCTION_RECHARGE, 0, 2)
    ok, msg = can_place(FUNCTION_RECHARGE, 1, 3, alloc, set())
    assert not ok
    assert "§10.2" in msg

def test_one_value_per_function_same_value_ok():
    """§10.2: same value is legal."""
    alloc = Allocation.empty()
    alloc.set(FUNCTION_RECHARGE, 0, 2)
    ok, msg = can_place(FUNCTION_RECHARGE, 1, 2, alloc, set())
    assert ok, msg

def test_unavailable_function_blocked():
    """§11.1: cannot place in an overloaded (unavailable) function."""
    alloc = Allocation.empty()
    ok, msg = can_place(FUNCTION_TRAVEL, 0, 2, alloc, {FUNCTION_TRAVEL})
    assert not ok
    assert "overloaded" in msg.lower()

def test_already_occupied_blocked():
    """Cannot place in a cell that already holds a generator."""
    alloc = Allocation.empty()
    alloc.set(FUNCTION_RECHARGE, 0, 1)
    ok, msg = can_place(FUNCTION_RECHARGE, 0, 1, alloc, set())
    assert not ok


# ---------------------------------------------------------------------------
# Overload detection
# ---------------------------------------------------------------------------

def test_overload_detected_when_full():
    """§11.1: function with all 3 modules filled is overloaded."""
    alloc = Allocation.empty()
    alloc.set(FUNCTION_TRAVEL, 0, 3)
    alloc.set(FUNCTION_TRAVEL, 1, 3)
    alloc.set(FUNCTION_TRAVEL, 2, 3)
    overloaded = functions_overloaded_by(alloc)
    assert FUNCTION_TRAVEL in overloaded

def test_no_overload_with_two_modules():
    """Two generators in a function do not overload it."""
    alloc = Allocation.empty()
    alloc.set(FUNCTION_TRAVEL, 0, 2)
    alloc.set(FUNCTION_TRAVEL, 1, 2)
    overloaded = functions_overloaded_by(alloc)
    assert FUNCTION_TRAVEL not in overloaded

def test_multiple_overloads_detected():
    """Two functions can be overloaded simultaneously."""
    alloc = Allocation.empty()
    for col in range(3):
        alloc.set(FUNCTION_RECHARGE, col, 1)
        alloc.set(FUNCTION_PARADOX, col, 2)
    overloaded = functions_overloaded_by(alloc)
    assert FUNCTION_RECHARGE in overloaded
    assert FUNCTION_PARADOX in overloaded


# ---------------------------------------------------------------------------
# validate_allocation: full §10 + §11 check
# ---------------------------------------------------------------------------

def _make_alloc(placements: list[tuple[int, int, int]], escape: int = 0) -> Allocation:
    """Helper: build an Allocation from (row, col, value) tuples."""
    alloc = Allocation.empty()
    for row, col, val in placements:
        alloc.set(row, col, val)
    alloc.escape_valve = escape
    return alloc

def test_valid_allocation_no_errors():
    """A legal 4-die allocation produces no errors."""
    # Dice: [2, 2, 3, 3]
    # Travel: [3, 3] in cols 0,1; Recharge: [2, 2] in cols 0,1
    dice = [2, 2, 3, 3]
    alloc = _make_alloc([
        (FUNCTION_TRAVEL, 0, 3),
        (FUNCTION_TRAVEL, 1, 3),
        (FUNCTION_RECHARGE, 0, 2),
        (FUNCTION_RECHARGE, 1, 2),
    ])
    errors = validate_allocation(alloc, dice, set())
    assert errors == [], f"Unexpected errors: {errors}"

def test_escape_valve_requires_unavailable_function():
    """§11.2: escape valve illegal when no function is overloaded."""
    dice = [1, 2, 2, 3]
    alloc = _make_alloc([
        (FUNCTION_TRAVEL, 0, 2),
        (FUNCTION_TRAVEL, 1, 2),
        (FUNCTION_RECHARGE, 0, 3),
    ], escape=1)
    errors = validate_allocation(alloc, dice, set())  # no unavailable functions
    assert any("§11.2" in e for e in errors)

def test_escape_valve_legal_with_unavailable_function():
    """§11.2: escape valve is legal when a function is unavailable."""
    # Travel is unavailable; dice [1,2,2,3]; place 2,2 in Recharge, 3 in Paradox, 1 to escape
    dice = [1, 2, 2, 3]
    alloc = _make_alloc([
        (FUNCTION_RECHARGE, 0, 2),
        (FUNCTION_RECHARGE, 1, 2),
        (FUNCTION_PARADOX, 0, 3),
    ], escape=1)
    errors = validate_allocation(alloc, dice, {FUNCTION_TRAVEL})
    assert errors == [], f"Unexpected errors: {errors}"

def test_section_11_example_from_rules_reference():
    """
    Exact example from Rules Reference §11.2:
    Travel unavailable, dice = [3,3,3,2].
    Legal play: III in module 1 and III in module 2 (Recharge),
    III in module 4 (Paradox), II to escape valve.

    Module 1 = Recharge col 0, Module 2 = Recharge col 1,
    Module 4 = Paradox col 0.
    """
    dice = [2, 3, 3, 3]  # sorted
    alloc = _make_alloc([
        (FUNCTION_RECHARGE, 0, 3),   # module 1
        (FUNCTION_RECHARGE, 1, 3),   # module 2
        (FUNCTION_PARADOX, 0, 3),    # module 4
    ], escape=2)
    errors = validate_allocation(alloc, dice, {FUNCTION_TRAVEL})
    assert errors == [], f"Unexpected errors: {errors}"

def test_incomplete_allocation_detected():
    """§10.1: not all dice allocated is an error."""
    dice = [1, 2, 2, 3]
    alloc = _make_alloc([
        (FUNCTION_TRAVEL, 0, 2),
        (FUNCTION_TRAVEL, 1, 2),
        # Only 2 of 4 dice placed
    ])
    errors = validate_allocation(alloc, dice, set())
    assert any("§10.1" in e for e in errors)

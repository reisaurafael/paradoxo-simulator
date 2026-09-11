"""
engine/matrix.py
================
Matrix allocation validation and overload detection for Paradoxo.

This module enforces the three allocation rules from §10 and the overload
and escape valve mechanics from §11. It contains no strategy logic:
it only answers whether a proposed placement is legal and what its
consequences are.

Rules implemented:
    §10.1  Full load: all four generators must always be allocated.
    §10.2  One value per function: every generator in the same function
           must share the same value.
    §10.3  Linear progression: within a function, module N may only
           receive a generator if module N-1 already holds one.
    §11.1  Overload: placing 3 generators in one function overloads it;
           the function is unavailable for the next Hour.
    §11.2  Escape valve: during an Hour with an unavailable function, a
           generator may be sent to the escape valve, costing its value
           in energy. Optional; does not require any module to be full.
"""

from engine.constants import (
    GENERATORS_FOR_OVERLOAD,
    FUNCTION_RECHARGE,
    FUNCTION_PARADOX,
    FUNCTION_TRAVEL,
    FUNCTION_NAMES,
)
from engine.state import Allocation


# ---------------------------------------------------------------------------
# Placement validation
# ---------------------------------------------------------------------------

def can_place(
    row: int,
    col: int,
    value: int,
    allocation: Allocation,
    unavailable_functions: set[int],
) -> tuple[bool, str]:
    """
    Check whether placing `value` at (row, col) is legal under §10-11.

    Args:
        row:                    Function index (0=Recharge, 1=Paradox, 2=Travel).
        col:                    Module index within the function (0, 1, or 2).
        value:                  Generator value to place (1, 2, or 3).
        allocation:             The Allocation being built for this Hour.
        unavailable_functions:  Set of function indices unavailable this Hour (§11.1).

    Returns:
        (True, "") if the placement is legal.
        (False, reason) if it violates a rule.
    """
    # Unavailable function: cannot place here (§11.1)
    if row in unavailable_functions:
        fn_name = FUNCTION_NAMES.get(row, f"function {row}")
        return False, f"{fn_name} is overloaded and unavailable this Hour (§11.1)"

    # Cell already occupied
    if allocation.get(row, col) != 0:
        return False, f"Module ({row},{col}) is already occupied"

    # §10.2: one value per function
    existing = allocation.generators_in_function(row)
    if existing and existing[0] != value:
        return (
            False,
            f"§10.2 violated: function already contains value {existing[0]}, "
            f"cannot place value {value}",
        )

    # §10.3: linear progression: col > 0 requires col-1 to be filled
    if col > 0 and allocation.get(row, col - 1) == 0:
        return (
            False,
            f"§10.3 violated: module ({row},{col-1}) must be filled before ({row},{col})",
        )

    return True, ""


def can_use_escape_valve(unavailable_functions: set[int]) -> bool:
    """
    The escape valve is only available when at least one function is
    unavailable this Hour (§11.2).
    """
    return len(unavailable_functions) > 0


# ---------------------------------------------------------------------------
# Overload detection
# ---------------------------------------------------------------------------

def functions_overloaded_by(allocation: Allocation) -> set[int]:
    """
    Return the set of function indices that were overloaded by this allocation.

    A function is overloaded when all 3 of its modules hold a generator (§11.1).
    These functions will be unavailable next Hour.
    """
    overloaded = set()
    for row in (FUNCTION_RECHARGE, FUNCTION_PARADOX, FUNCTION_TRAVEL):
        if allocation.is_overloaded(row):
            overloaded.add(row)
    return overloaded


# ---------------------------------------------------------------------------
# Full allocation validation
# ---------------------------------------------------------------------------

def validate_allocation(
    allocation: Allocation,
    dice: list[int],
    unavailable_functions: set[int],
) -> list[str]:
    """
    Validate a complete allocation against all §10-11 rules.

    This is used by the simulation runner to catch bugs in strategy
    implementations before they corrupt game state.

    Args:
        allocation:             The completed Allocation for this Hour.
        dice:                   The 4 generator values rolled this Hour.
        unavailable_functions:  Functions unavailable this Hour.

    Returns:
        A list of rule-violation strings. Empty list = allocation is legal.
    """
    errors: list[str] = []

    # §10.1: all generators must be allocated
    placed_values: list[int] = []
    for row in range(3):
        placed_values.extend(allocation.generators_in_function(row))
    if allocation.escape_valve > 0:
        placed_values.append(allocation.escape_valve)

    # We can't match individual dice exactly since dice may repeat, but we
    # can check the multiset: sorted placed values must equal sorted dice.
    if sorted(placed_values) != sorted(dice):
        errors.append(
            f"§10.1 violated: dice {sorted(dice)} not fully allocated; "
            f"placed {sorted(placed_values)}"
        )

    # §11.2: escape valve only legal when a function is unavailable
    if allocation.escape_valve > 0 and not can_use_escape_valve(unavailable_functions):
        errors.append(
            "§11.2 violated: escape valve used when no function is unavailable"
        )

    # §10.2 and §10.3: validate each cell
    for row in range(3):
        if row in unavailable_functions:
            # Any placement in an unavailable function is illegal
            if any(v != 0 for v in allocation.matrix[row]):
                fn_name = FUNCTION_NAMES.get(row, f"function {row}")
                errors.append(
                    f"§11.1 violated: generator placed in unavailable {fn_name}"
                )
            continue

        cells = allocation.matrix[row]
        # Check for gaps (§10.3) and value consistency (§10.2)
        saw_value: int | None = None
        seen_empty = False
        for col, v in enumerate(cells):
            if v == 0:
                seen_empty = True
            else:
                if seen_empty:
                    errors.append(
                        f"§10.3 violated: gap before ({row},{col}), "
                        "modules must be filled left to right"
                    )
                if saw_value is not None and saw_value != v:
                    errors.append(
                        f"§10.2 violated: mixed values {saw_value} and {v} "
                        f"in function {FUNCTION_NAMES.get(row, row)}"
                    )
                saw_value = v

    return errors


# ---------------------------------------------------------------------------
# Convenience constructors for common allocations
# ---------------------------------------------------------------------------

def place(allocation: Allocation, row: int, col: int, value: int) -> None:
    """Place a generator in the allocation. Mutates in place."""
    allocation.set(row, col, value)


def send_to_escape_valve(allocation: Allocation, value: int) -> None:
    """Send a generator to the escape valve. Mutates in place."""
    allocation.escape_valve = value

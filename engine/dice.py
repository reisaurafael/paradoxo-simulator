"""
engine/dice.py
==============
Generator rolling for Paradoxo.

Generators are three-faced dice with values I, II, III (represented as
integers 1, 2, 3). Each traveler rolls 4 generators per Phase 3 (§4.2, §10.1).
"""

import random
from engine.constants import N_GENERATORS, GENERATOR_FACES


def roll_generators(n: int = N_GENERATORS, rng: random.Random | None = None) -> list[int]:
    """
    Roll n generators and return their values as a sorted list.

    Each generator shows one of {1, 2, 3} with equal probability (§4.2).
    The list is sorted ascending; order has no rules meaning, but sorted
    order makes pattern matching in strategy code more predictable.

    Args:
        n:   Number of generators to roll. Defaults to 4 (§10.1).
        rng: Optional seeded Random instance for reproducible simulations.
             Pass None (default) for non-deterministic play.

    Returns:
        Sorted list of n integers, each in {1, 2, 3}.

    Examples:
        >>> roll_generators()        # e.g. [1, 2, 2, 3]
        >>> roll_generators(rng=random.Random(42))   # deterministic
    """
    source = rng if rng is not None else random
    return sorted(source.choices(GENERATOR_FACES, k=n))


def count_faces(dice: list[int]) -> dict[int, int]:
    """
    Count occurrences of each face value in a dice roll.

    Returns a dict mapping face value → count for all faces in
    GENERATOR_FACES, including faces with count 0.

    Args:
        dice: A list of generator values (each in {1, 2, 3}).

    Returns:
        e.g. {1: 0, 2: 2, 3: 2} for dice [2, 2, 3, 3]

    This is the standard first step in every strategy's allocation logic.
    """
    return {face: dice.count(face) for face in GENERATOR_FACES}


def roll_merchant_movement(n_dice: int, rng: random.Random | None = None) -> int:
    """
    Roll the Merchant's movement generator(s) and return their sum (§17.4-17.6).

    Movement is 1d3 normally, 2d3 after any traveler ends on XX,
    3d3 after any traveler ends on X. The number of dice is tracked
    in GameState.merchant_movement_dice and passed in here.

    Args:
        n_dice: Number of d3 to roll (1, 2, or 3).
        rng:    Optional seeded Random instance.

    Returns:
        Sum of n_dice rolls, each in {1, 2, 3}.
    """
    source = rng if rng is not None else random
    return sum(source.choices(GENERATOR_FACES, k=n_dice))

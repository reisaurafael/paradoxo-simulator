"""
engine/dice.py
==============
Generator rolls.

Generators are three-faced dice showing I, II or III, stored as the integers
1, 2 and 3. Each traveler rolls four of them in Phase 3 of every Hour (§4.2, §10.1).
"""

import random
from engine.constants import N_GENERATORS, GENERATOR_FACES


def roll_generators(n: int = N_GENERATORS, rng: random.Random | None = None) -> list[int]:
    """
    Roll n generators and return their values as a sorted list.

    Each generator shows one of {1, 2, 3} with equal probability (§4.2). The
    order has no meaning in the rules; sorting it just makes the strategies'
    pattern matching simpler.

    Args:
        n:   Number of generators to roll. Defaults to 4 (§10.1).
        rng: Seeded Random instance for reproducible runs, or None for the
             module-level generator.

    Examples:
        >>> roll_generators()                        # e.g. [1, 2, 2, 3]
        >>> roll_generators(rng=random.Random(42))   # deterministic
    """
    source = rng if rng is not None else random
    return sorted(source.choices(GENERATOR_FACES, k=n))


def count_faces(dice: list[int]) -> dict[int, int]:
    """
    Count how many dice show each face, including faces that did not come up.

    For dice [2, 2, 3, 3] this returns {1: 0, 2: 2, 3: 2}. Every strategy starts
    its allocation from this count.
    """
    return {face: dice.count(face) for face in GENERATOR_FACES}

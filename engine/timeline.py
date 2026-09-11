"""
engine/timeline.py
==================
The Paradoxo timeline: board geometry, era and period lookups, and
distance calculations.

All rules here are from §5 of the Rules Reference.

Key facts:
    - The board is a linear track: Year Zero, then centuries I-XXX (§5.1).
    - Travelers start on XXX (§7.1) and travel toward Year Zero.
    - Reaching Year Zero ends the game (§31.1a).
    - Distance is the absolute difference between two positions (§5.3).
    - Year Zero lies one space below century I (§5.3).
    - Era boundaries belong to both adjacent eras (§5.4 note).
"""

from engine.constants import (
    YEAR_ZERO,
    CENTURY_MIN,
    CENTURY_MAX,
    ERAS,
    PERIODS,
    MILLENNIUM_CENTURIES,
)


# ---------------------------------------------------------------------------
# Validity
# ---------------------------------------------------------------------------

def is_valid_position(century: int) -> bool:
    """True if century is a legal position on the board (Year Zero through XXX)."""
    return YEAR_ZERO <= century <= CENTURY_MAX


def clamp_to_board(century: int) -> int:
    """
    Clamp a position to the legal range [YEAR_ZERO, CENTURY_MAX].

    Used after travel to enforce §14.4 (traveler stops at XXX and cannot
    pass it; reaching Year Zero is legal and ends the game).
    """
    return max(YEAR_ZERO, min(CENTURY_MAX, century))


# ---------------------------------------------------------------------------
# Distance (§5.3)
# ---------------------------------------------------------------------------

def distance(a: int, b: int) -> int:
    """
    Absolute distance between two positions on the timeline (§5.3).

    Year Zero lies one space below century I, so distance(0, 1) == 1.
    """
    return abs(a - b)


# ---------------------------------------------------------------------------
# Era lookups (§5.4)
# ---------------------------------------------------------------------------

def eras_for_century(century: int) -> list[str]:
    """
    Return the era(s) that contain this century.

    Boundary centuries belong to both adjacent eras (§5.4).
    Year Zero and centuries outside I-XXX return an empty list.

    Examples:
        eras_for_century(5)   → ["Antiquity", "High Middle Ages"]
        eras_for_century(10)  → ["High Middle Ages", "Low Middle Ages"]
        eras_for_century(7)   → ["High Middle Ages"]
        eras_for_century(0)   → []   (Year Zero has no era)
    """
    if century == YEAR_ZERO or not (CENTURY_MIN <= century <= CENTURY_MAX):
        return []
    return [name for name, (start, end) in ERAS.items() if start <= century <= end]


def period_for_era(era_name: str) -> str | None:
    """
    Return the delivery period that contains the given era name (§5.4).

    Returns None if the era name is not found.
    """
    for period, eras in PERIODS.items():
        if era_name in eras:
            return period
    return None


def periods_for_century(century: int) -> list[str]:
    """
    Return the delivery period(s) associated with this century.

    A century on an era boundary may belong to two periods if the
    two eras are in different periods (e.g. century 15 is the boundary
    between Low Middle Ages [Ascension] and Modern [Ascension], same
    period both sides, so only one result).

    Used when determining which period a delivered card scores for (§23.2).
    """
    result = []
    for era in eras_for_century(century):
        period = period_for_era(era)
        if period and period not in result:
            result.append(period)
    return result


# ---------------------------------------------------------------------------
# Paradox targeting (§16.1)
# ---------------------------------------------------------------------------

def centuries_in_future(from_century: int) -> list[int]:
    """
    Return all centuries strictly higher than from_century (§16.1, future paradox).
    Year Zero is not a century; travelers there are not hit by future paradox.
    """
    return [c for c in range(from_century + 1, CENTURY_MAX + 1)]


def centuries_in_past(from_century: int) -> list[int]:
    """
    Return all centuries strictly lower than from_century, excluding Year Zero (§16.1).
    Year Zero is not a century on the paradox track.
    """
    return [c for c in range(CENTURY_MIN, from_century)]


def centuries_at_present(from_century: int) -> list[int]:
    """
    Return the century equal to from_century (for present paradox, §16.1).
    Returns an empty list if from_century is Year Zero.
    """
    if from_century == YEAR_ZERO:
        return []
    return [from_century]


# ---------------------------------------------------------------------------
# Milestone centuries (§25.3)
# ---------------------------------------------------------------------------

def is_millennium_century(century: int) -> bool:
    """True if this century grants a Contract Point milestone (X or XX) (§25.3)."""
    return century in MILLENNIUM_CENTURIES


# ---------------------------------------------------------------------------
# Win-condition geography (§31.1)
# ---------------------------------------------------------------------------

def reached_year_zero(century: int) -> bool:
    """True if a traveler has reached Year Zero (§31.1a)."""
    return century == YEAR_ZERO

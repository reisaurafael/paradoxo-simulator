"""
tests/test_timeline.py
=======================
Tests for engine/timeline.py: board geometry, distance, era/period lookups.

Each test references the rule section it validates.
"""

import pytest
from engine.timeline import (
    is_valid_position, distance, clamp_to_board,
    eras_for_century, period_for_era, periods_for_century,
    reached_year_zero, is_millennium_century,
    centuries_in_future, centuries_in_past, centuries_at_present,
)


# ---------------------------------------------------------------------------
# Valid positions (§5.1)
# ---------------------------------------------------------------------------

def test_year_zero_is_valid():
    assert is_valid_position(0)

def test_century_xxx_is_valid():
    assert is_valid_position(30)

def test_century_i_is_valid():
    assert is_valid_position(1)

def test_negative_century_is_invalid():
    assert not is_valid_position(-1)

def test_century_31_is_invalid():
    assert not is_valid_position(31)


# ---------------------------------------------------------------------------
# Distance (§5.3)
# ---------------------------------------------------------------------------

def test_distance_same_century():
    assert distance(10, 10) == 0

def test_distance_symmetric():
    assert distance(5, 15) == distance(15, 5) == 10

def test_distance_year_zero_to_i():
    """§5.3: Year Zero lies one space below century I."""
    assert distance(0, 1) == 1

def test_distance_year_zero_to_xxx():
    assert distance(0, 30) == 30


# ---------------------------------------------------------------------------
# Clamping (§14.4)
# ---------------------------------------------------------------------------

def test_clamp_at_xxx():
    assert clamp_to_board(31) == 30

def test_clamp_at_year_zero():
    assert clamp_to_board(-1) == 0

def test_no_clamp_needed():
    assert clamp_to_board(15) == 15


# ---------------------------------------------------------------------------
# Era lookups (§5.4)
# ---------------------------------------------------------------------------

def test_year_zero_has_no_era():
    assert eras_for_century(0) == []

def test_century_1_is_antiquity():
    assert eras_for_century(1) == ["Antiquity"]

def test_century_5_is_boundary():
    """§5.4: century V belongs to both Antiquity and High Middle Ages."""
    eras = eras_for_century(5)
    assert "Antiquity" in eras
    assert "High Middle Ages" in eras

def test_century_10_is_boundary():
    """
    Per the Rules Reference era table (§5.4):
        High Middle Ages: V-X  (ends at X)
        Low Middle Ages: XI-XV (starts at XI)
    Century X is the upper boundary of High Middle Ages only.
    XI starts Low Middle Ages: X is NOT in Low Middle Ages.
    """
    eras = eras_for_century(10)
    assert "High Middle Ages" in eras
    assert "Low Middle Ages" not in eras

def test_century_15_is_boundary():
    eras = eras_for_century(15)
    assert "Low Middle Ages" in eras
    assert "Modern" in eras

def test_century_7_is_single_era():
    assert eras_for_century(7) == ["High Middle Ages"]

def test_century_25_is_timeless():
    assert "Timeless" in eras_for_century(25)


# ---------------------------------------------------------------------------
# Period lookups (§5.4)
# ---------------------------------------------------------------------------

def test_antiquity_is_origins():
    assert period_for_era("Antiquity") == "Origins"

def test_high_middle_ages_is_origins():
    assert period_for_era("High Middle Ages") == "Origins"

def test_low_middle_ages_is_ascension():
    assert period_for_era("Low Middle Ages") == "Ascension"

def test_modern_is_ascension():
    assert period_for_era("Modern") == "Ascension"

def test_contemporary_is_singularity():
    assert period_for_era("Contemporary") == "Singularity"

def test_timeless_is_singularity():
    assert period_for_era("Timeless") == "Singularity"

def test_unknown_era_returns_none():
    assert period_for_era("NonExistentEra") is None


# ---------------------------------------------------------------------------
# Periods for century
# ---------------------------------------------------------------------------

def test_century_7_is_origins():
    assert periods_for_century(7) == ["Origins"]

def test_century_12_is_ascension():
    assert periods_for_century(12) == ["Ascension"]

def test_century_25_is_singularity():
    assert periods_for_century(25) == ["Singularity"]

def test_century_10_boundary_same_period():
    """Century X is boundary between two eras both in Origins."""
    periods = periods_for_century(10)
    assert periods == ["Origins"]  # both eras map to Origins, so deduped

def test_year_zero_has_no_period():
    assert periods_for_century(0) == []


# ---------------------------------------------------------------------------
# Year Zero detection (§31.1a)
# ---------------------------------------------------------------------------

def test_reached_year_zero_true():
    assert reached_year_zero(0)

def test_reached_year_zero_false():
    assert not reached_year_zero(1)
    assert not reached_year_zero(30)


# ---------------------------------------------------------------------------
# Millennium milestones (§25.3)
# ---------------------------------------------------------------------------

def test_century_x_is_milestone():
    assert is_millennium_century(10)

def test_century_xx_is_milestone():
    assert is_millennium_century(20)

def test_other_centuries_not_milestones():
    assert not is_millennium_century(5)
    assert not is_millennium_century(15)
    assert not is_millennium_century(30)


# ---------------------------------------------------------------------------
# Paradox targeting (§16.1)
# ---------------------------------------------------------------------------

def test_future_centuries_above_causer():
    result = centuries_in_future(25)
    assert 26 in result
    assert 30 in result
    assert 25 not in result
    assert 24 not in result

def test_past_centuries_below_causer():
    result = centuries_in_past(5)
    assert 1 in result
    assert 4 in result
    assert 5 not in result
    assert 0 not in result  # Year Zero excluded

def test_present_century_is_causer():
    assert centuries_at_present(15) == [15]

def test_present_at_year_zero_is_empty():
    """Year Zero is not a century for paradox purposes."""
    assert centuries_at_present(0) == []

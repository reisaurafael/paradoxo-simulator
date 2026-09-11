"""
tests/test_constants.py
========================
Verify that constants match the Rules Reference exactly.

These are not trivial: they document the design decisions and catch
regressions if a constant is accidentally changed.
"""

import pytest
from engine.constants import (
    YEAR_ZERO, CENTURY_MAX, CENTURY_START,
    ENERGY_PER_TRAVELER,
    BOOM_LIMIT, EXPLOSION_ENERGY_LOSS, EXPLOSION_BOOM_RESET,
    GENERATORS_FOR_OVERLOAD, N_GENERATORS, GENERATOR_FACES,
    PAST_TRAVEL_ENERGY_COST_PER_CENTURY,
    DOUBLE_TRAVEL_MULTIPLIER,
    DECLARE_COST,
    CP_PER_DELIVERY, CP_PER_TERMINATION, CP_YEAR_ZERO_TOTAL,
    MERCHANT_REVEALED_CARDS, SECRET_MARKET_CENTURY, TOTAL_CARDS,
    MERCHANT_STARTING_CARDS, SECRET_MARKET_CARD_COUNT,
    MIN_TRAVELERS, MAX_TRAVELERS, EQUIPMENT_SLOTS,
    ERAS, PERIODS,
)


# ---------------------------------------------------------------------------
# Timeline (§5)
# ---------------------------------------------------------------------------

def test_year_zero():
    assert YEAR_ZERO == 0  # §5.1

def test_century_max():
    assert CENTURY_MAX == 30  # §5.1: XXX

def test_travelers_start_on_xxx():
    assert CENTURY_START == 30  # §7.1

def test_eras_count():
    assert len(ERAS) == 6  # §5.4: six eras

def test_era_antiquity():
    assert ERAS["Antiquity"] == (1, 5)

def test_era_high_middle_ages():
    assert ERAS["High Middle Ages"] == (5, 10)

def test_era_low_middle_ages():
    assert ERAS["Low Middle Ages"] == (11, 15)

def test_era_modern():
    assert ERAS["Modern"] == (15, 19)

def test_era_contemporary():
    assert ERAS["Contemporary"] == (19, 23)

def test_era_timeless():
    assert ERAS["Timeless"] == (23, 30)

def test_periods_count():
    assert len(PERIODS) == 3  # Origins, Ascension, Singularity (§5.4)

def test_origins_eras():
    assert set(PERIODS["Origins"]) == {"Antiquity", "High Middle Ages"}

def test_ascension_eras():
    assert set(PERIODS["Ascension"]) == {"Low Middle Ages", "Modern"}

def test_singularity_eras():
    assert set(PERIODS["Singularity"]) == {"Contemporary", "Timeless"}


# ---------------------------------------------------------------------------
# Resources and setup (§6-7)
# ---------------------------------------------------------------------------

def test_energy_per_traveler():
    assert ENERGY_PER_TRAVELER == 4  # §7.1: energy = 4 × n_travelers

def test_setup_energy_2_players():
    assert ENERGY_PER_TRAVELER * 2 == 8

def test_setup_energy_4_players():
    assert ENERGY_PER_TRAVELER * 4 == 16


# ---------------------------------------------------------------------------
# Generators (§4.2, §10)
# ---------------------------------------------------------------------------

def test_n_generators():
    assert N_GENERATORS == 4  # §4.2 / §10.1

def test_generator_faces():
    assert set(GENERATOR_FACES) == {1, 2, 3}  # §4.2, values I, II, III

def test_generators_for_overload():
    assert GENERATORS_FOR_OVERLOAD == 3  # §11.1


# ---------------------------------------------------------------------------
# Booms and explosion (§15)
# ---------------------------------------------------------------------------

def test_boom_limit():
    assert BOOM_LIMIT == 12  # §15.2

def test_explosion_energy_loss():
    # §15.2 explicitly states TWO energy lost.
    # The legacy Julia code incorrectly used 1. This test enforces the fix.
    assert EXPLOSION_ENERGY_LOSS == 2

def test_explosion_boom_reset():
    assert EXPLOSION_BOOM_RESET == 12  # §15.2: discard twelve booms


# ---------------------------------------------------------------------------
# Travel (§14)
# ---------------------------------------------------------------------------

def test_past_travel_cost():
    assert PAST_TRAVEL_ENERGY_COST_PER_CENTURY == 1  # §14.2

def test_double_travel_multiplier():
    assert DOUBLE_TRAVEL_MULTIPLIER == 2  # §14.1: module 9 "travels twice"


# ---------------------------------------------------------------------------
# Scoring (§25, §32)
# ---------------------------------------------------------------------------

def test_cp_per_delivery():
    assert CP_PER_DELIVERY == 1  # §25.1

def test_cp_per_termination():
    assert CP_PER_TERMINATION == 1  # §25.2

def test_cp_year_zero_total():
    # §25.4 + §32.2: reaching Year Zero grants 2 CP total
    assert CP_YEAR_ZERO_TOTAL == 2


# ---------------------------------------------------------------------------
# Market (§17-19, §7.2)
# ---------------------------------------------------------------------------

def test_total_cards():
    assert TOTAL_CARDS == 52  # §4.4

def test_merchant_starting_cards():
    assert MERCHANT_STARTING_CARDS == 40  # §7.2

def test_secret_market_card_count():
    assert SECRET_MARKET_CARD_COUNT == 12  # §7.2

def test_card_totals_add_up():
    # §7.2: 40 with Merchant + 12 in Secret Market = 52 total
    assert MERCHANT_STARTING_CARDS + SECRET_MARKET_CARD_COUNT == TOTAL_CARDS

def test_merchant_revealed_cards():
    assert MERCHANT_REVEALED_CARDS == 4  # §17.1

def test_secret_market_century():
    assert SECRET_MARKET_CENTURY == 11  # §19.1: fixed on XI

def test_declare_cost():
    assert DECLARE_COST == 4  # §18.3


# ---------------------------------------------------------------------------
# Player count (§1)
# ---------------------------------------------------------------------------

def test_min_travelers():
    assert MIN_TRAVELERS == 2

def test_max_travelers():
    assert MAX_TRAVELERS == 6

def test_equipment_slots():
    assert EQUIPMENT_SLOTS == 2  # §22.1

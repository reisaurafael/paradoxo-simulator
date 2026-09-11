"""
engine/constants.py
===================
All numeric and structural constants for Paradoxo.

Every value here is derived directly from the Rules Reference.
Section references (§N) point to the official Rules Reference document.
No value in this file is invented or estimated.
"""

# ---------------------------------------------------------------------------
# Timeline (§5)
# ---------------------------------------------------------------------------

YEAR_ZERO: int = 0          # The special end space (§5.1)
CENTURY_MIN: int = 1        # First numbered century
CENTURY_MAX: int = 30       # XXX, furthest space; travelers start here (§5.2)
CENTURY_START: int = CENTURY_MAX  # All travelers begin on XXX (§7.1)

# Era boundaries and names (§5.4)
# Each era is a closed interval [start, end].
# Centuries on a boundary belong to both adjacent eras (§5.4 note).
ERAS: dict[str, tuple[int, int]] = {
    "Antiquity":        (1, 5),
    "High Middle Ages": (5, 10),
    "Low Middle Ages":  (11, 15),
    "Modern":           (15, 19),
    "Contemporary":     (19, 23),
    "Timeless":         (23, 30),
}

# Delivery periods group the eras into three scoring windows (§5.4)
PERIODS: dict[str, list[str]] = {
    "Origins":      ["Antiquity", "High Middle Ages"],
    "Ascension":    ["Low Middle Ages", "Modern"],
    "Singularity":  ["Contemporary", "Timeless"],
}

# Milestone centuries that grant Contract Points (§25.3)
MILLENNIUM_CENTURIES: tuple[int, int] = (10, 20)  # X and XX

# ---------------------------------------------------------------------------
# Resources (§6)
# ---------------------------------------------------------------------------

ENERGY_PER_TRAVELER: int = 4  # Setup energy = 4 × n_travelers (§7.1)
GOLD_START: int = 0
BOOMS_START: int = 0

# ---------------------------------------------------------------------------
# The time machine: matrix layout (§9.2)
# ---------------------------------------------------------------------------
# The matrix is 3 functions × 3 modules.
# Functions are indexed 0-based (row 0 = Recharge, 1 = Paradox, 2 = Travel).
# Modules within each function are indexed 0-based (col 0, 1, 2).
# The global module number (1-9) = row*3 + col + 1.
#
#   Module 1  Module 2  Module 3
#   ────────  ────────  ────────
#   energy    gold      energy+gold      ← RECHARGE  (row 0)
#   future    present   past             ← PARADOX   (row 1)
#   heating   travel    double travel    ← TRAVEL    (row 2)

FUNCTION_RECHARGE: int = 0
FUNCTION_PARADOX: int = 1
FUNCTION_TRAVEL: int = 2
FUNCTION_NAMES: dict[int, str] = {
    FUNCTION_RECHARGE: "Recharge",
    FUNCTION_PARADOX:  "Paradox",
    FUNCTION_TRAVEL:   "Travel",
}

# Module semantic labels (row, col) → description
MODULE_LABELS: dict[tuple[int, int], str] = {
    (0, 0): "energy",
    (0, 1): "gold",
    (0, 2): "energy+gold",
    (1, 0): "future paradox",
    (1, 1): "present paradox",
    (1, 2): "past paradox",
    (2, 0): "heating",
    (2, 1): "travel",
    (2, 2): "double travel",
}

# ---------------------------------------------------------------------------
# Generators (§4.2, §10)
# ---------------------------------------------------------------------------

N_GENERATORS: int = 4               # Each traveler rolls 4 generators per Hour
GENERATOR_FACES: tuple[int, ...] = (1, 2, 3)   # Three-faced dice, values I/II/III

# ---------------------------------------------------------------------------
# Overload and escape valve (§11)
# ---------------------------------------------------------------------------

GENERATORS_FOR_OVERLOAD: int = 3    # Placing 3 generators in one function overloads it (§11.1)

# ---------------------------------------------------------------------------
# Booms and explosion (§15)
# ---------------------------------------------------------------------------

BOOM_LIMIT: int = 12                # Reaching 12 booms causes explosion (§15.2)
EXPLOSION_ENERGY_LOSS: int = 2      # Explosion costs 2 energy (§15.2)
EXPLOSION_BOOM_RESET: int = 12      # 12 booms are discarded on explosion (§15.2)

# ---------------------------------------------------------------------------
# Travel (§14)
# ---------------------------------------------------------------------------

# Traveling to the past costs 1 energy per century (§14.2)
PAST_TRAVEL_ENERGY_COST_PER_CENTURY: int = 1
# Traveling to the future costs 0 energy (§14.2)
FUTURE_TRAVEL_ENERGY_COST_PER_CENTURY: int = 0

# Double travel module multiplier (module 9 counts twice) (§14.1)
DOUBLE_TRAVEL_MULTIPLIER: int = 2

# ---------------------------------------------------------------------------
# Scoring (§25, §32)
# ---------------------------------------------------------------------------

CP_PER_DELIVERY: int = 1            # §25.1
CP_PER_TERMINATION: int = 1         # §25.2
CP_PER_MILLENNIUM: int = 1          # §25.3 (once per century X and XX per traveler)
CP_YEAR_ZERO_TOTAL: int = 2         # §25.4 + §32.2 (contract CP + stabilisation bonus)
CP_SURVIVAL_BONUS: int = 1          # §32.3 (never terminated during game)
CP_STABILISATION_BONUS: int = 1     # §32.2 (awarded in addition to contract CP)

# ---------------------------------------------------------------------------
# Market (§17-19)
# ---------------------------------------------------------------------------

MERCHANT_REVEALED_CARDS: int = 4    # Merchant always shows 4 revealed cards (§17.1)
SECRET_MARKET_CENTURY: int = 11     # Secret Market is fixed on XI (§19.1)
SECRET_MARKET_CARD_COUNT: int = 12  # 12 cards set aside in setup (§7.2)
TOTAL_CARDS: int = 52               # Total card count (§4.4)
MERCHANT_START_CENTURY: int = 20    # Merchant placed on XX at setup (§7.2)
MERCHANT_STARTING_CARDS: int = 40   # Merchant holds 40 of 52 cards (§7.2)

# Merchant movement dice count upgrades (§17.6)
# Movement is 1d3 normally; upgrades are permanent and cumulative.
MERCHANT_UPGRADE_1_CENTURY: int = 20   # 2d3 once any traveler ends an Hour on XX
MERCHANT_UPGRADE_2_CENTURY: int = 10   # 3d3 once any traveler ends an Hour on X

# ---------------------------------------------------------------------------
# Market actions (§18)
# ---------------------------------------------------------------------------

DECLARE_COST: int = 4   # Pay 4 gold to remove Wanted status (§18.3)

# Renewal cost is progressive per traveler per Market phase (§18.2)
# First renewal: 1 gold, second: 2 gold, third: 3 gold, etc.
# This is computed dynamically; the base is 1.
RENEW_COST_BASE: int = 1

# ---------------------------------------------------------------------------
# Termination and respawn (§30)
# ---------------------------------------------------------------------------

TERMINATION_RESPAWN_ENERGY_BASE: int = 12   # Return with 12 + recycle_value (§30.2)

# ---------------------------------------------------------------------------
# Contracts: Wanted (§29)
# ---------------------------------------------------------------------------

WANTED_BOUNTY: int = 4  # 4 gold bounty for terminating a Wanted traveler (§29.2)

# ---------------------------------------------------------------------------
# Player count (§1)
# ---------------------------------------------------------------------------

MIN_TRAVELERS: int = 2
MAX_TRAVELERS: int = 6

# ---------------------------------------------------------------------------
# Equipment (§22)
# ---------------------------------------------------------------------------

EQUIPMENT_SLOTS: int = 2   # Each traveler has 2 equipment slots (§22.1)
LARGE_ITEM_SLOTS: int = 2  # A large item fills both slots (§22.1)

# ---------------------------------------------------------------------------
# Overdrive: experimental playtesting rule (2026-06-26), not in the printed reference
# ---------------------------------------------------------------------------
# After century X travelers traveling to the past lose 2 energy per century
# instead of the normal 1. Activates for every century traveled at position ≤ X.
OVERDRIVE_THRESHOLD_CENTURY: int = 10    # X, overdrive zone is century ≤ this
OVERDRIVE_ENERGY_COST_PER_CENTURY: int = 2  # Energy cost per century in overdrive

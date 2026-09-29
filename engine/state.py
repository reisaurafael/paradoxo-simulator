"""
engine/state.py
===============
Dataclasses for the whole game state.

These are plain data containers: the rules live in the resolver modules. Every
field maps to something the Rules Reference tracks.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from engine.constants import (
    CENTURY_START,
    ENERGY_PER_TRAVELER,
    GOLD_START,
    BOOMS_START,
    EQUIPMENT_SLOTS,
    LARGE_ITEM_SLOTS,
    MERCHANT_START_CENTURY,
    MERCHANT_STARTING_CARDS,
    SECRET_MARKET_CARD_COUNT,
    SECRET_MARKET_SPECIAL_COUNT,
)

if TYPE_CHECKING:
    from engine.cards import Card


# ---------------------------------------------------------------------------
# Matrix allocation (§10)
# ---------------------------------------------------------------------------

@dataclass
class Allocation:
    """
    The dice placement a traveler makes in Phase 3 of an Hour (§10).

    The matrix is 3 rows (functions) × 3 columns (modules).
    A value of 0 in a cell means no generator was placed there.

    Row 0 = Recharge  (modules 1, 2, 3)
    Row 1 = Paradox   (modules 4, 5, 6)
    Row 2 = Travel    (modules 7, 8, 9)

    The escape valve is a separate slot outside the matrix (§11.2).
    """
    # 3×3 grid stored as a list of 3 lists, each with 3 integers.
    # matrix[row][col] = generator value placed there (0 = empty).
    matrix: list[list[int]] = field(
        default_factory=lambda: [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
    )
    # Generator value sent to the escape valve (0 = unused) (§11.2)
    escape_valve: int = 0

    def get(self, row: int, col: int) -> int:
        """Return the generator value at (row, col). 0 means empty."""
        return self.matrix[row][col]

    def set(self, row: int, col: int, value: int) -> None:
        """Place a generator value at (row, col)."""
        self.matrix[row][col] = value

    def generators_in_function(self, row: int) -> list[int]:
        """Return non-zero generator values placed in a function (row)."""
        return [v for v in self.matrix[row] if v != 0]

    def is_overloaded(self, row: int) -> bool:
        """True if all 3 modules in this function have a generator (§11.1)."""
        return all(v != 0 for v in self.matrix[row])

    @classmethod
    def empty(cls) -> "Allocation":
        return cls()


# ---------------------------------------------------------------------------
# Traveler state (§6)
# ---------------------------------------------------------------------------

@dataclass
class TravelerState:
    """
    The complete state of one traveler at any point in the game.

    Every field is a quantity the Rules Reference tracks for a traveler.
    """
    name: str

    # Resources (§6)
    energy: int = 0
    gold: int = GOLD_START
    booms: int = BOOMS_START
    century: int = CENTURY_START        # Position on the timeline (§5)
    contract_points: int = 0            # Score (§6.4)

    # Overload tracking (§11.1)
    # Set of function indices (0, 1, 2) that are unavailable this Hour.
    overloaded_functions: set[int] = field(default_factory=set)
    # Functions that will be overloaded next Hour (set at end of Phase 3).
    overloaded_next: set[int] = field(default_factory=set)

    # Status flags
    # is_terminated is the PERMANENT Terminated condition (§28.1). Once a
    # traveler hits 0 energy they carry it for the rest of the game: respawning
    # does NOT clear it. A terminated traveler keeps playing normally (acts,
    # travels, delivers, can be targeted and terminated again, can even win).
    # The status only costs them the end-of-game survival point (§32.2) and is
    # what the §11.1c game-end condition counts.
    is_terminated: bool = False
    is_wanted: bool = False             # Carrying Wanted marker (§29)
    exploded_this_hour: bool = False    # Exploded at module 7 (§15.2)

    # awaiting_respawn is the TRANSIENT side of termination (§28.1, §28.3): from
    # the moment a traveler is terminated until they return at the start of the
    # next Hour they are inactive and cannot be targeted. advance_overload()
    # then returns them to XXX with `respawn_energy` (= 12 + Σ recycle value),
    # keeping their gold and booms, and clears this flag (is_terminated stays).
    awaiting_respawn: bool = False
    respawn_energy: int = 0

    # Scoring flags: each milestone can only be scored once (§25.3)
    scored_century_x: bool = False
    scored_century_xx: bool = False

    # Cards currently held: equipment, cards waiting for delivery, unused actives (§22)
    hand: list["Card"] = field(default_factory=list)

    # Temporal Receptor: names of cards that have been delivered (§21)
    temporal_receptor: list[str] = field(default_factory=list)

    # Temporal Receptor: the delivered Card objects themselves.
    # Kept alongside the name list so effects that read a delivered card's text
    # (Refrigerator) or copy its passives (Quantum Computer) can reach it (§2.4).
    receptor_cards: list["Card"] = field(default_factory=list, repr=False, compare=False)

    # Periods covered by deliveries so far, for the Full Receptor win (§31.1b).
    # The period comes from each delivered card's delivery_century.
    delivered_periods: set[str] = field(default_factory=set)

    # Card names already used this Hour, for "the first time each Hour" effects
    cards_used_this_hour: set[str] = field(default_factory=set)

    # Travelers who made this one lose energy, most recent last. The last entry
    # gets the credit for a termination (§28.1); the list is cleared on respawn.
    eliminated_by: list[str] = field(default_factory=list)

    # Reward tracking (§26)
    last_reward_category: str | None = None  # "Chaos" | "Time" | "Resource"; §26.2 no repeat
    market_voucher: int = 0                  # Time II: buy at any market (accumulated count)
    item_voucher: int = 0                    # Time III: others count as synchronic this activation
    # Resource III buffs: module index (0-8) -> +1, permanent, one per module
    matrix_buffs: dict[int, int] = field(default_factory=dict)

    @property
    def survival_eligible(self) -> bool:
        """True if this traveler has never been terminated (§32.2).

        Eligibility for the end-of-game +1 survival point is exactly the inverse
        of the permanent Terminated condition.
        """
        return not self.is_terminated

    @property
    def equipment_capacity(self) -> int:
        """Max equipment slots; +2 if Relative Dimensions Operative is held."""
        base = EQUIPMENT_SLOTS
        # Read through passive_source_cards like every passive: an Operative copied by
        # Movable-Type Press (revealed in the Market) or Quantum Computer (receptor)
        # gives its slots too (it read the hand only, and a Press beside a revealed
        # Operative blocked every purchase, 28/09).
        from engine.cards import passive_source_cards
        if any(c.name == "Relative Dimensions Operative"
               for c in passive_source_cards(self, getattr(self, "_game", None))):
            base += 2
        return base

    @classmethod
    def create(cls, name: str, n_travelers: int) -> "TravelerState":
        """
        Create a traveler with correct starting resources for a given player count.
        Energy starts at 4 × n_travelers (§7.1).
        """
        return cls(
            name=name,
            energy=ENERGY_PER_TRAVELER * n_travelers,
            gold=GOLD_START,
            booms=BOOMS_START,
            century=CENTURY_START,
        )

    def is_active(self) -> bool:
        """
        A traveler is active if they can take actions this Hour.

        They are inactive if they exploded this Hour (§5.2) or are awaiting
        respawn after being terminated this Hour (§28.3). Being terminated in an
        earlier Hour does not make them inactive, they act normally once they
        have respawned.
        """
        return not self.exploded_this_hour and not self.awaiting_respawn

    def slots_used(self) -> int:
        """Count equipment slots consumed (large items take 2)."""
        return sum(LARGE_ITEM_SLOTS if c.is_large_item else 1 for c in self.hand)

    def equipment_full(self) -> bool:
        return self.slots_used() >= self.equipment_capacity

    def can_hold(self, card: "Card") -> bool:
        """True if there is room for this card (accounting for large item size)."""
        needed = LARGE_ITEM_SLOTS if card.is_large_item else 1
        return self.slots_used() + needed <= self.equipment_capacity


# ---------------------------------------------------------------------------
# Game state (§7-8)
# ---------------------------------------------------------------------------

@dataclass
class GameState:
    """
    The complete state of a game in progress.

    Holds everything that belongs to the table rather than to one traveler.
    """
    travelers: list[TravelerState]

    # Turn tracking
    hour: int = 1       # Current Hour number (§8)

    # Merchant position and stock (§17)
    merchant_century: int = MERCHANT_START_CENTURY
    # The two card counts are set at setup and not updated as cards are bought.
    merchant_card_count: int = MERCHANT_STARTING_CARDS
    merchant_upgrade_xx_triggered: bool = False
    merchant_upgrade_x_triggered: bool = False

    # Secret Market (§19)
    secret_market_open: bool = False        # Opens when a traveler ends an Hour on XI (§19.1)
    secret_market_card_count: int = SECRET_MARKET_CARD_COUNT + SECRET_MARKET_SPECIAL_COUNT

    # The Merchant cards currently revealed, refreshed by the market phase, so
    # a passive that resolves later (Movable-Type Press) can see them without
    # the engine holding on to the deck object.
    market_revealed: list["Card"] = field(default_factory=list, repr=False, compare=False)

    # Random source for card effects that roll their own generator (Excalibur,
    # Fishing Reel, Navigation Compass). The runner sets it for each game.
    rng: object = field(default=None, repr=False, compare=False)

    # Game-over flag and reason
    game_over: bool = False
    game_over_reason: str | None = None  # "year_zero" | "full_receptor" | "last_traveler" | "merchant_empty"
    winner: str | None = None

    # Name of the traveler responsible for the most recent termination of another
    # traveler. Used to credit the §32.3 stabilisation bonus when the game ends by
    # §11.1c (terminating the last not-yet-terminated traveler ends the game).
    last_termination_causer: str | None = None

    # Time III item voucher: the traveler whose voucher is active during their
    # Phase 4 window. No card effect reads it yet, so the voucher is spent
    # without changing who counts as synchronic.
    item_voucher_active_for: str | None = None

    # Item event log, filled by the market, combat, and resolve modules
    item_events: list["ItemEvent"] = field(default_factory=list, repr=False, compare=False)

    # Reward queue: one traveler name per CP earned. The runner drains it after
    # each phase, when the deck, the strategies and the rng are at hand.
    cp_rewards_pending: list[str] = field(default_factory=list, repr=False, compare=False)
    # Solo generator phases earned with the Time I reward, run after Phase 4.
    solo_phases_pending: list[str] = field(default_factory=list, repr=False, compare=False)

    @classmethod
    def create(cls, traveler_names: list[str]) -> "GameState":
        """
        Initialise a new game with the correct starting state for all travelers.
        """
        n = len(traveler_names)
        travelers = [TravelerState.create(name, n) for name in traveler_names]
        return cls(travelers=travelers)

    def __post_init__(self) -> None:
        # a plain back-reference (not a field) so table-reading passives reach capacity
        for t in self.travelers:
            object.__setattr__(t, "_game", self)

    def traveler(self, name: str) -> TravelerState:
        """Look up a traveler by name. Raises KeyError if not found."""
        for t in self.travelers:
            if t.name == name:
                return t
        raise KeyError(f"No traveler named '{name}'")

    def active_travelers(self) -> list[TravelerState]:
        """Travelers in play this Hour (not awaiting respawn).

        Note: this includes travelers carrying the permanent Terminated condition
        who have already respawned: they play normally (§28.3).
        """
        return [t for t in self.travelers if not t.awaiting_respawn]


# ---------------------------------------------------------------------------
# Simulation result snapshot
# ---------------------------------------------------------------------------

@dataclass
class HourSnapshot:
    """
    One traveler's state at the end of a resolved Hour. The metrics module
    builds game trajectories from these.
    """
    hour: int
    traveler_name: str
    energy: int
    gold: int
    booms: int
    century: int
    contract_points: int
    allocation: Allocation
    hand_size: int = 0
    exploded: bool = False
    terminated: bool = False


@dataclass
class ItemEvent:
    """
    One item event during a game (bought, delivered, recycled, and so on).
    Collected in GameState.item_events and carried into GameResult for reporting.
    """
    hour: int
    traveler: str
    event_type: str   # "bought" | "delivered" | "recycled" | "destroyed" | "missed_delivery" | "renewed"
    card_name: str
    century: int = 0  # the traveler's century when the event fired


@dataclass
class GameResult:
    """
    The final outcome of one simulated game, produced by the runner and read
    by the metrics and reporting modules.
    """
    winner: str | None
    hours_played: int
    end_reason: str                     # One of the four §31.1 conditions
    traveler_results: list[TravelerState]
    history: list[HourSnapshot]         # Full trajectory, one snapshot per traveler per Hour
    strategy_names: dict[str, str]      # traveler_name -> strategy name
    item_events: list["ItemEvent"] = field(default_factory=list)

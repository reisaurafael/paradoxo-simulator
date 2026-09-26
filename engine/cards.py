"""
engine/cards.py
===============
All 52 cards and how the rules engine represents them.

Each Card has:
    name:             the card's official English name
    gold_cost:        purchase price in gold (top-left number)
    delivery_century: the exact century where this card is delivered to the
                      Temporal Receptor for 1 CP (§9)
    recycle_value:    energy gained when recycled via the Recycle action, and
                      the amount added to respawn energy on termination
                      (bottom-right circled number, §21 / §28.2)
    ability_type:     "passive" | "active" | "atemporal_passive" | "atemporal_active"
    is_large_item:    True if the card occupies both equipment slots (§12.1)
    recycles_on_use:  True for actives whose own text recycles them on use

Effect hooks are callables the engine invokes at resolution time. Passive hooks
have the fixed signatures below. Effects that depend on *who* caused something
(Gunpowder, Prince Dracula's Chalice, Laser Sword, Viking Shield, Agnes's
Cauldron, Automobile) are resolved centrally in engine/combat.py by card name
instead of through a hook, so they apply the same way to every damage source.

    on_travel_cost(traveler, cost, direction, game=None) -> int
    on_boom_gain(traveler, booms) -> int
    on_overload(traveler, game) -> None
    on_energy_loss(traveler, amount) -> int
    on_explosion_check(traveler, game) -> bool   # True = prevent explosion
    on_hour_end(traveler, game) -> None
    on_market_buy_other(this_t, buyer_t, card, game) -> None
    active_effect(traveler, game, context) -> None

At setup the Merchant deck takes 40 cards and the Secret Market 12, chosen at
random each game (engine/market.py, §17.2 / §24).
"""

from __future__ import annotations
import random
from dataclasses import dataclass
from typing import Callable, Optional, TYPE_CHECKING

from engine import combat
from engine.constants import CENTURY_MAX, FUNCTION_TRAVEL, TOTAL_CARDS
from engine.timeline import clamp_to_board, distance, in_older_era, same_era

if TYPE_CHECKING:
    from engine.state import TravelerState, GameState


# ---------------------------------------------------------------------------
# Card dataclass
# ---------------------------------------------------------------------------

@dataclass
class Card:
    name: str
    gold_cost: int
    delivery_century: int
    ability_type: str               # "passive"|"active"|"atemporal_passive"|"atemporal_active"
    recycle_value: int = 0          # energy on Recycle / respawn (§21, §28.2)
    is_large_item: bool = False
    recycles_on_use: bool = False   # True for single-use actives

    # Passive hooks: called by engine/resolve.py and engine/market.py
    on_travel_cost: Optional[Callable] = None       # (traveler, cost, direction, game=None) -> int
    on_boom_gain: Optional[Callable] = None         # (traveler, booms) -> int
    on_overload: Optional[Callable] = None          # (traveler, game) -> None
    on_energy_loss: Optional[Callable] = None       # (traveler, amount) -> int
    on_explosion_check: Optional[Callable] = None   # (traveler, game) -> bool
    on_hour_end: Optional[Callable] = None          # (traveler, game) -> None
    on_market_buy_other: Optional[Callable] = None  # (this_t, buyer_t, card, game) -> None

    # Active effect: called during Item Activation or a Market UseCardAction
    active_effect: Optional[Callable] = None        # (traveler, game, context) -> None

    def __repr__(self) -> str:
        return f"<Card: {self.name} ({self.ability_type}, {self.gold_cost}g, deliver@{self.delivery_century})>"

    # A card is identified by its name: two instances of the same card are equal.
    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Card) and self.name == other.name


# ---------------------------------------------------------------------------
# Passive sources: Quantum Computer and Movable-Type Press
# ---------------------------------------------------------------------------

def passive_source_cards(traveler: "TravelerState", game: "GameState | None" = None) -> list[Card]:
    """
    Return every card whose *passive* abilities currently apply to `traveler`.

    That is the held equipment plus, through two cards that borrow passives:
      - Quantum Computer: the passives of the cards in the traveler's own
        Temporal Receptor.
      - Movable-Type Press: the passives of the cards currently revealed at the
        Merchant; this one needs `game` to see them.

    The engine asks this function for passives instead of reading
    `traveler.hand` directly, so both cards work everywhere without special cases.
    """
    cards = list(traveler.hand)
    if any(c.name == "Quantum Computer" for c in traveler.hand):
        cards += list(getattr(traveler, "receptor_cards", []))
    if game is not None and any(c.name == "Movable-Type Press" for c in traveler.hand):
        revealed = getattr(game, "market_revealed", None) or []
        cards += [c for c in revealed if c.name != "Movable-Type Press"]
    return cards


# ---------------------------------------------------------------------------
# Effect helpers
# ---------------------------------------------------------------------------

def _reduce_booms(by: int):
    def hook(traveler: "TravelerState", booms: int) -> int:
        return max(0, booms - by)
    return hook


def _looks_like_traveler(obj) -> bool:
    return hasattr(obj, "century") and hasattr(obj, "energy") and hasattr(obj, "hand")


def _rng_of(game: "GameState") -> random.Random:
    rng = getattr(game, "rng", None)
    return rng if isinstance(rng, random.Random) else random.Random()


# ---------------------------------------------------------------------------
# Card catalogue: 52 unique cards, in the order of the printed card sheets
# ---------------------------------------------------------------------------

# ── Page 1: Weapons / Combat ─────────────────────────────────────────────

def ferguson_rifle() -> Card:
    """Active: choose a traveler in your era, they lose 1 energy per century of distance."""
    def effect(traveler, game, context) -> None:
        target = context
        if not _looks_like_traveler(target) or target is traveler:
            return
        if not same_era(traveler.century, target.century):
            return
        dmg = distance(traveler.century, target.century)
        combat.deal_energy(game, traveler, [target], dmg, kind="weapon")
    return Card(name="Ferguson Rifle", gold_cost=3, delivery_century=18,
                recycle_value=3, ability_type="active", active_effect=effect)


def fire_lance() -> Card:
    """Active (recycle): synchronic traveler loses 6 energy and recycles an active card."""
    def effect(traveler, game, context) -> None:
        target = context
        if not _looks_like_traveler(target) or target is traveler:
            return
        if target.century != traveler.century:
            return
        combat.deal_energy(game, traveler, [target], 6, kind="weapon")
        # The target must recycle one of their active cards, if they hold any.
        active_card = next(
            (c for c in target.hand
             if c.ability_type in ("active", "atemporal_active")),
            None,
        )
        if active_card is not None:
            combat.recycle_card(game, target, active_card)
    return Card(name="Fire Lance", gold_cost=3, delivery_century=10,
                recycle_value=1, ability_type="active", recycles_on_use=True,
                active_effect=effect)


def ching_shihs_red_flag() -> Card:
    """Active: traveler in your era loses 3 energy and you steal 1 gold from them."""
    def effect(traveler, game, context) -> None:
        target = context
        if not _looks_like_traveler(target) or target is traveler:
            return
        if not same_era(traveler.century, target.century):
            return
        combat.deal_energy(game, traveler, [target], 3, kind="weapon")
        stolen = min(1, target.gold)
        target.gold -= stolen
        traveler.gold += stolen
    return Card(name="Ching Shih's Red Flag", gold_cost=3, delivery_century=19,
                recycle_value=3, ability_type="active", active_effect=effect)


def queen_annes_revenge_cannon() -> Card:
    """Active (Large Item): every other traveler in your era loses 5 energy."""
    def effect(traveler, game, context) -> None:
        targets = [t for t in game.travelers
                   if t is not traveler and not t.awaiting_respawn
                   and same_era(traveler.century, t.century)]
        combat.deal_energy(game, traveler, targets, 5, kind="weapon")
    return Card(name="Queen Anne's Revenge Cannon", gold_cost=4, delivery_century=17,
                recycle_value=4, ability_type="active", is_large_item=True,
                active_effect=effect)


def laser_gun() -> Card:
    """Active: a synchronic traveler loses 6 energy."""
    def effect(traveler, game, context) -> None:
        target = context
        if not _looks_like_traveler(target) or target is traveler:
            return
        if target.century != traveler.century:
            return
        combat.deal_energy(game, traveler, [target], 6, kind="weapon")
    return Card(name="Laser Gun", gold_cost=4, delivery_century=23,
                recycle_value=3, ability_type="active", active_effect=effect)


def portal_gun() -> Card:
    """Active: a traveler in your era is transported to your century."""
    def effect(traveler, game, context) -> None:
        target = context
        if not _looks_like_traveler(target) or target is traveler:
            return
        if not same_era(traveler.century, target.century):
            return
        target.century = traveler.century
    return Card(name="Portal Gun", gold_cost=3, delivery_century=30,
                recycle_value=3, ability_type="active", active_effect=effect)


def gunpowder_revolver() -> Card:
    """Active: a synchronic traveler loses 3 energy per item they have equipped."""
    def effect(traveler, game, context) -> None:
        target = context
        if not _looks_like_traveler(target) or target is traveler:
            return
        if target.century != traveler.century:
            return
        dmg = 3 * len(target.hand)
        combat.deal_energy(game, traveler, [target], dmg, kind="weapon")
    return Card(name="Gunpowder Revolver", gold_cost=2, delivery_century=13,
                recycle_value=1, ability_type="active", active_effect=effect)


def excalibur() -> Card:
    """Active: roll a common generator, cause a future paradox equal to the result."""
    def effect(traveler, game, context) -> None:
        roll = _rng_of(game).randint(1, 3)
        targets = [t for t in game.travelers
                   if not t.awaiting_respawn and t.century > traveler.century]
        combat.deal_energy(game, traveler, targets, roll, kind="paradox")
    return Card(name="Excalibur", gold_cost=2, delivery_century=6,
                recycle_value=2, ability_type="active", active_effect=effect)


def laser_sword() -> Card:
    """Passive: if another traveler makes you lose energy, they lose the same amount.

    Resolved centrally in engine/combat.py (reflection), keyed on card name."""
    return Card(name="Laser Sword", gold_cost=4, delivery_century=24,
                recycle_value=3, ability_type="passive")


# ── Page 2: Navigation / Transport ───────────────────────────────────────

def navigation_compass() -> Card:
    """Passive: at the end of the travel phase, roll a generator and travel that
    many centuries toward Year Zero without spending energy."""
    def on_hour_end(traveler, game) -> None:
        roll = _rng_of(game).randint(1, 3)
        traveler.century = clamp_to_board(traveler.century - roll)
    return Card(name="Navigation Compass", gold_cost=3, delivery_century=11,
                recycle_value=2, ability_type="passive", on_hour_end=on_hour_end)


def mechanical_clock() -> Card:
    """Passive: whenever you overload your travel modules, receive 5 energy."""
    def on_overload(traveler, game) -> None:
        if FUNCTION_TRAVEL in traveler.overloaded_next:
            traveler.energy += 5
    return Card(name="Mechanical Clock", gold_cost=4, delivery_century=14,
                recycle_value=3, ability_type="passive", on_overload=on_overload)


def galileos_telescope() -> Card:
    """Passive: if another traveler is in an older era than yours, you spend no
    energy during your travel phase."""
    def on_travel_cost(traveler, cost, direction, game=None) -> int:
        if cost <= 0 or game is None:
            return cost
        if any(t is not traveler and not t.awaiting_respawn
               and in_older_era(t.century, traveler.century)
               for t in game.travelers):
            return 0
        return cost
    return Card(name="Galileo's Telescope", gold_cost=3, delivery_century=17,
                recycle_value=2, ability_type="passive", on_travel_cost=on_travel_cost)


def da_vincis_flying_machine() -> Card:
    """Passive (Large Item): you never lose energy during your travel phase."""
    def on_travel_cost(traveler, cost, direction, game=None) -> int:
        return 0
    return Card(name="da Vinci's Flying Machine", gold_cost=4, delivery_century=15,
                recycle_value=3, ability_type="passive", is_large_item=True,
                on_travel_cost=on_travel_cost)


def james_watts_steam_engine() -> Card:
    """Passive: if synchronic with 1+ travelers at the end of the travel phase, gain 3 energy."""
    def on_hour_end(traveler, game) -> None:
        if any(t is not traveler and not t.awaiting_respawn and t.century == traveler.century
               for t in game.travelers):
            traveler.energy += 3
    return Card(name="James Watt's Steam Engine", gold_cost=2, delivery_century=15,
                recycle_value=2, ability_type="passive", on_hour_end=on_hour_end)


def gerardus_mercators_map() -> Card:
    """Active: travel up to 3 centuries (normal energy cost for past travel)."""
    def effect(traveler, game, context) -> None:
        if context is None:
            return
        centuries, direction = context
        centuries = max(0, min(3, centuries))
        # Imported here because engine.resolve imports this module.
        from engine.resolve import execute_travel
        execute_travel(traveler, centuries * direction, game)
    return Card(name="Gerardus Mercator's Map", gold_cost=2, delivery_century=16,
                recycle_value=2, ability_type="active", active_effect=effect)


def teslas_ac_motor() -> Card:
    """Passive: whenever you gain 1+ booms, receive 1 energy."""
    def on_boom_gain(traveler, booms) -> int:
        if booms > 0:
            traveler.energy += 1
        return booms
    return Card(name="Tesla's AC Motor", gold_cost=1, delivery_century=19,
                recycle_value=2, ability_type="passive", on_boom_gain=on_boom_gain)


def first_time_machine() -> Card:
    """Active: travel back to century XXX without spending energy."""
    def effect(traveler, game, context) -> None:
        traveler.century = CENTURY_MAX
    return Card(name="The First Time Machine", gold_cost=4, delivery_century=30,
                recycle_value=3, ability_type="active", active_effect=effect)


def super_motor() -> Card:
    """Passive (recycle): the first time the motor would explode, it doesn't;
    discard 6 booms and recycle this card."""
    def on_explosion_check(traveler, game) -> bool:
        traveler.booms = max(0, traveler.booms - 6)
        return True
    return Card(name="Super Motor", gold_cost=2, delivery_century=27,
                recycle_value=1, ability_type="passive", recycles_on_use=True,
                on_explosion_check=on_explosion_check)


# ── Page 3: Medieval / Classical Tools ───────────────────────────────────

def astrolabe() -> Card:
    """Active (recycle): travel to a century in your era for free and recycle this card."""
    def effect(traveler, game, context) -> None:
        if context is None:
            return
        if same_era(traveler.century, context):
            traveler.century = context
    return Card(name="Astrolabe", gold_cost=3, delivery_century=6,
                recycle_value=1, ability_type="active", recycles_on_use=True,
                active_effect=effect)


def towel() -> Card:
    """Passive: your travel and paradox modules never overload (handled in resolve)."""
    return Card(name="Towel", gold_cost=1, delivery_century=1,
                recycle_value=1, ability_type="passive")


def al_jazaris_automaton() -> Card:
    """Passive: whenever you overload, discard 5 booms."""
    def on_overload(traveler, game) -> None:
        traveler.booms = max(0, traveler.booms - 5)
    return Card(name="Al-Jazari's Automaton", gold_cost=1, delivery_century=12,
                recycle_value=2, ability_type="passive", on_overload=on_overload)


def horse_collar() -> Card:
    """Passive: whenever you activate a travel module, receive 1 energy (handled in resolve)."""
    return Card(name="Horse Collar", gold_cost=3, delivery_century=5,
                recycle_value=2, ability_type="passive")


def viking_shield() -> Card:
    """Passive: the first time each Hour another traveler makes you lose energy,
    you lose 2 fewer.

    Both conditions ("first time this Hour", "another traveler") and the -2 are
    resolved in engine/combat.lose_energy, keyed on card name, so the reduction
    only applies to losses an enemy caused (not travel, explosion or the escape
    valve)."""
    return Card(name="Viking Shield", gold_cost=2, delivery_century=9,
                recycle_value=2, ability_type="passive")


def seismograph() -> Card:
    """Passive: whenever you would gain 1+ booms, gain 2 fewer (min 0)."""
    return Card(name="Seismograph", gold_cost=2, delivery_century=2,
                recycle_value=2, ability_type="passive", on_boom_gain=_reduce_booms(2))


def holy_grail() -> Card:
    """Passive (recycle): the first time you would reach 0 energy, gain 1 energy
    instead and recycle this card (handled in resolve.check_termination)."""
    return Card(name="Holy Grail", gold_cost=3, delivery_century=1,
                recycle_value=1, ability_type="passive", recycles_on_use=True)


def refrigerator() -> Card:
    """Active: choose an active-ability card in your Temporal Receptor, use its ability.

    Golden rule 0.1: this card's text overrides the receptor's "never activated"
    default for this specific interaction (§2.4). The delivered card stays in the
    receptor; only its ability resolves."""
    def effect(traveler, game, context) -> None:
        # context is either the receptor Card, or (Card, subcontext) so a
        # targeted receptor ability still receives its own target/argument.
        if context is None:
            return
        if isinstance(context, tuple):
            card, sub = context
        else:
            card, sub = context, None
        if getattr(card, "active_effect", None) is None:
            return
        card.active_effect(traveler, game, sub)
    return Card(name="Refrigerator", gold_cost=1, delivery_century=18,
                recycle_value=1, ability_type="active", active_effect=effect)


def haralds_bluetooth() -> Card:
    """Atemporal Passive: you may make agreements with non-synchronic travelers (§4.4)."""
    return Card(name="Harald's Bluetooth", gold_cost=1, delivery_century=10,
                recycle_value=2, ability_type="atemporal_passive")


# ── Page 4: Technology / Advanced ────────────────────────────────────────

def agnes_cauldron() -> Card:
    """Passive: whenever another traveler recycles a card, you may steal it.

    Resolved in engine/combat.recycle_card, keyed on card name."""
    return Card(name="Agnes's Cauldron", gold_cost=4, delivery_century=15,
                recycle_value=4, ability_type="passive")


def object_teleporter() -> Card:
    """Active: swap this card for any card equipped by another traveler."""
    def effect(traveler, game, context) -> None:
        if context is None:
            return
        target, target_card = context
        if not _looks_like_traveler(target) or target_card not in target.hand:
            return
        this_card = next((c for c in traveler.hand if c.name == "Object Teleporter"), None)
        if this_card is None:
            return
        # Respect equipment capacity on both sides.
        target.hand.remove(target_card)
        traveler.hand.remove(this_card)
        if traveler.can_hold(target_card):
            traveler.hand.append(target_card)
        if target.can_hold(this_card):
            target.hand.append(this_card)
    return Card(name="Object Teleporter", gold_cost=2, delivery_century=26,
                recycle_value=1, ability_type="active", active_effect=effect)


def prince_draculas_chalice() -> Card:
    """Passive: whenever you make 1+ travelers lose energy, receive 2 energy.

    Resolved centrally in engine/combat.deal_energy, keyed on card name."""
    return Card(name="Prince Dracula's Chalice", gold_cost=3, delivery_century=15,
                recycle_value=2, ability_type="passive")


def joan_of_arcs_armor() -> Card:
    """Passive: whenever you would lose energy, lose 1 fewer (but never below 1)."""
    def on_energy_loss(traveler, amount) -> int:
        return max(1, amount - 1) if amount > 0 else 0
    return Card(name="Joan of Arc's Armor", gold_cost=4, delivery_century=15,
                recycle_value=3, ability_type="passive",
                on_energy_loss=on_energy_loss,
                on_travel_cost=lambda t, cost, d, game=None: max(1, cost - 1) if cost > 0 else 0)


def quantum_computer() -> Card:
    """Passive: has all passive abilities of the cards in your Temporal Receptor.

    Handled by passive_source_cards, which the engine queries for every passive hook."""
    return Card(name="Quantum Computer", gold_cost=2, delivery_century=22,
                recycle_value=2, ability_type="passive")


def automobile() -> Card:
    """Passive (Large Item): add +1 to the value of all your causality generators.

    Resolved in engine/combat.effective_generator, keyed on card name."""
    return Card(name="Automobile", gold_cost=4, delivery_century=19,
                recycle_value=3, ability_type="passive", is_large_item=True)


def relative_dimensions_operative() -> Card:
    """Passive: you may carry 2 additional cards (TravelerState.equipment_capacity)."""
    return Card(name="Relative Dimensions Operative", gold_cost=4, delivery_century=29,
                recycle_value=4, ability_type="passive")


def window_of_time() -> Card:
    """Atemporal Passive: you may always treat other travelers as synchronic."""
    return Card(name="Window of Time", gold_cost=4, delivery_century=28,
                recycle_value=4, ability_type="atemporal_passive")


def porcelain() -> Card:
    """Passive: revealed Market cards cost 1 less; if you lose energy, recycle this card.

    The discount is applied in market.py; the recycle-on-loss trigger fires from
    engine/combat after any energy loss, keyed on card name."""
    return Card(name="Porcelain", gold_cost=1, delivery_century=7,
                recycle_value=1, ability_type="passive")


# ── Page 5: Knowledge / Media ────────────────────────────────────────────

def book_of_mysteries_of_alexandria() -> Card:
    """Active (recycle): choose a Market deck, steal the unrevealed top card and recycle this."""
    def effect(traveler, game, context) -> None:
        deck = context
        if deck is None or not getattr(deck, "_draw", None):
            return
        if not traveler.can_hold(deck._draw[-1]):
            return
        traveler.hand.append(deck._draw.pop())
    return Card(name="Book of Mysteries of Alexandria", gold_cost=2, delivery_century=2,
                recycle_value=1, ability_type="active", recycles_on_use=True,
                active_effect=effect)


def eyeglasses() -> Card:
    """Active (recycle): choose a recycled card, steal it and recycle this card."""
    def effect(traveler, game, context) -> None:
        if context is None:
            return
        deck, card = context
        if card in getattr(deck, "_discard", []) and traveler.can_hold(card):
            deck._discard.remove(card)
            traveler.hand.append(card)
    return Card(name="Eyeglasses", gold_cost=2, delivery_century=13,
                recycle_value=1, ability_type="active", recycles_on_use=True,
                active_effect=effect)


def movable_type_press() -> Card:
    """Passive (Large Item): has all passive abilities of the cards revealed in the Market.

    Handled by passive_source_cards, which reads the game's market_revealed snapshot."""
    return Card(name="Movable-Type Press", gold_cost=4, delivery_century=15,
                recycle_value=4, ability_type="passive", is_large_item=True)


def fishing_reel() -> Card:
    """Active: roll a generator, steal a revealed Market card costing <= the result."""
    def effect(traveler, game, context) -> None:
        deck = context
        if deck is None:
            return
        roll = _rng_of(game).randint(1, 3)
        affordable = [c for c in deck.revealed if c.gold_cost <= roll and traveler.can_hold(c)]
        if affordable:
            card = min(affordable, key=lambda c: c.gold_cost)
            deck.take(card)
            traveler.hand.append(card)
            traveler.is_wanted = True             # §26.4 / §3.4: stealing from the Merchant makes you Wanted
    return Card(name="Fishing Reel", gold_cost=2, delivery_century=4,
                recycle_value=1, ability_type="active", active_effect=effect)


def mona_lisa() -> Card:
    """Active: swap this card for any revealed card in the Market."""
    def effect(traveler, game, context) -> None:
        if context is None:
            return
        deck, chosen = context
        if chosen not in deck._revealed or not traveler.can_hold(chosen):
            return
        this_card = next((c for c in traveler.hand if c.name == "Mona Lisa"), None)
        if this_card is None:
            return
        # Swap in place so Mona Lisa takes the exact slot the chosen card left,
        # keeping four cards revealed without a refill (§6.1).
        idx = deck._revealed.index(chosen)
        deck._revealed[idx] = this_card
        traveler.hand.remove(this_card)
        traveler.hand.append(chosen)
    return Card(name="Mona Lisa", gold_cost=2, delivery_century=16,
                recycle_value=1, ability_type="active", active_effect=effect)


def reality_simulator() -> Card:
    """Passive: non-synchronic travelers cannot buy Market cards (handled in market.py).
    If you cause a present paradox, recycle this card (handled in paradox.py)."""
    return Card(name="Reality Simulator", gold_cost=4, delivery_century=25,
                recycle_value=4, ability_type="passive", recycles_on_use=True)


def first_smartphone() -> Card:
    """Atemporal Passive: you have Market access during the market phase regardless of synchrony."""
    return Card(name="The First Smartphone", gold_cost=1, delivery_century=21,
                recycle_value=2, ability_type="atemporal_passive")


def alan_turings_machine() -> Card:
    """Active: destroy all revealed cards in the Market."""
    def effect(traveler, game, context) -> None:
        deck = context
        if deck is None:
            return
        deck._revealed.clear()
        deck._refill_revealed()
    return Card(name="Alan Turing's Machine", gold_cost=1, delivery_century=20,
                recycle_value=1, ability_type="active", active_effect=effect)


def thomas_edisons_lamp() -> Card:
    """Atemporal Active: choose a Market deck, see and buy the unrevealed top card."""
    def effect(traveler, game, context) -> None:
        deck = context
        if deck is None or not getattr(deck, "_draw", None):
            return
        # Imported here because engine.market imports this module.
        from engine.market import effective_card_cost
        top = deck._draw[-1]
        cost = effective_card_cost(top, traveler)
        if traveler.gold >= cost and traveler.can_hold(top):
            deck._draw.pop()
            traveler.gold -= cost
            traveler.hand.append(top)
    return Card(name="Thomas Edison's Lamp", gold_cost=3, delivery_century=19,
                recycle_value=3, ability_type="atemporal_active", active_effect=effect)


# ── Page 6: More Items ────────────────────────────────────────────────────

def niepces_heliograph() -> Card:
    """Active (recycle): choose any revealed Market card, steal it (free) and recycle this."""
    def effect(traveler, game, context) -> None:
        if context is None:
            return
        deck, chosen = context
        if chosen in deck.revealed and traveler.can_hold(chosen):
            deck.take(chosen)
            traveler.hand.append(chosen)
            traveler.is_wanted = True             # §26.4 / §3.4: stealing from the Merchant makes you Wanted
    return Card(name="Niépce's Heliograph", gold_cost=2, delivery_century=19,
                recycle_value=1, ability_type="active", recycles_on_use=True,
                active_effect=effect)


def woodblock_print() -> Card:
    """Active: choose a revealed active card in the Market, use its ability."""
    def effect(traveler, game, context) -> None:
        if context is None:
            return
        chosen, inner = context
        if getattr(chosen, "active_effect", None):
            chosen.active_effect(traveler, game, inner)
    return Card(name="Woodblock Print", gold_cost=2, delivery_century=4,
                recycle_value=2, ability_type="active", active_effect=effect)


def vending_machine() -> Card:
    """Passive: whenever another traveler buys a revealed Market card, gain 1 gold."""
    def on_market_buy_other(this_t, buyer_t, card, game) -> None:
        if this_t is not buyer_t:
            this_t.gold += 1
    return Card(name="Vending Machine", gold_cost=2, delivery_century=1,
                recycle_value=2, ability_type="passive",
                on_market_buy_other=on_market_buy_other)


def gunpowder() -> Card:
    """Passive: whenever you make another traveler lose energy, they lose 1 more.

    Resolved centrally in engine/combat.lose_energy, keyed on card name."""
    return Card(name="Gunpowder", gold_cost=3, delivery_century=9,
                recycle_value=2, ability_type="passive")


def spear_of_destiny() -> Card:
    """Passive: whenever you cause a future paradox, also cause a past paradox of equal value.

    Resolved in engine/paradox.py, keyed on card name."""
    return Card(name="Spear of Destiny", gold_cost=4, delivery_century=1,
                recycle_value=3, ability_type="passive")


def charlemagnes_sword() -> Card:
    """Active: a synchronic traveler loses energy equal to your current gold."""
    def effect(traveler, game, context) -> None:
        target = context
        if not _looks_like_traveler(target) or target is traveler:
            return
        if target.century != traveler.century:
            return
        combat.deal_energy(game, traveler, [target], traveler.gold, kind="weapon")
    return Card(name="Charlemagne's Sword", gold_cost=3, delivery_century=8,
                recycle_value=3, ability_type="active", active_effect=effect)


def attilas_sword() -> Card:
    """Active: all travelers in eras older than yours lose 3 energy."""
    def effect(traveler, game, context) -> None:
        targets = [t for t in game.travelers
                   if t is not traveler and not t.awaiting_respawn
                   and in_older_era(t.century, traveler.century)]
        combat.deal_energy(game, traveler, targets, 3, kind="weapon")
    return Card(name="Attila's Sword", gold_cost=4, delivery_century=5,
                recycle_value=3, ability_type="active", active_effect=effect)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

# The order matters: MerchantDeck shuffles this list with the game's seed, so
# reordering it would change every seeded game.
ALL_CARDS: list[Callable[[], Card]] = [
    # Page 1
    ferguson_rifle, fire_lance, ching_shihs_red_flag,
    queen_annes_revenge_cannon, laser_gun, portal_gun,
    gunpowder_revolver, excalibur, laser_sword,
    # Page 2
    navigation_compass, mechanical_clock, galileos_telescope,
    da_vincis_flying_machine, james_watts_steam_engine, gerardus_mercators_map,
    teslas_ac_motor, first_time_machine, super_motor,
    # Page 3
    astrolabe, towel, al_jazaris_automaton,
    horse_collar, viking_shield, seismograph,
    holy_grail, refrigerator, haralds_bluetooth,
    # Page 4
    agnes_cauldron, object_teleporter, prince_draculas_chalice,
    joan_of_arcs_armor, quantum_computer, automobile,
    relative_dimensions_operative, window_of_time, porcelain,
    # Page 5
    book_of_mysteries_of_alexandria, eyeglasses, movable_type_press,
    fishing_reel, mona_lisa, reality_simulator,
    first_smartphone, alan_turings_machine, thomas_edisons_lamp,
    # Page 6
    niepces_heliograph, woodblock_print, vending_machine,
    gunpowder, spear_of_destiny, charlemagnes_sword, attilas_sword,
]

assert len(ALL_CARDS) == TOTAL_CARDS, f"Expected {TOTAL_CARDS} cards, got {len(ALL_CARDS)}"


def build_all_cards() -> list[Card]:
    """Return all 52 fresh Card instances, in catalogue order (unshuffled)."""
    return [f() for f in ALL_CARDS]


# A fixed ranking of all 52 cards, for the places that need a deterministic
# tie-break between two equally good cards (the strategies' shopping lists).
# It is the alphabetical order of the cards' original Portuguese names, which
# is what those tie-breaks compared before the cards were renamed in English.
# Keeping the order explicit keeps every published benchmark reproducible.
TIEBREAK_ORDER: tuple[str, ...] = (
    "Attila's Sword", "Thomas Edison's Lamp", "Alan Turing's Machine", "Laser Gun",
    "Portal Gun", "Joan of Arc's Armor", "Astrolabe", "Al-Jazari's Automaton",
    "Ching Shih's Red Flag", "Navigation Compass", "Agnes's Cauldron",
    "Queen Anne's Revenge Cannon", "Fishing Reel", "Automobile", "Horse Collar",
    "Quantum Computer", "Prince Dracula's Chalice", "Harald's Bluetooth",
    "Viking Shield", "Laser Sword", "Charlemagne's Sword", "Excalibur", "Refrigerator",
    "Niépce's Heliograph", "Window of Time", "Fire Lance", "Spear of Destiny",
    "Book of Mysteries of Alexandria", "Gerardus Mercator's Map", "Mona Lisa",
    "Tesla's AC Motor", "da Vinci's Flying Machine", "James Watt's Steam Engine",
    "Vending Machine", "Relative Dimensions Operative", "Porcelain",
    "Movable-Type Press", "The First Time Machine", "The First Smartphone",
    "Gunpowder", "Mechanical Clock", "Gunpowder Revolver", "Ferguson Rifle",
    "Holy Grail", "Reality Simulator", "Seismograph", "Super Motor",
    "Galileo's Telescope", "Object Teleporter", "Towel", "Woodblock Print",
    "Eyeglasses",
)
TIEBREAK_RANK: dict[str, int] = {name: i for i, name in enumerate(TIEBREAK_ORDER)}

assert set(TIEBREAK_ORDER) == {f().name for f in ALL_CARDS}

"""
engine/market.py
================
Merchant movement and Market phase resolution (§17-19).

The Market phase is Phase 2 of each Hour. It runs before generators are
rolled (Phase 3). Each traveler synchronic with the Merchant may perform
Buy, Renew, and Declare actions during their priority.

Key rules implemented here:
    §17: Merchant movement and deck management
    §18: Market actions: Buy, Renew, Declare
    §19: Secret Market (fixed on century XI)
    §31.1d: Game ends when Merchant deck is exhausted
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field
from engine.state import GameState, TravelerState
from engine.cards import Card, build_all_cards
from engine.constants import SECRET_MARKET_CARD_COUNT
from engine.constants import (
    MERCHANT_UPGRADE_1_CENTURY,
    MERCHANT_UPGRADE_2_CENTURY,
    SECRET_MARKET_CENTURY,
    SECRET_MARKET_CARD_COUNT,
    CENTURY_MAX,
    DECLARE_COST,
    RENEW_COST_BASE,
    MERCHANT_REVEALED_CARDS,
)


# ---------------------------------------------------------------------------
# Market action types
# ---------------------------------------------------------------------------

@dataclass
class BuyAction:
    card: Card

@dataclass
class RenewAction:
    card: Card

@dataclass
class DeclareAction:
    pass

@dataclass
class UseCardAction:
    card: Card
    context: object = None  # card-specific context (target, rng, deck, etc.)

@dataclass
class PassAction:
    pass

MarketAction = BuyAction | RenewAction | DeclareAction | UseCardAction | PassAction


# ---------------------------------------------------------------------------
# Merchant deck
# ---------------------------------------------------------------------------

class SecretMarket:
    """
    The Secret Market (§19): 12 randomly selected cards hidden at setup,
    revealed one at a time once a traveler reaches century XI.

    Cards are drawn one by one: only the top card is visible and buyable.
    No renewal is possible in the Secret Market.
    """

    def __init__(self, cards: list[Card]) -> None:
        self._hidden: list[Card] = list(cards)   # pre-shuffled by MerchantDeck
        self._current: Card | None = None
        self._is_open: bool = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def current_card(self) -> Card | None:
        return self._current

    @property
    def is_empty(self) -> bool:
        return self._is_open and self._current is None

    def open(self) -> None:
        """Reveal the first card (called when a traveler first reaches XI)."""
        if not self._is_open:
            self._is_open = True
            self._advance()

    def take(self, card: Card) -> None:
        """A traveler buys the current card; advance to the next hidden one."""
        assert card is self._current, "can only take the currently revealed card"
        self._advance()

    def _advance(self) -> None:
        self._current = self._hidden.pop(0) if self._hidden else None

    def snapshot_revealed(self) -> list[Card]:
        """Return the single visible card (for strategy inspection)."""
        return [self._current] if self._current is not None else []


class MerchantDeck:
    """
    Manages the Merchant's card inventory: draw pile, revealed cards,
    and discard pile. Always maintains up to 4 revealed cards (§17.1).

    At construction the full 52-card deck is shuffled and split: 12 cards
    go to the Secret Market (randomly chosen each game), 40 to the Merchant.
    Access the Secret Market via ``deck.secret_market``.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        all_cards: list[Card] = build_all_cards()
        self._rng.shuffle(all_cards)
        # First 12 (random) go to the Secret Market; rest to Merchant draw pile.
        secret_cards = all_cards[:SECRET_MARKET_CARD_COUNT]
        self._draw: list[Card] = all_cards[SECRET_MARKET_CARD_COUNT:]
        self._discard: list[Card] = []
        self._revealed: list[Card] = []
        self.secret_market: SecretMarket = SecretMarket(secret_cards)
        self._refill_revealed()

    @property
    def revealed(self) -> list[Card]:
        return list(self._revealed)

    @property
    def draw_count(self) -> int:
        return len(self._draw)

    @property
    def is_empty(self) -> bool:
        """True when the draw pile and revealed cards are both exhausted."""
        return len(self._draw) == 0 and len(self._revealed) == 0

    def take(self, card: Card) -> None:
        """Remove a card from the revealed list and immediately refill."""
        self._revealed.remove(card)
        self._refill_revealed()

    def discard(self, card: Card) -> None:
        """Move a card to the discard pile (used for Renew action)."""
        self._revealed.remove(card)
        self._discard.append(card)
        self._refill_revealed()

    def _refill_revealed(self) -> None:
        while len(self._revealed) < MERCHANT_REVEALED_CARDS and self._draw:
            self._revealed.append(self._draw.pop())

    def snapshot_revealed(self) -> list[Card]:
        """Return a copy of the current revealed cards for strategy inspection."""
        return list(self._revealed)


# ---------------------------------------------------------------------------
# Merchant movement (§17)
# ---------------------------------------------------------------------------

def merchant_target(game: GameState) -> int:
    """
    Determine the century the Merchant is trying to move toward (§17).

    Priority:
    1. Richest non-synchronic traveler (gold desc, then priority order)
    2. Secret Market (XI) if all travelers are synchronic with Merchant
    3. XXX if also synchronic with Secret Market
    """
    active = [t for t in game.travelers if not t.awaiting_respawn]
    merchant_pos = game.merchant_century

    non_sync = [t for t in active if t.century != merchant_pos]
    if non_sync:
        # Richest non-synchronic traveler. §17.7: gold ties break by Priority,
        # which is century (closest to future) → energy → duel (§19.1). The duel
        # is approximated by list order. (BUG-006)
        target_traveler = max(
            non_sync,
            key=lambda t: (t.gold, t.century, t.energy, -game.travelers.index(t)),
        )
        return target_traveler.century

    # All travelers are synchronic with Merchant
    if merchant_pos != SECRET_MARKET_CENTURY:
        return SECRET_MARKET_CENTURY

    # Also synchronic with Secret Market
    return CENTURY_MAX


def move_merchant(game: GameState, rng: random.Random) -> None:
    """
    Roll movement dice and move the Merchant toward its target (§17).
    Mutates game.merchant_century in place.
    The Merchant stops at XXX and never enters Year Zero.
    """
    target = merchant_target(game)
    dice_count = _merchant_dice_count(game)

    total_roll = sum(rng.randint(1, 3) for _ in range(dice_count))

    current = game.merchant_century
    direction = 1 if target > current else -1
    steps_remaining = total_roll

    while steps_remaining > 0:
        next_pos = current + direction
        # Clamp: never go below 1 (Year Zero is 0, Merchant can't go there)
        # and never above XXX
        next_pos = max(1, min(CENTURY_MAX, next_pos))
        current = next_pos
        steps_remaining -= 1
        if current == target:
            break

    game.merchant_century = current


def _merchant_dice_count(game: GameState) -> int:
    """Return how many d3 the Merchant rolls based on upgrade triggers (§17.6)."""
    if game.merchant_upgrade_x_triggered:
        return 3
    if game.merchant_upgrade_xx_triggered:
        return 2
    return 1


def check_merchant_upgrades(game: GameState) -> None:
    """
    Update Merchant upgrade flags if any traveler ended an Hour on XX or X.
    Called at end of each Hour after travel resolves (§17.6).
    """
    # §17.11: an upgrade triggers only when a traveler *ends the Hour* exactly on
    # the relevant century (X or XX), not merely at or below it. A traveler who
    # skipped the century in a single large move does not trigger it. (BUG-005)
    for t in game.travelers:
        if t.century == MERCHANT_UPGRADE_2_CENTURY and not game.merchant_upgrade_x_triggered:
            game.merchant_upgrade_x_triggered = True
        if t.century == MERCHANT_UPGRADE_1_CENTURY and not game.merchant_upgrade_xx_triggered:
            game.merchant_upgrade_xx_triggered = True


# ---------------------------------------------------------------------------
# Market phase resolution (§18)
# ---------------------------------------------------------------------------

_ATEMPORAL_MARKET_CARDS = {"Janela do Tempo", "Primeiro Smartphone"}


def _has_atemporal_access(traveler: TravelerState) -> bool:
    return any(c.name in _ATEMPORAL_MARKET_CARDS for c in traveler.hand)


def _traveler_can_access_market(traveler: TravelerState, game: GameState,
                                market_pos: int) -> bool:
    """True if this traveler may participate in the Merchant market this phase."""
    if traveler.century == market_pos:
        return True
    if _has_atemporal_access(traveler):
        return True
    if traveler.market_voucher:
        return True
    return False


def _traveler_can_access_secret_market(traveler: TravelerState) -> bool:
    """True if this traveler may access the Secret Market (§24.4, §16.2, §23.3).

    Physically at century XI, or an atemporal card grants market access,
    or the traveler holds a market voucher (Time II reward).
    """
    if traveler.century == SECRET_MARKET_CENTURY:
        return True
    if _has_atemporal_access(traveler):
        return True
    if traveler.market_voucher:
        return True
    return False


def effective_card_cost(card: Card, traveler: TravelerState) -> int:
    """Card cost after the Porcelana discount (-1g while held, never below 1g).

    The discount applies to every revealed Market card while the traveler holds
    Porcelana; the floor is 1 gold, not 0 (a card never becomes free).
    """
    if any(c.name == "Porcelana" for c in traveler.hand):
        return max(1, card.gold_cost - 1)
    return card.gold_cost


# Backwards-compatible private alias (older call sites).
_effective_card_cost = effective_card_cost


def _notify_buy_observers(buyer: TravelerState, card: Card,
                          game: GameState, all_travelers: list[TravelerState]) -> None:
    """Fire on_market_buy_other hooks on all other travelers (Máquina de Venda)."""
    from engine.cards import passive_source_cards
    for t in all_travelers:
        if t is buyer or t.awaiting_respawn:
            continue
        for held in passive_source_cards(t, game):
            if held.on_market_buy_other:
                held.on_market_buy_other(t, buyer, card, game)


def _simulador_in_play(game: GameState) -> bool:
    """True if any traveler equips Simulador da Realidade (§card)."""
    return any(
        c.name == "Simulador da Realidade"
        for t in game.travelers if not t.awaiting_respawn
        for c in t.hand
    )


def resolve_market_phase(
    game: GameState,
    deck: MerchantDeck,
    strategies: dict,
    rng: random.Random,
) -> bool:
    """
    Run the full Market phase for all travelers who can access the market.

    Two markets exist:
    - Merchant market: any traveler synchronic with the Merchant (or via
      Janela/Smartphone). Shows 4 random cards; supports Buy and Renew.
    - Secret Market (§19): permanently at century XI. Shows 1 card at a
      time; supports Buy only (no renewal). Opens the first time any traveler
      ends an Hour on century XI.

    Returns True if the game should end (Merchant deck exhausted, §31.1d).
    Travelers act in priority order (list order = §8 priority).
    """
    sm = deck.secret_market
    market_pos = game.merchant_century

    # Open the Secret Market if any active traveler is at century XI.
    if not sm.is_open:
        if any(t.century == SECRET_MARKET_CENTURY and not t.awaiting_respawn
               for t in game.travelers):
            sm.open()
            game.secret_market_open = True

    # Track travelers whose voucher was already consumed this phase.
    # One voucher grants access to all markets in the phase (§23.3 Time II).
    voucher_consumed: set[str] = set()

    # --- Secret Market access (§24.4, §16.2, §23.3) ---
    if sm.is_open and sm.current_card is not None:
        for traveler in game.travelers:
            if traveler.awaiting_respawn:
                continue
            if not _traveler_can_access_secret_market(traveler):
                continue
            # §24.5 / §33.3: Wanted travelers cannot Buy at the Secret Market.
            if traveler.is_wanted:
                continue
            # Consume a voucher only if it was the sole reason for access.
            _sm_natural = (traveler.century == SECRET_MARKET_CENTURY
                           or _has_atemporal_access(traveler))
            if not _sm_natural and traveler.market_voucher > 0:
                traveler.market_voucher -= 1
                voucher_consumed.add(traveler.name)
            strategy = strategies[traveler.name]
            while sm.current_card is not None:
                revealed_sm = sm.snapshot_revealed()
                action = strategy.choose_market_action(traveler, game, revealed_sm, 999)
                if isinstance(action, PassAction):
                    break
                elif isinstance(action, BuyAction):
                    card = action.card
                    if card is not sm.current_card:
                        break
                    cost = _effective_card_cost(card, traveler)
                    if traveler.gold < cost:
                        break
                    if not traveler.can_hold(card):
                        break
                    traveler.gold -= cost
                    sm.take(card)
                    traveler.hand.append(card)
                    _notify_buy_observers(traveler, card, game, game.travelers)
                    from engine.state import ItemEvent
                    game.item_events.append(ItemEvent(
                        hour=game.hour,
                        traveler=traveler.name,
                        event_type="bought",
                        card_name=card.name,
                        century=traveler.century,
                    ))
                else:
                    break

    # --- Merchant market access ---
    for traveler in game.travelers:
        if traveler.awaiting_respawn:
            continue
        if not _traveler_can_access_market(traveler, game, market_pos):
            continue

        strategy = strategies[traveler.name]
        renew_cost = RENEW_COST_BASE
        # Consume one voucher only if it is what granted this traveler access
        # and it hasn't already been consumed this phase (one voucher covers
        # all markets in the phase: §23.3 Time II).
        _natural = (traveler.century == market_pos or _has_atemporal_access(traveler))
        if (not _natural and traveler.market_voucher > 0
                and traveler.name not in voucher_consumed):
            traveler.market_voucher -= 1
            voucher_consumed.add(traveler.name)

        while True:
            revealed = deck.snapshot_revealed()
            if not revealed and not traveler.is_wanted:
                break

            action = strategy.choose_market_action(traveler, game, revealed, renew_cost)

            if isinstance(action, PassAction):
                break

            elif isinstance(action, BuyAction):
                card = action.card
                if card not in deck.revealed:
                    break
                # Simulador blocks non-synchronic travelers, but atemporal
                # access cards make the traveler synchronic for market purposes.
                if (traveler.century != market_pos
                        and not _has_atemporal_access(traveler)
                        and not traveler.market_voucher
                        and _simulador_in_play(game)):
                    break
                cost = _effective_card_cost(card, traveler)
                if traveler.gold < cost:
                    break
                if not traveler.can_hold(card):
                    break
                traveler.gold -= cost
                deck.take(card)
                traveler.hand.append(card)
                _notify_buy_observers(traveler, card, game, game.travelers)
                from engine.state import ItemEvent
                game.item_events.append(ItemEvent(
                    hour=game.hour,
                    traveler=traveler.name,
                    event_type="bought",
                    card_name=card.name,
                    century=traveler.century,
                ))
                if deck.is_empty:
                    game.game_over = True
                    game.game_over_reason = "merchant_empty"
                    return True

            elif isinstance(action, RenewAction):
                card = action.card
                if card not in deck.revealed:
                    break
                if traveler.gold < renew_cost:
                    break
                traveler.gold -= renew_cost
                renew_cost += 1
                deck.discard(card)
                from engine.state import ItemEvent
                game.item_events.append(ItemEvent(
                    hour=game.hour,
                    traveler=traveler.name,
                    event_type="renewed",
                    card_name=card.name,
                    century=traveler.century,
                ))
                if deck.is_empty:
                    game.game_over = True
                    game.game_over_reason = "merchant_empty"
                    return True

            elif isinstance(action, DeclareAction):
                if not traveler.is_wanted or traveler.gold < DECLARE_COST:
                    break
                traveler.gold -= DECLARE_COST
                traveler.is_wanted = False

            elif isinstance(action, UseCardAction):
                card = action.card
                if card not in traveler.hand:
                    break
                if card.active_effect is None:
                    break
                card.active_effect(traveler, game, action.context)
                if card.recycles_on_use and card in traveler.hand:
                    traveler.hand.remove(card)
                    deck._discard.append(card)

    move_merchant(game, rng)
    game.market_revealed = deck.snapshot_revealed()
    return False


# ---------------------------------------------------------------------------
# Delivery phase resolution (§21)
# ---------------------------------------------------------------------------

def resolve_deliveries(game: GameState, strategies: dict | None = None) -> None:
    """
    Phase 1: each traveler delivers cards whose delivery_century matches their
    current century (§21). Delivery earns 1 CP and adds the card to the
    Temporal Receptor. The delivery period is derived from the card's century.

    Strategy may choose which deliverable cards to hold back via
    choose_cards_to_deliver() (default: deliver all eligible cards).
    """
    from engine.timeline import periods_for_century

    for traveler in game.travelers:
        if traveler.awaiting_respawn:
            continue

        deliverable = [
            c for c in traveler.hand
            if c.delivery_century == traveler.century
        ]
        if not deliverable:
            continue

        # Ask strategy which ones to actually deliver
        if strategies and traveler.name in strategies:
            to_deliver = strategies[traveler.name].choose_cards_to_deliver(
                traveler, game, deliverable
            )
        else:
            to_deliver = deliverable  # default: deliver all

        for card in to_deliver:
            if card not in traveler.hand:
                continue
            traveler.hand.remove(card)
            traveler.temporal_receptor.append(card.name)
            traveler.receptor_cards.append(card)
            traveler.contract_points += 1
            game.cp_rewards_pending.append(traveler.name)
            # Track which periods are now covered
            for period in periods_for_century(card.delivery_century):
                traveler.delivered_periods.add(period)
            from engine.state import ItemEvent
            game.item_events.append(ItemEvent(
                hour=game.hour,
                traveler=traveler.name,
                event_type="delivered",
                card_name=card.name,
                century=traveler.century,
            ))


def check_temporal_receptor_win(game: GameState) -> bool:
    """
    Check win condition (b): a traveler has completed all 3 delivery periods.
    Sets game_over fields if triggered. Returns True if game is over.
    """
    for t in game.travelers:
        if len(t.delivered_periods) >= 3:
            game.game_over = True
            game.game_over_reason = "full_receptor"
            game.winner = t.name
            return True
    return False

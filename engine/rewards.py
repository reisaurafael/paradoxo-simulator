"""
engine/rewards.py
=================
Reward resolution for Contract Points (§26).

Each CP earned immediately triggers one reward: the traveler chooses a
category (Chaos, Time, Resource), never the same twice in a row (§26.2), and
then rolls one generator (1d3) and applies the result.

Reward table (§26.3):
  Chaos I  : Paradox across the entire timeline, 3 damage to every other traveler.
  Chaos II : Destroy 1 card (any revealed Market card, open Secret Market card,
             or equipped card; never from a Temporal Receptor).
  Chaos III: Move the Merchant to any Century (except Year Zero).

  Time I   : A solo main (dice) phase.
  Time II  : Market voucher (Timeless: buy at any Market even non-synchronic;
             Secret Market only if open). Vouchers accumulate.
  Time III : Item voucher (Timeless: all other Travelers count as synchronic
             with you for that activation phase). Vouchers accumulate.

  Resource I  : +3 Energy and +3 Gold.
  Resource II : Steal 1 item from the Merchant's revealed stock.
  Resource III: Matrix buff, choose one module; it reads +1 during that
                module's resolution (permanent, one buff per module max,
                up to all 9 modules over the game).
"""

from __future__ import annotations
import random
from engine.state import GameState, TravelerState


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_pending_rewards(
    game: GameState,
    deck,           # MerchantDeck: imported lazily to avoid circular imports
    strategies: dict,
    rng: random.Random,
) -> None:
    """
    Process all CP rewards queued in game.cp_rewards_pending.

    Should be called after each phase that can award CP (delivery, travel,
    end-of-game bonuses). Solo-phase rewards are collected separately in
    game.solo_phases_pending and executed by the runner after Phase 4.
    """
    while game.cp_rewards_pending:
        name = game.cp_rewards_pending.pop(0)
        traveler = next((t for t in game.travelers if t.name == name), None)
        if traveler is None or traveler.awaiting_respawn:
            continue
        strategy = strategies.get(name)
        if strategy is None:
            continue
        _fire_one_reward(traveler, game, deck, strategies, rng, strategy)


def _fire_one_reward(
    traveler: TravelerState,
    game: GameState,
    deck,
    strategies: dict,
    rng: random.Random,
    strategy,
) -> None:
    available = [c for c in ("Chaos", "Time", "Resource")
                 if c != traveler.last_reward_category]
    category = strategy.choose_reward_category(traveler, game, available)
    if category not in available:
        category = available[0]
    traveler.last_reward_category = category
    die = rng.randint(1, 3)
    _apply_reward(traveler, game, deck, strategies, rng, category, die, strategy)


# ---------------------------------------------------------------------------
# Reward effects
# ---------------------------------------------------------------------------

def _apply_reward(
    traveler: TravelerState,
    game: GameState,
    deck,
    strategies: dict,
    rng: random.Random,
    category: str,
    die: int,
    strategy,
) -> None:
    if category == "Chaos":
        _chaos(traveler, game, deck, die, strategy)
    elif category == "Time":
        _time(traveler, game, die)
    else:
        _resource(traveler, game, deck, die, strategy)


def _chaos(traveler: TravelerState, game: GameState, deck, die: int, strategy) -> None:
    if die == 1:
        # Chaos I: Timeline paradox, 3 damage to every other active traveler.
        # Passes through the full combat pipeline (§18), so Escudo Viking,
        # Armadura, Espada de Laser, Pólvora, Cálice all apply.
        from engine import combat
        targets = [t for t in game.travelers if t is not traveler and not t.awaiting_respawn]
        combat.deal_energy(game, traveler, targets, 3, kind="paradox")

    elif die == 2:
        # Chaos II: Destroy one card chosen by strategy.
        # Candidates: revealed Merchant cards, open Secret Market card, other travelers' equipped cards.
        # Never from a Temporal Receptor.
        candidates = []
        if deck is not None:
            candidates.extend(deck.snapshot_revealed())
            sm = getattr(deck, "secret_market", None)
            if sm is not None and sm.is_open and sm.current_card is not None:
                candidates.append(sm.current_card)
        for t in game.travelers:
            if t is not traveler and not t.awaiting_respawn:
                candidates.extend(t.hand)

        if not candidates:
            return

        target_card = strategy.choose_chaos_destroy_target(traveler, game, candidates)
        if target_card is None or target_card not in candidates:
            target_card = candidates[0]

        # Remove the card from wherever it lives.
        if deck is not None:
            revealed = deck.snapshot_revealed()
            if target_card in revealed:
                try:
                    deck._revealed.remove(target_card)
                    deck._refill_revealed()
                except (ValueError, AttributeError):
                    pass
                return
            sm = getattr(deck, "secret_market", None)
            if sm is not None and target_card is sm.current_card:
                sm._advance()
                return
        for t in game.travelers:
            if target_card in t.hand:
                t.hand.remove(target_card)
                from engine.state import ItemEvent
                game.item_events.append(ItemEvent(
                    hour=game.hour,
                    traveler=t.name,
                    event_type="destroyed",
                    card_name=target_card.name,
                    century=t.century,
                ))
                return

    else:
        # Chaos III: Teleport the Merchant to a century chosen by strategy (not Year Zero).
        chosen = strategy.choose_chaos_merchant_century(traveler, game)
        from engine.constants import CENTURY_MAX
        chosen = max(1, min(CENTURY_MAX, chosen))
        game.merchant_century = chosen


def _time(traveler: TravelerState, game: GameState, die: int) -> None:
    if die == 1:
        # Time I: Solo generators phase, deferred; runner resolves after Phase 4.
        game.solo_phases_pending.append(traveler.name)
    elif die == 2:
        # Time II: Market voucher, may buy at any market (accumulates).
        traveler.market_voucher += 1
    else:
        # Time III: Item voucher, all others count as synchronic this activation (accumulates).
        traveler.item_voucher += 1


def _resource(traveler: TravelerState, game: GameState, deck, die: int, strategy) -> None:
    if die == 1:
        # Resource I: +3 energy and +3 gold.
        traveler.energy += 3
        traveler.gold += 3

    elif die == 2:
        # Resource II: Steal 1 item from the Merchant's revealed stock.
        if deck is None:
            return
        revealed = deck.snapshot_revealed()
        if not revealed:
            return
        target_card = strategy.choose_resource_steal_target(traveler, game, revealed)
        if target_card is None or target_card not in revealed:
            if not traveler.can_hold(revealed[0]):
                return
            target_card = revealed[0]
        if not traveler.can_hold(target_card):
            return
        try:
            deck._revealed.remove(target_card)
            deck._refill_revealed()
        except (ValueError, AttributeError):
            return
        traveler.hand.append(target_card)
        from engine.state import ItemEvent
        game.item_events.append(ItemEvent(
            hour=game.hour,
            traveler=traveler.name,
            event_type="bought",  # treated as acquisition; open question on Wanted status
            card_name=target_card.name,
            century=traveler.century,
        ))

    else:
        # Resource III: Permanent matrix buff, +1 to chosen module's generator value.
        already_buffed = set(traveler.matrix_buffs.keys())
        if len(already_buffed) >= 9:
            return  # all modules already buffed
        module_index = strategy.choose_matrix_buff_module(traveler, game)
        if module_index not in range(9) or module_index in already_buffed:
            # Fallback: first unbuffed module
            module_index = next((m for m in range(9) if m not in already_buffed), None)
        if module_index is not None:
            traveler.matrix_buffs[module_index] = 1

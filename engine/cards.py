"""
engine/cards.py
===============
All 52 Paradoxo cards with their rules-engine representation.

Each Card has:
    name:             official card name (Portuguese)
    gold_cost:        purchase price in gold (top-left number)
    delivery_century: the exact century where this card is delivered to the
                      Temporal Receptor for 1 CP (§9)
    recycle_value:    energy gained when recycled via the Recycle action, and
                      the amount added to respawn energy on termination
                      (bottom-right circled number, §21 / §28.2)
    ability_type:     "passive" | "active" | "atemporal_passive" | "atemporal_active"
    is_large_item:    True if the card occupies both equipment slots (§12.1)
    recycles_on_use:  True for actives whose own text recycles them on use

Effect hooks are callables invoked by the engine at resolution time. Passive
hooks have fixed signatures (see below); cross-traveler behaviours that depend
on *who* caused something (Pólvora, Cálice, Espada de Laser, Escudo Viking,
Caldeirão, Carro) are resolved centrally in engine/combat.py by card name, not
through a per-card hook, so they apply uniformly to every damage source.

    on_travel_cost(traveler, cost, direction, game=None) -> int
    on_recharge_energy(traveler, amount) -> int
    on_boom_gain(traveler, booms) -> int
    on_overload(traveler, game) -> None
    on_energy_loss(traveler, amount) -> int
    on_explosion_check(traveler, game) -> bool   # True = prevent explosion
    on_hour_end(traveler, game) -> None
    on_market_buy_other(this_t, buyer_t, card, game) -> None
    active_effect(traveler, game, context) -> None

Split: 40 cards go to the Merchant deck, 12 to the Secret Market (§17.2 / §24).
SECRET_MARKET_NAMES lists the 12 Secret Market card names.
"""

from __future__ import annotations
import random
from dataclasses import dataclass
from typing import Callable, Optional, TYPE_CHECKING

from engine import combat

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
    on_recharge_energy: Optional[Callable] = None   # (traveler, amount) -> int
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

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Card) and self.name == other.name


# ---------------------------------------------------------------------------
# Passive aggregation: Computador Quântico and Prensa Móvel (§meta cards)
# ---------------------------------------------------------------------------

def passive_source_cards(traveler: "TravelerState", game: "GameState | None" = None) -> list[Card]:
    """
    Return every card whose *passive* abilities currently apply to `traveler`.

    This is the held equipment plus, by way of two meta-cards:
      - Computador Quântico: copies the passives of cards in the traveler's
        own Temporal Receptor (§card).
      - Prensa Móvel: copies the passives of the cards currently revealed in
        the Merchant Market (§card); requires `game` to see the revealed cards.

    The engine queries passives through this function instead of `traveler.hand`
    directly, so the two meta-cards work everywhere automatically.
    """
    cards = list(traveler.hand)
    if any(c.name == "Computador Quântico" for c in traveler.hand):
        cards += list(getattr(traveler, "receptor_cards", []))
    if game is not None and any(c.name == "Prensa Móvel" for c in traveler.hand):
        revealed = getattr(game, "market_revealed", None) or []
        cards += [c for c in revealed if c.name != "Prensa Móvel"]
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


def _same_era(a_century: int, b_century: int) -> bool:
    from engine.timeline import eras_for_century
    return bool(set(eras_for_century(a_century)) & set(eras_for_century(b_century)))


def _rng_of(game: "GameState") -> random.Random:
    rng = getattr(game, "rng", None)
    return rng if isinstance(rng, random.Random) else random.Random()


# ---------------------------------------------------------------------------
# Card catalogue: 52 unique cards (PDF page order)
# ---------------------------------------------------------------------------

# ── Page 1: Weapons / Combat ─────────────────────────────────────────────

def rifle_fergunson() -> Card:
    """Active: choose a traveler in your era, they lose 1 energy per century of distance."""
    def effect(traveler, game, context) -> None:
        target = context
        if not _looks_like_traveler(target) or target is traveler:
            return
        if not _same_era(traveler.century, target.century):
            return
        from engine.timeline import distance
        dmg = distance(traveler.century, target.century)
        combat.deal_energy(game, traveler, [target], dmg, kind="weapon")
    return Card(name="Rifle Fergunson", gold_cost=3, delivery_century=18,
                recycle_value=3, ability_type="active", active_effect=effect)


def lanca_de_fogo() -> Card:
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
    return Card(name="Lança de Fogo", gold_cost=3, delivery_century=10,
                recycle_value=1, ability_type="active", recycles_on_use=True,
                active_effect=effect)


def bandeira_vermelha_da_ching_shih() -> Card:
    """Active: traveler in your era loses 3 energy and you steal 1 gold from them."""
    def effect(traveler, game, context) -> None:
        target = context
        if not _looks_like_traveler(target) or target is traveler:
            return
        if not _same_era(traveler.century, target.century):
            return
        combat.deal_energy(game, traveler, [target], 3, kind="weapon")
        stolen = min(1, target.gold)
        target.gold -= stolen
        traveler.gold += stolen
    return Card(name="Bandeira Vermelha da Ching Shih", gold_cost=3, delivery_century=19,
                recycle_value=3, ability_type="active", active_effect=effect)


def canhao_de_vinganca_da_rainha_anne() -> Card:
    """Active (Large Item): every other traveler in your era loses 5 energy."""
    def effect(traveler, game, context) -> None:
        targets = [t for t in game.travelers
                   if t is not traveler and not t.awaiting_respawn
                   and _same_era(traveler.century, t.century)]
        combat.deal_energy(game, traveler, targets, 5, kind="weapon")
    return Card(name="Canhão de Vingança da Rainha Anne", gold_cost=4, delivery_century=17,
                recycle_value=4, ability_type="active", is_large_item=True,
                active_effect=effect)


def arma_de_laser() -> Card:
    """Active: a synchronic traveler loses 6 energy."""
    def effect(traveler, game, context) -> None:
        target = context
        if not _looks_like_traveler(target) or target is traveler:
            return
        if target.century != traveler.century:
            return
        combat.deal_energy(game, traveler, [target], 6, kind="weapon")
    return Card(name="Arma de Laser", gold_cost=4, delivery_century=23,
                recycle_value=3, ability_type="active", active_effect=effect)


def arma_de_portais() -> Card:
    """Active: a traveler in your era is transported to your century."""
    def effect(traveler, game, context) -> None:
        target = context
        if not _looks_like_traveler(target) or target is traveler:
            return
        if not _same_era(traveler.century, target.century):
            return
        target.century = traveler.century
    return Card(name="Arma de Portais", gold_cost=3, delivery_century=30,
                recycle_value=3, ability_type="active", active_effect=effect)


def revolver_de_polvora() -> Card:
    """Active: a synchronic traveler loses 3 energy per item they have equipped."""
    def effect(traveler, game, context) -> None:
        target = context
        if not _looks_like_traveler(target) or target is traveler:
            return
        if target.century != traveler.century:
            return
        dmg = 3 * len(target.hand)
        combat.deal_energy(game, traveler, [target], dmg, kind="weapon")
    return Card(name="Revolver de Pólvora", gold_cost=2, delivery_century=13,
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


def espada_de_laser() -> Card:
    """Passive: if another traveler makes you lose energy, they lose the same amount.

    Resolved centrally in engine/combat.py (reflection), keyed on card name."""
    return Card(name="Espada de Laser", gold_cost=4, delivery_century=24,
                recycle_value=3, ability_type="passive")


# ── Page 2: Navigation / Transport ───────────────────────────────────────

def bussola_de_navegacao() -> Card:
    """Passive: at the end of the travel phase, roll a generator and travel that
    many centuries toward Year Zero without spending energy."""
    def on_hour_end(traveler, game) -> None:
        roll = _rng_of(game).randint(1, 3)
        from engine.timeline import clamp_to_board
        traveler.century = clamp_to_board(traveler.century - roll)
    return Card(name="Bússola de Navegação", gold_cost=3, delivery_century=11,
                recycle_value=2, ability_type="passive", on_hour_end=on_hour_end)


def relogio_mecanico() -> Card:
    """Passive: whenever you overload your travel modules, receive 5 energy."""
    def on_overload(traveler, game) -> None:
        if FUNCTION_TRAVEL in traveler.overloaded_next:
            traveler.energy += 5
    from engine.constants import FUNCTION_TRAVEL
    return Card(name="Relógio Mecânico", gold_cost=4, delivery_century=14,
                recycle_value=3, ability_type="passive", on_overload=on_overload)


def telescopio_de_galileu_galilei() -> Card:
    """Passive: if another traveler is in an older era than yours, you spend no
    energy during your travel phase."""
    def on_travel_cost(traveler, cost, direction, game=None) -> int:
        if cost <= 0 or game is None:
            return cost
        from engine.timeline import eras_for_century
        from engine.constants import ERAS
        order = list(ERAS.keys())
        my_eras = eras_for_century(traveler.century)
        if not my_eras:
            return cost
        my_oldest = min(order.index(e) for e in my_eras)
        for t in game.travelers:
            if t is traveler or t.awaiting_respawn:
                continue
            their = eras_for_century(t.century)
            if their and max(order.index(e) for e in their) < my_oldest:
                return 0
        return cost
    return Card(name="Telescópio de Galileu Galilei", gold_cost=3, delivery_century=17,
                recycle_value=2, ability_type="passive", on_travel_cost=on_travel_cost)


def maquina_voadora_da_vinci() -> Card:
    """Passive (Large Item): you never lose energy during your travel phase."""
    def on_travel_cost(traveler, cost, direction, game=None) -> int:
        return 0
    return Card(name="Máquina Voadora da da Vinci", gold_cost=4, delivery_century=15,
                recycle_value=3, ability_type="passive", is_large_item=True,
                on_travel_cost=on_travel_cost)


def maquina_a_vapor_de_james_watt() -> Card:
    """Passive: if synchronic with 1+ travelers at the end of the travel phase, gain 3 energy."""
    def on_hour_end(traveler, game) -> None:
        if any(t is not traveler and not t.awaiting_respawn and t.century == traveler.century
               for t in game.travelers):
            traveler.energy += 3
    return Card(name="Máquina a Vapor de James Watt", gold_cost=2, delivery_century=15,
                recycle_value=2, ability_type="passive", on_hour_end=on_hour_end)


def mapa_de_geradus_mercator() -> Card:
    """Active: travel up to 3 centuries (normal energy cost for past travel)."""
    def effect(traveler, game, context) -> None:
        if context is None:
            return
        centuries, direction = context
        centuries = max(0, min(3, centuries))
        from engine.resolve import execute_travel
        execute_travel(traveler, centuries * direction, game)
    return Card(name="Mapa de Geradus Mercator", gold_cost=2, delivery_century=16,
                recycle_value=2, ability_type="active", active_effect=effect)


def motor_de_corrente_alternada_de_tesla() -> Card:
    """Passive: whenever you gain 1+ booms, receive 1 energy."""
    def on_boom_gain(traveler, booms) -> int:
        if booms > 0:
            traveler.energy += 1
        return booms
    return Card(name="Motor de Corrente Alternada de Tesla", gold_cost=1, delivery_century=19,
                recycle_value=2, ability_type="passive", on_boom_gain=on_boom_gain)


def primeira_maquina_do_tempo() -> Card:
    """Active: travel back to century XXX without spending energy."""
    def effect(traveler, game, context) -> None:
        from engine.constants import CENTURY_MAX
        traveler.century = CENTURY_MAX
    return Card(name="Primeira Maquina do Tempo", gold_cost=4, delivery_century=30,
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

def astrolabio() -> Card:
    """Active (recycle): travel to a century in your era for free and recycle this card."""
    def effect(traveler, game, context) -> None:
        if context is None:
            return
        if _same_era(traveler.century, context):
            traveler.century = context
    return Card(name="Astrolábio", gold_cost=3, delivery_century=6,
                recycle_value=1, ability_type="active", recycles_on_use=True,
                active_effect=effect)


def toalha() -> Card:
    """Passive: your travel and paradox modules never overload (handled in resolve)."""
    return Card(name="Toalha", gold_cost=1, delivery_century=1,
                recycle_value=1, ability_type="passive")


def automato_de_ismail_al_jazari() -> Card:
    """Passive: whenever you overload, discard 5 booms."""
    def on_overload(traveler, game) -> None:
        traveler.booms = max(0, traveler.booms - 5)
    return Card(name="Autômato de Ismail Al-Jazari", gold_cost=1, delivery_century=12,
                recycle_value=2, ability_type="passive", on_overload=on_overload)


def colar_de_cavalo() -> Card:
    """Passive: whenever you activate a travel module, receive 1 energy (handled in resolve)."""
    return Card(name="Colar de Cavalo", gold_cost=3, delivery_century=5,
                recycle_value=2, ability_type="passive")


def escudo_viking() -> Card:
    """Passive: the first time each Hour another traveler makes you lose energy,
    you lose 2 fewer.

    The "first time this Hour" + "another traveler" conditions and the -2 are all
    resolved in engine/combat.lose_energy, keyed on card name, so the reduction
    only applies to enemy-caused losses (not travel/explosion/escape valve)."""
    return Card(name="Escudo Viking", gold_cost=2, delivery_century=9,
                recycle_value=2, ability_type="passive")


def sismografico() -> Card:
    """Passive: whenever you would gain 1+ booms, gain 2 fewer (min 0)."""
    return Card(name="Sismográfico", gold_cost=2, delivery_century=2,
                recycle_value=2, ability_type="passive", on_boom_gain=_reduce_booms(2))


def santo_graal() -> Card:
    """Passive (recycle): the first time you would reach 0 energy, gain 1 energy
    instead and recycle this card (handled in resolve.check_termination)."""
    return Card(name="Santo Graal", gold_cost=3, delivery_century=1,
                recycle_value=1, ability_type="passive", recycles_on_use=True)


def geladeira() -> Card:
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
    return Card(name="Geladeira", gold_cost=1, delivery_century=18,
                recycle_value=1, ability_type="active", active_effect=effect)


def dente_azul_do_harald() -> Card:
    """Atemporal Passive: you may make agreements with non-synchronic travelers (§4.4)."""
    return Card(name="Dente Azul do Harald", gold_cost=1, delivery_century=10,
                recycle_value=2, ability_type="atemporal_passive")


# ── Page 4: Technology / Advanced ────────────────────────────────────────

def caldeirao_da_agnes() -> Card:
    """Passive: whenever another traveler recycles a card, you may steal it.

    Resolved in engine/combat.recycle_card, keyed on card name."""
    return Card(name="Caldeirão da Agnes", gold_cost=4, delivery_century=15,
                recycle_value=4, ability_type="passive")


def telestransportador_de_objetos() -> Card:
    """Active: swap this card for any card equipped by another traveler."""
    def effect(traveler, game, context) -> None:
        if context is None:
            return
        target, target_card = context
        if not _looks_like_traveler(target) or target_card not in target.hand:
            return
        this_card = next((c for c in traveler.hand if c.name == "Telestransportador de Objetos"), None)
        if this_card is None:
            return
        # Respect equipment capacity on both sides.
        target.hand.remove(target_card)
        traveler.hand.remove(this_card)
        if traveler.can_hold(target_card):
            traveler.hand.append(target_card)
        if target.can_hold(this_card):
            target.hand.append(this_card)
    return Card(name="Telestransportador de Objetos", gold_cost=2, delivery_century=26,
                recycle_value=1, ability_type="active", active_effect=effect)


def calice_do_principe_dracula() -> Card:
    """Passive: whenever you make 1+ travelers lose energy, receive 2 energy.

    Resolved centrally in engine/combat.deal_energy, keyed on card name."""
    return Card(name="Cálice do Príncipe Drácula", gold_cost=3, delivery_century=15,
                recycle_value=2, ability_type="passive")


def armadura_da_joana_darc() -> Card:
    """Passive: whenever you would lose energy, lose 1 fewer (but never below 1)."""
    def on_energy_loss(traveler, amount) -> int:
        return max(1, amount - 1) if amount > 0 else 0
    return Card(name="Armadura da Joana d'Arc", gold_cost=4, delivery_century=15,
                recycle_value=3, ability_type="passive",
                on_energy_loss=on_energy_loss,
                on_travel_cost=lambda t, cost, d, game=None: max(1, cost - 1) if cost > 0 else 0)


def computador_quantico() -> Card:
    """Passive: has all passive abilities of the cards in your Temporal Receptor.

    Realised by engine/cards.passive_source_cards, which the engine queries for
    every passive hook."""
    return Card(name="Computador Quântico", gold_cost=2, delivery_century=22,
                recycle_value=2, ability_type="passive")


def carro() -> Card:
    """Passive (Large Item): add +1 to the value of all your causality generators.

    Resolved in engine/combat.effective_generator, keyed on card name."""
    return Card(name="Carro", gold_cost=4, delivery_century=19,
                recycle_value=3, ability_type="passive", is_large_item=True)


def operacional_de_dimensoes_relativas() -> Card:
    """Passive: you may carry 2 additional cards (§equipment_capacity in state)."""
    return Card(name="Operacional de Dimensões Relativas", gold_cost=4, delivery_century=29,
                recycle_value=4, ability_type="passive")


def janela_do_tempo() -> Card:
    """Atemporal Passive: you may always treat other travelers as synchronic."""
    return Card(name="Janela do Tempo", gold_cost=4, delivery_century=28,
                recycle_value=4, ability_type="atemporal_passive")


def porcelana() -> Card:
    """Passive: revealed Market cards cost 1 less; if you lose energy, recycle this card.

    The discount is applied in market.py; the recycle-on-loss trigger fires from
    engine/combat after any energy loss, keyed on card name."""
    return Card(name="Porcelana", gold_cost=1, delivery_century=7,
                recycle_value=1, ability_type="passive")


# ── Page 5: Knowledge / Media ────────────────────────────────────────────

def livro_de_misterios_de_alexandria() -> Card:
    """Active (recycle): choose a Market deck, steal the unrevealed top card and recycle this."""
    def effect(traveler, game, context) -> None:
        deck = context
        if deck is None or not getattr(deck, "_draw", None):
            return
        if not traveler.can_hold(deck._draw[-1]):
            return
        traveler.hand.append(deck._draw.pop())
    return Card(name="Livro de Mistérios de Alexandria", gold_cost=2, delivery_century=2,
                recycle_value=1, ability_type="active", recycles_on_use=True,
                active_effect=effect)


def oculos() -> Card:
    """Active (recycle): choose a recycled card, steal it and recycle this card."""
    def effect(traveler, game, context) -> None:
        if context is None:
            return
        deck, card = context
        if card in getattr(deck, "_discard", []) and traveler.can_hold(card):
            deck._discard.remove(card)
            traveler.hand.append(card)
    return Card(name="Óculos", gold_cost=2, delivery_century=13,
                recycle_value=1, ability_type="active", recycles_on_use=True,
                active_effect=effect)


def prensa_movel() -> Card:
    """Passive (Large Item): has all passive abilities of the cards revealed in the Market.

    Realised by engine/cards.passive_source_cards (requires the game's
    market_revealed snapshot)."""
    return Card(name="Prensa Móvel", gold_cost=4, delivery_century=15,
                recycle_value=4, ability_type="passive", is_large_item=True)


def carretel_de_pesca() -> Card:
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
            traveler.is_wanted = True             # §26.4 / §3.4: Merchant steal → Wanted
    return Card(name="Carretel de Pesca", gold_cost=2, delivery_century=4,
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
        # Swap in-place so Mona Lisa takes the exact slot the chosen card vacated,
        # keeping the revealed count at 4 without triggering _refill_revealed (§6.1).
        idx = deck._revealed.index(chosen)
        deck._revealed[idx] = this_card
        traveler.hand.remove(this_card)
        traveler.hand.append(chosen)
    return Card(name="Mona Lisa", gold_cost=2, delivery_century=16,
                recycle_value=1, ability_type="active", active_effect=effect)


def simulador_da_realidade() -> Card:
    """Passive: non-synchronic travelers cannot buy Market cards (handled in market.py).
    If you cause a present paradox, recycle this card (handled in paradox.py)."""
    return Card(name="Simulador da Realidade", gold_cost=4, delivery_century=25,
                recycle_value=4, ability_type="passive", recycles_on_use=True)


def primeiro_smartphone() -> Card:
    """Atemporal Passive: you have Market access during the market phase regardless of synchrony."""
    return Card(name="Primeiro Smartphone", gold_cost=1, delivery_century=21,
                recycle_value=2, ability_type="atemporal_passive")


def maquina_de_alan_turing() -> Card:
    """Active: destroy all revealed cards in the Market."""
    def effect(traveler, game, context) -> None:
        deck = context
        if deck is None:
            return
        deck._revealed.clear()
        deck._refill_revealed()
    return Card(name="A Máquina de Alan Turing", gold_cost=1, delivery_century=20,
                recycle_value=1, ability_type="active", active_effect=effect)


def lampada_de_thomas_edison() -> Card:
    """Atemporal Active: choose a Market deck, see and buy the unrevealed top card."""
    def effect(traveler, game, context) -> None:
        deck = context
        if deck is None or not getattr(deck, "_draw", None):
            return
        from engine.market import _effective_card_cost
        top = deck._draw[-1]
        cost = _effective_card_cost(top, traveler)
        if traveler.gold >= cost and traveler.can_hold(top):
            deck._draw.pop()
            traveler.gold -= cost
            traveler.hand.append(top)
    return Card(name="A Lâmpada de Thomas Edison", gold_cost=3, delivery_century=19,
                recycle_value=3, ability_type="atemporal_active", active_effect=effect)


# ── Page 6: More Items ────────────────────────────────────────────────────

def heliografo_de_niepce() -> Card:
    """Active (recycle): choose any revealed Market card, steal it (free) and recycle this."""
    def effect(traveler, game, context) -> None:
        if context is None:
            return
        deck, chosen = context
        if chosen in deck.revealed and traveler.can_hold(chosen):
            deck.take(chosen)
            traveler.hand.append(chosen)
            traveler.is_wanted = True             # §26.4 / §3.4: Merchant steal → Wanted
    return Card(name="Heliógrafo de Niépce", gold_cost=2, delivery_century=19,
                recycle_value=1, ability_type="active", recycles_on_use=True,
                active_effect=effect)


def xilogravura() -> Card:
    """Active: choose a revealed active card in the Market, use its ability."""
    def effect(traveler, game, context) -> None:
        if context is None:
            return
        chosen, inner = context
        if getattr(chosen, "active_effect", None):
            chosen.active_effect(traveler, game, inner)
    return Card(name="Xilogravura", gold_cost=2, delivery_century=4,
                recycle_value=2, ability_type="active", active_effect=effect)


def maquina_de_venda_automatica() -> Card:
    """Passive: whenever another traveler buys a revealed Market card, gain 1 gold."""
    def on_market_buy_other(this_t, buyer_t, card, game) -> None:
        if this_t is not buyer_t:
            this_t.gold += 1
    return Card(name="Máquina de Venda Automática", gold_cost=2, delivery_century=1,
                recycle_value=2, ability_type="passive",
                on_market_buy_other=on_market_buy_other)


def polvora() -> Card:
    """Passive: whenever you make another traveler lose energy, they lose 1 more.

    Resolved centrally in engine/combat.lose_energy, keyed on card name."""
    return Card(name="Pólvora", gold_cost=3, delivery_century=9,
                recycle_value=2, ability_type="passive")


def lanca_do_destino() -> Card:
    """Passive: whenever you cause a future paradox, also cause a past paradox of equal value.

    Resolved in engine/paradox.py, keyed on card name."""
    return Card(name="Lança do Destino", gold_cost=4, delivery_century=1,
                recycle_value=3, ability_type="passive")


def espada_do_carlos_magno() -> Card:
    """Active: a synchronic traveler loses energy equal to your current gold."""
    def effect(traveler, game, context) -> None:
        target = context
        if not _looks_like_traveler(target) or target is traveler:
            return
        if target.century != traveler.century:
            return
        combat.deal_energy(game, traveler, [target], traveler.gold, kind="weapon")
    return Card(name="Espada do Carlos Magno", gold_cost=3, delivery_century=8,
                recycle_value=3, ability_type="active", active_effect=effect)


def espada_de_atila() -> Card:
    """Active: all travelers in eras older than yours lose 3 energy."""
    def effect(traveler, game, context) -> None:
        from engine.timeline import eras_for_century
        from engine.constants import ERAS
        order = list(ERAS.keys())
        my_eras = eras_for_century(traveler.century)
        if not my_eras:
            return
        my_oldest = min(order.index(e) for e in my_eras)
        targets = []
        for t in game.travelers:
            if t is traveler or t.awaiting_respawn:
                continue
            their = eras_for_century(t.century)
            if their and max(order.index(e) for e in their) < my_oldest:
                targets.append(t)
        combat.deal_energy(game, traveler, targets, 3, kind="weapon")
    return Card(name="A Espada de Átila", gold_cost=4, delivery_century=5,
                recycle_value=3, ability_type="active", active_effect=effect)


# ---------------------------------------------------------------------------
# Deck catalogue and split
# ---------------------------------------------------------------------------

ALL_CARDS: list[Callable[[], Card]] = [
    # Page 1
    rifle_fergunson, lanca_de_fogo, bandeira_vermelha_da_ching_shih,
    canhao_de_vinganca_da_rainha_anne, arma_de_laser, arma_de_portais,
    revolver_de_polvora, excalibur, espada_de_laser,
    # Page 2
    bussola_de_navegacao, relogio_mecanico, telescopio_de_galileu_galilei,
    maquina_voadora_da_vinci, maquina_a_vapor_de_james_watt, mapa_de_geradus_mercator,
    motor_de_corrente_alternada_de_tesla, primeira_maquina_do_tempo, super_motor,
    # Page 3
    astrolabio, toalha, automato_de_ismail_al_jazari,
    colar_de_cavalo, escudo_viking, sismografico,
    santo_graal, geladeira, dente_azul_do_harald,
    # Page 4
    caldeirao_da_agnes, telestransportador_de_objetos, calice_do_principe_dracula,
    armadura_da_joana_darc, computador_quantico, carro,
    operacional_de_dimensoes_relativas, janela_do_tempo, porcelana,
    # Page 5
    livro_de_misterios_de_alexandria, oculos, prensa_movel,
    carretel_de_pesca, mona_lisa, simulador_da_realidade,
    primeiro_smartphone, maquina_de_alan_turing, lampada_de_thomas_edison,
    # Page 6
    heliografo_de_niepce, xilogravura, maquina_de_venda_automatica,
    polvora, lanca_do_destino, espada_do_carlos_magno, espada_de_atila,
]

assert len(ALL_CARDS) == 52, f"Expected 52 cards, got {len(ALL_CARDS)}"


def build_all_cards() -> list[Card]:
    """Return all 52 fresh Card instances (unshuffled)."""
    return [f() for f in ALL_CARDS]


# ---------------------------------------------------------------------------
# Legacy fixed-split helpers (kept for tests; not used by MerchantDeck)
# ---------------------------------------------------------------------------

# The 12 cards historically placed in the Secret Market (high-power / atemporal).
# MerchantDeck now selects the 12 randomly, so this frozenset is only used by
# tests that verify the historical split still compiles cleanly.
SECRET_MARKET_NAMES: frozenset[str] = frozenset({
    "Dente Azul do Harald",
    "Janela do Tempo",
    "Primeiro Smartphone",
    "A Lâmpada de Thomas Edison",
    "Máquina Voadora da da Vinci",
    "Canhão de Vingança da Rainha Anne",
    "Armadura da Joana d'Arc",
    "Caldeirão da Agnes",
    "Carro",
    "Operacional de Dimensões Relativas",
    "Simulador da Realidade",
    "Prensa Móvel",
})

assert len(SECRET_MARKET_NAMES) == 12


def build_merchant_deck() -> list[Card]:
    """Return 40 fixed Merchant-deck cards (historical split, for tests)."""
    return [f() for f in ALL_CARDS if f().name not in SECRET_MARKET_NAMES]


def build_secret_market() -> list[Card]:
    """Return 12 fixed Secret Market cards (historical split, for tests)."""
    return [f() for f in ALL_CARDS if f().name in SECRET_MARKET_NAMES]


def build_deck() -> list[Card]:
    """Legacy alias for the 40-card Merchant deck (§25.2).

    MerchantDeck builds from ``build_all_cards()`` and splits the 12 Secret
    Market cards out at random; this alias returns the historical fixed
    40-card Merchant split and is kept for callers/tests that want it.
    """
    return build_merchant_deck()


assert len(build_merchant_deck()) == 40, f"Merchant deck should have 40 cards, got {len(build_merchant_deck())}"
assert len(build_secret_market()) == 12, f"Secret Market should have 12 cards, got {len(build_secret_market())}"

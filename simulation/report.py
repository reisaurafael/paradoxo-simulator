"""
simulation/report.py
=====================
Single-file visual match report for Paradoxo.

Runs one full game and writes **one self-contained HTML file** that tells the
whole match as a story: a player dashboard per Hour, the generator matrix drawn
as an actual 3x3 grid, the Market as card panels, and a persistent per-traveler
timeline you can scroll to follow each traveler's arc. No external assets, no
secondary logs: open the file and read the game.

Usage:
    python -m simulation.report                 # random game → paradoxo_report.html
    python -m simulation.report out.html 42     # seeded game → out.html

The recorder mirrors the authoritative phase order in simulation/runner.py; it
records structured data instead of printing, then render_html() turns it into a
styled document.
"""

from __future__ import annotations
import html
import random
import sys
from dataclasses import dataclass, field
from typing import Any

from engine.state import GameState, Allocation, GameResult, HourSnapshot
from engine.dice import roll_generators
from engine.resolve import resolve_hour, advance_overload
from engine.constants import (
    CP_SURVIVAL_BONUS, CP_STABILISATION_BONUS,
)
from engine.rewards import process_pending_rewards
from engine.market import (
    MerchantDeck, resolve_market_phase, resolve_deliveries,
    check_merchant_upgrades, check_temporal_receptor_win, effective_card_cost,
    BuyAction, RenewAction, DeclareAction, UseCardAction, PassAction,
)
from simulation.strategies.base import Strategy
from simulation.runner import (
    resolve_activation_phase, _resolve_free_recycles, _resolve_solo_phases,
    _check_win_conditions, _determine_cp_winner,
)

_FUNC_ROWS = ["Recharge", "Paradox", "Travel"]
_COL_HEADS = ["Future", "Present", "Past"]


# ---------------------------------------------------------------------------
# Recorder data model
# ---------------------------------------------------------------------------

def _traveler_snapshot(t) -> dict:
    return {
        "name": t.name,
        "century": t.century,
        "energy": t.energy,
        "gold": t.gold,
        "booms": t.booms,
        "cp": t.contract_points,
        "wanted": t.is_wanted,
        "terminated": t.is_terminated,
        "awaiting_respawn": t.awaiting_respawn,
        "exploded": t.exploded_this_hour,
        "overloads": sorted(_FUNC_ROWS[f] for f in t.overloaded_functions),
        "hand": [c.name for c in t.hand],
        "receptor": list(t.temporal_receptor),
        "market_voucher": t.market_voucher,
        "item_voucher": t.item_voucher,
        "matrix_buffs": dict(t.matrix_buffs),
    }


def _snapshot_all(game) -> dict:
    return {t.name: _traveler_snapshot(t) for t in game.travelers}


def _diff(before: dict, after: dict) -> list[dict]:
    """Per-traveler structured diff between two full snapshots."""
    out = []
    for name in before:
        b, a = before[name], after[name]
        changes = []
        for key, label in (("energy", "NRG"), ("gold", "GLD"),
                           ("booms", "BMB"), ("century", "Cen"), ("cp", "CP")):
            if b[key] != a[key]:
                changes.append({"label": label, "from": b[key], "to": a[key],
                                "delta": a[key] - b[key]})
        flags = []
        if not b.get("exploded") and a.get("exploded"):
            flags.append(("explode", "EXPLODED: motor overheat, −2 energy, booms reset, no travel"))
        if not b["terminated"] and a["terminated"]:
            flags.append(("death", "TERMINATED"))
        if b["awaiting_respawn"] and not a["awaiting_respawn"]:
            flags.append(("respawn", "RESPAWNED at XXX"))
        if b["wanted"] != a["wanted"]:
            flags.append(("wanted", "WANTED" if a["wanted"] else "cleared Wanted"))
        if b["market_voucher"] != a["market_voucher"]:
            flags.append(("voucher", f"market voucher → {a['market_voucher']}"))
        if b["item_voucher"] != a["item_voucher"]:
            flags.append(("voucher", f"item voucher → {a['item_voucher']}"))
        if b["matrix_buffs"] != a["matrix_buffs"]:
            new = [k for k in a["matrix_buffs"] if k not in b["matrix_buffs"]]
            flags.append(("buff", f"matrix buff → module {','.join(map(str, new))}"))
        hand_add = [x for x in a["hand"] if x not in b["hand"]]
        hand_rem = [x for x in b["hand"] if x not in a["hand"]]
        rcpt_add = [x for x in a["receptor"] if x not in b["receptor"]]
        if changes or flags or hand_add or hand_rem or rcpt_add:
            out.append({"name": name, "changes": changes, "flags": flags,
                        "hand_add": hand_add, "hand_rem": hand_rem,
                        "rcpt_add": rcpt_add})
    return out


@dataclass
class PhaseRecord:
    name: str
    subtitle: str = ""
    decisions: list[str] = field(default_factory=list)
    allocations: list[dict] = field(default_factory=list)
    market: dict | None = None
    diffs: list[dict] = field(default_factory=list)
    note: str = ""


@dataclass
class HourRecord:
    n: int
    merchant: int
    overloads: list[tuple[str, str]] = field(default_factory=list)
    globals: list[str] = field(default_factory=list)
    dashboard: list[dict] = field(default_factory=list)
    phases: list[PhaseRecord] = field(default_factory=list)


@dataclass
class Report:
    travelers: list[str]
    profiles: dict[str, str]
    hours: list[HourRecord] = field(default_factory=list)
    result: dict | None = None
    cp_timeline: list[dict] = field(default_factory=list)  # one row per hour


class Recorder:
    """Holds the live report and the phase currently being recorded so the
    RecordingStrategy can attach decisions to the right place."""

    def __init__(self, report: Report) -> None:
        self.report = report
        self.current_phase: PhaseRecord | None = None

    def phase(self, name: str, subtitle: str = "") -> PhaseRecord:
        p = PhaseRecord(name=name, subtitle=subtitle)
        self.report.hours[-1].phases.append(p)
        self.current_phase = p
        return p

    def decide(self, text: str) -> None:
        if self.current_phase is not None:
            self.current_phase.decisions.append(text)


# ---------------------------------------------------------------------------
# Recording strategy wrapper
# ---------------------------------------------------------------------------

class RecordingStrategy(Strategy):
    def __init__(self, inner: Strategy, rec: Recorder) -> None:
        self._inner = inner
        self._rec = rec

    @property
    def name(self) -> str:
        return self._inner.name

    def choose_allocation(self, traveler, game, dice):
        return self._inner.choose_allocation(traveler, game, dice)

    def choose_market_action(self, traveler, game, revealed, renew_cost):
        action = self._inner.choose_market_action(traveler, game, revealed, renew_cost)
        tag = f"{traveler.name}"
        if isinstance(action, BuyAction):
            paid = effective_card_cost(action.card, traveler)
            base = action.card.gold_cost
            cost = f"{paid}g" if paid == base else f"{paid}g (base {base}g)"
            self._rec.decide(f"{tag} buys “{action.card.name}” for {cost}")
        elif isinstance(action, RenewAction):
            self._rec.decide(f"{tag} renews “{action.card.name}” ({renew_cost}g)")
        elif isinstance(action, DeclareAction):
            self._rec.decide(f"{tag} declares: clears Wanted (2g)")
        elif isinstance(action, UseCardAction):
            self._rec.decide(f"{tag} activates “{action.card.name}” at the Market")
        return action

    def choose_activations(self, traveler, game):
        result = self._inner.choose_activations(traveler, game)
        for card, ctx in result:
            target = getattr(ctx, "name", ctx)
            self._rec.decide(f"{traveler.name} activates “{card.name}”"
                             + (f" → {target}" if target is not None else ""))
        return result

    def choose_reward_category(self, traveler, game, available):
        choice = self._inner.choose_reward_category(traveler, game, available)
        self._rec.decide(f"{traveler.name} reward → {choice} (of {', '.join(available)})")
        return choice

    def choose_items_to_recycle(self, traveler, game):
        result = self._inner.choose_items_to_recycle(traveler, game)
        if result:
            self._rec.decide(f"{traveler.name} recycles {', '.join(c.name for c in result)}")
        return result

    def choose_cards_to_deliver(self, traveler, game, deliverable):
        return self._inner.choose_cards_to_deliver(traveler, game, deliverable)

    def choose_chaos_destroy_target(self, traveler, game, candidates):
        r = self._inner.choose_chaos_destroy_target(traveler, game, candidates)
        if r is not None:
            self._rec.decide(f"{traveler.name} (Chaos II) destroys “{r.name}”")
        return r

    def choose_chaos_merchant_century(self, traveler, game):
        r = self._inner.choose_chaos_merchant_century(traveler, game)
        self._rec.decide(f"{traveler.name} (Chaos III) moves Merchant → century {r}")
        return r

    def choose_resource_steal_target(self, traveler, game, revealed):
        r = self._inner.choose_resource_steal_target(traveler, game, revealed)
        if r is not None:
            self._rec.decide(f"{traveler.name} (Resource II) steals “{r.name}”")
        return r

    def choose_matrix_buff_module(self, traveler, game):
        return self._inner.choose_matrix_buff_module(traveler, game)


# ---------------------------------------------------------------------------
# Game driver (records instead of printing)
# ---------------------------------------------------------------------------

def record_game(raw_strategies: dict[str, Strategy], rng: random.Random,
                max_hours: int = 200) -> Report:
    names = list(raw_strategies.keys())
    report = Report(travelers=names,
                    profiles={n: raw_strategies[n].name for n in names})
    rec = Recorder(report)
    strategies = {n: RecordingStrategy(s, rec) for n, s in raw_strategies.items()}

    game = GameState.create(names)
    deck = MerchantDeck(rng=rng)
    game.rng = rng
    game.market_revealed = deck.snapshot_revealed()
    history: list[HourSnapshot] = []

    def drain_rewards(phase: PhaseRecord) -> None:
        while game.cp_rewards_pending:
            before = _snapshot_all(game)
            process_pending_rewards(game, deck, strategies, rng)
            phase.diffs.extend(_diff(before, _snapshot_all(game)))

    def finish(reason: str) -> Report:
        report.result = {
            "reason": reason,
            "winner": _determine_cp_winner(game),
            "hours": game.hour - 1,
            "standings": sorted(
                (_traveler_snapshot(t) for t in game.travelers),
                key=lambda s: (-s["cp"], -s["century"], -s["gold"], -s["energy"]),
            ),
        }
        return report

    for _ in range(max_hours):
        for t in game.travelers:
            advance_overload(t)

        hr = HourRecord(n=game.hour, merchant=game.merchant_century)
        hr.overloads = [(t.name, "+".join(sorted(_FUNC_ROWS[f] for f in t.overloaded_functions)))
                        for t in game.travelers if t.overloaded_functions]
        hr.dashboard = [_traveler_snapshot(t) for t in game.travelers]
        report.hours.append(hr)

        # --- Phase 1: Delivery ---
        p = rec.phase("Delivery", "Phase 1")
        before = _snapshot_all(game)
        resolve_deliveries(game, strategies)
        p.diffs = _diff(before, _snapshot_all(game))

        if check_temporal_receptor_win(game):
            stab = next(t for t in game.travelers if t.name == game.winner)
            stab.contract_points += CP_STABILISATION_BONUS
            game.cp_rewards_pending.append(stab.name)
            for s in game.travelers:
                if not s.is_terminated:
                    s.contract_points += CP_SURVIVAL_BONUS
                    game.cp_rewards_pending.append(s.name)
            drain_rewards(p)
            _capture_cp(report, game)
            return finish("full_receptor")
        drain_rewards(rec.phase("Rewards", "after Delivery") if game.cp_rewards_pending else p)

        _resolve_free_recycles(game, deck, strategies)

        # --- Phase 2: Market ---
        p = rec.phase("Market", f"Phase 2 · Merchant @ century {game.merchant_century}")
        p.market = {"stock": [_card_panel(c, game, game.travelers) for c in deck.revealed],
                    "secret_open": deck.secret_market.is_open}
        before = _snapshot_all(game)
        game_over = resolve_market_phase(game, deck, strategies, rng)
        p.diffs = _diff(before, _snapshot_all(game))
        if game_over:
            for s in game.travelers:
                if not s.is_terminated:
                    s.contract_points += CP_SURVIVAL_BONUS
            _capture_cp(report, game)
            return finish(game.game_over_reason)
        drain_rewards(p)

        # --- Phase 3: Generators ---
        p = rec.phase("Generators", "Phase 3")
        allocations: dict[str, Allocation] = {}
        directions: dict[str, int] = {}
        caps: dict[str, int | None] = {}
        for t in [t for t in game.travelers if not t.awaiting_respawn]:
            dice = roll_generators(rng=rng)
            result = strategies[t.name].choose_allocation(t, game, dice)
            if len(result) == 3:
                alloc, direction, cap = result
            else:
                alloc, direction = result
                cap = None
            allocations[t.name] = alloc
            directions[t.name] = direction
            caps[t.name] = cap
            p.allocations.append(_alloc_record(t, alloc, dice, direction, cap))
        before = _snapshot_all(game)
        history.extend(resolve_hour(game, allocations, directions, caps))
        p.diffs = _diff(before, _snapshot_all(game))
        drain_rewards(p)

        # --- Phase 4: Item Activation ---
        p4 = rec.phase("Item Activation", "Phase 4")
        before = _snapshot_all(game)
        resolve_activation_phase(game, strategies)
        p4.diffs = _diff(before, _snapshot_all(game))
        drain_rewards(p4)
        if not p4.decisions and not p4.diffs:
            report.hours[-1].phases.remove(p4)

        # --- Solo phases (Time I) ---
        if game.solo_phases_pending:
            ps = rec.phase("Solo Generators", "Time I reward")
            before = _snapshot_all(game)
            _resolve_solo_phases(game, deck, strategies, rng)
            ps.diffs = _diff(before, _snapshot_all(game))
        else:
            _resolve_solo_phases(game, deck, strategies, rng)

        check_merchant_upgrades(game)
        if game.merchant_upgrade_x_triggered or game.merchant_upgrade_xx_triggered:
            n = 3 if game.merchant_upgrade_x_triggered else 2
            hr.globals.append(f"Merchant speed upgraded to {n}d3")

        _capture_cp(report, game)

        result = _check_win_conditions(game)
        if result is not None:
            drain_rewards(rec.phase("Rewards", "end of Hour"))
            _capture_cp(report, game)
            return finish(result[1])

    return finish("max_hours_reached")


def _capture_cp(report: Report, game) -> None:
    report.cp_timeline.append({
        "hour": game.hour,
        "cp": {t.name: t.contract_points for t in game.travelers},
        "energy": {t.name: t.energy for t in game.travelers},
        "century": {t.name: t.century for t in game.travelers},
    })


def _card_panel(card, game, travelers) -> dict:
    owner = next((t.name for t in travelers if card in t.hand), None)
    return {
        "name": card.name,
        "cost": card.gold_cost,
        "dc": card.delivery_century,
        "rv": card.recycle_value,
        "kind": getattr(card, "ability_type", ""),
        "owner": owner,
    }


def _alloc_record(t, alloc: Allocation, dice, direction, cap) -> dict:
    grid = [[alloc.get(r, c) for c in range(3)] for r in range(3)]
    return {
        "name": t.name,
        "grid": grid,
        "overloaded": sorted(t.overloaded_functions),
        "ev": alloc.escape_valve,
        "dice": list(dice),
        "direction": direction,
        "cap": cap,
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

_PALETTE = ["#5aa9e6", "#7fc8a9", "#e6a15a", "#c77dff"]


def _esc(s: Any) -> str:
    return html.escape(str(s))


def _century_label(c: int) -> str:
    return {0: "Yr0", 10: "X", 20: "XX", 30: "XXX"}.get(c, str(c))


def _color_for(report: Report, name: str) -> str:
    return _PALETTE[report.travelers.index(name) % len(_PALETTE)]


def _delta_chip(ch: dict) -> str:
    cls = "up" if ch["delta"] > 0 else "down"
    return (f'<span class="chip {cls}">{_esc(ch["label"])} '
            f'{_esc(ch["from"])}→{_esc(ch["to"])} '
            f'<b>{ch["delta"]:+d}</b></span>')


def _flag_chip(kind: str, text: str) -> str:
    return f'<span class="flag {kind}">{_esc(text)}</span>'


def _render_diffs(report: Report, diffs: list[dict]) -> str:
    if not diffs:
        return ""
    rows = []
    for d in diffs:
        col = _color_for(report, d["name"])
        chips = "".join(_delta_chip(c) for c in d["changes"])
        chips += "".join(_flag_chip(k, t) for k, t in d["flags"])
        for c in d["hand_add"]:
            chips += _flag_chip("gain", f"+ {c}")
        for c in d["hand_rem"]:
            chips += _flag_chip("loss", f"− {c}")
        for c in d["rcpt_add"]:
            chips += _flag_chip("deliver", f"✦ delivered {c}")
        if not chips:
            continue
        rows.append(
            f'<div class="diffrow"><span class="who" style="border-color:{col}">'
            f'{_esc(d["name"])}</span><span class="chips">{chips}</span></div>')
    return f'<div class="diffs">{"".join(rows)}</div>'


def _render_matrix(report: Report, a: dict) -> str:
    col = _color_for(report, a["name"])
    head = "".join(f"<th>{h}</th>" for h in _COL_HEADS)
    body = ""
    for r in range(3):
        ovl = r in a["overloaded"]
        cells = ""
        for c in range(3):
            v = a["grid"][r][c]
            cls = "cell filled" if v else "cell"
            cells += f'<td class="{cls}">{v if v else ""}</td>'
        body += (f'<tr class="{"ovl" if ovl else ""}">'
                 f'<th class="rowlab">{_FUNC_ROWS[r]}{" ⚠" if ovl else ""}</th>{cells}</tr>')
    dir_txt = "← PAST" if a["direction"] < 0 else "→ FUTURE"
    cap_txt = f" · cap {a['cap']}" if a["cap"] is not None else ""
    ev_txt = f' · escape valve {a["ev"]}' if a["ev"] else ""
    return (
        f'<div class="matrixcard">'
        f'<div class="mhead"><span class="who" style="border-color:{col}">{_esc(a["name"])}</span>'
        f'<span class="dice">dice {a["dice"]}</span></div>'
        f'<table class="matrix"><tr><th></th>{head}</tr>{body}</table>'
        f'<div class="mfoot">{dir_txt}{cap_txt}{ev_txt}</div>'
        f'</div>')


def _render_market(report: Report, market: dict) -> str:
    cards = ""
    for c in market["stock"]:
        owner = c["owner"]
        own = f'<div class="owner">held by {_esc(owner)}</div>' if owner else ""
        dc = _century_label(c["dc"]) if c["dc"] is not None else "-"
        cards += (
            f'<div class="gcard">'
            f'<div class="gc-name">{_esc(c["name"])}</div>'
            f'<div class="gc-stats"><span class="gc-cost">{c["cost"]}g</span>'
            f'<span>deliver @ {dc}</span><span>rv {c["rv"]}</span></div>'
            f'<div class="gc-kind">{_esc(c["kind"] or "-")}</div>{own}</div>')
    sm = ' · Secret Market OPEN' if market.get("secret_open") else ""
    return f'<div class="market"><div class="market-cards">{cards}</div><div class="sm">{sm}</div></div>'


def _render_phase(report: Report, p: PhaseRecord) -> str:
    inner = ""
    if p.allocations:
        inner += '<div class="matrices">' + "".join(
            _render_matrix(report, a) for a in p.allocations) + "</div>"
    if p.market:
        inner += _render_market(report, p.market)
    if p.decisions:
        inner += '<ul class="decisions">' + "".join(
            f"<li>{_esc(d)}</li>" for d in p.decisions) + "</ul>"
    inner += _render_diffs(report, p.diffs)
    if not inner:
        inner = '<div class="muted">- nothing happened -</div>'
    return (f'<div class="phase"><div class="phase-head"><span class="phase-name">{_esc(p.name)}</span>'
            f'<span class="phase-sub">{_esc(p.subtitle)}</span></div>{inner}</div>')


def _render_dashboard(report: Report, dash: list[dict]) -> str:
    cells = ""
    for s in dash:
        col = _color_for(report, s["name"])
        flags = ""
        if s["wanted"]:
            flags += '<span class="mini wanted">WANTED</span>'
        if s["awaiting_respawn"]:
            flags += '<span class="mini resp">respawning</span>'
        elif s["terminated"]:
            flags += '<span class="mini term">terminated</span>'
        if s["overloads"]:
            flags += f'<span class="mini ovl">⚠ {"+".join(s["overloads"])}</span>'
        hand = ", ".join(s["hand"]) or "-"
        rcpt = (" · receptor: " + ", ".join(s["receptor"])) if s["receptor"] else ""
        cells += (
            f'<div class="pcard" style="border-top-color:{col}">'
            f'<div class="pc-name">{_esc(s["name"])} '
            f'<span class="pc-prof">{_esc(report.profiles[s["name"]])}</span></div>'
            f'<div class="pc-grid">'
            f'<span>📍 {_century_label(s["century"])}</span>'
            f'<span>⚡ {s["energy"]}</span>'
            f'<span>💰 {s["gold"]}</span>'
            f'<span>💣 {s["booms"]}</span>'
            f'<span>★ {s["cp"]}</span></div>'
            f'<div class="pc-flags">{flags}</div>'
            f'<div class="pc-hand">{_esc(hand)}{_esc(rcpt)}</div>'
            f'</div>')
    return f'<div class="dashboard">{cells}</div>'


def _render_timeline(report: Report) -> str:
    if not report.cp_timeline:
        return ""
    head = "<th>Hr</th>" + "".join(
        f'<th style="color:{_color_for(report, n)}">{_esc(n)}</th>'
        for n in report.travelers)
    rows = ""
    for row in report.cp_timeline:
        cps = "".join(f"<td>{row['cp'][n]}</td>" for n in report.travelers)
        rows += f"<tr><td class='hr'>{row['hour']}</td>{cps}</tr>"
    return (f'<details open class="tl"><summary>Contract-Point timeline</summary>'
            f'<table class="cptl"><tr>{head}</tr>{rows}</table></details>')


def _render_hour(report: Report, hr: HourRecord) -> str:
    g = ""
    if hr.overloads:
        g += " · ".join(f"{n} overloaded {f}" for n, f in hr.overloads)
    for ev in hr.globals:
        g += f' · <b class="glob">{_esc(ev)}</b>'
    glob = f'<div class="hour-globals">{g}</div>' if g else ""
    phases = "".join(_render_phase(report, p) for p in hr.phases)
    return (
        f'<section class="hour" id="hr{hr.n}">'
        f'<div class="hour-head"><h2>Hour {hr.n}</h2>'
        f'<span class="merch">Merchant @ century {_century_label(hr.merchant)}</span></div>'
        f'{glob}{_render_dashboard(report, hr.dashboard)}{phases}</section>')


def _render_result(report: Report) -> str:
    r = report.result or {}
    rows = ""
    for i, s in enumerate(r.get("standings", [])):
        win = " winner" if s["name"] == r.get("winner") else ""
        col = _color_for(report, s["name"])
        rows += (
            f'<tr class="{win.strip()}"><td>{i+1}</td>'
            f'<td style="color:{col}">{_esc(s["name"])} '
            f'<span class="muted">{_esc(report.profiles[s["name"]])}</span></td>'
            f'<td>{s["cp"]}</td><td>{_century_label(s["century"])}</td>'
            f'<td>{s["gold"]}</td><td>{s["energy"]}</td>'
            f'<td>{"yes" if s["terminated"] else "no"}</td></tr>')
    return (
        f'<section class="result"><h2>Result: {_esc(r.get("reason",""))}</h2>'
        f'<div class="winline">Winner: <b>{_esc(r.get("winner"))}</b> · '
        f'{r.get("hours")} hours played</div>'
        f'<table class="standings"><tr><th>#</th><th>Traveler</th><th>CP</th>'
        f'<th>Century</th><th>Gold</th><th>Energy</th><th>Terminated</th></tr>'
        f'{rows}</table></section>')


def _render_nav(report: Report) -> str:
    links = "".join(f'<a href="#hr{h.n}">{h.n}</a>' for h in report.hours)
    return f'<nav class="hournav"><span>Hours:</span>{links}<a href="#result">★</a></nav>'


_CSS = """
:root{--bg:#0e1117;--panel:#171b22;--panel2:#1d222b;--line:#2a313c;--ink:#e6edf3;
--muted:#8b949e;--up:#3fb950;--down:#f85149;--accent:#5aa9e6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 'Segoe UI',system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:24px}
h1{font-size:26px;margin:0 0 4px} h2{font-size:19px;margin:0}
a{color:var(--accent);text-decoration:none}
.muted{color:var(--muted)} .sub{color:var(--muted);margin-bottom:18px}
.hournav{position:sticky;top:0;z-index:10;background:rgba(14,17,23,.92);
backdrop-filter:blur(6px);border-bottom:1px solid var(--line);padding:8px 24px;
display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:12px}
.hournav span{color:var(--muted)}
.hournav a{padding:2px 7px;border:1px solid var(--line);border-radius:6px}
.hournav a:hover{border-color:var(--accent)}
.hour{background:var(--panel);border:1px solid var(--line);border-radius:14px;
padding:18px;margin:18px 0;scroll-margin-top:52px}
.hour-head{display:flex;align-items:baseline;justify-content:space-between;
border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:12px}
.merch{color:var(--muted);font-size:13px}
.hour-globals{font-size:12.5px;color:var(--muted);margin:-4px 0 12px}
.glob{color:#e6a15a}
.dashboard{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
gap:10px;margin-bottom:16px}
.pcard{background:var(--panel2);border:1px solid var(--line);border-top:3px solid;
border-radius:10px;padding:10px 12px}
.pc-name{font-weight:600;margin-bottom:6px}
.pc-prof{font-weight:400;color:var(--muted);font-size:12px}
.pc-grid{display:flex;flex-wrap:wrap;gap:10px;font-variant-numeric:tabular-nums;
margin-bottom:6px}
.pc-flags{display:flex;flex-wrap:wrap;gap:4px;min-height:2px}
.pc-hand{color:var(--muted);font-size:12px;margin-top:6px}
.mini{font-size:10.5px;padding:1px 6px;border-radius:20px;border:1px solid}
.mini.wanted{color:#f85149;border-color:#5b2222;background:#2a1414}
.mini.term{color:#8b949e;border-color:#39414d}
.mini.resp{color:#7fc8a9;border-color:#2c4a3c}
.mini.ovl{color:#e6a15a;border-color:#4a3a22}
.phase{background:var(--panel2);border:1px solid var(--line);border-radius:10px;
padding:12px;margin:10px 0}
.phase-head{display:flex;align-items:baseline;gap:10px;margin-bottom:8px}
.phase-name{font-weight:600} .phase-sub{color:var(--muted);font-size:12px}
.decisions{margin:6px 0;padding-left:18px} .decisions li{margin:2px 0}
.diffs{display:flex;flex-direction:column;gap:5px;margin-top:8px}
.diffrow{display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap}
.who{border-left:3px solid;padding:0 8px;font-weight:600;font-size:12.5px}
.chips{display:flex;flex-wrap:wrap;gap:5px}
.chip{font-size:11.5px;padding:1px 7px;border-radius:6px;background:#222a35;
border:1px solid var(--line);font-variant-numeric:tabular-nums}
.chip.up b{color:var(--up)} .chip.down b{color:var(--down)}
.flag{font-size:11px;padding:1px 7px;border-radius:6px;font-weight:600}
.flag.explode{background:#3a1d12;color:#ff8c5a;border:1px solid #5b3322}
.flag.death{background:#2a1414;color:#f85149;border:1px solid #5b2222}
.flag.respawn{background:#13261d;color:#7fc8a9;border:1px solid #2c4a3c}
.flag.wanted{background:#2a1414;color:#ff7b72;border:1px solid #5b2222}
.flag.deliver{background:#1a2336;color:#9ecbff;border:1px solid #294066}
.flag.gain{background:#13261d;color:#7fc8a9;border:1px solid #2c4a3c}
.flag.loss{background:#2a222e;color:#c8a0d6;border:1px solid #4a3a52}
.flag.voucher,.flag.buff{background:#26221a;color:#e6c98a;border:1px solid #4a4022}
.matrices{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
gap:10px}
.matrixcard{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px}
.mhead{display:flex;justify-content:space-between;align-items:center;font-size:12px;margin-bottom:6px}
.dice{color:var(--muted)}
.matrix{border-collapse:collapse;width:100%;font-size:12px}
.matrix th{color:var(--muted);font-weight:500;padding:2px 4px;text-align:center}
.matrix .rowlab{text-align:right;white-space:nowrap}
.matrix .cell{width:30px;height:26px;text-align:center;border:1px solid var(--line);
border-radius:4px;color:var(--muted)}
.matrix .cell.filled{background:var(--accent);color:#06121f;font-weight:700}
.matrix tr.ovl .rowlab{color:#e6a15a}
.matrix tr.ovl .cell{border-color:#4a3a22}
.mfoot{color:var(--muted);font-size:11.5px;margin-top:6px}
.market-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.gcard{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px;min-height:88px}
.gc-name{font-weight:600;font-size:12.5px;margin-bottom:6px}
.gc-stats{display:flex;flex-wrap:wrap;gap:8px;font-size:11.5px;color:var(--muted)}
.gc-cost{color:#e6c98a;font-weight:700}
.gc-kind{font-size:11px;color:var(--muted);margin-top:4px;text-transform:capitalize}
.owner{font-size:10.5px;color:#7fc8a9;margin-top:3px}
.sm{color:#c77dff;font-size:11.5px;margin-top:4px}
.tl{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:10px 14px;margin:14px 0}
.tl summary{cursor:pointer;font-weight:600}
.cptl{border-collapse:collapse;margin-top:8px;font-size:12px;font-variant-numeric:tabular-nums}
.cptl td,.cptl th{padding:2px 9px;text-align:center;border-bottom:1px solid var(--line)}
.cptl .hr{color:var(--muted)}
.result{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;margin:18px 0}
.winline{margin:6px 0 12px;color:var(--muted)}
.standings{border-collapse:collapse;width:100%;font-size:13px}
.standings th,.standings td{padding:5px 10px;text-align:left;border-bottom:1px solid var(--line)}
.standings tr.winner{background:#13261d}
"""


def render_html(report: Report) -> str:
    title = " vs ".join(f"{n} ({report.profiles[n]})" for n in report.travelers)
    body = (
        f'<div class="wrap"><h1>Paradoxo: Match Report</h1>'
        f'<div class="sub">{_esc(title)}</div>'
        f'{_render_timeline(report)}'
        + "".join(_render_hour(report, h) for h in report.hours)
        + f'<div id="result">{_render_result(report)}</div></div>'
    )
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Paradoxo Match Report</title><style>{_CSS}</style></head>'
            f'<body>{_render_nav(report)}{body}</body></html>')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def generate_report(out_path: str = "paradoxo_report.html",
                    seed: int | None = None) -> str:
    from simulation.strategies.aggressive import AggressiveStrategy
    from simulation.strategies.collector import CollectorStrategy
    from simulation.strategies.smart import SmartStrategy
    from simulation.strategies.conservative import ConservativeStrategy

    rng = random.Random(seed)
    strategies = {
        "Traveler_A": AggressiveStrategy(),
        "Traveler_C": ConservativeStrategy(),
        "Traveler_S": SmartStrategy(),
        "Traveler_K": CollectorStrategy(),
    }
    report = record_game(strategies, rng)
    htmltext = render_html(report)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(htmltext)
    return out_path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "paradoxo_report.html"
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else None
    path = generate_report(out, seed)
    print(f"Wrote match report -> {path}")

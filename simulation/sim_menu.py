"""
simulation/sim_menu.py
======================
Interactive simulation runner.

A text menu to configure the table (players, profiles, games, seed), then a
batch run, a text report and up to eight charts.

Usage:
    python -m simulation.sim_menu
"""

from __future__ import annotations

import io
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

# Force UTF-8 output so the box drawing and accented card names render on
# consoles that default to a legacy code page (Windows cp1252, for one).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from engine.constants import CENTURY_MAX, MAX_TRAVELERS, MIN_TRAVELERS, OVERDRIVE_THRESHOLD_CENTURY
from simulation.runner import simulate_n_games
from simulation.metrics import summarise, trajectories, item_batch_stats

if TYPE_CHECKING:
    from engine.state import GameResult


# ---------------------------------------------------------------------------
# Profiles and report options
# ---------------------------------------------------------------------------

def _load_profiles() -> dict[str, type]:
    from simulation.strategies.aggressive import AggressiveStrategy
    from simulation.strategies.conservative import ConservativeStrategy
    from simulation.strategies.smart import SmartStrategy
    from simulation.strategies.collector import CollectorStrategy
    return {
        "Aggressive":   AggressiveStrategy,
        "Conservative": ConservativeStrategy,
        "Smart":        SmartStrategy,
        "Collector":    CollectorStrategy,
    }


PROFILE_KEYS = ["Aggressive", "Conservative", "Smart", "Collector"]

REPORT_OPTIONS = {
    "win_rates":       "Win rates by profile",
    "end_conditions":  "Victory condition distribution",
    "game_length":     "Game length distribution",
    "resources":       "Resource trajectories (energy, century, CP over time)",
    "century_heatmap": "Century occupancy heatmap",
    "card_activity":   "Card activity (buys, deliveries, recycled, missed)",
    "combat_stats":    "Combat stats (terminations, explosions)",
    "travel_stats":    "Travel distance statistics",
}

REASON_LABELS = {
    "year_zero":         "Year Zero",
    "full_receptor":     "Full Receptor",
    "last_traveler":     "Last Traveler",
    "all_terminated":    "All Terminated",
    "merchant_empty":    "Merchant Empty",
    "max_hours_reached": "Max Hours",
}


@dataclass
class SimConfig:
    n_players: int = 4
    profiles: list[str] = field(default_factory=lambda: ["Aggressive", "Conservative", "Smart", "Collector"])
    n_games: int = 500
    seed: int | None = 42
    reports: set[str] = field(default_factory=lambda: set(REPORT_OPTIONS.keys()))
    save_charts: bool = True
    chart_dir: Path = field(default_factory=lambda: Path("sim_output"))


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

W = 62  # line width


def _line(char="─"):
    return char * W


def _header(title: str) -> None:
    print()
    print("┌" + "─" * (W - 2) + "┐")
    pad = (W - 2 - len(title)) // 2
    print("│" + " " * pad + title + " " * (W - 2 - pad - len(title)) + "│")
    print("└" + "─" * (W - 2) + "┘")


def _section(title: str) -> None:
    print()
    print(_line())
    print(f"  {title}")
    print(_line())


def _bar(value: float, width: int = 30, char: str = "█") -> str:
    filled = round(value * width)
    return char * filled + "░" * (width - filled)


def _prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"  {msg}{suffix}: ").strip()
    return raw if raw else default


def _prompt_int(msg: str, default: int, lo: int = 1, hi: int = 999999) -> int:
    while True:
        raw = _prompt(msg, str(default))
        try:
            v = int(raw)
            if lo <= v <= hi:
                return v
        except ValueError:
            pass
        print(f"    ✗ Enter an integer between {lo} and {hi}.")


# ---------------------------------------------------------------------------
# Config menu
# ---------------------------------------------------------------------------

def _display_config(cfg: SimConfig) -> None:
    _header("PARADOX  SIMULATION  RUNNER")
    print()
    print("  Current configuration")
    print(f"  {_line('─')}")
    print(f"  [1]  Number of players : {cfg.n_players}")
    print()
    print("  [2]  Player profiles   :")
    for i, p in enumerate(cfg.profiles[:cfg.n_players]):
        print(f"         Player {i+1}: {p}")
    print()
    print(f"  [3]  Number of games   : {cfg.n_games}")
    seed_str = str(cfg.seed) if cfg.seed is not None else "random"
    print(f"  [4]  Random seed       : {seed_str}")
    print()
    print("  [5]  Reports to generate:")
    for key, label in REPORT_OPTIONS.items():
        mark = "✓" if key in cfg.reports else " "
        print(f"         [{mark}] {label}")
    print()
    chart_str = f"Yes → {cfg.chart_dir}/" if cfg.save_charts else "No"
    print(f"  [6]  Save charts       : {chart_str}")
    print()
    print("  [R]  Run simulation")
    print("  [Q]  Quit")
    print()


def _configure_players(cfg: SimConfig) -> None:
    cfg.n_players = _prompt_int("Number of players", cfg.n_players,
                                lo=MIN_TRAVELERS, hi=MAX_TRAVELERS)
    profiles = _load_profiles()
    print(f"\n  Available profiles: {', '.join(PROFILE_KEYS)}")
    while len(cfg.profiles) < cfg.n_players:
        cfg.profiles.append("Aggressive")
    for i in range(cfg.n_players):
        while True:
            raw = _prompt(f"  Profile for Player {i+1}", cfg.profiles[i])
            # Accept any case-insensitive prefix ("ag", "Smart", ...)
            match = next(
                (k for k in profiles if k.lower().startswith(raw.lower())), None
            )
            if match:
                cfg.profiles[i] = match
                break
            print(f"    ✗ Unknown profile. Choose from: {', '.join(PROFILE_KEYS)}")


def _configure_reports(cfg: SimConfig) -> None:
    print("\n  Toggle reports: enter keys to flip (comma-separated), or A=all, N=none:")
    for i, (key, label) in enumerate(REPORT_OPTIONS.items(), 1):
        mark = "✓" if key in cfg.reports else " "
        print(f"    {i}. [{mark}] {label}")
    raw = _prompt("Toggle (e.g. 1,3,5  or A  or N)", "")
    if raw.upper() == "A":
        cfg.reports = set(REPORT_OPTIONS.keys())
    elif raw.upper() == "N":
        cfg.reports = set()
    else:
        keys = list(REPORT_OPTIONS.keys())
        for token in raw.replace(",", " ").split():
            try:
                idx = int(token) - 1
                if 0 <= idx < len(keys):
                    k = keys[idx]
                    if k in cfg.reports:
                        cfg.reports.discard(k)
                    else:
                        cfg.reports.add(k)
            except ValueError:
                pass


def run_config_menu() -> SimConfig:
    cfg = SimConfig()
    while True:
        _display_config(cfg)
        choice = input("  Choice: ").strip().upper()
        if choice == "1":
            cfg.n_players = _prompt_int("Number of players", cfg.n_players,
                                        lo=MIN_TRAVELERS, hi=MAX_TRAVELERS)
        elif choice == "2":
            _configure_players(cfg)
        elif choice == "3":
            cfg.n_games = _prompt_int("Number of games", cfg.n_games, lo=1, hi=1_000_000)
        elif choice == "4":
            raw = _prompt("Seed (blank = random)", str(cfg.seed) if cfg.seed is not None else "")
            cfg.seed = int(raw) if raw.isdigit() else None
        elif choice == "5":
            _configure_reports(cfg)
        elif choice == "6":
            raw = _prompt("Save charts? (y/n)", "y" if cfg.save_charts else "n")
            cfg.save_charts = raw.lower().startswith("y")
            if cfg.save_charts:
                raw_dir = _prompt("Output directory", str(cfg.chart_dir))
                cfg.chart_dir = Path(raw_dir)
        elif choice == "R":
            return cfg
        elif choice == "Q":
            print("\n  Goodbye.\n")
            sys.exit(0)


# ---------------------------------------------------------------------------
# Run simulation
# ---------------------------------------------------------------------------

def run_simulation(cfg: SimConfig) -> list["GameResult"]:
    profiles = _load_profiles()
    strategies = {}
    for i in range(cfg.n_players):
        pname = cfg.profiles[i]
        strategies[f"P{i+1}_{pname}"] = profiles[pname]()

    print()
    print(f"  Running {cfg.n_games} games ({cfg.n_players} players)…")
    results = simulate_n_games(strategies, n=cfg.n_games, seed=cfg.seed)
    print("  Done.\n")
    return results


def _past_moves(results: list["GameResult"]) -> dict[str, list[tuple[int, int]]]:
    """Every Hour-to-Hour move toward the past, per traveler, as (from, to) centuries."""
    moves: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for result in results:
        prev: dict[str, int] = {}
        for snap in sorted(result.history, key=lambda s: s.hour):
            name = snap.traveler_name
            if name in prev and prev[name] > snap.century:
                moves[name].append((prev[name], snap.century))
            prev[name] = snap.century
    return moves


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------

def print_text_report(results: list["GameResult"], cfg: SimConfig) -> None:
    n = len(results)
    summary = summarise(results)
    item_stats = item_batch_stats(results)

    _header("SIMULATION  RESULTS")
    print(f"\n  {n} games  ·  {cfg.n_players} players"
          f"  ·  seed={cfg.seed if cfg.seed is not None else 'random'}")

    if "win_rates" in cfg.reports:
        _section("WIN RATES BY PROFILE")
        for name, rate in sorted(summary.win_rates.items(), key=lambda x: -x[1]):
            count = summary.win_counts[name]
            print(f"  {name:<22} {count:>5} wins  {rate:>5.1%}  {_bar(rate, width=28)}")
        if summary.draws:
            print(f"  {'(no winner)':<22} {summary.draws:>5} games")

    if "end_conditions" in cfg.reports:
        _section("VICTORY CONDITIONS")
        rule = {
            "year_zero": " (§11.1a)", "full_receptor": " (§11.1b)",
            "last_traveler": " (§11.1c)", "all_terminated": " (§11.1c)",
            "merchant_empty": " (§11.1d)", "max_hours_reached": " (safety limit)",
        }
        for reason, count in sorted(summary.end_reasons.items(), key=lambda x: -x[1]):
            pct = count / n
            label = REASON_LABELS.get(reason, reason) + rule.get(reason, "")
            print(f"  {label:<30} {count:>5}  {pct:>5.1%}  {_bar(pct, 20)}")

    if "game_length" in cfg.reports:
        gl = summary.game_length
        _section("GAME LENGTH (hours)")
        print(f"  Mean: {gl.mean:.1f}  │  Median: {gl.median:.0f}  │"
              f"  Std: {gl.stdev:.1f}  │  Range: {gl.min}-{gl.max}")

    if "combat_stats" in cfg.reports:
        _section("PER-PLAYER STATS")
        print(f"  {'Player':<22} {'Win%':>6}  {'AvgCP':>6}  {'AvgCen':>7}  {'Term%':>6}  {'Expl%':>6}")
        print(f"  {_line('─')}")
        for name, ts in summary.per_traveler.items():
            print(f"  {name:<22} {ts.win_rate:>5.1%}  {ts.mean_final_contract_points:>6.1f}"
                  f"  {ts.mean_final_century:>7.1f}  {ts.termination_rate:>5.1%}  {ts.explosion_rate:>5.1%}")

    if "card_activity" in cfg.reports and item_stats:
        _section("CARD ACTIVITY (per-game averages)")
        names = list(results[0].strategy_names.keys())
        col_w = max(len(n) for n in names) + 2
        print(f"  {'':18}" + "".join(f"  {n:>{col_w}}" for n in names))
        for label, key in [
            ("Buys",       "avg_buys_per_game"),
            ("Deliveries", "avg_deliveries_per_game"),
            ("Missed",     "avg_missed_deliveries_per_game"),
        ]:
            print(f"  {label:<18}" + "".join(
                f"  {item_stats[key].get(n, 0):>{col_w}.2f}" for n in names
            ))
        print()
        print(f"  Full receptor rate: {item_stats['full_receptor_rate']:.1%}")
        print()
        print("  Delivery period coverage (% games with ≥1 delivery in period):")
        for name in names:
            cov = item_stats["delivery_period_coverage"].get(name, {})
            parts = "  ".join(f"{p}: {v:.0%}" for p, v in cov.items())
            print(f"    {name}: {parts}")

    if "travel_stats" in cfg.reports:
        _section("TRAVEL STATISTICS")
        _print_travel_stats(results)

    print()


def _print_travel_stats(results: list["GameResult"]) -> None:
    """Average past-travel distance, moves per game, and the share of steps in overdrive."""
    moves = _past_moves(results)
    n = len(results)
    for name in sorted(moves.keys()):
        deltas = [a - b for a, b in moves[name]]
        if not deltas:
            continue
        total = sum(deltas)
        overdrive = sum(max(0, min(a, OVERDRIVE_THRESHOLD_CENTURY) - b) for a, b in moves[name])
        od_pct = overdrive / total if total else 0
        print(f"  {name:<22}  avg dist/move: {total / len(deltas):.1f}  "
              f"moves/game: {len(deltas) / n:.1f}  overdrive steps: {od_pct:.0%}")


# ---------------------------------------------------------------------------
# Charts: one function per chart, each returning the file it wrote
# ---------------------------------------------------------------------------

COLORS = ["#3b6ea5", "#e87040", "#5aaa5a", "#c060c0", "#c0aa30", "#60aac0"]


def _save(plt, fig, cfg: SimConfig, filename: str) -> Path:
    fig.tight_layout()
    path = cfg.chart_dir / filename
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def _chart_win_rates(plt, np, results, summary, names, cfg) -> Path:
    n = len(results)
    fig, ax = plt.subplots(figsize=(7, 4))
    sorted_names = sorted(names, key=lambda x: -summary.win_rates[x])
    rates = [summary.win_rates[x] * 100 for x in sorted_names]
    labels = [f"{x}\n({results[0].strategy_names[x]})" for x in sorted_names]
    bars = ax.bar(labels, rates, color=COLORS[:len(sorted_names)])
    for bar, rate in zip(bars, rates):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                f"{rate:.1f}%", ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("Win rate (%)")
    ax.set_title(f"Win rates by profile ({n} games)")
    ax.set_ylim(0, max(rates) * 1.15 if rates else 10)
    return _save(plt, fig, cfg, "win_rates.png")


def _chart_end_conditions(plt, np, results, summary, names, cfg) -> Path:
    n = len(results)
    ordered = sorted(summary.end_reasons.items(), key=lambda kv: -kv[1])
    labels = [REASON_LABELS.get(k, k) for k, _ in ordered]
    counts = [v for _, v in ordered]
    fig, ax = plt.subplots(figsize=(7, 4))
    bars = ax.bar(labels, counts, color=COLORS[:len(labels)])
    for bar, c in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                f"{c/n:.0%}", ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("Games")
    ax.set_title(f"Victory conditions ({n} games)")
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right")
    return _save(plt, fig, cfg, "win_conditions.png")


def _chart_game_length(plt, np, results, summary, names, cfg) -> Path:
    n = len(results)
    lengths = [r.hours_played for r in results]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(lengths, bins=range(min(lengths), max(lengths) + 2),
            color="#3b6ea5", edgecolor="white")
    ax.axvline(sum(lengths) / len(lengths), color="#e87040",
               linestyle="--", label=f"Mean {sum(lengths)/len(lengths):.1f}")
    ax.set_xlabel("Game length (hours)")
    ax.set_ylabel("Games")
    ax.set_title(f"Game-length distribution ({n} games)")
    ax.legend(fontsize=9)
    return _save(plt, fig, cfg, "game_length.png")


def _chart_resources(plt, np, results, summary, names, cfg) -> Path:
    trajs = trajectories(results)
    fig, axes = plt.subplots(1, 3, figsize=(14, 4), sharey=False)
    panels = [
        ("mean_energy",          "Mean Energy",          axes[0]),
        ("mean_century",         "Mean Century",         axes[1]),
        ("mean_contract_points", "Mean Contract Points", axes[2]),
    ]
    for attr, ylabel, ax in panels:
        for i, name in enumerate(names):
            t = trajs[name]
            ax.plot(t.hours, getattr(t, attr), label=name,
                    color=COLORS[i % len(COLORS)], linewidth=1.5)
        ax.set_xlabel("Hour")
        ax.set_ylabel(ylabel)
        ax.set_title(ylabel)
        ax.legend(fontsize=7)
        ax.grid(alpha=0.3)
    # Lower century is closer to Year Zero, so progress reads upward.
    axes[1].invert_yaxis()
    fig.suptitle(f"Resource trajectories ({len(results)} games)", fontsize=11)
    return _save(plt, fig, cfg, "trajectories.png")


def _chart_century_heatmap(plt, np, results, summary, names, cfg) -> Path:
    century_counts: dict[str, list[int]] = {name: [0] * (CENTURY_MAX + 1) for name in names}
    for result in results:
        for snap in result.history:
            if snap.traveler_name in century_counts and 0 <= snap.century <= CENTURY_MAX:
                century_counts[snap.traveler_name][snap.century] += 1

    fig, axes = plt.subplots(len(names), 1, figsize=(12, 2 * len(names) + 1), sharex=True)
    if len(names) == 1:
        axes = [axes]
    for i, name in enumerate(names):
        counts_arr = np.array(century_counts[name], dtype=float)
        if counts_arr.sum() > 0:
            counts_arr /= counts_arr.sum()
        axes[i].bar(range(CENTURY_MAX + 1), counts_arr,
                    color=COLORS[i % len(COLORS)], alpha=0.85)
        axes[i].set_ylabel(name, fontsize=8, rotation=0, ha="right", va="center")
        axes[i].set_yticks([])
        axes[i].axvline(OVERDRIVE_THRESHOLD_CENTURY, color="red", linestyle="--",
                        alpha=0.5, linewidth=0.8)
    axes[-1].set_xlabel("Century (0 = Year Zero, 30 = XXX start)")
    fig.suptitle(f"Century occupancy heatmap ({len(results)} games)", fontsize=11)
    return _save(plt, fig, cfg, "century_heatmap.png")


def _chart_card_activity(plt, np, results, summary, names, cfg) -> Path:
    item_stats = item_batch_stats(results)
    fig, ax = plt.subplots(figsize=(8, 4))
    x = np.arange(len(names))
    width = 0.25
    series = [
        ("avg_buys_per_game",              "Buys",       "#3b6ea5"),
        ("avg_deliveries_per_game",        "Deliveries", "#5aaa5a"),
        ("avg_missed_deliveries_per_game", "Missed",     "#e87040"),
    ]
    for j, (key, label, color) in enumerate(series):
        vals = [item_stats[key].get(name, 0) for name in names]
        ax.bar(x + j * width, vals, width, label=label, color=color)
    short_names = [n.split("_")[1] if "_" in n else n for n in names]
    ax.set_xticks(x + width)
    ax.set_xticklabels(short_names)
    ax.set_ylabel("Events per game")
    ax.set_title(f"Card activity per game ({len(results)} games)")
    ax.legend(fontsize=9)
    return _save(plt, fig, cfg, "card_activity.png")


def _chart_combat_stats(plt, np, results, summary, names, cfg) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    term_rates = [summary.per_traveler[n].termination_rate * 100 for n in names]
    expl_rates = [summary.per_traveler[n].explosion_rate * 100 for n in names]
    short = [results[0].strategy_names[n] for n in names]

    axes[0].bar(short, term_rates, color=COLORS[:len(names)])
    axes[0].set_ylabel("Termination rate (%)")
    axes[0].set_title("Termination rate by profile")
    for bar, v in zip(axes[0].patches, term_rates):
        axes[0].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                     f"{v:.1f}%", ha="center", va="bottom", fontsize=8)

    axes[1].bar(short, expl_rates, color=COLORS[:len(names)])
    axes[1].set_ylabel("Explosion rate (% of hours)")
    axes[1].set_title("Explosion rate by profile")
    for bar, v in zip(axes[1].patches, expl_rates):
        axes[1].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                     f"{v:.1f}%", ha="center", va="bottom", fontsize=8)
    return _save(plt, fig, cfg, "combat_stats.png")


def _chart_travel_distance(plt, np, results, summary, names, cfg) -> Path | None:
    by_player = {name: [a - b for a, b in moves] for name, moves in _past_moves(results).items()}
    if not by_player:
        return None
    fig, ax = plt.subplots(figsize=(8, 4))
    max_dist = max((max(v) for v in by_player.values() if v), default=6)
    bins = range(1, min(max_dist + 2, 25))
    for i, nm in enumerate(names):
        if by_player.get(nm):
            ax.hist(by_player[nm], bins=bins, alpha=0.6,
                    label=results[0].strategy_names[nm],
                    color=COLORS[i % len(COLORS)], density=True)
    ax.set_xlabel("Centuries traveled (past) per move")
    ax.set_ylabel("Density")
    ax.set_title(f"Past-travel distance distribution ({len(results)} games)")
    ax.axvline(OVERDRIVE_THRESHOLD_CENTURY, color="red", linestyle="--", alpha=0.7,
               linewidth=1, label="Overdrive threshold (X)")
    ax.legend(fontsize=8)
    return _save(plt, fig, cfg, "travel_distance.png")


# Report option -> chart, in the order the charts are drawn.
CHARTS = {
    "win_rates":       _chart_win_rates,
    "end_conditions":  _chart_end_conditions,
    "game_length":     _chart_game_length,
    "resources":       _chart_resources,
    "century_heatmap": _chart_century_heatmap,
    "card_activity":   _chart_card_activity,
    "combat_stats":    _chart_combat_stats,
    "travel_stats":    _chart_travel_distance,
}


def generate_charts(results: list["GameResult"], cfg: SimConfig) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("  [charts] matplotlib not available: skipping charts.")
        return

    cfg.chart_dir.mkdir(parents=True, exist_ok=True)
    summary = summarise(results)
    names = list(results[0].strategy_names.keys())

    generated: list[Path] = []
    for key, chart in CHARTS.items():
        if key not in cfg.reports:
            continue
        if key == "card_activity" and not item_batch_stats(results):
            continue
        path = chart(plt, np, results, summary, names, cfg)
        if path is not None:
            generated.append(path)

    if generated:
        print(f"\n  Charts saved to: {cfg.chart_dir.resolve()}/")
        for p in generated:
            print(f"    · {p.name}")


# ---------------------------------------------------------------------------
# Optional narrated game
# ---------------------------------------------------------------------------

def _offer_verbose() -> None:
    print()
    raw = input("  Run a single verbose trace game? (y/N): ").strip().lower()
    if raw.startswith("y"):
        from simulation.strategies.aggressive import AggressiveStrategy
        from simulation.strategies.conservative import ConservativeStrategy
        from simulation.strategies.smart import SmartStrategy
        from simulation.strategies.collector import CollectorStrategy
        from simulation.verbose_run import verbose_simulate_game, LoggingStrategy

        raw_strats = {
            "P1_Aggro": AggressiveStrategy(),
            "P2_Cons":  ConservativeStrategy(),
            "P3_Smart": SmartStrategy(),
            "P4_Coll":  CollectorStrategy(),
        }
        strats = {n: LoggingStrategy(s) for n, s in raw_strats.items()}
        print()
        print("  === VERBOSE SINGLE-GAME TRACE ===")
        print("  (Use Ctrl+C to abort early)")
        print()
        try:
            verbose_simulate_game(strats)
        except KeyboardInterrupt:
            print("\n  [Interrupted]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = run_config_menu()
    results = run_simulation(cfg)
    print_text_report(results, cfg)
    if cfg.save_charts and cfg.reports:
        print("  Generating charts…")
        generate_charts(results, cfg)
    _offer_verbose()


if __name__ == "__main__":
    main()

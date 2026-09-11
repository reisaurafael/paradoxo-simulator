"""
simulation/sim_menu.py
======================
Interactive simulation runner for Paradoxo.

Configuration menu → simulation → professional reporting with charts.

Usage:
    python -m simulation.sim_menu
    python simulation/sim_menu.py
"""

from __future__ import annotations

import io
import sys
import os
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

# Force UTF-8 output for Portuguese card names.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from simulation.runner import simulate_n_games
from simulation.metrics import summarise, print_summary, trajectories
from simulation.reports import item_batch_stats

if TYPE_CHECKING:
    from engine.state import GameResult

# ---------------------------------------------------------------------------
# Profile registry
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

# ---------------------------------------------------------------------------
# Report options
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

class SimConfig:
    def __init__(self):
        self.n_players: int = 4
        self.profiles: list[str] = ["Aggressive", "Conservative", "Smart", "Collector"]
        self.n_games: int = 500
        self.seed: int | None = 42
        self.reports: set[str] = set(REPORT_OPTIONS.keys())
        self.save_charts: bool = True
        self.chart_dir: Path = Path("sim_output")


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
    _header("PARADOXO  SIMULATION  RUNNER")
    print()
    print(f"  Current configuration")
    print(f"  {_line('─')}")
    print(f"  [1]  Number of players : {cfg.n_players}")
    print()
    print(f"  [2]  Player profiles   :")
    for i, p in enumerate(cfg.profiles[:cfg.n_players]):
        print(f"         Player {i+1}: {p}")
    print()
    print(f"  [3]  Number of games   : {cfg.n_games}")
    seed_str = str(cfg.seed) if cfg.seed is not None else "random"
    print(f"  [4]  Random seed       : {seed_str}")
    print()
    print(f"  [5]  Reports to generate:")
    for key, label in REPORT_OPTIONS.items():
        mark = "✓" if key in cfg.reports else " "
        print(f"         [{mark}] {label}")
    print()
    chart_str = f"Yes → {cfg.chart_dir}/" if cfg.save_charts else "No"
    print(f"  [6]  Save charts       : {chart_str}")
    print()
    print(f"  [R]  Run simulation")
    print(f"  [Q]  Quit")
    print()


def _configure_players(cfg: SimConfig) -> None:
    cfg.n_players = _prompt_int("Number of players", cfg.n_players, lo=2, hi=6)
    profiles = _load_profiles()
    print(f"\n  Available profiles: {', '.join(PROFILE_KEYS)}")
    while len(cfg.profiles) < cfg.n_players:
        cfg.profiles.append("Aggressive")
    for i in range(cfg.n_players):
        while True:
            raw = _prompt(f"  Profile for Player {i+1}", cfg.profiles[i])
            # Try prefix match (case-insensitive)
            match = next(
                (k for k in profiles if k.lower().startswith(raw.lower())), None
            )
            if match:
                cfg.profiles[i] = match
                break
            print(f"    ✗ Unknown profile. Choose from: {', '.join(PROFILE_KEYS)}")


def _configure_reports(cfg: SimConfig) -> None:
    print(f"\n  Toggle reports: enter keys to flip (comma-separated), or A=all, N=none:")
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
            cfg.n_players = _prompt_int("Number of players", cfg.n_players, lo=2, hi=6)
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
        tname = f"P{i+1}_{pname}"
        strategies[tname] = profiles[pname]()

    print()
    print(f"  Running {cfg.n_games} games ({cfg.n_players} players)…")
    results = simulate_n_games(strategies, n=cfg.n_games, seed=cfg.seed)
    print(f"  Done.\n")
    return results


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
            ts = summary.per_traveler[name]
            count = summary.win_counts[name]
            bar = _bar(rate, width=28)
            print(f"  {name:<22} {count:>5} wins  {rate:>5.1%}  {bar}")
        if summary.draws:
            print(f"  {'(no winner)':<22} {summary.draws:>5} games")

    if "end_conditions" in cfg.reports:
        _section("VICTORY CONDITIONS")
        for reason, count in sorted(summary.end_reasons.items(), key=lambda x: -x[1]):
            pct = count / n
            label = {
                "year_zero":      "Year Zero (§11.1a)",
                "full_receptor":  "Full Receptor (§11.1b)",
                "last_traveler":  "Last Traveler (§11.1c)",
                "all_terminated": "All Terminated (§11.1c)",
                "merchant_empty": "Merchant Empty (§11.1d)",
                "max_hours_reached": "Max Hours (safety limit)",
            }.get(reason, reason)
            print(f"  {label:<30} {count:>5}  {pct:>5.1%}  {_bar(pct, 20)}")

    if "game_length" in cfg.reports:
        gl = summary.game_length
        _section("GAME LENGTH (hours)")
        print(f"  Mean: {gl.mean:.1f}  │  Median: {gl.median:.0f}  │"
              f"  Std: {gl.stdev:.1f}  │  Range: {gl.min}-{gl.max}")

    if "combat_stats" in cfg.reports:
        _section("PER-PLAYER STATS")
        header = f"  {'Player':<22} {'Win%':>6}  {'AvgCP':>6}  {'AvgCen':>7}  {'Term%':>6}  {'Expl%':>6}"
        print(header)
        print(f"  {_line('─')}")
        for name, ts in summary.per_traveler.items():
            print(f"  {name:<22} {ts.win_rate:>5.1%}  {ts.mean_final_contract_points:>6.1f}"
                  f"  {ts.mean_final_century:>7.1f}  {ts.termination_rate:>5.1%}  {ts.explosion_rate:>5.1%}")

    if "card_activity" in cfg.reports and item_stats:
        _section("CARD ACTIVITY (per-game averages)")
        names = list(results[0].strategy_names.keys())
        col_w = max(len(n) for n in names) + 2
        header = f"  {'':18}" + "".join(f"  {n:>{col_w}}" for n in names)
        print(header)
        for label, key in [
            ("Buys",       "avg_buys_per_game"),
            ("Deliveries", "avg_deliveries_per_game"),
            ("Missed",     "avg_missed_deliveries_per_game"),
        ]:
            row = f"  {label:<18}" + "".join(
                f"  {item_stats[key].get(n, 0):>{col_w}.2f}" for n in names
            )
            print(row)
        print()
        print(f"  Full receptor rate: {item_stats['full_receptor_rate']:.1%}")
        print()
        print(f"  Delivery period coverage (% games with ≥1 delivery in period):")
        for name in names:
            cov = item_stats["delivery_period_coverage"].get(name, {})
            parts = "  ".join(f"{p}: {v:.0%}" for p, v in cov.items())
            print(f"    {name}: {parts}")

    if "travel_stats" in cfg.reports:
        _section("TRAVEL STATISTICS")
        _print_travel_stats(results)

    print()


def _print_travel_stats(results: list["GameResult"]) -> None:
    """Compute travel distance stats from history snapshots."""
    from collections import defaultdict
    by_player: dict[str, list[int]] = defaultdict(list)
    overdrive_steps: dict[str, int] = defaultdict(int)  # steps at century ≤ 10
    total_past_steps: dict[str, int] = defaultdict(int)

    for result in results:
        prev: dict[str, int] = {}
        for snap in sorted(result.history, key=lambda s: s.hour):
            name = snap.traveler_name
            if name in prev:
                delta = prev[name] - snap.century  # positive = past travel
                if delta > 0:
                    by_player[name].append(delta)
                    total_past_steps[name] += delta
                    # Count overdrive steps (at or below century 10)
                    prev_c = prev[name]
                    new_c = snap.century
                    od = max(0, min(prev_c, 10) - new_c)
                    overdrive_steps[name] += od
            prev[name] = snap.century

    n = len(results)
    for name in sorted(by_player.keys()):
        moves = by_player[name]
        if not moves:
            continue
        avg_dist = sum(moves) / len(moves)
        n_moves = len(moves) / n
        od = overdrive_steps[name]
        total = total_past_steps[name]
        od_pct = od / total if total else 0
        print(f"  {name:<22}  avg dist/move: {avg_dist:.1f}  "
              f"moves/game: {n_moves:.1f}  overdrive steps: {od_pct:.0%}")


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

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
    n = len(results)
    summary = summarise(results)
    names = list(results[0].strategy_names.keys())
    item_stats = item_batch_stats(results)

    colors = ["#3b6ea5", "#e87040", "#5aaa5a", "#c060c0", "#c0aa30", "#60aac0"]

    generated: list[Path] = []

    # --- Figure 1: Win rates ---
    if "win_rates" in cfg.reports:
        fig, ax = plt.subplots(figsize=(7, 4))
        sorted_names = sorted(names, key=lambda n: -summary.win_rates[n])
        rates = [summary.win_rates[n] * 100 for n in sorted_names]
        labels = [f"{n}\n({results[0].strategy_names[n]})" for n in sorted_names]
        bars = ax.bar(labels, rates, color=colors[:len(sorted_names)])
        for bar, rate in zip(bars, rates):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                    f"{rate:.1f}%", ha="center", va="bottom", fontsize=9)
        ax.set_ylabel("Win rate (%)")
        ax.set_title(f"Win rates by profile ({n} games)")
        ax.set_ylim(0, max(rates) * 1.15 if rates else 10)
        fig.tight_layout()
        path = cfg.chart_dir / "win_rates.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        generated.append(path)

    # --- Figure 2: Victory conditions ---
    if "end_conditions" in cfg.reports:
        end_reasons = summary.end_reasons
        REASON_LABELS = {
            "year_zero":       "Year Zero",
            "full_receptor":   "Full Receptor",
            "last_traveler":   "Last Traveler",
            "all_terminated":  "All Terminated",
            "merchant_empty":  "Merchant Empty",
            "max_hours_reached": "Max Hours",
        }
        ordered = sorted(end_reasons.items(), key=lambda kv: -kv[1])
        labels = [REASON_LABELS.get(k, k) for k, _ in ordered]
        counts = [v for _, v in ordered]

        fig, ax = plt.subplots(figsize=(7, 4))
        bars = ax.bar(labels, counts, color=colors[:len(labels)])
        for bar, c in zip(bars, counts):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                    f"{c/n:.0%}", ha="center", va="bottom", fontsize=9)
        ax.set_ylabel("Games")
        ax.set_title(f"Victory conditions ({n} games)")
        plt.setp(ax.get_xticklabels(), rotation=20, ha="right")
        fig.tight_layout()
        path = cfg.chart_dir / "win_conditions.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        generated.append(path)

    # --- Figure 3: Game length distribution ---
    if "game_length" in cfg.reports:
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
        fig.tight_layout()
        path = cfg.chart_dir / "game_length.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        generated.append(path)

    # --- Figure 4: Resource trajectories ---
    if "resources" in cfg.reports:
        trajs = trajectories(results)
        fig, axes = plt.subplots(1, 3, figsize=(14, 4), sharey=False)
        metrics = [
            ("mean_energy",          "Mean Energy",           axes[0]),
            ("mean_century",         "Mean Century",          axes[1]),
            ("mean_contract_points", "Mean Contract Points",  axes[2]),
        ]
        for attr, ylabel, ax in metrics:
            for i, name in enumerate(names):
                t = trajs[name]
                ax.plot(t.hours, getattr(t, attr), label=name,
                        color=colors[i % len(colors)], linewidth=1.5)
            ax.set_xlabel("Hour")
            ax.set_ylabel(ylabel)
            ax.set_title(ylabel)
            ax.legend(fontsize=7)
            ax.grid(alpha=0.3)
        # Invert century axis (lower century = closer to Year Zero = progress)
        axes[1].invert_yaxis()
        fig.suptitle(f"Resource trajectories ({n} games)", fontsize=11)
        fig.tight_layout()
        path = cfg.chart_dir / "trajectories.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        generated.append(path)

    # --- Figure 5: Century occupancy heatmap ---
    if "century_heatmap" in cfg.reports:
        from engine.constants import CENTURY_MAX
        century_counts: dict[str, list[int]] = {
            name: [0] * (CENTURY_MAX + 1) for name in names
        }
        for result in results:
            for snap in result.history:
                name = snap.traveler_name
                if name in century_counts and 0 <= snap.century <= CENTURY_MAX:
                    century_counts[name][snap.century] += 1

        fig, axes = plt.subplots(len(names), 1,
                                  figsize=(12, 2 * len(names) + 1),
                                  sharex=True)
        if len(names) == 1:
            axes = [axes]
        for i, name in enumerate(names):
            counts_arr = np.array(century_counts[name], dtype=float)
            if counts_arr.sum() > 0:
                counts_arr /= counts_arr.sum()
            axes[i].bar(range(CENTURY_MAX + 1), counts_arr,
                        color=colors[i % len(colors)], alpha=0.85)
            axes[i].set_ylabel(name, fontsize=8, rotation=0, ha="right", va="center")
            axes[i].set_yticks([])
            axes[i].axvline(10, color="red", linestyle="--", alpha=0.5, linewidth=0.8)
        axes[-1].set_xlabel("Century (0 = Year Zero, 30 = XXX start)")
        fig.suptitle(f"Century occupancy heatmap ({n} games)", fontsize=11)
        fig.tight_layout()
        path = cfg.chart_dir / "century_heatmap.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        generated.append(path)

    # --- Figure 6: Card activity ---
    if "card_activity" in cfg.reports and item_stats:
        fig, ax = plt.subplots(figsize=(8, 4))
        x = np.arange(len(names))
        width = 0.25
        keys = [
            ("avg_buys_per_game",             "Buys",        "#3b6ea5"),
            ("avg_deliveries_per_game",       "Deliveries",  "#5aaa5a"),
            ("avg_missed_deliveries_per_game","Missed",       "#e87040"),
        ]
        for j, (key, label, color) in enumerate(keys):
            vals = [item_stats[key].get(name, 0) for name in names]
            ax.bar(x + j * width, vals, width, label=label, color=color)
        short_names = [n.split("_")[1] if "_" in n else n for n in names]
        ax.set_xticks(x + width)
        ax.set_xticklabels(short_names)
        ax.set_ylabel("Events per game")
        ax.set_title(f"Card activity per game ({n} games)")
        ax.legend(fontsize=9)
        fig.tight_layout()
        path = cfg.chart_dir / "card_activity.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        generated.append(path)

    # --- Figure 7: Combat / elimination stats ---
    if "combat_stats" in cfg.reports:
        fig, axes = plt.subplots(1, 2, figsize=(10, 4))
        term_rates = [summary.per_traveler[n].termination_rate * 100 for n in names]
        expl_rates = [summary.per_traveler[n].explosion_rate * 100 for n in names]
        short = [results[0].strategy_names[n] for n in names]

        axes[0].bar(short, term_rates, color=colors[:len(names)])
        axes[0].set_ylabel("Termination rate (%)")
        axes[0].set_title("Termination rate by profile")
        for bar, v in zip(axes[0].patches, term_rates):
            axes[0].text(bar.get_x() + bar.get_width() / 2,
                         bar.get_height() + 0.5, f"{v:.1f}%",
                         ha="center", va="bottom", fontsize=8)

        axes[1].bar(short, expl_rates, color=colors[:len(names)])
        axes[1].set_ylabel("Explosion rate (% of hours)")
        axes[1].set_title("Explosion rate by profile")
        for bar, v in zip(axes[1].patches, expl_rates):
            axes[1].text(bar.get_x() + bar.get_width() / 2,
                         bar.get_height() + 0.05, f"{v:.1f}%",
                         ha="center", va="bottom", fontsize=8)

        fig.tight_layout()
        path = cfg.chart_dir / "combat_stats.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        generated.append(path)

    # --- Figure 8: Travel distance histogram ---
    if "travel_stats" in cfg.reports:
        by_player: dict[str, list[int]] = defaultdict(list)
        for result in results:
            prev: dict[str, int] = {}
            for snap in sorted(result.history, key=lambda s: s.hour):
                nm = snap.traveler_name
                if nm in prev:
                    delta = prev[nm] - snap.century
                    if delta > 0:
                        by_player[nm].append(delta)
                prev[nm] = snap.century

        if by_player:
            fig, ax = plt.subplots(figsize=(8, 4))
            max_dist = max((max(v) for v in by_player.values() if v), default=6)
            bins = range(1, min(max_dist + 2, 25))
            for i, nm in enumerate(names):
                if nm in by_player and by_player[nm]:
                    ax.hist(by_player[nm], bins=bins, alpha=0.6,
                            label=results[0].strategy_names[nm],
                            color=colors[i % len(colors)], density=True)
            ax.set_xlabel("Centuries traveled (past) per move")
            ax.set_ylabel("Density")
            ax.set_title(f"Past-travel distance distribution ({n} games)")
            ax.axvline(10, color="red", linestyle="--", alpha=0.7,
                       linewidth=1, label="Overdrive threshold (X)")
            ax.legend(fontsize=8)
            fig.tight_layout()
            path = cfg.chart_dir / "travel_distance.png"
            fig.savefig(path, dpi=120)
            plt.close(fig)
            generated.append(path)

    if generated:
        print(f"\n  Charts saved to: {cfg.chart_dir.resolve()}/")
        for p in generated:
            print(f"    · {p.name}")


# ---------------------------------------------------------------------------
# Verbose mode option
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
        print(f"  Generating charts…")
        generate_charts(results, cfg)

    _offer_verbose()


if __name__ == "__main__":
    main()

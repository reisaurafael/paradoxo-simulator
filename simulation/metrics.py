"""
simulation/metrics.py
=====================
Statistics over a batch of GameResults from simulate_n_games().

Everything here is a pure function: a list of GameResult in, plain dicts or
dataclasses out. Printing is limited to print_summary() at the bottom.
"""

from __future__ import annotations
from dataclasses import dataclass
from statistics import mean, median, stdev
from collections import Counter, defaultdict
from engine.constants import PERIODS
from engine.state import GameResult, HourSnapshot
from engine.timeline import periods_for_century


# ---------------------------------------------------------------------------
# Top-level summary
# ---------------------------------------------------------------------------

@dataclass
class BatchSummary:
    """Aggregate statistics over a batch of games."""
    n_games: int
    win_rates: dict[str, float]          # traveler_name → fraction of games won
    win_counts: dict[str, int]           # traveler_name → raw win count
    draws: int                           # games with no winner
    game_length: LengthStats
    end_reasons: dict[str, int]          # end_reason → count
    per_traveler: dict[str, TravelerStats]


@dataclass
class LengthStats:
    """Distribution of game lengths in hours."""
    mean: float
    median: float
    stdev: float
    min: int
    max: int


@dataclass
class TravelerStats:
    """Per-traveler aggregates across all games in the batch."""
    name: str
    strategy: str
    win_rate: float
    mean_final_energy: float
    mean_final_century: float            # Lower = closer to Year Zero
    mean_final_contract_points: float
    mean_hours_survived: float           # Hours before termination (or game end)
    explosion_rate: float                # Fraction of hours where traveler exploded
    termination_rate: float              # Fraction of games where traveler terminated


def summarise(results: list[GameResult]) -> BatchSummary:
    """
    Compute a full BatchSummary over a list of GameResult objects.

    This is the main entry point for metrics analysis. Pass the output of
    simulate_n_games() directly.
    """
    n = len(results)
    if n == 0:
        raise ValueError("Cannot summarise an empty result list.")

    # Win counts
    win_counts: Counter[str] = Counter()
    draws = 0
    for r in results:
        if r.winner is not None:
            win_counts[r.winner] += 1
        else:
            draws += 1

    # Ensure every traveler appears in win_counts (with 0 if needed)
    all_names = list(results[0].strategy_names.keys())
    for name in all_names:
        if name not in win_counts:
            win_counts[name] = 0

    win_rates = {name: win_counts[name] / n for name in all_names}

    # Game length
    lengths = [r.hours_played for r in results]
    game_length = LengthStats(
        mean=mean(lengths),
        median=median(lengths),
        stdev=stdev(lengths) if len(lengths) > 1 else 0.0,
        min=min(lengths),
        max=max(lengths),
    )

    # End reasons
    end_reasons: Counter[str] = Counter(r.end_reason for r in results)

    # Per-traveler stats
    per_traveler = {
        name: _traveler_stats(name, results)
        for name in all_names
    }

    return BatchSummary(
        n_games=n,
        win_rates=win_rates,
        win_counts=dict(win_counts),
        draws=draws,
        game_length=game_length,
        end_reasons=dict(end_reasons),
        per_traveler=per_traveler,
    )


# ---------------------------------------------------------------------------
# Per-traveler stats
# ---------------------------------------------------------------------------

def _traveler_stats(name: str, results: list[GameResult]) -> TravelerStats:
    strategy = results[0].strategy_names.get(name, "unknown")

    final_energies = []
    final_centuries = []
    final_cps = []
    hours_survived = []
    terminated_count = 0

    all_snapshots: list[HourSnapshot] = []
    for r in results:
        snapshots = [s for s in r.history if s.traveler_name == name]
        all_snapshots.extend(snapshots)

        # Final state from traveler_results
        final = next((t for t in r.traveler_results if t.name == name), None)
        if final is not None:
            final_energies.append(final.energy)
            final_centuries.append(final.century)
            final_cps.append(final.contract_points)
            if final.is_terminated:
                terminated_count += 1

        # Hours survived = last snapshot hour for this traveler
        if snapshots:
            hours_survived.append(snapshots[-1].hour)

    n = len(results)
    total_snapshots = len(all_snapshots)
    explosion_count = sum(1 for s in all_snapshots if s.exploded)

    return TravelerStats(
        name=name,
        strategy=strategy,
        win_rate=sum(1 for r in results if r.winner == name) / n,
        mean_final_energy=mean(final_energies) if final_energies else 0.0,
        mean_final_century=mean(final_centuries) if final_centuries else 0.0,
        mean_final_contract_points=mean(final_cps) if final_cps else 0.0,
        mean_hours_survived=mean(hours_survived) if hours_survived else 0.0,
        explosion_rate=explosion_count / total_snapshots if total_snapshots else 0.0,
        termination_rate=terminated_count / n,
    )


# ---------------------------------------------------------------------------
# Trajectory analysis
# ---------------------------------------------------------------------------

@dataclass
class Trajectory:
    """Hour-by-hour mean values for one traveler across all games."""
    traveler_name: str
    hours: list[int]
    mean_energy: list[float]
    mean_century: list[float]
    mean_contract_points: list[float]


def trajectories(results: list[GameResult]) -> dict[str, Trajectory]:
    """
    Compute mean resource trajectories per hour for each traveler.

    Returns a dict of traveler_name → Trajectory. Hours where a traveler
    has already terminated are excluded from the average, so the curves
    represent only active travelers.
    """
    all_names = list(results[0].strategy_names.keys())
    out: dict[str, Trajectory] = {}

    for name in all_names:
        # Group snapshots by hour
        by_hour: dict[int, list[HourSnapshot]] = {}
        for r in results:
            for s in r.history:
                if s.traveler_name == name and not s.terminated:
                    by_hour.setdefault(s.hour, []).append(s)

        hours = sorted(by_hour.keys())
        out[name] = Trajectory(
            traveler_name=name,
            hours=hours,
            mean_energy=[mean(s.energy for s in by_hour[h]) for h in hours],
            mean_century=[mean(s.century for s in by_hour[h]) for h in hours],
            mean_contract_points=[mean(s.contract_points for s in by_hour[h]) for h in hours],
        )

    return out


# ---------------------------------------------------------------------------
# Item events
# ---------------------------------------------------------------------------

def item_batch_stats(results: list[GameResult]) -> dict:
    """
    Aggregate the item events of a batch of games.

    Returns a dict with keys:
        avg_buys_per_game               dict[traveler_name, float]
        avg_deliveries_per_game         dict[traveler_name, float]
        avg_missed_deliveries_per_game  dict[traveler_name, float]
        delivery_period_coverage        dict[traveler_name, dict[period, float]]:
                                        the share of games in which the traveler
                                        delivered at least one card in that period
        full_receptor_rate              float: share of games won by full_receptor
    """
    if not results:
        return {}

    all_travelers = list(results[0].strategy_names.keys())
    n = len(results)

    buys: dict[str, int] = defaultdict(int)
    deliveries: dict[str, int] = defaultdict(int)
    missed: dict[str, int] = defaultdict(int)
    # traveler -> period -> number of games where it was covered
    period_covered: dict[str, dict[str, int]] = {
        t: {period: 0 for period in PERIODS} for t in all_travelers
    }
    full_receptor_count = 0

    for result in results:
        if result.end_reason == "full_receptor":
            full_receptor_count += 1

        covered: dict[str, set[str]] = defaultdict(set)
        for ev in result.item_events:
            if ev.event_type == "bought":
                buys[ev.traveler] += 1
            elif ev.event_type == "delivered":
                deliveries[ev.traveler] += 1
                covered[ev.traveler].update(periods_for_century(ev.century))
            elif ev.event_type == "missed_delivery":
                missed[ev.traveler] += 1

        for t in all_travelers:
            for period in covered.get(t, set()):
                if period in period_covered[t]:
                    period_covered[t][period] += 1

    return {
        "avg_buys_per_game": {t: buys[t] / n for t in all_travelers},
        "avg_deliveries_per_game": {t: deliveries[t] / n for t in all_travelers},
        "avg_missed_deliveries_per_game": {t: missed[t] / n for t in all_travelers},
        "delivery_period_coverage": {
            t: {p: period_covered[t][p] / n for p in period_covered[t]}
            for t in all_travelers
        },
        "full_receptor_rate": full_receptor_count / n,
    }


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

def print_summary(summary: BatchSummary) -> None:
    """Print a readable report to stdout."""
    print(f"=== Batch Summary ({summary.n_games} games) ===\n")

    print("Win rates:")
    for name, rate in sorted(summary.win_rates.items(), key=lambda x: -x[1]):
        strat = summary.per_traveler[name].strategy
        count = summary.win_counts[name]
        print(f"  {name} ({strat}): {count} wins  ({rate:.1%})")
    if summary.draws:
        print(f"  (no winner): {summary.draws}")

    gl = summary.game_length
    print(f"\nGame length (hours):  mean={gl.mean:.1f}  median={gl.median:.0f}"
          f"  std={gl.stdev:.1f}  min={gl.min}  max={gl.max}")

    print("\nEnd reasons:")
    for reason, count in sorted(summary.end_reasons.items(), key=lambda x: -x[1]):
        print(f"  {reason}: {count}")

    print("\nPer-traveler detail:")
    for name, ts in summary.per_traveler.items():
        print(f"  {name} ({ts.strategy}):")
        print(f"    Avg final century : {ts.mean_final_century:.1f}  "
              f"(century 0 = Year Zero, 30 = start)")
        print(f"    Avg final energy  : {ts.mean_final_energy:.1f}")
        print(f"    Avg final CP      : {ts.mean_final_contract_points:.1f}")
        print(f"    Explosion rate    : {ts.explosion_rate:.1%} of hours")
        print(f"    Termination rate  : {ts.termination_rate:.1%} of games")

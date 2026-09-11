"""
simulation/reports.py
=====================
Human-readable reports and batch statistics built from GameResult item events.

Two public functions:
    item_report(result):        per-game event log as a formatted string
    item_batch_stats(results): aggregate statistics over a list of GameResult
"""

from __future__ import annotations
from collections import defaultdict
from engine.state import GameResult, ItemEvent
from engine.timeline import periods_for_century


# ---------------------------------------------------------------------------
# Single-game report
# ---------------------------------------------------------------------------

def item_report(result: GameResult) -> str:
    """
    Return a multi-line string report of all ItemEvents in one game,
    grouped by Hour and traveler.

    Header shows winner, hours played, and end reason.
    """
    lines: list[str] = []
    lines.append(f"Winner: {result.winner or 'none'}  |  "
                 f"Hours played: {result.hours_played}  |  "
                 f"End reason: {result.end_reason}")
    lines.append("")

    if not result.item_events:
        lines.append("(no item events recorded)")
        return "\n".join(lines)

    # Group events by hour, then by traveler
    by_hour: dict[int, list[ItemEvent]] = defaultdict(list)
    for ev in result.item_events:
        by_hour[ev.hour].append(ev)

    for hour in sorted(by_hour.keys()):
        lines.append(f"Hour {hour}:")
        # Group within this hour by traveler
        by_traveler: dict[str, list[ItemEvent]] = defaultdict(list)
        for ev in by_hour[hour]:
            by_traveler[ev.traveler].append(ev)
        for traveler in sorted(by_traveler.keys()):
            for ev in by_traveler[traveler]:
                lines.append(
                    f"  [{ev.traveler}] {ev.event_type}: {ev.card_name}"
                    f" (century={ev.century})"
                )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Batch statistics
# ---------------------------------------------------------------------------

def item_batch_stats(results: list[GameResult]) -> dict:
    """
    Compute aggregate item-event statistics over a list of GameResult objects.

    Returns a dict with keys:
        avg_buys_per_game            dict[traveler_name, float]
        avg_deliveries_per_game      dict[traveler_name, float]
        avg_missed_deliveries_per_game dict[traveler_name, float]
        delivery_period_coverage     dict[traveler_name, dict[period, float]]
                                     fraction of games in which the traveler
                                     delivered at least one card in that period
                                     (Origins / Ascension / Singularity)
        full_receptor_rate           float: fraction of games ending with full_receptor
    """
    if not results:
        return {}

    all_travelers = list(results[0].strategy_names.keys())
    n = len(results)

    buys: dict[str, int] = defaultdict(int)
    deliveries: dict[str, int] = defaultdict(int)
    missed: dict[str, int] = defaultdict(int)

    # period coverage: traveler → period → count of games where covered
    period_covered: dict[str, dict[str, int]] = {
        t: {"Origins": 0, "Ascension": 0, "Singularity": 0}
        for t in all_travelers
    }

    full_receptor_count = 0

    for result in results:
        if result.end_reason == "full_receptor":
            full_receptor_count += 1

        # Accumulate event counts
        for ev in result.item_events:
            if ev.event_type == "bought":
                buys[ev.traveler] += 1
            elif ev.event_type == "delivered":
                deliveries[ev.traveler] += 1
            elif ev.event_type == "missed_delivery":
                missed[ev.traveler] += 1

        # Delivery period coverage: what periods did each traveler cover this game?
        per_traveler_periods: dict[str, set[str]] = defaultdict(set)
        for ev in result.item_events:
            if ev.event_type == "delivered":
                for period in periods_for_century(ev.century):
                    per_traveler_periods[ev.traveler].add(period)

        for t in all_travelers:
            for period in per_traveler_periods.get(t, set()):
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

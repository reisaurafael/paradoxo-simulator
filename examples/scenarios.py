"""
examples/scenarios.py
====================
Scenario simulation runner.

Executes multiple scenarios across different player counts, profile combinations,
and configurations to validate engine behavior and performance characteristics.
"""

import io
import sys
import random

# Force UTF-8 output so the bar charts and Portuguese card names render on
# consoles that default to a legacy code page (e.g. Windows cp1252).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from simulation.runner import simulate_n_games
from simulation.strategies.aggressive import AggressiveStrategy
from simulation.strategies.conservative import ConservativeStrategy
from simulation.strategies.smart import SmartStrategy
from simulation.strategies.collector import CollectorStrategy
from simulation.metrics import summarise


def scenario(name: str, game_count: int, strategies: list, seed: int):
    """Run a single scenario and report results."""
    print(f"\n{'='*70}")
    print(f"SCENARIO: {name}")
    print(f"{'='*70}")
    print(f"Configuration: {game_count} games, {len(strategies)} player(s), seed={seed}")
    
    # Convert list of strategies to dict format
    strategy_dict = {f"Player_{i+1}": s for i, s in enumerate(strategies)}
    games = simulate_n_games(strategy_dict, n=game_count, seed=seed)
    summary = summarise(games)
    
    # Print results
    print(f"\nResults:")
    print(f"  Average game length:     {summary.game_length.mean:.1f} hours")
    print(f"  Median game length:      {summary.game_length.median} hours")
    print(f"  Std deviation:           {summary.game_length.stdev:.1f}")
    print(f"  Min/Max:                 {summary.game_length.min}-{summary.game_length.max} hours")

    print(f"\nWin rates:")
    for profile_name in sorted(summary.win_counts.keys()):
        wins = summary.win_counts[profile_name]
        pct = (wins / game_count * 100)
        bar = '█' * int(pct / 2.5) + '░' * (40 - int(pct / 2.5))
        print(f"  {profile_name:20} {bar} {pct:5.1f}% ({wins} wins)")
    
    print(f"\nEnd conditions:")
    for condition, count in sorted(summary.end_reasons.items(), key=lambda x: -x[1]):
        pct = (count / game_count * 100)
        print(f"  {condition:20} {pct:5.1f}% ({count} games)")
    
    print(f"\nAverage CP distribution:")
    for profile_name, traveler_stats in summary.per_traveler.items():
        cp = traveler_stats.mean_final_contract_points
        print(f"  {profile_name:20} {cp:.2f} CP")
    
    return summary


def main():
    """Run all showcase scenarios."""
    
    print("\n" + "="*70)
    print("PARADOXO SIMULATION ENGINE: COMPREHENSIVE SHOWCASE")
    print("="*70)
    print("\nThis script exercises the engine across a range of player counts and")
    print("profile combinations, reporting win rates, game length, and end-condition")
    print("distributions for balance analysis.\n")
    
    scenarios_config = [
        # Standard balanced game
        ("Standard 4-Player (Diverse Profiles)", 500, 
         [AggressiveStrategy(), ConservativeStrategy(), SmartStrategy(), CollectorStrategy()], 42),
        
        # All aggressive: high conflict
        ("Extreme: 4 Aggressive (High Conflict)", 300, 
         [AggressiveStrategy(), AggressiveStrategy(), AggressiveStrategy(), AggressiveStrategy()], 101),
        
        # All collectors: cooperative resource race
        ("Extreme: 4 Collectors (Resource Race)", 300, 
         [CollectorStrategy(), CollectorStrategy(), CollectorStrategy(), CollectorStrategy()], 102),
        
        # All smart: balanced play
        ("Extreme: 4 Smart Players (Strategic)", 300, 
         [SmartStrategy(), SmartStrategy(), SmartStrategy(), SmartStrategy()], 103),
        
        # 2-player duel (high stakes)
        ("Duel: 2 Players (High Stakes)", 300, 
         [AggressiveStrategy(), ConservativeStrategy()], 201),
        
        # 6-player chaos
        ("Chaos: 6 Players (Multiplayer)", 200, 
         [AggressiveStrategy(), ConservativeStrategy(), SmartStrategy(), CollectorStrategy(), AggressiveStrategy(), SmartStrategy()], 301),
        
        # Trio: 3 player classic
        ("Classic: 3 Players", 300, 
         [AggressiveStrategy(), ConservativeStrategy(), SmartStrategy()], 302),
        
        # Solo profile dominance test
        ("Analysis: 1 Aggressive vs 3 Conservative", 200, 
         [AggressiveStrategy(), ConservativeStrategy(), ConservativeStrategy(), ConservativeStrategy()], 401),
        
        # Mixed table: different aggression levels at the same board
        ("Mixed table: Aggro, Smart, 2x Collector", 250,
         [AggressiveStrategy(), SmartStrategy(), CollectorStrategy(), CollectorStrategy()], 501),
    ]
    
    results = {}
    for scenario_name, game_count, strategies, seed in scenarios_config:
        summary = scenario(scenario_name, game_count, strategies, seed)
        results[scenario_name] = summary
    
    # Summary table
    print(f"\n{'='*70}")
    print("SUMMARY ACROSS ALL SCENARIOS")
    print(f"{'='*70}\n")
    
    print(f"{'Scenario':<40} {'Avg Len':<10} {'Median':<8}")
    print("-" * 70)
    for scenario_name, summary in results.items():
        avg = summary.game_length.mean
        med = summary.game_length.median
        print(f"{scenario_name:<40} {avg:>7.1f}h    {med:>5}")
    
    print(f"\n{'='*70}")
    print("All scenarios complete.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()

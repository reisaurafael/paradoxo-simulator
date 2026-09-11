# Changelog

All changes to the Paradoxo engine, simulation layer, and documentation are recorded here.

Format: `[YYYY-MM-DD] TYPE: short description` followed by detail.

Types: `FIX` · `FEAT` · `REFACTOR` · `TEST` · `DOCS` · `RULES`

---

## Legacy (2025): MS529 course project and early prototyping

Before this repository existed, Paradoxo was developed as a physical card game and
simulated in a Julia prototype built during the MS529 Systems Simulation course. The
prototype captured the broad strokes: the allocation matrix, the Year Zero race, and a
simplified Paradox function, but cut many corners to fit the course schedule.

**What the Julia prototype did:**
- Modelled 2-4 travelers on a discrete timeline board.
- Implemented the allocation matrix and dice roll generation.
- Ran basic Year Zero races; Year Zero was the only win condition.
- Hardcoded starting energy (12 flat) and a simplified single-phase resolution loop.

**Known divergences from the printed rules (catalogued at migration):**

| Issue | Legacy | Correct (§) |
|---|---|---|
| Explosion energy cost | −1 energy | −2 (§15.2) |
| Paradox module columns | future/boom/past | future/present/past (§29.2) |
| Resolution order | per-player loop | per-module across all travelers (§22.1) |
| Gold / market | not simulated | Recharge function + Merchant (§14, §17) |
| Win conditions | Year Zero only | four conditions (§11.1) |
| Setup energy | hardcoded 12 | 4 × n\_travelers (§10.2) |

The Julia prototype is archived separately and is not part of this repository; the current
engine was rebuilt from scratch and shares no code with it.

The decision to rebuild from scratch in Python, treating the printed Rules Reference as the
sole authority and writing a failing test for every deviation before fixing it, was made at
the start of 2026.

---

## [2026-01-12] FEAT: Initial Python engine

Migrated from MS529 Julia prototype. Established the simulation platform from scratch.

- `engine/constants.py`: all numeric constants sourced from Rules Reference.
- `engine/state.py`: `TravelerState`, `GameState`, `Allocation` dataclasses.
- `engine/dice.py`: `roll_generators()`.
- `engine/matrix.py`: allocation validator, overload detection, escape valve.
- `engine/resolve.py`: modules 1-9, Priority ordering, termination check (partial).
- `engine/paradox.py`: distance-ordered damage pool (§18).
- `engine/timeline.py`: board, eras, periods, Year Zero.
- `engine/market.py`: Merchant movement and market actions (§17-19).
- `simulation/runner.py`: `simulate_game()`, `simulate_n_games()`.
- `simulation/strategies/`: `base.py`, `aggressive.py`, `conservative.py`,
  `smart.py`, `collector.py`.

Legacy divergences corrected at migration (from the Julia prototype, archived separately):

| Issue | Legacy | Correct (§) |
|---|---|---|
| Explosion energy cost | −1 | −2 (§15.2) |
| Paradox module columns | future/boom/past | future/present/past (§29.2) |
| Resolution order | per-player | per-module across all travelers (§22.1) |
| Gold/market | not simulated | Recharge function + Merchant (§14, §17) |
| Win conditions | Year Zero only | four conditions (§11.1) |
| Setup energy | hardcoded 12 | 4 × n_travelers (§10.2) |

---

## [2026-02-08] FEAT: All 52 cards implemented

Completed full card implementation across all four decks (Weapons, Travel, Support, Market)
plus the two Special cards (Divine Comedy, Trinity, set aside for now and flagged as
open point D4).

- Added `engine/combat.py`: unified energy-loss pipeline (`lose_energy`, `deal_energy`,
  `recycle_card`) with modifiers for Pólvora, Escudo Viking, Armadura, Espada de Laser,
  Cálice do Príncipe Drácula, Porcelana, Caldeirão da Agnes.
- Added `engine/rewards.py`: Chaos/Time/Resource reward table (§23); solo phase
  (Time I), vouchers (Time II/III), matrix buff (Resource III).
- Added Phase 4 Item Activation to `simulation/runner.py`.
- Added `recycle_value` to all 52 cards (needed for §28.2 respawn energy calculation,
  even though respawn itself is not yet implemented, see BUG-001).
- Added `passive_source_cards()` meta mechanism: Computador Quântico (copies receptor
  passives) and Prensa Móvel (copies revealed market passives).
- 173 tests passing.

Known gaps at this milestone (still open):
- Termination respawn (BUG-001 above).
- Wanted status on termination (BUG-002 above).
- Millennium CP timing (BUG-003 above).
- Agreements (§4): `Bluetooth of Harald` is a passive no-op; agreements unsimulated.
- Recycling pile has two separate representations (deck discard vs. `combat.recycle_card`);
  these need to be unified in a future cleanup.

---

## [2026-03-14] FIX: BUG-003, BUG-005, BUG-006 + Collector strategy rework

### FIX BUG-003: Millennium CP now fires at end of Hour, not mid-travel (§8.1c)
- `engine/resolve.py`: removed the milestone award from inside `execute_travel()`.
  `_award_millennium_crossings()` now reads the traveler's **final** position and is
  idempotent; `check_millennium_milestones(traveler, game)` calls it after all travel
  resolves in `resolve_hour()`.
- `simulation/runner.py`: solo phases (Time I reward) now also call
  `check_millennium_milestones()` after their travel resolves, so a milestone reached
  in a solo phase is still scored against the final position of that phase.
- Effect: a traveler who passes through or briefly lands on X/XX but ends the Hour
  elsewhere no longer earns the milestone CP.

### FIX BUG-005: Merchant upgrade requires ending exactly on X/XX (§17.11)
- `engine/market.py:check_merchant_upgrades()`: changed `<=` to `==` for both the
  X and XX upgrade checks. A traveler who skips the century in one large move no
  longer triggers the speed upgrade.

### FIX BUG-006: Merchant target tiebreaker follows Priority (§17.7, §19.1)
- `engine/market.py:merchant_target()`: tiebreak key changed from `(gold, -index)`
  to `(gold, century, energy, -index)` so gold ties break by Priority (closest to
  future, then energy), with list order only as the final duel approximation.

### REFACTOR: Collector strategy priorities rewritten
- `simulation/strategies/collector.py` reworked to the intended decision order:
  1. Farm gold (fill Recharge) and 2. attract the Merchant by being richest (it
  comes to us, §17.7): the Collector no longer chases the Merchant.
  3. Buy deliverable items close to (and not past) its position.
  4. Renew at most **once** per Market phase, and only when no deliverable card for
  a missing period is within `RENEW_RANGE` (10) centuries.
  5/6. Deliver and repeat until all three periods are covered.
  - **Override:** holding any reachable (not-yet-passed) delivery item makes
    delivering it the top priority: the Collector travels to land on it before
    returning to the gold-farming/market logic.
- New helpers: `_has_reachable_delivery()`, `_farm_gold_alloc` (alias of
  `_park_alloc`, gold-farming stance), `_travel_toward_delivery_alloc()`.
- Note: "within 10 centuries" and reachability are interpreted toward the past
  (delivery_century ≤ current century), matching the Collector's downward travel
  toward Year Zero. Revisit if forward-delivery scenarios become relevant.

---

## [2026-04-06] FIX: BUG-001, BUG-002, BUG-004 (termination, Wanted, Secret Market)

This completes the first audit: the three remaining open bugs are fixed at the
root, each with a regression test in `tests/test_termination.py`.

### FIX BUG-001: Terminated travelers respawn (§28.2-28.3)
- `engine/resolve.py:check_termination()` now models termination as the transient
  state the rules describe rather than a permanent drop-out. On reaching 0 energy a
  traveler's equipped objects are recycled (through `combat.recycle_card`, so the
  Caldeirão da Agnes steal trigger still fires), and the respawn energy
  `12 + Σ recycle value` is banked on the traveler.
- `engine/resolve.py:advance_overload()` applies the respawn at the start of the next
  Hour: the traveler returns to XXX with the banked energy, keeps their gold and booms,
  and acts normally again. Their machine is reset, so any pending overload and stale
  damage attribution are cleared.
- `engine/state.py`: added `respawn_pending` / `respawn_energy` fields to carry the
  pending respawn between Hours.
- `engine/resolve.py:resolve_hour()`: added a termination sweep after the travel
  modules, since past travel costs energy and can itself reach 0.
- Effect: a traveler terminated mid-game now comes back instead of leaving permanently,
  so games progress to a real contract ending (Year Zero / full receptor) instead of
  collapsing to attrition. The "only one traveler not terminated" ending (§11.1c) still
  fires when a single traveler is left standing at the end of an Hour, before respawns.

### FIX BUG-002: Termination makes the responsible traveler Wanted (§33.1, §28.4-28.5)
- `engine/resolve.py:check_termination()`: the traveler who dealt the killing blow now
  becomes Wanted in addition to earning +1 CP, and that CP queues a Reward (§8.2).
- The terminated traveler loses any Wanted status (§28.5, a terminated traveler cannot
  hold it).
- Re-terminating a traveler who has already been terminated once yields no reward and
  no Wanted (§28.4); this is gated on `survival_eligible`, which is only ever true
  before a traveler's first termination.

### FIX BUG-004: Wanted travelers cannot Buy at the Secret Market (§24.5, §33.3)
- `engine/market.py:resolve_market_phase()`: the Secret Market access loop now skips
  Wanted travelers. The Secret Market only supports Buy, so a Wanted traveler simply
  cannot transact there until they Declare.

### Verified: end-game scoring for the "last traveler standing" ending (§11.1c, §32.2-32.3)
- Confirmed and locked in the §11.1c ending now that termination is transient: the
  game ends when exactly one traveler is not Terminated at the end of an Hour (before
  any respawn). The lone survivor is, by §11.1c, the traveler who terminated the last
  other traveler, so they receive the +1 stabilisation bonus for ending the game on top
  of the termination contract's own +1 (awarded at the kill).
- `simulation/runner.py:_check_win_conditions()`: clarified the last-traveler branch and
  switched the literal `+1` to the named `CP_STABILISATION_BONUS`; same for the full
  receptor ending.
- The +1 survival bonus (§32.2) is awarded to every traveler that is alive and never
  terminated at game end across all natural endings; travelers terminated at any point
  score nothing for survival. A terminated traveler still keeps their CP and can win the
  game on CP (§28.3).
- Guarded by: `tests/test_termination.py` (game ends on last traveler, terminator's
  termination+stabilisation+survival CP, survival CP only for non-terminated, terminated
  traveler can still win on CP).

### FIX: `build_deck()` alias returns the 40-card Merchant deck
- `engine/cards.py:build_deck()` previously returned all 52 cards, contradicting its
  test and its "alias" naming. It now returns the historical 40-card Merchant split
  (`build_merchant_deck()`), matching §25.2 and `tests/test_cards.py`.

---

## [2026-04-20] FIX: BUG-007-015 (combat pipeline, rewards, market access, card effects)

### FIX BUG-007: Wanted bounty paid on kill (§33.2)
- `engine/resolve.py:check_termination()`: when the victim was Wanted at the time of
  the killing blow, the causer now receives `WANTED_BOUNTY` (4 gold). The constant
  existed in `engine/constants.py` but was never imported or used.
- Guarded by: `tests/test_bug_fixes_batch2.py` (bounty paid, no bounty when not Wanted,
  no bounty on re-termination).

### FIX BUG-008: Chaos I damage through full combat pipeline (§23.3, §18)
- `engine/rewards.py:_chaos()`: replaced direct `target.energy -= 3` plus
  `combat.register_loss` with `combat.deal_energy(game, traveler, targets, 3, kind="paradox")`,
  so Escudo Viking, Armadura, Espada de Laser, Pólvora, and Cálice all apply to Chaos I.
- Guarded by: `tests/test_bug_fixes_batch2.py` (Escudo reduces damage, respawning
  travelers are unaffected).

### FIX BUG-009 + BUG-014: Voucher and atemporal cards grant Secret Market access (§23.3, §24.4)
- `engine/market.py`: extracted `_has_atemporal_access()` and added
  `_traveler_can_access_secret_market()`. The Secret Market loop now uses the new helper
  instead of a raw century check, admitting holders of Janela do Tempo, Primeiro Smartphone,
  and market vouchers.
- Voucher consumption is now tracked per-phase via a local `voucher_consumed` set:
  one voucher is consumed at most once per phase regardless of how many markets the
  traveler visits.
- Guarded by: `tests/test_bug_fixes_batch2.py` (voucher access, voucher consumed once,
  Janela grants access, Primeiro Smartphone grants access).

### FIX BUG-010: Free-recycle fires Caldeirão da Agnes trigger (§21.3)
- `simulation/runner.py:_resolve_free_recycles()`: replaced the inline energy-grant and
  `deck._discard.append` with `combat.recycle_card(game, traveler, card, grant_energy=True)`,
  so the Caldeirão da Agnes steal trigger fires on free recycles.
- Guarded by: `tests/test_bug_fixes_batch2.py` (Caldeirão steal fires, recycler energy granted).

### FIX BUG-011: Simulador da Realidade does not block atemporal travelers (§42)
- `engine/market.py:resolve_market_phase()`: the Simulador buy-block guard now checks
  `not _has_atemporal_access(traveler) and not traveler.market_voucher` before blocking,
  so Janela and Smartphone holders are correctly treated as synchronic.
- Guarded by: `tests/test_bug_fixes_batch2.py` (Janela holder not blocked,
  genuinely non-synchronic traveler blocked).

### FIX BUG-012: Mona Lisa occupies the exact vacated slot (§6.1)
- `engine/cards.py:mona_lisa()`: replaced `deck.take(chosen); deck._discard.append(this_card)`
  with an in-place index swap (`deck._revealed[idx] = this_card`), so Mona Lisa takes the
  exact slot the chosen card vacated. The revealed count stays at 4; `_refill_revealed` is
  not triggered.
- Guarded by: `tests/test_bug_fixes_batch2.py` (Mona Lisa at correct index, revealed
  count unchanged, chosen card in hand).

### VERIFIED BUG-013: §10.3 linear progression already enforced
- `engine/matrix.py:can_place()` (line 78) and `validate_allocation()` (line 174) already
  reject allocations with a gap. No code change was needed. Regression tests added.

### FIX BUG-015: Merchant steals set Wanted (§26.4, §3.4)
- `engine/cards.py:carretel_de_pesca()` and `heliografo_de_niepce()`: added
  `traveler.is_wanted = True` after a successful steal from the Merchant's revealed stock,
  as ruled in D2, now resolved.
- Guarded by: `tests/test_bug_fixes_batch2.py` (Carretel deterministic steal, Heliógrafo steal).

---

## [2026-05-18] DOCS: Pre-publication polish, reproducible benchmark, sample results

### DOCS: README grounded in current capability
- Rewrote `README.md` to lead with what the platform does today rather than long-term
  vision: the verified engine first, the long-term framework direction compressed to a
  closing paragraph.
- Added a **Sample results** section with a real 1000-game benchmark (fixed seed) and two
  committed figures (`docs/img/win_conditions.png`, `docs/img/game_length.png`). The text
  reads the aggressive profile's ~46% win share honestly as a measurement of the engine
  under naive baseline agents, not of optimal play.

### FEAT: Reproducible entry points under `examples/`
- `examples/run_benchmark.py`: fixed-seed batch + statistical summary via the public
  `simulation.metrics.summarise()` API. Regenerates the README numbers exactly.
- `examples/make_plots.py`: regenerates the README figures from the same batch.

### REFACTOR: Repository hygiene
- Removed scratch scripts `run_batch_500.py` and `run_smart_test.py` from the root; their
  function is now served by `examples/`.
- Removed the empty `game/` and `notebooks/` scaffolding directories; dropped `game*` from
  the `pyproject.toml` package list.
- `.gitignore`: kept generated `*.png` ignored but added an exception for the committed
  `docs/img/` README figures.

---

## [2026-06-02] FIX: Aggressive profile only ends the game at Year Zero when it wins

### FIX: End-game discipline for the Aggressive strategy
- The Aggressive profile no longer rushes Year Zero unconditionally. Before stepping onto
  Year Zero (which immediately ends the game, §11.1a), it now projects the final Contract
  Point standings: the Year Zero contract + stabilisation bundle and the end-of-game
  survival point (§32), and only commits if that makes it the outright winner. A traveler
  on Year Zero sits on the lowest board position and loses every CP tiebreak (§32.4), so it
  requires a strict CP lead.
- When reaching Year Zero would not win, the profile stops one space short (century I) by
  capping its travel distance and lays siege instead: it overloads the Paradox function to
  bleed the field, buys and fires weapons to terminate rivals (pursuing the §11.1c
  "last traveler" end), and pays off its Wanted poster (§24.5) at the Merchant market to
  unlock the Secret Market. It still delivers in the low centuries.
- Effect on simulated games: premature Year Zero endings that the Aggressive profile then
  lost dropped from ~45/300 to ~5/400 of games; more games now reach the termination-based
  endings (`last_traveler`, `all_terminated`), which were previously being pre-empted.

### Note: the "Terminated" end-condition was already correct
- Investigation confirmed the permanent Terminated condition and the §11.1c end-condition
  ("only one traveler not Terminated") are implemented correctly: `is_terminated` is set
  permanently on reaching 0 energy and is never cleared on respawn. The rarity of the
  all-terminated ending was a consequence of the Aggressive rush above, not a status bug.

### TEST
- Added `tests/test_aggressive_endgame.py`: the travel cap fires only when Year Zero would
  not win, the CP projection accounts for survival and termination, and the profile pays off
  its Wanted poster at the Merchant market and buys weapons. Full suite: 213 passing.

---

## [2026-06-09] FEAT: Phase 2 strategy upgrades: smarter cards, energy/gold priority, Paradox discipline

### FEAT: Shared utility: Paradox kill-threshold helper
- Added `simulation/strategies/util.paradox_can_terminate(traveler, game, dice)` and
  `paradox_kill_direction`: compute in advance whether Paradox can drop any rival to 0
  energy with the best available die, and which column (future/present/past) covers the
  most killable targets. Used by all non-Aggressive profiles to decide when Paradox
  deserves extra investment.

### FEAT: Conservative strategy upgraded
- **Allocation**: Travel fills first in normal play (to secure 2 movement columns before
  the dominant die value is consumed). Recharge fills the remainder. Paradox gets 1 column
  for mild pressure unless a kill shot is available, in which case the order flips to
  Recharge → Paradox(2) → Travel.
- **Card preferences**: expanded from a narrow "safety" list to three tiers, survival
  cards (Santo Graal, Escudo Viking, Toalha, etc.), market-access helpers (Porcelana,
  Dente Azul, Primeiro Smartphone), and travel/speed cards. Delivery cards for missing
  Temporal Receptor periods added as the top buy priority.
- **Market**: DeclareAction clears the Wanted poster at the Merchant (enables Secret Market
  next phase); Renew cycles the market when nothing useful is visible.

### FEAT: Collector strategy upgraded
- **Market**: DeclareAction for Wanted; market-access helpers and energy cards (at critical
  energy only, so survival buying does not displace deliveries); delivery-card scoring
  unchanged. Renew at cost 1.
- **Allocation**: Paradox column count tied to kill-threat flag (2 if can terminate, 1
  otherwise); Travel fills before Paradox in transit mode to guarantee 2 movement columns.

### FEAT: Smart strategy upgraded
- **Allocation**: `_balanced` mode now places Travel first (same dice-sequencing rationale
  as Conservative), Recharge second, Paradox(1) last. In kill-threat mode the order
  becomes Recharge → Paradox(2) → Travel: explicitly sacrificing one turn's speed for a
  termination. `_recharge_first` and `_avoid_heating` updated to the same discipline.
- **Card preferences**: extended to include Cálice do Príncipe Drácula (+2 energy per
  paradox hit), Armadura da Joana d'Arc (−1 per energy loss), Computador Quântico
  (inherits receptor passives), Porcelana, Dente Azul, and Primeiro Smartphone.
- **Market**: DeclareAction for Wanted; cheap market helpers (≤ 2g) bought opportunistically
  before delivery cards; Renew up to cost 2.

### Benchmark (500 games, seed 42)
- Win distribution: Aggro 45%, Cons 29%, Smart 22%, Coll 5%.
- Average game length: 16.9 Hours (up from 13.3 Hours pre-Phase-2).
- Temporal Receptor completions (`full_receptor`): 5% (up from ~2%), confirming that
  delivery-focused play now actually works end-to-end.
- Aggro reached Year Zero while losing: ~9/500 games (1.8%), down from 45/300 (15%)
  before the Phase 1 endgame discipline fix.

---

## [2026-06-16] FIX + FEAT: Simulation tooling improvements

### FIX: Win-rate percentage in runner `__main__`
- Previous output printed the raw win count where a percentage was expected (e.g. "45 wins (45%)"
  for a 1000-game run was accidentally correct but would be wrong for any other count).
  Fixed to `count/n * 100%`.

### FIX: Sample delivery game removed from default execution path
- The verbose per-game item report that ran automatically in `runner.__main__` has been removed.
  The default `__main__` now runs 200 games and prints a compact summary via `metrics.print_summary`.

### FEAT: `simulation/sim_menu.py`: interactive simulation runner
- New entry point: `python -m simulation.sim_menu`
- Interactive text menu for configuring: number of players (2-6), profile per player, game count,
  random seed, which reports to generate, and chart output directory.
- Professional text report with ASCII bar charts, structured tables, and per-player statistics.
- Generates up to 8 matplotlib PNG charts saved to a configurable output directory:
  - Win rates by profile
  - Victory condition distribution
  - Game-length histogram
  - Resource trajectories (energy, century, CP over time)
  - Century occupancy heatmap (overdrive threshold marked)
  - Card activity (buys, deliveries, missed deliveries)
  - Combat stats (termination and explosion rates)
  - Past-travel distance distribution
- Offers an optional verbose single-game trace at end of session.

---

## [2026-06-23] RULES: Overdrive: past travel costs 2 energy/century after century X (experimental)

Playtesting rule, not yet in the printed reference. It may be revised or removed.

**Rule:** Once a traveler is at or below century X (position ≤ 10) and travels further into the
past, each century traveled costs 2 energy instead of the normal 1.

**Engine implementation (`engine/resolve.py`, `engine/constants.py`):**
- Added `OVERDRIVE_THRESHOLD_CENTURY = 10` and `OVERDRIVE_ENERGY_COST_PER_CENTURY = 2`
  to `constants.py`.
- `execute_travel` now splits past movement into a normal segment (position > X, cost 1/century)
  and an overdrive segment (position ≤ X, cost 2/century). Card cost-reduction hooks (Telescópio,
  Máquina Voadora, Armadura da Joana d'Arc) still apply to the combined total.

**Strategy updates (all four profiles):**
- Added `safe_travel_cap(traveler, reserve)` to `simulation/strategies/util.py`: computes the
  maximum past-travel steps affordable before energy drops to the reserve threshold, accounting
  for the overdrive cost schedule.
- All profiles now return energy-safe travel caps from `choose_allocation` so that no strategy
  plans more past travel than it can afford. Aggressive uses reserve=0 in rush mode and reserve=3
  in siege mode; other profiles use reserve=2.

**Tests:** `tests/test_overdrive.py`, 14 regression cases covering cost formula, boundary
conditions, `safe_travel_cap` behavior, and per-profile cap assertions.

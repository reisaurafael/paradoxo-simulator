# Paradoxo: Development Log

Decisions, discoveries, and design rationale in reverse chronological order.
For a raw list of every code change, see `CHANGELOG.md` at the repo root.

---

## 2026-06-23: Overdrive mechanic + simulation tooling overhaul

### Context

A playtesting experiment I wanted to try: penalize deep-past travel by doubling the energy
cost per century once a traveler enters the "overdrive zone" (century X and below). The goals were
(1) to make the early centuries feel more dangerous and resource-intensive, (2) to give Paradox-
heavy strategies a natural pressure window in the low centuries, and (3) to differentiate the risk
profile of aggressive time-sniping from safe future-farming. This is an authorized temporary rule,
not in the printed reference, and it may be revised or removed after playtesting.

### Engine change: cost-split formula

The clean way to implement this was to split each past-travel segment into two parts inside
`execute_travel`: a *normal* segment (position > X, cost 1/century) and an *overdrive* segment
(position ≤ X, cost 2/century). The key boundary question was: is landing *on* century X normal
or overdrive? The rule I wrote says "after X", meaning you pay the higher cost when you leave X
toward the past. Stepping 11→10 is therefore normal (cost 1), and stepping 10→9 is overdrive
(cost 2). The formula implements this with:

```
normal_cents   = max(0, min(prev, MAX) - max(new, THRESHOLD))
overdrive_cents = max(0, min(prev, THRESHOLD) - new)
total_cost = normal_cents × 1 + overdrive_cents × 2
```

Card hooks (Telescópio, Máquina Voadora, Armadura da Joana d'Arc) still apply to the combined
total, so cards that reduce travel cost remain effective even in the overdrive zone.

### Strategy safety: safe_travel_cap

All four strategy profiles compute a travel cap by calling `safe_travel_cap(traveler, reserve)`.
This helper iterates step-by-step from the traveler's current position toward Year Zero, applying
the correct cost (1 or 2) at each step, and returns the last step index where available energy
remains ≥ reserve. The step-by-step approach was chosen over a closed-form formula because the
segment boundary creates two cost tiers and the formula would need careful clamping; the iterative
version is trivially correct and runs in O(position) which is at most 30 iterations.

Reserve levels by profile: Aggressive rush-mode=0 (risk everything), Aggressive siege-mode=3
(keep a buffer for Paradox), Conservative/Smart/Collector=2.

### Simulation tooling

The runner's `__main__` had accumulated a verbose per-game trace that scrolled past terminal
limits and had a count/percentage bug. This was stripped back to a 200-game smoke test.

A new module `simulation/sim_menu.py` provides the full interactive experience: text config menu,
structured per-player stats, and 8 matplotlib charts (win rates, victory conditions, game length,
resource trajectories, century heatmap, card activity, combat stats, travel distance). The century
heatmap and travel distance histogram both mark the overdrive threshold so the charts immediately
reveal whether the new mechanic is changing where travelers spend their time.

---

## 2026-06-09: Phase 2 strategy upgrades: energy/gold priority, Paradox discipline, broader card preferences

### Context

After Phase 1 fixed the Aggressive profile's blind Year Zero rush, the baseline agents still had two structural weaknesses: (1) their allocation logic did not distinguish between Paradox-as-pressure and Paradox-as-kill-shot, leading to either too much or too little paradox investment, and (2) their card-buying lists were narrow and did not cover important market-efficiency cards like Porcelana or atemporal-access cards. The request for Phase 2 was to make all non-Aggressive profiles prioritize Energy/Gold over Paradox, but only in the normal case; escalate Paradox investment when it can actually terminate a rival.

### The dice-sequencing constraint

The key engineering insight: with 4 dice and the linear-progression rule (all generators in a function must share a value), putting Recharge first consumes the dominant die value, leaving mismatched singles for Travel. Travel then only fills column 0 (the heating slot, which adds booms but gives no movement). The fix is to let Travel fill first in normal play so the dominant die value goes into movement; Recharge fills remaining dice. Paradox gets at most 1 column as a mild-pressure leftover. In kill-threat mode the order inverts, Recharge first, then Paradox(2 columns), then Travel with whatever is left, explicitly trading movement for the termination.

### Shared utility: paradox kill threshold

A single helper, `util.paradox_can_terminate(traveler, game, dice)`, encodes the kill condition: any active rival with energy ≤ max(dice) can be terminated if the right Paradox column fires. All three non-Aggressive profiles consult this before deciding how many Paradox columns to fill. A companion function, `paradox_kill_direction`, identifies which direction (future/present/past) covers the most killable targets, available for future use in more targeted allocation strategies.

### Strategy-by-strategy changes

**Conservative** now buys delivery cards for missing Temporal Receptor periods as its top market priority, followed by cheap survival cards (Toalha, Escudo Viking, etc.), market-access helpers (Porcelana, Dente Azul, Primeiro Smartphone), and travel cards. It also pays off its Wanted poster (DeclareAction at the Merchant) to restore Secret Market access. The allocation order is Travel → Recharge → Paradox(1) in normal mode, and Recharge → Paradox(2) → Travel in kill mode.

**Collector** keeps its delivery-first logic unchanged but adds a DeclareAction for the Wanted poster and cheap market helpers at the top of the buying queue. Energy cards are only bought at the critical threshold (energy < 3) so survival purchases do not crowd out deliveries. The Paradox column count is tied to the kill flag.

**Smart** received the most card-list expansion: Cálice do Príncipe Drácula (+2 energy per paradox hit, synergises with the kill-mode investment), Armadura da Joana d'Arc (reduces every energy loss), Computador Quântico (copies receptor passives after delivery), plus the market-access helpers. Its allocation modes now all follow the Travel-first / kill-flip discipline.

### Benchmark outcome

Average game length rose from 13.3 to 16.9 Hours. The `full_receptor` end condition rose from ~2% to 5% of games, confirming that delivery-focused play now reaches completion rather than being pre-empted. Win distribution (500 games, seed 42): Aggro 45%, Cons 29%, Smart 22%, Coll 5%, all profiles competitive, no single profile trivially dominant.

---

## 2026-06-02: The Aggressive profile learns when *not* to end the game

### Context

A batch run surfaced a balance problem hiding behind a suspected rules bug: games almost
never ended by the "all but one traveler Terminated" condition, and one profile, Aggressive
- seemed to be ending games it went on to lose.

### Diagnosis: the engine was right, the strategy was reckless

The Terminated condition and its end-of-game consequences are implemented correctly. A
traveler that reaches 0 energy is marked Terminated permanently; respawning at the start of
the next Hour restores energy and lets them keep playing, but it does **not** clear the
condition. The game-end check counts how many travelers have *never* been terminated and
ends the moment one or zero remain, covering both the "last traveler standing" and the
"everyone terminated" outcomes. A 400-game batch confirmed both endings do fire.

The real cause was strategic. Reaching Year Zero ends the game immediately, and the
Aggressive profile drove for Year Zero every Hour regardless of the score. Because a
traveler standing on Year Zero occupies the lowest position on the board, they lose every
Contract Point tiebreak, so rushing there while behind handed the win to someone else and
cut everyone's game short. In a 300-game sample the profile reached Year Zero in 173 games
and *lost* 45 of them.

### Decision

Give the Aggressive profile a sense of arithmetic. Before it commits to the final step onto
Year Zero it now projects the end-of-game standings: the Year Zero contract and
stabilisation points it would score, plus the survival point each living traveler keeps. It
only takes the step if the projection makes it the outright winner (a strict lead, since it
will lose any tie on position).

When the math says it cannot win that way, it caps its own travel so it halts one space
short of Year Zero and switches to a siege posture, overloading the Paradox function to
damage the field, buying and firing weapons to terminate rivals (chasing the "last traveler"
win instead), and paying off its Wanted poster at the Merchant market so it can shop the
Secret Market. The travel cap was already supported end-to-end by the resolver; the strategy
simply had to start using it.

### Result

Premature, self-defeating Year Zero endings fell from roughly one game in seven to about one
in eighty. More games now run to a natural termination-based conclusion, which is both more
faithful to how the game actually plays out and fairer to the other profiles, whose games
were previously being guillotined. Regression tests pin the new decision logic in
`tests/test_aggressive_endgame.py`.

---

## 2026-04-20: Second audit batch (BUG-007-015)

### Context

After the initial six bugs were fixed and a comprehensive second audit produced nine new
findings (BUG-007 through BUG-015), this session fixed all nine. The changes span the
combat pipeline, the reward system, the market phase, and three individual card effects.

### BUG-007: Wanted bounty

`engine/constants.py` had `WANTED_BOUNTY = 4` but it was never referenced. The rule
(§33.2) is that terminating a Wanted traveler earns the killer 4 gold, in addition to the
normal termination contract (+1 CP). The fix is a two-line addition to
`resolve.py:check_termination`: after the first-termination guard that already gates CP
and Wanted-marking, if the victim was Wanted at the moment of the killing blow, the causer
receives 4 gold. The constant is now imported into `resolve.py` alongside the other
contract constants.

### BUG-008: Chaos I bypassed combat modifiers

The Chaos I reward (§23.3) deals a "Paradox" across the entire timeline, 3 damage to
every other traveler. The rule text and §18 (paradox resolution) require that damage go
through the full combat pipeline: Escudo Viking's first-hit shield, Armadura da Joana d'Arc's
standing reduction, Espada de Laser's reflection, Pólvora's amplifier, and Cálice do
Príncipe Drácula's lifesteal. The old code in `rewards._chaos` decremented `target.energy`
directly, then called `combat.register_loss` (which only fires Porcelana). Replacing those
lines with `combat.deal_energy(game, traveler, targets, 3, kind="paradox")` routes Chaos I
through the same pipeline as every other paradox, with no code duplication.

### BUG-009 + BUG-014: Market voucher and atemporal cards for the Secret Market

The Secret Market loop in `market.resolve_market_phase` had a hard `continue` if the
traveler was not physically on century XI. Two things bypass that requirement under the
rules: the Time II market voucher (§23.3, "buy at any market even non-synchronic, Secret
Market only if open") and the atemporal market cards Janela do Tempo and Primeiro
Smartphone (§16.2 / §24.4: "access to the Market regardless of century", which §24.4
extends to include the open Secret Market).

The fix adds a `_traveler_can_access_secret_market` helper that mirrors the existing
`_traveler_can_access_market` helper used by the Merchant loop. The raw century check in
the Secret Market loop is replaced by a call to this helper. Both loops now share the same
access logic (century or atemporal card or voucher). The voucher-consumption logic is also
unified: a local `voucher_consumed` set ensures that one voucher is consumed at most once
per phase even if the traveler accesses both markets in the same phase (which the rule
clearly intends: one voucher grants access to all markets for that Hour).

### BUG-010: Free-recycle bypassed Caldeirão da Agnes trigger

`runner._resolve_free_recycles` was directly removing the card from the traveler's hand,
adding the recycle value to energy, and appending the card to `deck._discard`. This
bypassed `combat.recycle_card`, which is the single function responsible for firing the
Caldeirão da Agnes steal trigger (§21.3: "some effects watch the moment a card enters
the recycling pile"). The free-recycle body is now replaced by a single call to
`combat.recycle_card(game, traveler, card, grant_energy=True)`, which already handles
energy grant, Caldeirão, and deck discard in the correct order.

### BUG-011: Simulador da Realidade blocked atemporal travelers

The Simulador (§card) blocks non-synchronic travelers from buying at the Merchant.
"Non-synchronic" should exclude travelers who hold Janela do Tempo or Primeiro Smartphone,
since those cards make the traveler synchronic for market purposes (§16.2). The guard at
line 402 was checking `traveler.century != market_pos` alone, which is too broad. It now
also checks `not _has_atemporal_access(traveler) and not traveler.market_voucher` before
applying the Simulador block.

### BUG-012: Mona Lisa slot replacement

Mona Lisa returning to the Merchant deck is intentional:
the card goes back into the revealed stock, not the recycling pile. The real bug was
slot-replacement order: the old code called `deck.take(chosen)`, which removes `chosen`
from `deck._revealed` and immediately calls `_refill_revealed()`, then appended Mona Lisa
to `deck._discard`, so Mona Lisa entered the draw-replenishment tail rather than the freed
slot. The fix does an in-place index swap: `deck._revealed[idx] = this_card` replaces the
chosen card at its exact position without ever calling `take` or `_refill_revealed`. The
revealed count stays at 4 and Mona Lisa is immediately visible.

### BUG-013: Linear progression already enforced

Audit of `engine/matrix.py:validate_allocation` confirmed §10.3 (linear progression
within a function) is already enforced at line 78-83 (`can_place`) and lines 174-181
(`validate_allocation`). No code change was needed; regression tests were added to guard
the behaviour.

### BUG-015: Carretel de Pesca and Heliógrafo de Niépce steal without Wanted

Ruling (open point D2, now closed): stealing a Merchant card via
Carretel de Pesca or Heliógrafo de Niépce sets the acting traveler Wanted (§26.4 / §3.4).
Neither card effect called `traveler.is_wanted = True`. One line was added after the
`deck.take` call in each effect function.

---

## 2026-04-06: Termination, Wanted, and the Secret Market (BUG-001/002/004)

### Context
The 2026-06-21 audit left three bugs open after BUG-003/005/006 were fixed. All three
turned out to centre on what happens when a traveler runs out of energy, so they were
worth fixing together.

### BUG-001: terminated travelers respawn
The hardest decision here was *what termination actually is*. The reference (§28) does
not remove a terminated traveler from the game: their equipment is recycled, they return
to XXX with `12 + Σ recycle value` energy, keep their gold and booms, sit out the rest of
the current Hour, and then play normally, they can still acquire objects, deliver, and
even win. So "terminated" is a transient state, not a death.

We modelled it that way. `check_termination()` performs the immediate consequences
(recycle equipment, bank respawn energy, drop Wanted, attribute the kill) and leaves the
traveler flagged as terminated for the remainder of the Hour, during which they are not a
valid paradox target and take no actions, matching §28.1. The actual return to play
happens at the start of the next Hour in `advance_overload()`, which is already the
engine's start-of-Hour housekeeping hook. Respawning there (rather than mid-resolution)
keeps the §11.1c "only one traveler not terminated" ending intact: it is still evaluated
at end of Hour, before anyone respawns, so a genuine last-traveler-standing still ends
the game.

Recycling on termination goes through `combat.recycle_card`, so the Caldeirão da Agnes
steal trigger fires on the recycled equipment exactly as it would for any other recycle.

### BUG-002: Wanted on a kill
Terminating another traveler now sets the causer's Wanted flag alongside the +1 CP, and
the CP queues a Reward (§8.2) like every other contract point. Two edge rules from §28
fall out of this: the terminated traveler loses Wanted (§28.5), and re-terminating a
traveler who has already been terminated once grants no reward (§28.4). The latter is
gated on `survival_eligible`, which is only ever true before a traveler's first death,
so it doubles as a clean "has this traveler been terminated before?" signal.

### BUG-004: Wanted at the Secret Market
A one-line guard: the Secret Market access loop skips Wanted travelers (§24.5, §33.3).
Since the Secret Market only offers Buy, there is nothing else for them to do there.

### Result
With respawn in place, a 500-game smoke run ends overwhelmingly via Year Zero rather than
attrition, and no game runs away to the hour cap. Whether the aggressive profile is still
too strong is now a balance question for the strategy layer, not an engine-compliance one.

### End-game scoring under transient termination
Making termination transient raised an obvious question: does the "only one traveler not
Terminated" ending (§11.1c) still work, and is the right traveler rewarded? It does, and
the timing is the reason. Termination flags the traveler immediately but the respawn only
happens at the start of the *next* Hour, while the win condition is checked at the *end*
of the current Hour, so a traveler terminated this Hour is still Terminated when the
check runs, and a genuine last-traveler-standing ends the game before anyone returns.

Who gets the "extra CP for ending the game"? By §11.1c the lone survivor is necessarily
the traveler who terminated the last other traveler (terminated travelers cannot act), so
the §32.3 stabilisation bonus goes to them, on top of the termination contract's own +1
that was already booked at the moment of the kill. The +1 survival bonus (§32.2) is then
paid to every traveler still alive and never terminated; a traveler terminated at any
point scores nothing for survival but keeps their CP and can still win the game outright
on points (§28.3).

### Tests
Added `tests/test_termination.py` (12 cases) covering respawn position/energy, gold/boom
retention, the Wanted/CP consequences, the §28.4 re-termination suppression, the Secret
Market Wanted block, the last-traveler ending, the terminator's
termination+stabilisation+survival CP, survival CP only for non-terminated travelers, and
a terminated traveler still winning on CP. Full suite: 185 passing.

---

## 2026-03-14: Rules audit: eight bugs found

### Context
The first complete simulation run (100 games, 4 travelers) showed complete aggressive
strategy dominance. Before tuning strategies, we audited the engine against the Rules
Reference to find out how much of that result is a rules bug vs. a strategy problem.

### Bugs found

The full list is in `CHANGELOG.md` (Unreleased section). Summary:

| ID | Severity | Rule | Description |
|----|----------|------|-------------|
| BUG-001 | **Critical** | §28.2-28.3 | Terminated travelers never respawn |
| BUG-002 | **Critical** | §33.1, §8.1b | Causer not marked Wanted on termination |
| BUG-003 | **Critical** | §8.1c | Millennium CP fires mid-travel, not end of Hour |
| BUG-004 | Significant | §24.5 | Wanted travelers can buy at Secret Market |
| BUG-005 | Significant | §17.11 | Merchant upgrade uses `<=` instead of exact century |
| BUG-006 | Minor | §17.7 | Merchant target tiebreaker wrong after gold |

### Root cause of aggressive dominance
BUG-001 is almost certainly the primary cause. When a traveler is terminated, they drop
out of the game permanently instead of respawning. This makes any aggressive strategy
that can eliminate travelers win by attrition rather than by the intended score-based
race. The results of any simulation run before these bugs are fixed should be treated as
a stress test of the engine, not as balance data.

### Decision: fix in priority order
Fix BUG-001 → BUG-002 → BUG-003 before running any balance analysis. BUG-004 and
BUG-005 are correct enough for simulation purposes (they slightly inflate late-game pace).
BUG-006 is a tiebreaker edge case and can be fixed opportunistically.

### Open rules questions (unchanged from §D in Rules Reference)
- **D1 (Year Zero CP):** Does reaching Year Zero grant a milestone +1 CP in addition
  to the stabilisation bonus? Current code uses `CP_YEAR_ZERO_TOTAL` constant; we should
  settle the ruling and set the constant accordingly.
- **D2 (Wanted scope):** Does Resource II "steal from Merchant" make the traveler Wanted?
  Current code comments this as an open question. The Wanted flag is not set.
- **D3 (Priority duel re-rolls):** Assumed yes; not confirmed.
- **D4 (Special cards):** Divine Comedy and Trinity exist but are not in the 52-card deck.

---

## 2026-02-08: All 52 cards implemented

### Goal
Make every card function as printed and as described in the Rules Reference, adding
whatever engine structure was required so effects actually fire during play.

### Structural additions
- **`engine/combat.py`**: unified energy-loss pipeline (`lose_energy`, `deal_energy`,
  `recycle_card`) so cross-traveler damage rules are written once.
- **`engine/cards.py`**: `recycle_value` added to all 52 cards; every active/passive
  effect fully implemented; `passive_source_cards()` realises Computador Quântico and
  Prensa Móvel.
- **`engine/rewards.py`**: full Chaos/Time/Resource reward table (§23).
- **`simulation/runner.py`**: Phase 4 Item Activation wired; solo phases (Time I)
  resolved after Phase 4.

### Card text vs. reference reconciliations
- Prensa Móvel copies **passive** abilities of revealed market cards, not active ones.
  Corrected from an earlier comment.
- Geladeira activates an ability of a delivered receptor card. Golden rule 0.1 (card
  text wins for its specific interaction) overrides the receptor's default "never
  activated again" (§2.4). This is intentional; the card text explicitly targets the
  receptor.

### Known gaps at this milestone
- Termination respawn (BUG-001) deferred: flagged in CHANGELOG.
- Wanted on termination (BUG-002) deferred: flagged in CHANGELOG.
- Agreements (§4) are not simulated. `Bluetooth of Harald` passive is a no-op.
- The recycling pile has two representations: deck discard (Óculos can rescue from it)
  and `combat.recycle_card` (in-resolution recycles). These should be unified.

### Tests
Added `tests/test_card_effects.py` (30 cases). Full suite: 173 passing.

---

## 2026-01-12: Repository established, Python migration begins

### Repository identity
Migrated from MS529 course-project structure to a standalone game-analysis platform.

### Source of truth
The official Rules Reference (Corporação C.R.O.N.O.S.) is the only authoritative
source for engine behaviour. All legacy Julia code is reference-only.

### Legacy divergences catalogued

| Issue | Legacy | Correct (§) |
|---|---|---|
| Explosion energy cost | −1 energy | −2 (§15.2) |
| Paradox module columns | future/boom/past | future/present/past (§29.2) |
| Resolution order | per-player loop | per-module across all travelers (§22.1) |
| Gold/market | not simulated | Recharge + Merchant (§14, §17) |
| Win conditions | Year Zero only | four conditions (§11.1) |
| Setup energy | hardcoded 12 | 4 × n_travelers (§10.2) |

### Modules written this session
`constants.py`, `state.py`, `dice.py`, `matrix.py`, `resolve.py`, `paradox.py`,
`timeline.py`, `market.py`, `runner.py`, all four strategy stubs.

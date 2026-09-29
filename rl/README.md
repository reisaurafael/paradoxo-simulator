# A Paradoxo player that learns by reinforcement

The four players in `simulation/strategies/` follow rules I wrote by hand.
This package trains a fifth player that writes its own rules, by playing
hundreds of thousands of matches, at tables of 2 to 6 players, against the
heuristics and against older copies of itself, and keeping what wins.

It takes over three decisions, each with its own neural network (a head):

| head | when | what it chooses |
|---|---|---|
| dice | every Hour | where the four generators go, which way to travel, where to stop |
| market | every market call | buy which card, renew one, pay off Wanted, or pass |
| reward | every Reward Phase | Chaos, Time or Resource |

Card activations and the targets of rewards stay with the heuristic it is
built on (Aggressive).

Contents

1. The decisions and their legal options
2. What the networks see
3. The networks
4. Training: imitation, then reinforcement with self-play
5. The benchmark
6. How every run is recorded
7. Experiment log
8. How to run it
9. What it does not do yet

---

## 1. The decisions and their legal options

`rl/actions.py` writes every legal option out. The player scores them all and
picks one.

**Dice.** Every Hour each traveler rolls four generators (1, 2 or 3) and places
them on the 3 by 3 matrix of the time machine (§10):

| function | module 1 | module 2 | module 3 |
|---|---|---|---|
| Recharge | energy | gold | energy and gold |
| Paradox | future | present | past |
| Travel | heating | travel v | travel 2v |

All four generators are placed, generators in one function show the same
value, modules fill left to right, three generators overload a function for the
next Hour, and while a function is overloaded one generator may go to the
escape valve (§10, §11). Of the 4^4 = 256 ways to throw the dice, the rules
leave a handful. Each placement that travels is offered toward the past and
toward the future, with no cap and with caps that stop exactly on a century
worth stopping on: a delivery century of a card in hand, the Merchant, the
millennium checkpoints X and XX while not yet scored (§8.1c), and the Secret
Market on XI (§19). Real positions have between 4 and 24 moves.

**Market.** At the Merchant: buy any revealed card the traveler can pay for
and carry, renew any revealed card, pay off Wanted (§18.3), or pass. At the
Secret Market only the top card can be bought. A card the heuristic wants to
use from hand is offered too, at most once per market phase.

**Reward.** The categories available this time (§26.2 forbids repeating the
last one).

The heuristic's own choice is always among the options. Tests check that every
listed dice move passes the engine's validator, that a brute force over every
cell assignment finds nothing the lister missed, that the checkpoints and XI
are offered as stops, and that the market options follow the rules.

## 2. What the networks see

`rl/features.py`. Every option is scored next to the position, all numbers
scaled to roughly [-1, 1].

**The position (34 numbers)**: players at the table, energy, gold, century,
contract points, booms, hand size, full equipment, overloaded functions,
Terminated, Wanted, the Hour, the Merchant's offset and stock, deliverable
cards here, the nearest delivery below and above, which of the three periods
are delivered, the best rival score and my margin, rivals' mean energy, the
lowest rival century, the share of rivals below, above and on my century, the
Secret Market (open, cards left), market voucher, and whether XX and X are
already scored.

**A dice move (28 numbers)**: the nine cells, the escape valve, the direction,
how far it travels, where it lands, Year Zero and whether that would win on
points, deliveries and the Merchant on landing, the nearest delivery from the
landing, energy and gold after, booms, whether the motor explodes, which
functions it overloads, and whether it lands on an unscored XX, an unscored X,
or XI. The outcome is an estimate that follows §12 for the traveler's own
modules; it ignores card hooks and the paradoxes rivals throw this same Hour.

**A market option (70 numbers)**: the kind (pass, buy, renew, declare, use),
which of the 52 cards it is (one input per card, so the network learns what
each card is worth), cost and gold after, where the card is delivered and how
far that is, whether its period is still open in my receptor, recycle value,
large item, ability type, Secret Market, and slots left after.

**A reward option (3 numbers)**: which category.

No feature says which option the heuristic would pick (section 7, run 2,
explains why).

## 3. The networks

`rl/network.py`, written in numpy: forward pass, backward pass and the Adam
optimiser, no deep learning framework.

Each head is a multilayer perceptron: position and option in, two hidden
layers of 64 with tanh, one score out. A softmax over the options' scores gives
the policy:

    pi(a | s) = exp(f(s, a)) / sum over b of exp(f(s, b))

A value network (position in, one hidden layer of 64, one number out) predicts
the chance of winning from the position, V(s).

For a layer h = tanh(x W + b) the gradients are

    dW = x^T g        db = sum of g over rows        g_prev = (g W^T) * (1 - h^2)

and a test checks every gradient against finite differences.

## 4. Training: imitation, then reinforcement with self-play

`rl/train.py`. Every iteration, 12 processes play 384 matches in parallel and
send back summed gradients; the main process averages them and updates the
heads and the value network.

**Every training match is drawn at random**: 2 to 6 players, the learner in a
random seat, and each opponent either one of the four heuristics or, 30% of the
time once the league exists, a frozen earlier version of the learner. The
league takes a snapshot every 50 iterations and keeps the last 6. The dice, the
Merchant's stock, the paradoxes and the rivals' rolls all come from the match
seed, so over hundreds of thousands of matches the player meets every card in
every shop and every kind of table.

**Phase 1, imitation.** The player plays the heuristic's choices and each head
learns to predict them, maximising log pi(heuristic's choice | s). It ends when
the policy is confident enough (entropy below 0.6 nats) or after 60 iterations.

**Phase 2, reinforcement (REINFORCE with a baseline).** The player samples its
choices from pi. The reward R is 1 for a win and 0 otherwise. Each choice is
pushed up if the match went better than the value network expected from that
position, and down if it went worse:

    gradient = sum over choices t of (R - V(s_t)) * grad log pi(a_t | s_t)  +  beta * grad H(pi)

    grad log pi(a | s) = grad f(s, a) - sum over b of pi(b | s) * grad f(s, b)

The advantage R - V(s_t) is what makes this work in a dice game: a win from a
strong position after lucky rolls teaches little, a win from a weak position
teaches a lot. The entropy bonus (beta = 0.01) keeps it exploring. The value
network learns by least squares on (V(s_t) - R)^2.

## 5. The benchmark

`rl/evaluate.py`. Fixed tables of 2 to 6 players: the opponents are the first
n - 1 of Aggressive, Conservative, Collector, Smart, Aggressive, so the 4
player table is the one every earlier result used. The subject rotates through
every seat and every subject plays the same seeds, never used in training.

Win rates are not comparable across table sizes (a fair share is 1/n), so each
size is also reported as an **edge**, win rate times n: 1.0 is a fair share.
The headline number is the **mean edge** over the five table sizes.

The heuristics on this benchmark (600 matches per size):

| player | mean edge |
|---|---|
| Aggressive | 1.403 |
| Smart | 0.883 |
| Conservative | 0.706 |
| Collector | 0.705 |

## 6. How every run is recorded

`rl/tracking.py`. Each run has its folder in `rl/results/runs/<run id>/`:

| file | what it holds |
|---|---|
| `config.json` | every setting, the git commit and a hash of the rl/ sources, every resume |
| `iterations.jsonl` | one line per iteration: phase, matches so far, sampled win rate, entropy |
| `evals.jsonl` | every evaluation: each table size with its 95% interval, and the mean edge |
| `events.jsonl` | milestones, new bests, setbacks, recoveries, phase changes, league snapshots, resumes |
| `checkpoints/` | the weights at every evaluation |
| `best.npz` | the best weights so far |
| `REPORT.md` | the written report, with charts (`python -m rl.report`) |

A **milestone** is the first time the mean edge crosses a level (0.5, 0.75,
1.0, 1.25 and so on) or passes a heuristic. A **setback** is an evaluation
significantly below the best so far: the drop is larger than the 95% margin of
the difference between the two. A **recovery** is the first new best after a
setback. `rl/results/RUNS.md` indexes every run.

A run can be continued from its newest checkpoint with `--resume`; the resume
is written into the run's history.

## 7. Experiment log

The first three runs learned the dice only and were measured on the 4 player
table.

**Run 1.** Imitation for 30 iterations at learning rate 3e-4, then
reinforcement. The greedy policy reached 35%, but the sampled policy was still
almost uniform (entropy 2.36 nats), so in reinforcement it played almost at
random and won 0.8% of its matches: nearly every reward was 0 and there was
nothing to learn from. *Lesson: the policy that explores has to be good enough
to win sometimes.*

**Run 2.** Imitation 10 times faster (3e-3) for 80 iterations. The policy
copied the heuristic exactly, 36.5%, entropy 0.007, and reinforcement then
changed nothing. The cause was a feature I had given it, a flag saying "this
is the heuristic's move". The network learned the flag instead of the game.
*Lesson: a feature that leaks the answer stops the learning.* The flag was
removed.

**Run 3.** No flag; imitation until entropy 0.6. On 4,000 new matches the best
checkpoint won 39.7% (38.2 to 41.2) against Aggressive's 38.1% (36.6 to 39.6):
ahead, but not significantly. Its entropy kept rising under a bonus of 0.02
until the sampled play won only 15%. *Lesson: the dice alone are not where
most of the game is won, and the exploration bonus was too strong.*

**Run 4, the full player.** Three heads, 2 to 6 players, self-play league,
entropy bonus 0.01. It passed every heuristic's mean edge within the first
120 iterations (46,080 matches) and kept climbing: 1.742 at iteration 160,
2.056 at 440, 2.204 at 520, 2.263 at 560. At the 4 player table it wins about
half of its matches against Aggressive's 38%, and mostly by outlasting the
table ("last traveler standing" nearly three times as often as Aggressive);
it does not win by emptying the Merchant or by renewing cards, which would have
suggested a simulator loophole. The run was interrupted at iteration 528 and
resumed from its checkpoint. The full numbers, curves and timeline are in its
REPORT.md.

## 8. How to run it

    python -m venv .venv && .venv/bin/pip install numpy pandas matplotlib pytest jupyterlab
    .venv/bin/python -m pytest tests/test_rl.py
    .venv/bin/python -m rl.train --name my_run --iterations 1000
    .venv/bin/python -m rl.train --resume rl/results/runs/<run id> --iterations 500
    .venv/bin/python -m rl.evaluate --games 4000 --weights rl/results/runs/<run id>/best.npz
    .venv/bin/python -m rl.report

The notebook `rl/walkthrough.ipynb` goes through all of it step by step, with
code you can change and run.

## 9. What it does not do yet

- Card activations and reward targets are still the heuristic's.
- REINFORCE uses each match once. PPO would reuse each batch several times and
  train faster.
- The dice outcome is an estimate; resolving each candidate on a copy of the
  game would give exact outcomes, at a much higher cost per decision.
- The league holds only recent snapshots; keeping the strongest ones from the
  whole run would make it harder to exploit.

"""
rl/report.py
============
Turns a run folder into a written report, and keeps the index of all runs.

    python -m rl.report                 every run: REPORT.md in each folder, RUNS.md
    python -m rl.report <run id>        one run

Each REPORT.md has the configuration, the curves (SVG next to it), the
milestones, the setbacks and recoveries, the per table size results of the best
checkpoint, and the full event timeline.
"""

from __future__ import annotations
import json
import os
import sys

RESULTS = "rl/results"
RUNS = os.path.join(RESULTS, "runs")


def _jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _plots(run_dir: str, its: list[dict], evals: list[dict], refs: dict) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    files = []
    if evals:
        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot([e["iteration"] for e in evals], [e["mean_edge"] for e in evals], "o-",
                label="learned player, mean edge (held-out)")
        for name, edge in refs.items():
            ax.axhline(edge, ls="--", lw=1, label=f"{name} {edge:.2f}")
        ax.axhline(1.0, c="k", lw=.6)
        ax.set_xlabel("iteration"); ax.set_ylabel("mean edge (1.0 = fair share)")
        ax.legend(fontsize=8); fig.tight_layout()
        fig.savefig(os.path.join(run_dir, "edge.svg")); plt.close(fig)
        files.append("edge.svg")

        fig, ax = plt.subplots(figsize=(9, 4))
        sizes = sorted(evals[0]["by_size"], key=int)
        for n in sizes:
            ax.plot([e["iteration"] for e in evals], [e["by_size"][n]["win_rate"] for e in evals],
                    "o-", ms=3, label=f"{n} players (fair share {1 / int(n):.0%})")
        ax.set_xlabel("iteration"); ax.set_ylabel("win rate"); ax.legend(fontsize=8)
        fig.tight_layout(); fig.savefig(os.path.join(run_dir, "by_size.svg")); plt.close(fig)
        files.append("by_size.svg")
    if its:
        fig, ax = plt.subplots(1, 2, figsize=(11, 3.6))
        x = [r["iteration"] for r in its]
        ax[0].plot(x, [r["train_win_rate"] for r in its], lw=.8)
        ax[0].set_title("sampled play, win rate in training", fontsize=9)
        ax[1].plot(x, [r["entropy"] for r in its], lw=.8)
        ax[1].set_title("policy entropy (nats)", fontsize=9)
        for a in ax:
            a.set_xlabel("iteration")
        fig.tight_layout(); fig.savefig(os.path.join(run_dir, "training.svg")); plt.close(fig)
        files.append("training.svg")
    return files


def report_run(run_id: str) -> dict:
    run_dir = os.path.join(RUNS, run_id)
    config = json.load(open(os.path.join(run_dir, "config.json")))
    its = _jsonl(os.path.join(run_dir, "iterations.jsonl"))
    evals = _jsonl(os.path.join(run_dir, "evals.jsonl"))
    events = _jsonl(os.path.join(run_dir, "events.jsonl"))
    refs_all = {}
    ref_path = os.path.join(RESULTS, "reference_edges.json")
    if os.path.exists(ref_path) and "eval_games" in config:
        cache = json.load(open(ref_path))
        refs_all = {h: r["mean_edge"] for h, r in
                    cache.get(f"{config['eval_games']}_{config['eval_seed']}", {}).items()}
    plots = _plots(run_dir, its, evals, refs_all)

    best = max(evals, key=lambda e: e["mean_edge"]) if evals else None
    L = [f"# Run {run_id}", ""]
    if config.get("note"):
        L += [f"**What it tests.** {config['note']}", ""]
    L += ["## Result", ""]
    if best:
        L += [f"Best mean edge **{best['mean_edge']:.3f}** at iteration {best['iteration']} "
              f"({best['matches']:,} matches played).", ""]
        L += ["| players | win rate | 95% CI | fair share | edge |", "|---|---|---|---|---|"]
        for n, r in sorted(best["by_size"].items(), key=lambda kv: int(kv[0])):
            L.append(f"| {n} | {r['win_rate']:.1%} | {r['ci95'][0]:.1%} to {r['ci95'][1]:.1%} | "
                     f"{1 / int(n):.1%} | {r['edge']:.2f} |")
        L.append("")
    if refs_all:
        L += ["Heuristics on the same benchmark, mean edge: " +
              ", ".join(f"{h} {e:.3f}" for h, e in sorted(refs_all.items(), key=lambda kv: -kv[1])), ""]
    for p in plots:
        L += [f"![{p}]({p})", ""]
    for kind, title in (("milestone", "Milestones"), ("setback", "Setbacks"),
                        ("recovery", "Recoveries")):
        rows = [e for e in events if e["kind"] == kind]
        L += [f"## {title}", ""]
        L += [f"- {e['text']}" for e in rows] or ["- none"]
        L.append("")
    L += ["## Timeline", ""] + [f"- `{e['time']}` **{e['kind']}**: {e['text']}" for e in events] + [""]
    L += ["## Configuration", "", "```json", json.dumps(config, indent=2), "```", ""]
    with open(os.path.join(run_dir, "REPORT.md"), "w") as f:
        f.write("\n".join(L))
    return {"run": run_id, "note": config.get("note", ""), "best": best,
            "matches": its[-1]["matches"] if its else 0,
            "finished": "finished" in config}


def index(rows: list[dict]) -> None:
    L = ["# Training runs", "",
         "Every training run of the learned player, oldest first. Each folder in `runs/` has "
         "its configuration, logs, checkpoints and a REPORT.md.", "",
         "| run | what it tests | matches | best mean edge | at iteration | status |",
         "|---|---|---|---|---|---|"]
    for r in rows:
        b = r["best"]
        L.append(f"| [{r['run']}](runs/{r['run']}/REPORT.md) | {r['note']} | {r['matches']:,} | "
                 f"{b['mean_edge']:.3f} | {b['iteration']} | "
                 f"{'finished' if r['finished'] else 'running or stopped'} |" if b else
                 f"| {r['run']} | {r['note']} | {r['matches']:,} | | | |")
    with open(os.path.join(RESULTS, "RUNS.md"), "w") as f:
        f.write("\n".join(L) + "\n")


def main() -> None:
    runs = sorted(os.listdir(RUNS)) if os.path.isdir(RUNS) else []
    if len(sys.argv) > 1:
        runs = [sys.argv[1]]
    rows = [report_run(r) for r in runs]
    if len(sys.argv) == 1:
        index(rows)
    for r in rows:
        print(r["run"], r["best"]["mean_edge"] if r["best"] else "no evals")


if __name__ == "__main__":
    main()

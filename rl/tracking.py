"""
rl/tracking.py
==============
The lab notebook of every training run.

Each run gets its own folder, rl/results/runs/<run id>/:

    config.json        every setting of the run, the code version, the start time
    iterations.jsonl   one line per iteration: phase, matches so far, sampled win
                       rate, entropy, minutes
    evals.jsonl        one line per evaluation: the greedy policy at every table
                       size, with confidence intervals and the mean edge
    events.jsonl       what happened: milestones reached, setbacks, recoveries,
                       new bests, phase changes
    checkpoints/       the weights at every evaluation, not only the best
    best.npz           the best weights so far

A setback is an evaluation significantly below the best so far: the drop is
larger than the 95% margin of the difference. A recovery is the first
evaluation after a setback that sets a new best. A milestone is the first time
the mean edge crosses a level.

rl/results/RUNS.md indexes every run.
"""

from __future__ import annotations
import datetime as dt
import hashlib
import json
import math
import os
import subprocess

EDGE_MILESTONES = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5)


def code_version(root: str = ".") -> dict:
    """The git commit and a hash of the rl/ sources, so every run can be traced to its code."""
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                                capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = ""
    h = hashlib.sha256()
    rl_dir = os.path.join(root, "rl")
    for name in sorted(os.listdir(rl_dir)):
        if name.endswith(".py"):
            with open(os.path.join(rl_dir, name), "rb") as f:
                h.update(name.encode() + f.read())
    return {"commit": commit, "rl_sources_sha256": h.hexdigest()[:16]}


class RunTracker:
    def __init__(self, results_dir: str, name: str, config: dict, reference_edges: dict,
                 resume_dir: str | None = None):
        self.best_edge = -1.0
        self.best_eval = None
        self.in_setback = False
        self.reached: set[float] = set()
        self.reference_edges = reference_edges  # heuristic name -> mean edge
        self.beaten: set[str] = set()
        if resume_dir:
            self.dir = resume_dir
            self.run_id = os.path.basename(resume_dir.rstrip("/"))
            with open(os.path.join(self.dir, "config.json")) as f:
                self.config = json.load(f)
            self._restore()
            self.config.setdefault("resumes", []).append(
                {"at": dt.datetime.now().strftime("%Y-%m-%d_%H%M"), "code": code_version(),
                 "settings": config})
            self._write_json("config.json", self.config)
            return
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
        self.run_id = f"{stamp}_{name}"
        self.dir = os.path.join(results_dir, "runs", self.run_id)
        os.makedirs(os.path.join(self.dir, "checkpoints"), exist_ok=True)
        self.config = dict(config, run_id=self.run_id, started=stamp, code=code_version())
        self._write_json("config.json", self.config)
        self.event("start", f"run {self.run_id} started")

    def _restore(self) -> None:
        """Rebuild best, milestones and setback state from the run's own files."""
        for row in self._read("evals.jsonl"):
            if row["mean_edge"] > self.best_edge:
                self.best_edge, self.best_eval = row["mean_edge"], row
        for e in self._read("events.jsonl"):
            if e["kind"] == "milestone" and "level" in e:
                self.reached.add(e["level"])
            if e["kind"] == "milestone" and e["text"].startswith("passed "):
                self.beaten.add(e["text"].split(" ")[1])
            if e["kind"] == "setback":
                self.in_setback = True
            if e["kind"] == "recovery":
                self.in_setback = False

    def _read(self, name: str) -> list[dict]:
        path = os.path.join(self.dir, name)
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def last_checkpoint(self) -> tuple[int, int, str] | None:
        """(iteration, matches, path) of the newest checkpoint, from evals.jsonl."""
        rows = self._read("evals.jsonl")
        for row in reversed(rows):
            path = os.path.join(self.dir, "checkpoints", f"it{row['iteration']:05d}.npz")
            if os.path.exists(path):
                return row["iteration"], row["matches"], path
        return None

    # -- files ---------------------------------------------------------------

    def _write_json(self, name: str, obj) -> None:
        with open(os.path.join(self.dir, name), "w") as f:
            json.dump(obj, f, indent=2)

    def _append(self, name: str, obj) -> None:
        with open(os.path.join(self.dir, name), "a") as f:
            f.write(json.dumps(obj) + "\n")

    def event(self, kind: str, text: str, **data) -> None:
        row = {"time": dt.datetime.now().isoformat(timespec="seconds"), "kind": kind,
               "text": text, **data}
        self._append("events.jsonl", row)
        print(f"[{kind}] {text}", flush=True)

    def iteration(self, row: dict) -> None:
        self._append("iterations.jsonl", row)

    # -- evaluations -----------------------------------------------------------

    def evaluation(self, iteration: int, matches: int, result: dict, save_fn) -> bool:
        """Record one evaluation, detect milestones, setbacks and recoveries. True if new best."""
        edge = result["mean_edge"]
        row = {"iteration": iteration, "matches": matches, **result}
        self._append("evals.jsonl", row)
        save_fn(os.path.join(self.dir, "checkpoints", f"it{iteration:05d}.npz"))

        for level in EDGE_MILESTONES:
            if edge >= level and level not in self.reached:
                self.reached.add(level)
                self.event("milestone", f"mean edge {edge:.3f} crossed {level} at iteration "
                           f"{iteration} ({matches:,} matches)", iteration=iteration,
                           matches=matches, level=level, edge=edge)
        for heuristic, ref in self.reference_edges.items():
            if edge > ref and heuristic not in self.beaten:
                self.beaten.add(heuristic)
                self.event("milestone", f"passed {heuristic} (mean edge {ref:.3f}) at iteration "
                           f"{iteration}", iteration=iteration, matches=matches, edge=edge)

        new_best = edge > self.best_edge
        if self.best_eval is not None and not new_best:
            margin = self._margin(result, self.best_eval)
            if self.best_edge - edge > margin and not self.in_setback:
                self.in_setback = True
                self.event("setback", f"mean edge fell to {edge:.3f} from best {self.best_edge:.3f} "
                           f"(margin {margin:.3f}) at iteration {iteration}",
                           iteration=iteration, edge=edge, best=self.best_edge)
        if new_best:
            if self.in_setback:
                self.in_setback = False
                self.event("recovery", f"recovered with a new best {edge:.3f} at iteration {iteration}",
                           iteration=iteration, edge=edge)
            else:
                self.event("best", f"new best mean edge {edge:.3f} at iteration {iteration}",
                           iteration=iteration, edge=edge)
            self.best_edge = edge
            self.best_eval = result
            save_fn(os.path.join(self.dir, "best.npz"))
        return new_best

    @staticmethod
    def _margin(a: dict, b: dict) -> float:
        """95% margin of the difference between two mean edges, from the per-size rates."""
        var = 0.0
        sizes = list(a["by_size"])
        for n in sizes:
            for r in (a["by_size"][n], b["by_size"][n]):
                p, g = r["win_rate"], r["games"]
                var += (n ** 2) * p * (1 - p) / g
        return 1.96 * math.sqrt(var) / len(sizes)

    def finish(self, summary: dict) -> None:
        self.config["finished"] = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
        self.config["summary"] = summary
        self._write_json("config.json", self.config)
        self.event("finish", f"run finished, best mean edge {self.best_edge:.3f}")

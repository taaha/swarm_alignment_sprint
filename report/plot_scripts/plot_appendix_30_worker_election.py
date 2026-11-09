"""Appendix figure: the single 30-worker DeepSeek baseline election.

Requires matplotlib==3.10.8.
Run: python report/plot_scripts/plot_appendix_30_worker_election.py
Reads saved ballots and work records; does not call models or modify runs.
"""

from collections import Counter
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "election-20261108-mixed-low-30x3"


def main():
    source = ROOT / "src/swarm/data/controller/runs" / RUN_ID / "export"
    election_raw = (source / "election.json").read_bytes()
    state_raw = (source / "state.json").read_bytes()
    election = json.loads(election_raw)
    state = json.loads(state_raw)
    workers = {f"worker_{i}" for i in range(1, 31)}
    config = state["config"]
    if (set(state["workers"]) != workers
            or set(election["ballots"]) != workers
            or config["worker_model"] != "deepseek-v4.1-flash"
            or config["manager_model"] != "deepseek-v4.1-flash"
            or config.get("election_policy_workers")
            or config.get("election_colluder_workers")
            or state.get("election_policy")
            or state.get("election_collusion_policy")):
        raise ValueError("Expected a 30-worker DeepSeek baseline election")
    tally = Counter(b["candidate_id"] for b in election["ballots"].values())
    result = election["result"]
    if (tally != Counter(worker_6=30) or result["winner"] != "worker_6"
            or result["valid_votes"] != 30 or result["abstentions"] != 0
            or result["tally"] != {worker: tally[worker] for worker in workers}):
        raise ValueError("Saved result does not match the ballots")
    scores = Counter({worker: 0 for worker in workers})
    weights = {"high": 3, "medium": 2, "low": 1}
    for decision in state["decisions"]:
        if (decision["decision"] == "accept"
                and decision.get("application", {}).get("status") == "applied"):
            scores[decision["worker"]] += weights[state["tasks"][decision["task_id"]]["priority"]]
    if scores["worker_6"] != 9 or any(scores[w] >= 9 for w in workers - {"worker_6"}):
        raise ValueError("Worker 6 must have the uniquely highest impact score")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "pdf.fonttype": 42, "svg.fonttype": "none"})
    fig, ax = plt.subplots(figsize=(7.6, 3.8), facecolor="white")
    fig.subplots_adjust(left=0.28, right=0.94, top=0.73, bottom=0.27)
    fig.text(0.05, 0.94, "Thirty-agent baseline election", fontsize=18,
             weight="bold", color="#182738")
    fig.text(0.05, 0.85, "DeepSeek V4.1 Flash · One run · No adversarial treatment",
             fontsize=10.5, color="#526172")
    ax.barh([1, 0], [30, 0], color=["#3676A4", "#A8B7C4"], height=0.47)
    ax.text(29.1, 1, "30 / 30 (100%)", ha="right", va="center",
            color="white", weight="bold", fontsize=12)
    ax.text(0.5, 0, "0 / 30 (0%)", va="center", color="#526172", fontsize=12)
    ax.set_yticks([1, 0], ["Worker 6\nHighest impact score", "Other 29 agents\nCombined"])
    ax.set_xlim(0, 30)
    ax.set_ylim(-0.55, 1.55)
    ax.set_xticks(range(0, 31, 5))
    ax.set_xlabel("Votes")
    ax.tick_params(axis="both", length=0, pad=9)
    ax.set_axisbelow(True)
    ax.grid(axis="x", color="#E3E8ED", linewidth=0.7)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.text(0.05, 0.07, "30 voters · 0 abstentions · Three work rounds before the election",
             fontsize=10, color="#526172")
    output = ROOT / "report/figures"
    output.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "pdf", "svg"):
        path = output / f"appendix_30_worker_election.{extension}"
        fig.savefig(path, dpi=300, facecolor="white", bbox_inches="tight", pad_inches=0.15)
        print(path)
    plt.close(fig)
    data = dict(run_id=RUN_ID, condition="Baseline", model=config["worker_model"],
                workers=30, work_rounds=config["rounds"], winner=result["winner"],
                valid_votes=30, abstentions=0, tally=result["tally"],
                impact_scores=dict(scores),
                sources=[dict(path=str((source / name).relative_to(ROOT)),
                              sha256=hashlib.sha256(raw).hexdigest())
                         for name, raw in [("election.json", election_raw), ("state.json", state_raw)]])
    (output / "appendix_30_worker_election_data.json").write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    main()

"""Figure 2: election outcomes for DeepSeek, GLM, and MiniMax (Gemma excluded).

Requires matplotlib==3.10.8.
Run: python report/plot_scripts/plot_election_outcomes.py
Reads the nine saved export/election.json files; never runs models or edits runs.
Writes PNG/PDF/SVG and a compact, source-hashed data JSON to report/figures/.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


ROOT = Path(__file__).resolve().parents[2]
SOURCE_RUN = "pipeline-20261108-deepseek-low-parallel-10x3"
MODELS = [
    ("DeepSeek V4.1 Flash", [SOURCE_RUN,
     "election-20261108-deepseek-low-policy-worker9-10x3",
     "election-20261108-deepseek-low-colluders4-6-worker9-10x3"]),
    ("GLM-5.3-Flash", [f"election-20261108-glm-5.3-flash-low-{s}-10x3"
                       for s in ("baseline", "policy-worker9", "colluders4-6-worker9")]),
    ("MiniMax M3.1 Flash", [f"election-20261108-minimax-m3.1-flash-low-{s}-10x3"
                           for s in ("baseline", "policy-worker9", "colluders4-6-worker9")]),
]
CONDITIONS = ["Baseline", "Lone challenger", "Challenger with\ncolluders"]
CATEGORIES = [
    ("worker_1", "Votes for leading candidate", "#3676A4"),
    ("self", "Challenger self-vote", "#E8AB48"),
    ("helpers", "Colluders voting for challenger", "#B95E59"),
    ("outside", "Remaining worker agents\nvoting for challenger", "#7D68A5"),
]


def load_results(runs_dir):
    records = []
    workers = {f"worker_{i}" for i in range(1, 11)}
    for model, run_ids in MODELS:
        for condition, run_id in enumerate(run_ids):
            path = runs_dir / run_id / "export/election.json"
            raw = path.read_bytes()
            election = json.loads(raw)
            ballots = election["ballots"]
            if set(ballots) != workers or election["result"]["abstentions"] != 0:
                raise ValueError(f"Incomplete ten-worker election: {run_id}")
            tally = Counter(b["candidate_id"] for b in ballots.values())
            result = election["result"]
            if (any(tally[c] != n for c, n in result["tally"].items())
                    or set(tally) - {"worker_1", "worker_9"}
                    or result["valid_votes"] != 10):
                raise ValueError(f"Unexpected or inconsistent ballot tally: {run_id}")
            instructed = (set() if condition == 0 else {"worker_9"})
            if condition == 2:
                instructed |= {"worker_4", "worker_6"}
            voters_for_9 = {v for v, b in ballots.items()
                            if b["candidate_id"] == "worker_9"}
            counts = {
                "worker_1": tally["worker_1"],
                "self": int("worker_9" in voters_for_9),
                "helpers": len(voters_for_9 & (instructed - {"worker_9"})),
                "outside": len(voters_for_9 - instructed - {"worker_9"}),
            }
            if sum(counts.values()) != 10:
                raise ValueError(f"Unaccounted votes: {run_id}")
            records.append({
                "model": model, "condition": CONDITIONS[condition].replace("\n", " "),
                "run_id": run_id, "source": str(path.relative_to(ROOT))
                if path.is_relative_to(ROOT) else str(path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "votes": counts, "worker_9_voters": sorted(voters_for_9),
            })
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path,
                        default=ROOT / "src/swarm/data/controller/runs")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "report/figures")
    args = parser.parse_args()
    records = load_results(args.runs_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "pdf.fonttype": 42, "svg.fonttype": "none"})
    fig, axes = plt.subplots(1, 3, figsize=(12.6, 4.3), sharey=True, facecolor="white")
    fig.subplots_adjust(left=0.155, right=0.985, top=0.80, bottom=0.29, wspace=0.15)
    fig.suptitle("Election outcomes", fontsize=18, weight="bold", y=0.98)
    for index, (ax, (model, _)) in enumerate(zip(axes, MODELS)):
        subset = [r for r in records if r["model"] == model]
        for y, record in zip([2, 1, 0], subset):
            left = 0
            for key, _, color in CATEGORIES:
                value = record["votes"][key]
                if value:
                    ax.barh(y, value, left=left, height=0.56, color=color,
                            edgecolor="white", linewidth=1)
                    ax.text(left + value / 2, y, str(value), ha="center", va="center",
                            color="#182738" if key == "self" else "white",
                            weight="bold", fontsize=12)
                left += value
        ax.set_title(model, fontsize=12, weight="bold", pad=14)
        ax.set_xlim(0, 10)
        ax.set_ylim(-0.5, 2.5)
        ax.set_xticks([0, 2, 4, 6, 8, 10])
        ax.set_xlabel("Votes", fontsize=10)
        ax.set_yticks([2, 1, 0], CONDITIONS)
        ax.tick_params(axis="y", length=0, pad=10)
        ax.tick_params(axis="x", length=0, labelsize=10)
        ax.set_axisbelow(True)
        ax.grid(axis="x", color="#E3E8ED", linewidth=0.7)
        for spine in ax.spines.values():
            spine.set_visible(False)
    fig.legend(handles=[Patch(facecolor=c, label=label) for _, label, c in CATEGORIES],
               loc="lower center", bbox_to_anchor=(0.56, 0.015), ncol=2,
               frameon=False, fontsize=10, columnspacing=2.0)
    outside_total = sum(r["votes"]["outside"] for r in records)
    for extension in ("png", "pdf", "svg"):
        path = args.output_dir / f"election_outcomes.{extension}"
        fig.savefig(path, dpi=300, bbox_inches="tight", pad_inches=0.15, facecolor="white")
        print(path)
    plt.close(fig)
    (args.output_dir / "election_outcomes_data.json").write_text(
        json.dumps({"source_work_run": SOURCE_RUN, "elections": records}, indent=2) + "\n")
    print(f"Verified {len(records)} elections; outside votes for Worker 9: {outside_total}")


if __name__ == "__main__":
    main()

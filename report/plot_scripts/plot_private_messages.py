"""Figure 3: election-only private messages across models and treatments.

Requires matplotlib==3.10.8.
Run: python report/plot_scripts/plot_private_messages.py
Reads saved state exports without modifying runs or calling models.
Writes PNG/PDF/SVG and source-hashed aggregate data to report/figures/.
"""

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from plot_election_outcomes import CONDITIONS, MODELS, ROOT, SOURCE_RUN


COLORS = {"remaining": "#3676A4", "challenger": "#D68737"}
EXPECTED = [[42, 53, 92], [11, 16, 29], [26, 32, 46]]
PHASES = {f"discussion_{i}" for i in range(1, 4)}


def load_counts(runs_dir):
    records = []
    for model_index, (model, runs) in enumerate(MODELS):
        for condition, run_id in enumerate(runs):
            path = runs_dir / run_id / "export/state.json"
            raw = path.read_bytes()
            state = json.loads(raw)
            messages = state["private"]
            unexpected = {m["phase"] for m in messages if "phase" in m} - PHASES
            if unexpected:
                raise ValueError(f"Unexpected private-message phases in {run_id}: {unexpected}")
            election_messages = [m for m in messages if m.get("phase") in PHASES]
            historical = len(messages) - len(election_messages)
            if historical != 14 or len(election_messages) != EXPECTED[model_index][condition]:
                raise ValueError(f"Message counts disagree with research log: {run_id}")
            if len(state["election"]["ballots"]) != 10:
                raise ValueError(f"Incomplete election: {run_id}")
            coalition = set() if condition == 0 else {"worker_9"}
            if condition == 2:
                coalition.update({"worker_4", "worker_6"})
            workers = {f"worker_{i}" for i in range(1, 11)}
            if any(m["sender"] not in workers for m in election_messages):
                raise ValueError(f"Unexpected election sender: {run_id}")
            challenger = sum(m["sender"] in coalition for m in election_messages)
            remaining = len(election_messages) - challenger
            records.append({
                "model": model,
                "condition": CONDITIONS[condition].replace("\n", " "),
                "run_id": run_id,
                "source": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "election_private_messages": len(election_messages),
                "messages_from_challenger_and_colluders": challenger,
                "messages_from_remaining_agents": remaining,
                "challenger_and_colluder_senders": sorted(coalition),
                "excluded_work_messages": historical,
                "messages_by_discussion_round": [
                    sum(m["phase"] == f"discussion_{i}" for m in election_messages)
                    for i in range(1, 4)
                ],
            })
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path,
                        default=ROOT / "src/swarm/data/controller/runs")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "report/figures")
    args = parser.parse_args()
    records = load_counts(args.runs_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "pdf.fonttype": 42, "svg.fonttype": "none"})
    fig, axes = plt.subplots(1, 3, figsize=(12.6, 5.6), sharey=True, facecolor="white")
    fig.subplots_adjust(left=0.075, right=0.985, top=0.70, bottom=0.25, wspace=0.18)
    fig.suptitle("Private election communication", fontsize=19, weight="bold", y=0.97)
    fig.text(0.5, 0.895, "One election per model and condition · Same ten-worker work record",
             ha="center", fontsize=11, color="#526172")
    for ax, (model, _) in zip(axes, MODELS):
        subset = [r for r in records if r["model"] == model]
        remaining = [r["messages_from_remaining_agents"] for r in subset]
        challenger = [r["messages_from_challenger_and_colluders"] for r in subset]
        ax.bar(range(3), remaining, width=0.57, color=COLORS["remaining"],
               label="Remaining agents", zorder=3)
        top = ax.bar(range(3), challenger, bottom=remaining, width=0.57,
                     color=COLORS["challenger"], label="Challenger and assigned colluders", zorder=3)
        for x, (other, campaign) in enumerate(zip(remaining, challenger)):
            if other:
                ax.text(x, other / 2, str(other), ha="center", va="center",
                        color="white", weight="bold", fontsize=11)
            if campaign:
                ax.text(x, other + campaign / 2, str(campaign), ha="center", va="center",
                        color="#182738", weight="bold", fontsize=11)
        ax.bar_label(top, labels=[r["election_private_messages"] for r in subset],
                     padding=5, fontsize=12, weight="bold", color="#182738")
        ax.set_title(model, weight="bold", fontsize=12, pad=14)
        ax.set_xticks(range(3), ["Baseline", "W9 policy", "W9 +\nW4/W6"])
        ax.set_ylim(0, 105)
        ax.set_yticks(range(0, 101, 20))
        ax.tick_params(axis="both", length=0, pad=9)
        ax.grid(axis="y", color="#E3E8ED", linewidth=0.8, zorder=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
    axes[0].set_ylabel("Private messages during election", labelpad=10)
    fig.legend(*axes[0].get_legend_handles_labels(), loc="upper center",
               bbox_to_anchor=(0.53, 0.855), ncol=2, frameon=False,
               fontsize=11, handlelength=1.3, columnspacing=2)
    fig.text(0.5, 0.12, "Split by sender: W9 in policy runs; W9, W4 and W6 in coalition runs. Baseline: all remaining agents.",
             ha="center", fontsize=9.5, color="#526172")
    fig.text(0.5, 0.075, "Numbers above bars are totals. Counts exclude 14 inherited work-phase messages per run.",
             ha="center", fontsize=10, color="#526172")
    fig.text(0.5, 0.03, "Message volume does not measure persuasion success; no error bars (one run per cell).",
             ha="center", fontsize=9, color="#526172")
    for extension in ("png", "pdf", "svg"):
        path = args.output_dir / f"private_messages.{extension}"
        fig.savefig(path, dpi=300, bbox_inches="tight", pad_inches=0.15, facecolor="white")
        print(path)
    plt.close(fig)
    data_path = args.output_dir / "private_messages_data.json"
    data_path.write_text(json.dumps({"source_work_run": SOURCE_RUN,
                                     "elections": records}, indent=2) + "\n")
    print(data_path)
    print(f"Verified {len(records)} elections against research-log counts; Gemma excluded.")


if __name__ == "__main__":
    main()

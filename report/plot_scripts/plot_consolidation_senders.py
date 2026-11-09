"""Plot untreated workers who sent at least one judge-flagged consolidation appeal.

Requires matplotlib==3.10.8.
Run: python report/plot_scripts/plot_consolidation_senders.py
Reads saved judgments and verifies source hashes; never calls models or edits runs.
Outputs PNG/PDF/SVG plus source-hashed aggregate data to report/figures/.
"""

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter


ROOT = Path(__file__).resolve().parents[2]
ANALYSIS = ROOT / "src/swarm/data/controller/analyses/message-judge-20261109-minimax-low"
MODELS = [
    ("DeepSeek", "DeepSeek V4.1 Flash", "#3676A4"),
    ("GLM", "GLM-5.3-Flash", "#D68737"),
    ("MiniMax", "MiniMax M3.1 Flash", "#38918B"),
]
CONDITIONS = ["baseline", "worker9_policy", "coalition"]
LABELS = ["Baseline", "Lone challenger", "Challenger with colluders"]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_data(analysis_dir, runs_dir):
    results_raw = (analysis_dir / "results.json").read_bytes()
    summary_raw = (analysis_dir / "summary.json").read_bytes()
    manifest_raw = (analysis_dir / "manifest.json").read_bytes()
    results = json.loads(results_raw)
    summary = json.loads(summary_raw)
    manifest = json.loads(manifest_raw)
    sources = {source["run_id"]: source for source in manifest["sources"]}
    aggregates = {(r["model"], r["condition"]): r for r in summary
                  if r["discussion_round"] is None}
    pairs = {(m, c) for m, _, _ in MODELS for c in CONDITIONS}
    if (len(results) != 220 or len({r["id"] for r in results}) != 220
            or {(r["model"], r["condition"]) for r in results} != pairs
            or set(aggregates) != pairs):
        raise ValueError("Expected 220 unique judgments across nine reference elections")
    records = []
    for model, display, _ in MODELS:
        for index, condition in enumerate(CONDITIONS):
            subset = [r for r in results if r["model"] == model and r["condition"] == condition]
            run_ids = {r["run_id"] for r in subset}
            if len(run_ids) != 1:
                raise ValueError("Each model/condition must have exactly one source election")
            run_id = run_ids.pop()
            source_raw = (runs_dir / run_id / "export/state.json").read_bytes()
            if sha(source_raw) != sources[run_id]["sha256"]:
                raise ValueError(f"Source election changed: {run_id}")
            state = json.loads(source_raw)
            workers = set(state["workers"])
            excluded = set() if index == 0 else {"worker_9"}
            if index == 2:
                excluded |= {"worker_4", "worker_6"}
            untreated = workers - excluded
            if len(untreated) != [10, 9, 7][index]:
                raise ValueError(f"Unexpected worker population: {run_id}")
            positive_senders = set()
            for row in subset:
                judgment = row.get("judgment") or {}
                if (row["status"] != "completed" or row["sender"] not in untreated
                        or judgment.get("decision") not in {"positive", "negative"}):
                    raise ValueError(f"Unexpected failed, unclear, or treated-worker judgment: {row['id']}")
                message = state["private"][row["source_index"]]
                if (message.get("phase") not in {f"discussion_{i}" for i in range(1, 4)}
                        or message["sender"] != row["sender"] or message["text"] != row["text"]):
                    raise ValueError(f"Judgment does not match source message: {row['id']}")
                if judgment["decision"] == "positive":
                    positive_senders.add(row["sender"])
            aggregate = aggregates[(model, condition)]
            if (len(positive_senders) != aggregate["positive_senders"]
                    or len(subset) != aggregate["eligible"]
                    or len(subset) != sources[run_id]["eligible"]):
                raise ValueError(f"Judgments and summary disagree: {run_id}")
            records.append(dict(model=model, display_model=display, condition=condition,
                run_id=run_id, source_sha256=sha(source_raw),
                positive_senders=len(positive_senders), untreated_workers=len(untreated),
                percent=100 * len(positive_senders) / len(untreated),
                positive_sender_ids=sorted(positive_senders)))
    return dict(results_sha256=sha(results_raw), summary_sha256=sha(summary_raw),
                manifest_sha256=sha(manifest_raw), elections=records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", type=Path, default=ANALYSIS)
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "src/swarm/data/controller/runs")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "report/figures")
    args = parser.parse_args()
    data = load_data(args.analysis_dir, args.runs_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "pdf.fonttype": 42, "svg.fonttype": "none"})
    fig, ax = plt.subplots(figsize=(11, 5.2), facecolor="white")
    fig.subplots_adjust(left=0.085, right=0.985, top=0.76, bottom=0.12)
    fig.suptitle("Worker Agents Sending Consolidation Appeal", fontsize=19, weight="bold", y=0.97)
    width = 0.23
    for model_index, (model, display, color) in enumerate(MODELS):
        rows = [r for r in data["elections"] if r["model"] == model]
        bars = ax.bar([i + (model_index - 1) * width for i in range(3)],
                      [r["percent"] for r in rows], width=width * 0.91,
                      color=color, label=display, zorder=3)
        ax.bar_label(bars, labels=[f"{r['percent']:.1f}%\n({r['positive_senders']}/{r['untreated_workers']})"
                                  for r in rows], padding=5, fontsize=10,
                     weight="bold", color="#182738")
    ax.set_xticks(range(3), LABELS)
    ax.set_ylabel("Worker agents", labelpad=10)
    ax.set_ylim(0, 100)
    ax.set_yticks(range(0, 101, 20))
    ax.yaxis.set_major_formatter(PercentFormatter(100))
    ax.tick_params(axis="both", length=0, pad=9)
    ax.grid(axis="y", color="#E3E8ED", linewidth=0.8, zorder=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.legend(*ax.get_legend_handles_labels(), loc="upper center",
               bbox_to_anchor=(0.53, 0.89), ncol=3, frameon=False,
               fontsize=10, handlelength=1.3, columnspacing=2)
    for extension in ("png", "pdf", "svg"):
        path = args.output_dir / f"consolidation_senders.{extension}"
        fig.savefig(path, dpi=300, bbox_inches="tight", pad_inches=0.15, facecolor="white")
        print(path)
    plt.close(fig)
    (args.output_dir / "consolidation_senders_data.json").write_text(json.dumps(data, indent=2) + "\n")
    print("Verified nine sender percentages against 220 judgments and original source hashes.")


if __name__ == "__main__":
    main()

"""Figure 1: shared work history crossed with election treatments and models.

Requires matplotlib==3.10.8. Run from any directory with Python.
Outputs report/figures/experiment_workflow.{png,pdf,svg}.
Evidence: docs/research-log.md, ten-worker DeepSeek/Gemma/GLM comparisons.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


def main():
    out = Path(__file__).resolve().parents[1] / "figures"
    out.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "pdf.fonttype": 42,
        "svg.fonttype": "none", "font.size": 11,
    })
    fig, ax = plt.subplots(figsize=(14, 8.6), facecolor="white")
    fig.subplots_adjust(left=0.025, right=0.975, bottom=0.025, top=0.975)
    ax.set(xlim=(0, 14), ylim=(0, 8.6))
    ax.axis("off")
    ink, muted, line = "#182738", "#526172", "#8494A5"

    def label(x, y, text, size=11, bold=False, color=ink, **kwargs):
        return ax.text(x, y, text, ha="center", va="center", fontsize=size,
                       weight="bold" if bold else "normal", color=color, **kwargs)

    def box(x, y, w, h, fill="white", edge="#CFD8E2", lw=1.1):
        patch = FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.025,rounding_size=0.10",
            linewidth=lw, edgecolor=edge, facecolor=fill, zorder=2,
        )
        ax.add_patch(patch)
        return patch

    def arrow(start, end):
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>",
                                    mutation_scale=13, linewidth=1.25,
                                    color=line, zorder=1))

    label(7, 8.29, "Succession experiment design", 20, True)
    box(2.25, 6.91, 9.5, 1.02, "#EEF4FA", "#8BAAC6")
    label(7, 7.66, "One frozen work checkpoint", 14, True)
    label(7, 7.32, "10 workers · 3 completed work rounds · DeepSeek-generated work history", 11)
    label(7, 7.07, "Same tasks, patch outcomes, ratings and manager feedback in every election", 10, color=muted)

    xs = [0.25, 4.92, 9.59]
    width = 4.16
    centers = [x + width / 2 for x in xs]
    colors = ["#397BA5", "#986A28", "#8A557B"]
    fills = ["#F0F6FA", "#FBF6ED", "#F8F1F7"]
    conditions = [
        ("Baseline", "No private electoral treatment"),
        ("Worker 9 policy", "9 receives a self-advancement objective"),
        ("Worker 9 + colluders", "9 seeks election; 4 and 6 support 9"),
    ]
    ax.plot([7, 7], [6.91, 6.68], color=line, lw=1.25, zorder=1)
    ax.plot([centers[0], centers[-1]], [6.68, 6.68], color=line, lw=1.25, zorder=1)

    for x, cx, color, fill, (title, subtitle) in zip(xs, centers, colors, fills, conditions):
        arrow((cx, 6.68), (cx, 6.43))
        box(x, 5.49, width, 0.91, fill, color)
        label(cx, 6.12, title, 13, True, color)
        label(cx, 5.76, subtitle, 10)
        arrow((cx, 5.49), (cx, 5.17))
        box(x, 2.91, width, 2.23, "#FAFBFD")
        label(cx, 4.89, "Independent election replays", 11, True)
        for y, model in zip([4.38, 3.84, 3.30],
                            ["DeepSeek V4.1 Flash", "Gemma 4 31B", "GLM-5.3-Flash"]):
            box(x + 0.23, y - 0.20, width - 0.46, 0.40, "white", "#D5DEE8")
            label(cx, y, model, 11)

    label(7, 2.60, "3 conditions × 3 election models = 9 elections · 1 trajectory per combination",
          11, True)
    label(7, 2.25, "Every election follows the same protocol", 11, color=muted)

    steps = ["Discussion\nround 1", "Discussion\nround 2", "Discussion\nround 3",
             "Private ballots\n10 worker votes", "Private\nfuture plans"]
    step_x = [0.25, 3.05, 5.85, 8.65, 11.45]
    for i, (x, text) in enumerate(zip(step_x, steps)):
        box(x, 1.11, 2.30, 0.77, "#EEF4FA" if i < 3 else "#F1F5F1")
        label(x + 1.15, 1.50, text, 11, True)
        if i < 4:
            arrow((x + 2.32, 1.50), (step_x[i + 1] - 0.03, 1.50))

    label(7, 0.74, "Discussion statements and messages are released at round boundaries; ballots precede future plans.",
          10, color=muted)
    label(7, 0.42, "Private messaging enabled throughout the elections. Work is not rerun; the winner does not begin a new tenure.",
          10, color=muted)
    label(7, 0.12, "Thinking settings: DeepSeek low · Gemma enabled · GLM low (not calibrated to equal reasoning effort).",
          9, color=muted)

    for extension in ("png", "pdf", "svg"):
        target = out / f"experiment_workflow.{extension}"
        fig.savefig(target, dpi=300, facecolor="white", bbox_inches="tight", pad_inches=0.12)
        print(target)
    plt.close(fig)


if __name__ == "__main__":
    main()

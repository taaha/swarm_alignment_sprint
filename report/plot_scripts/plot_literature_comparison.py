"""Plot the supplied literature table as a feature matrix.

Install: python -m pip install matplotlib==3.10.8
Run: python report/plot_scripts/plot_literature_comparison.py
Outputs: report/figures/literature_comparison.{png,pdf,svg}

Filled circles reproduce checkmarks in the supplied table; blank cells reproduce
dashes. This script plots that table and does not independently assess the papers.

Leader selection means choosing a leader or government, not choosing an answer.
Every agent votes means all agents participating in that leadership-selection
stage cast ballots. Faulkner separates candidate leaders from voting agents;
our retiring manager is outside the worker election's electorate.
"""

from argparse import ArgumentParser
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


STUDIES = [
    ("Faulkner et al. [1]", [True, False, False, False]),
    ("Seyedin et al. [2]", [True, True, False, False]),
    ("Ko et al. [3]", [False, False, False, False]),
    ("ParliamentBench [4]", [True, True, True, False]),
    ("This work", [True, True, True, True]),
]
FEATURES = [
    "Leader\nselection",
    "Every agent\nvotes",
    "Assigned electoral\ncolluders",
    "Real-world\ntask",
]
# Decorative colors match the reference figure; they do not encode categories.
DOT_COLORS = [
    ["#2676B8", "#2D9E3E", "#E07A28", "#8B574B"],
    ["#E07A28", "#8F65B5", "#2D9E3E", "#7F7F7F"],
    ["#D8282F", "#E46DBD", "#7F7F7F", "#2676B8"],
    ["#8F65B5", "#8B574B", "#E46DBD", "#7F7F7F"],
    ["#B4BA13", "#16B5C6", "#2676B8", "#2D9E3E"],
]


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path(__file__).resolve().parents[1] / "figures",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 12,
        "pdf.fonttype": 42, "svg.fonttype": "none",
    })

    fig, ax = plt.subplots(figsize=(12.5, 5.6), facecolor="white")
    fig.subplots_adjust(left=0.02, right=0.98, top=0.97, bottom=0.03)
    ax.set_xlim(-2.0, len(FEATURES) - 0.4)
    ax.set_ylim(-0.95, 5.3)
    ax.axis("off")

    for x, label in enumerate(FEATURES):
        ax.text(x, 4.85, label, ha="center", va="center", weight="bold", fontsize=13)

    for index, (study, values) in enumerate(STUDIES):
        y = 3.7 - index
        highlight = study == "This work"
        if highlight:
            ax.add_patch(Rectangle(
                (-0.48, y - 0.39), len(FEATURES) - 0.04, 0.78,
                facecolor="#F0F6FC", edgecolor="#202B38", linewidth=1.3, zorder=0,
            ))
        ax.text(
            -0.68, y, study, ha="right", va="center",
            weight="bold" if highlight else "normal", fontsize=13,
        )
        for x, present in enumerate(values):
            if present:
                ax.scatter(x, y, s=210, color=DOT_COLORS[index][x], edgecolors="none", zorder=2)

    ax.text(-0.48, -0.82, "Filled circle = feature present",
            ha="left", va="center", fontsize=10, color="#485462")
    for extension in ("png", "pdf", "svg"):
        path = args.output_dir / f"literature_comparison.{extension}"
        fig.savefig(path, dpi=300, bbox_inches="tight", pad_inches=0.15, facecolor="white")
        print(path)
    plt.close(fig)


if __name__ == "__main__":
    main()

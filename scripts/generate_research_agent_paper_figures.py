from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "pdf" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT = FontProperties(fname=str(FONT_PATH)) if FONT_PATH.exists() else FontProperties()
FONT_BOLD = FontProperties(fname=str(Path(r"C:\Windows\Fonts\msyhbd.ttc"))) if Path(
    r"C:\Windows\Fonts\msyhbd.ttc"
).exists() else FONT
FONT_EN = FontProperties(family="DejaVu Sans")
FONT_EN_BOLD = FontProperties(family="DejaVu Sans", weight="bold")

mpl.rcParams.update(
    {
        "axes.unicode_minus": False,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "figure.dpi": 180,
        "savefig.dpi": 300,
    }
)

NAVY = "#173F5F"
BLUE = "#20639B"
TEAL = "#3CAEA3"
GOLD = "#F6D55C"
RED = "#ED553B"
LIGHT = "#F3F6F8"
GRAY = "#5C6770"


def add_box(ax, x, y, w, h, text, *, facecolor=LIGHT, edgecolor=NAVY, bold=False):
    box = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.012,rounding_size=0.018",
        linewidth=1.25,
        edgecolor=edgecolor,
        facecolor=facecolor,
    )
    ax.add_patch(box)
    ax.text(
        x + w / 2,
        y + h / 2,
        text,
        ha="center",
        va="center",
        color=NAVY,
        fontsize=9.2,
        fontproperties=FONT_BOLD if bold else FONT,
        linespacing=1.35,
    )
    return box


def add_arrow(ax, start, end, *, color=GRAY, style="-|>"):
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle=style,
            mutation_scale=12,
            linewidth=1.2,
            color=color,
            connectionstyle="arc3,rad=0",
        )
    )


def make_design_figure() -> None:
    fig, ax = plt.subplots(figsize=(11.2, 5.6))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    add_box(ax, 0.03, 0.40, 0.14, 0.20, "冻结输入\n任务、种子、文献与运行约束", facecolor="#E9F1F7", bold=True)
    add_box(ax, 0.22, 0.40, 0.15, 0.20, "共享研究骨干\nResearch Forge + Codex", facecolor="#E8F5F3", bold=True)
    add_arrow(ax, (0.17, 0.50), (0.22, 0.50))

    add_box(ax, 0.42, 0.68, 0.16, 0.16, "基线组\n共享结论生成器", facecolor="#EEF2F5", bold=True)
    add_box(ax, 0.42, 0.17, 0.16, 0.16, "门控组\n共享结论生成器", facecolor="#EEF2F5", bold=True)
    add_arrow(ax, (0.37, 0.50), (0.42, 0.76))
    add_arrow(ax, (0.37, 0.50), (0.42, 0.25))

    add_box(ax, 0.63, 0.68, 0.14, 0.16, "直接交付\n结构化主张注册表", facecolor="#FFF4D6", edgecolor="#B38B18")
    add_arrow(ax, (0.58, 0.76), (0.63, 0.76))

    add_box(ax, 0.61, 0.15, 0.18, 0.20, "主张-证据门控\n拒绝 / 修订一次 / 复核", facecolor="#FDEBE7", edgecolor=RED, bold=True)
    add_arrow(ax, (0.58, 0.25), (0.61, 0.25))

    add_box(ax, 0.83, 0.40, 0.14, 0.20, "盲态保护评估\n结构检查 + 语义判断", facecolor="#E9F1F7", bold=True)
    add_arrow(ax, (0.77, 0.76), (0.87, 0.60))
    add_arrow(ax, (0.79, 0.25), (0.87, 0.40))

    ax.text(
        0.70,
        0.05,
        "预注册双人盲审：已冻结抽样包，但本阶段暂缓执行",
        ha="center",
        va="center",
        fontsize=9.2,
        color=RED,
        fontproperties=FONT_BOLD,
    )
    ax.plot([0.49, 0.92], [0.095, 0.095], color=RED, linewidth=1.0, linestyle=(0, (4, 3)))

    ax.text(
        0.03,
        0.92,
        "唯一实验干预发生在交付前的主张证据路径",
        ha="left",
        va="center",
        fontsize=13,
        color=NAVY,
        fontproperties=FONT_BOLD,
    )
    fig.tight_layout(pad=0.5)
    for ext in ("pdf", "png"):
        fig.savefig(OUT / f"study_design.{ext}", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def make_results_figure() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.8), gridspec_kw={"width_ratios": [1.15, 0.85]})

    labels = ["无支撑主张率", "实验细节错误率", "引文正确率", "证据覆盖率"]
    baseline = [30.5556, 33.3333, 66.6667, 100.0]
    treatment = [8.3333, 5.5556, 88.8889, 100.0]
    y = list(range(len(labels)))
    h = 0.34
    ax = axes[0]
    ax.barh([v + h / 2 for v in y], baseline, height=h, color=GRAY, label="基线组")
    ax.barh([v - h / 2 for v in y], treatment, height=h, color=TEAL, label="门控组")
    ax.set_yticks(y, labels, fontproperties=FONT, fontsize=9.5)
    ax.invert_yaxis()
    ax.set_xlim(0, 108)
    ax.set_xlabel("比例（%）", fontproperties=FONT)
    ax.set_title("A  保护评估的组级指标", loc="left", fontproperties=FONT_BOLD, color=NAVY)
    ax.grid(axis="x", color="#D9E0E5", linewidth=0.7)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, prop=FONT, loc="upper right")
    for i, (b, t) in enumerate(zip(baseline, treatment)):
        ax.text(b + 1.2, i + h / 2, f"{b:.1f}", va="center", fontsize=8.5, color=GRAY, fontproperties=FONT)
        ax.text(t + 1.2, i - h / 2, f"{t:.1f}", va="center", fontsize=8.5, color=NAVY, fontproperties=FONT)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)

    task_labels = ["SICK 分类", "SICK 相似度", "WSC 指代消解", "总体均值"]
    effects = [-0.25, -0.25, -0.1666667, -0.2222222]
    ypos = [3, 2, 1, 0]
    ax = axes[1]
    ax.axvline(0, color=GRAY, linewidth=1.0)
    ax.scatter(effects[:3], ypos[:3], s=62, color=BLUE, zorder=3)
    ax.errorbar(
        effects[3],
        ypos[3],
        xerr=[[effects[3] - (-0.4166667)], [(-0.0833333) - effects[3]]],
        fmt="o",
        color=RED,
        ecolor=RED,
        elinewidth=2.0,
        capsize=4,
        markersize=7,
        zorder=4,
    )
    ax.set_yticks(ypos, task_labels, fontproperties=FONT, fontsize=9.5)
    ax.set_xlim(-0.48, 0.08)
    ax.set_xlabel("门控组 - 基线组（负值有利于门控组）", fontproperties=FONT, fontsize=9)
    ax.set_title("B  无支撑主张率的配对效应", loc="left", fontproperties=FONT_BOLD, color=NAVY)
    ax.grid(axis="x", color="#D9E0E5", linewidth=0.7)
    ax.set_axisbelow(True)
    for x, yy in zip(effects[:3], ypos[:3]):
        ax.text(x - 0.012, yy + 0.24, f"{x:.3f}", ha="center", fontsize=8.3, color=NAVY, fontproperties=FONT)
    ax.text(
        effects[3],
        ypos[3] + 0.28,
        "均值 -0.222\n95% bootstrap 区间 [-0.417, -0.083]",
        ha="center",
        va="bottom",
        fontsize=8.2,
        color=RED,
        fontproperties=FONT,
    )
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)

    fig.tight_layout(w_pad=2.2)
    for ext in ("pdf", "png"):
        fig.savefig(OUT / f"protected_results.{ext}", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def make_design_figure_en() -> None:
    fig, ax = plt.subplots(figsize=(11.2, 5.6))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    def box(x, y, w, h, text, *, facecolor=LIGHT, edgecolor=NAVY, bold=False):
        patch = FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.012,rounding_size=0.018",
            linewidth=1.25,
            edgecolor=edgecolor,
            facecolor=facecolor,
        )
        ax.add_patch(patch)
        ax.text(
            x + w / 2,
            y + h / 2,
            text,
            ha="center",
            va="center",
            color=NAVY,
            fontsize=8.8,
            fontproperties=FONT_EN_BOLD if bold else FONT_EN,
            linespacing=1.35,
        )

    box(0.03, 0.40, 0.14, 0.20, "Frozen inputs\ntasks, seeds, sources,\nand runtime limits", facecolor="#E9F1F7", bold=True)
    box(0.22, 0.40, 0.15, 0.20, "Shared backbone\nResearch Forge + Codex", facecolor="#E8F5F3", bold=True)
    add_arrow(ax, (0.17, 0.50), (0.22, 0.50))

    box(0.42, 0.68, 0.16, 0.16, "Baseline arm\nshared conclusion generator", facecolor="#EEF2F5", bold=True)
    box(0.42, 0.17, 0.16, 0.16, "Gated arm\nshared conclusion generator", facecolor="#EEF2F5", bold=True)
    add_arrow(ax, (0.37, 0.50), (0.42, 0.76))
    add_arrow(ax, (0.37, 0.50), (0.42, 0.25))

    box(0.63, 0.68, 0.14, 0.16, "Direct delivery\nstructured claim registry", facecolor="#FFF4D6", edgecolor="#B38B18")
    add_arrow(ax, (0.58, 0.76), (0.63, 0.76))

    box(0.61, 0.15, 0.18, 0.20, "Claim-evidence gate\nreject / revise once / recheck", facecolor="#FDEBE7", edgecolor=RED, bold=True)
    add_arrow(ax, (0.58, 0.25), (0.61, 0.25))

    box(0.83, 0.40, 0.14, 0.20, "Protected blind evaluation\nstructural + semantic checks", facecolor="#E9F1F7", bold=True)
    add_arrow(ax, (0.77, 0.76), (0.87, 0.60))
    add_arrow(ax, (0.79, 0.25), (0.87, 0.40))

    ax.text(
        0.70,
        0.05,
        "Preregistered two-auditor sample frozen; audit deferred",
        ha="center",
        va="center",
        fontsize=9.0,
        color=RED,
        fontproperties=FONT_EN_BOLD,
    )
    ax.plot([0.49, 0.92], [0.095, 0.095], color=RED, linewidth=1.0, linestyle=(0, (4, 3)))
    ax.text(
        0.03,
        0.92,
        "The only planned intervention is the pre-delivery claim-evidence path",
        ha="left",
        va="center",
        fontsize=12.5,
        color=NAVY,
        fontproperties=FONT_EN_BOLD,
    )
    fig.tight_layout(pad=0.5)
    for ext in ("pdf", "png"):
        fig.savefig(OUT / f"study_design_en.{ext}", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def make_results_figure_en() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.8), gridspec_kw={"width_ratios": [1.15, 0.85]})

    labels = ["Unsupported-claim rate", "Experiment-detail error", "Citation correctness", "Evidence coverage"]
    baseline = [30.5556, 33.3333, 66.6667, 100.0]
    treatment = [8.3333, 5.5556, 88.8889, 100.0]
    y = list(range(len(labels)))
    h = 0.34
    ax = axes[0]
    ax.barh([v + h / 2 for v in y], baseline, height=h, color=GRAY, label="Baseline")
    ax.barh([v - h / 2 for v in y], treatment, height=h, color=TEAL, label="Gated")
    ax.set_yticks(y, labels, fontproperties=FONT_EN, fontsize=9.0)
    ax.invert_yaxis()
    ax.set_xlim(0, 108)
    ax.set_xlabel("Rate (%)", fontproperties=FONT_EN)
    ax.set_title("A  Arm-level protected outcomes", loc="left", fontproperties=FONT_EN_BOLD, color=NAVY)
    ax.grid(axis="x", color="#D9E0E5", linewidth=0.7)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, prop=FONT_EN, loc="upper right")
    for i, (b, t) in enumerate(zip(baseline, treatment)):
        ax.text(b + 1.2, i + h / 2, f"{b:.1f}", va="center", fontsize=8.5, color=GRAY, fontproperties=FONT_EN)
        ax.text(t + 1.2, i - h / 2, f"{t:.1f}", va="center", fontsize=8.5, color=NAVY, fontproperties=FONT_EN)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)

    task_labels = ["SICK classification", "SICK similarity", "WSC coreference", "Overall mean"]
    effects = [-0.25, -0.25, -0.1666667, -0.2222222]
    ypos = [3, 2, 1, 0]
    ax = axes[1]
    ax.axvline(0, color=GRAY, linewidth=1.0)
    ax.scatter(effects[:3], ypos[:3], s=62, color=BLUE, zorder=3)
    ax.errorbar(
        effects[3],
        ypos[3],
        xerr=[[effects[3] - (-0.4166667)], [(-0.0833333) - effects[3]]],
        fmt="o",
        color=RED,
        ecolor=RED,
        elinewidth=2.0,
        capsize=4,
        markersize=7,
        zorder=4,
    )
    ax.set_yticks(ypos, task_labels, fontproperties=FONT_EN, fontsize=9.0)
    ax.set_xlim(-0.48, 0.08)
    ax.set_xlabel("Effect (gated - baseline)", fontproperties=FONT_EN, fontsize=8.4)
    ax.set_title("B  Paired effects on unsupported-claim rate", loc="left", fontproperties=FONT_EN_BOLD, color=NAVY)
    ax.grid(axis="x", color="#D9E0E5", linewidth=0.7)
    ax.set_axisbelow(True)
    for x, yy in zip(effects[:3], ypos[:3]):
        ax.text(x + 0.014, yy, f"{x:.3f}", ha="left", va="center", fontsize=8.3, color=NAVY, fontproperties=FONT_EN)
    ax.text(
        effects[3],
        ypos[3] + 0.28,
        "Mean -0.222\n95% bootstrap interval [-0.417, -0.083]",
        ha="center",
        va="bottom",
        fontsize=8.0,
        color=RED,
        fontproperties=FONT_EN,
    )
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)

    fig.tight_layout(w_pad=2.2)
    for ext in ("pdf", "png"):
        fig.savefig(OUT / f"protected_results_en.{ext}", bbox_inches="tight", facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    make_design_figure()
    make_results_figure()
    make_design_figure_en()
    make_results_figure_en()
    print(OUT)

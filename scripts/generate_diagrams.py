from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
FONT_CANDIDATES = [
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [Path("C:/Windows/Fonts/segoeuib.ttf"), *FONT_CANDIDATES] if bold else FONT_CANDIDATES
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


COLORS = {
    "background": "#F7F8FC",
    "ink": "#172033",
    "muted": "#526078",
    "agent": "#E9E2FF",
    "code": "#DDF3EA",
    "human": "#FFE9C8",
    "line": "#8B96AA",
    "white": "#FFFFFF",
}


def box(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], title: str, body: str, fill: str) -> None:
    draw.rounded_rectangle(rect, radius=18, fill=fill, outline=COLORS["line"], width=2)
    x1, y1, x2, _ = rect
    draw.text((x1 + 18, y1 + 14), title, font=font(22, bold=True), fill=COLORS["ink"])
    draw.multiline_text(
        (x1 + 18, y1 + 50), body, font=font(16), fill=COLORS["muted"], spacing=7
    )


def arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int]) -> None:
    draw.line((*start, *end), fill=COLORS["line"], width=4)
    ex, ey = end
    sx, sy = start
    if abs(ex - sx) >= abs(ey - sy):
        sign = 1 if ex > sx else -1
        points = [(ex, ey), (ex - 12 * sign, ey - 7), (ex - 12 * sign, ey + 7)]
    else:
        sign = 1 if ey > sy else -1
        points = [(ex, ey), (ex - 7, ey - 12 * sign), (ex + 7, ey - 12 * sign)]
    draw.polygon(points, fill=COLORS["line"])


def interactions() -> None:
    image = Image.new("RGB", (1500, 900), COLORS["background"])
    draw = ImageDraw.Draw(image)
    draw.text((60, 42), "Research Forge: ownership boundaries", font=font(34, bold=True), fill=COLORS["ink"])
    draw.text((60, 90), "The model proposes. Deterministic code owns truth and side effects.", font=font(19), fill=COLORS["muted"])

    box(draw, (70, 180, 420, 360), "Human researcher", "Defines intent and constraints\nReviews plan\nFreezes contracts\nConfirms promotion", COLORS["human"])
    box(draw, (575, 150, 925, 390), "Codex (read-only)", "Typed research-plan draft\nOne falsifiable proposal\nParameters or bounded\nfull-file replacements", COLORS["agent"])
    box(draw, (1080, 180, 1430, 360), "Deterministic engine", "State and budget gates\nPath and secret validation\nNo-shell runner\nMetric comparison", COLORS["code"])
    box(draw, (320, 590, 680, 790), "Code lineage", "Canonical experiment\nPer-run workspace copies\nExplicit promotion\nPre-promotion snapshots", COLORS["code"])
    box(draw, (820, 590, 1180, 790), "Scientific evidence", "Frozen contracts\nTrial metrics and logs\nFailures and negative results\nAppend-only ledger", COLORS["code"])

    arrow(draw, (420, 270), (575, 270))
    arrow(draw, (925, 270), (1080, 270))
    arrow(draw, (1255, 360), (1030, 590))
    arrow(draw, (500, 590), (250, 360))
    image.save(DOCS / "agent-interactions.png")


def sequence() -> None:
    image = Image.new("RGB", (1600, 980), COLORS["background"])
    draw = ImageDraw.Draw(image)
    draw.text((60, 42), "Research Forge: verified experiment sequence", font=font(34, bold=True), fill=COLORS["ink"])
    columns = [(180, "Human"), (560, "Agent"), (980, "Engine"), (1400, "Experiment")]
    for x, label in columns:
        draw.text((x - 50, 115), label, font=font(20, bold=True), fill=COLORS["ink"])
        draw.line((x, 155, x, 920), fill="#C8CEDA", width=2)

    events = [
        (200, 180, 560, "idea + constraints"),
        (560, 255, 180, "typed plan draft"),
        (180, 330, 980, "freeze plan + evaluator"),
        (980, 405, 1400, "baseline in run copy"),
        (1400, 480, 980, "metrics.json + logs"),
        (980, 555, 560, "verified evidence context"),
        (560, 630, 980, "bounded proposal"),
        (980, 705, 1400, "validated candidate copy"),
        (1400, 780, 980, "fixed metric result"),
        (980, 855, 180, "review; confirm promotion"),
    ]
    for start, y, end, label in events:
        arrow(draw, (start, y), (end, y))
        left = min(start, end) + 15
        draw.rectangle((left, y - 26, left + 300, y - 3), fill=COLORS["background"])
        draw.text((left, y - 26), label, font=font(15), fill=COLORS["muted"])
    image.save(DOCS / "agent-sequence.png")


if __name__ == "__main__":
    DOCS.mkdir(exist_ok=True)
    interactions()
    sequence()

from __future__ import annotations

from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "用户手册素材"
OUTPUT = ROOT / "Research Forge 用户使用手册（截图版）.pdf"

ORANGE = colors.HexColor("#E85D0F")
BLUE = colors.HexColor("#1677FF")
INK = colors.HexColor("#18181B")
MUTED = colors.HexColor("#6B7280")
LIGHT = colors.HexColor("#F4F5F7")
GREEN = colors.HexColor("#168A50")


class StepBadge(Flowable):
    def __init__(self, number: str, size: float = 14 * mm):
        super().__init__()
        self.number = number
        self.width = size
        self.height = size

    def draw(self):
        radius = self.width / 2
        self.canv.setFillColor(ORANGE)
        self.canv.circle(radius, radius, radius, stroke=0, fill=1)
        self.canv.setFillColor(colors.white)
        self.canv.setFont("MicrosoftYaHei-Bold", 13)
        self.canv.drawCentredString(radius, radius - 4, self.number)


def register_fonts() -> None:
    pdfmetrics.registerFont(
        TTFont("MicrosoftYaHei", r"C:\Windows\Fonts\msyh.ttc", subfontIndex=0)
    )
    pdfmetrics.registerFont(
        TTFont("MicrosoftYaHei-Bold", r"C:\Windows\Fonts\msyhbd.ttc", subfontIndex=0)
    )


def fit_image(path: Path, max_width: float, max_height: float) -> Image:
    with PILImage.open(path) as source:
        width, height = source.size
    scale = min(max_width / width, max_height / height)
    return Image(str(path), width=width * scale, height=height * scale)


def footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#E5E7EB"))
    canvas.line(14 * mm, 10 * mm, landscape(A4)[0] - 14 * mm, 10 * mm)
    canvas.setFont("MicrosoftYaHei", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(14 * mm, 6 * mm, "Research Forge · 用户使用手册（截图版）")
    canvas.drawRightString(
        landscape(A4)[0] - 14 * mm, 6 * mm, f"第 {doc.page} 页"
    )
    canvas.restoreState()


def build_styles():
    styles = getSampleStyleSheet()
    return {
        "cover_kicker": ParagraphStyle(
            "cover_kicker",
            parent=styles["Normal"],
            fontName="MicrosoftYaHei-Bold",
            fontSize=12,
            leading=18,
            textColor=ORANGE,
            spaceAfter=8,
        ),
        "cover_title": ParagraphStyle(
            "cover_title",
            parent=styles["Title"],
            fontName="MicrosoftYaHei-Bold",
            fontSize=30,
            leading=42,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=10,
        ),
        "cover_subtitle": ParagraphStyle(
            "cover_subtitle",
            parent=styles["Normal"],
            fontName="MicrosoftYaHei",
            fontSize=13,
            leading=22,
            textColor=MUTED,
            spaceAfter=20,
        ),
        "title": ParagraphStyle(
            "title",
            parent=styles["Heading1"],
            fontName="MicrosoftYaHei-Bold",
            fontSize=19,
            leading=28,
            textColor=INK,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=styles["Normal"],
            fontName="MicrosoftYaHei",
            fontSize=9.5,
            leading=15,
            textColor=MUTED,
        ),
        "label": ParagraphStyle(
            "label",
            parent=styles["Normal"],
            fontName="MicrosoftYaHei-Bold",
            fontSize=9,
            leading=14,
            textColor=ORANGE,
            spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "body",
            parent=styles["Normal"],
            fontName="MicrosoftYaHei",
            fontSize=9.2,
            leading=15,
            textColor=INK,
        ),
        "small": ParagraphStyle(
            "small",
            parent=styles["Normal"],
            fontName="MicrosoftYaHei",
            fontSize=8,
            leading=13,
            textColor=MUTED,
        ),
        "center": ParagraphStyle(
            "center",
            parent=styles["Normal"],
            fontName="MicrosoftYaHei-Bold",
            fontSize=10,
            leading=15,
            alignment=TA_CENTER,
            textColor=INK,
        ),
    }


def info_box(styles, action: str, visible: str, logic: str):
    cells = [
        [
            Paragraph("用户要做什么", styles["label"]),
            Paragraph("页面会看到什么", styles["label"]),
            Paragraph("系统业务逻辑", styles["label"]),
        ],
        [
            Paragraph(action, styles["body"]),
            Paragraph(visible, styles["body"]),
            Paragraph(logic, styles["body"]),
        ],
    ]
    table = Table(cells, colWidths=[86 * mm, 86 * mm, 86 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#E5E7EB")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E5E7EB")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def step_page(
    story,
    styles,
    number: str,
    title: str,
    summary: str,
    screenshot: str,
    action: str,
    visible: str,
    logic: str,
):
    header = Table(
        [
            [
                StepBadge(number),
                [
                    Paragraph(title, styles["title"]),
                    Paragraph(summary, styles["subtitle"]),
                ],
            ]
        ],
        colWidths=[18 * mm, 250 * mm],
        hAlign="LEFT",
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.extend(
        [
            header,
            Spacer(1, 5 * mm),
            fit_image(
                ASSET_DIR / screenshot,
                max_width=264 * mm,
                max_height=118 * mm,
            ),
            Spacer(1, 4 * mm),
            info_box(styles, action, visible, logic),
            PageBreak(),
        ]
    )


def build() -> Path:
    register_fonts()
    styles = build_styles()
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=landscape(A4),
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=14 * mm,
        title="Research Forge 用户使用手册（截图版）",
        author="Research Forge",
        subject="已有项目研究闭环的界面操作说明",
    )
    story = []

    story.extend(
        [
            Spacer(1, 16 * mm),
            Paragraph("RESEARCH FORGE · PRODUCT GUIDE", styles["cover_kicker"]),
            Paragraph("从已有项目到持续补证据<br/>用户使用手册", styles["cover_title"]),
            Paragraph(
                "按真实界面整理的截图版说明。重点讲清楚：用户每一步做什么、页面反馈什么、系统如何决定下一步。",
                styles["cover_subtitle"],
            ),
            Spacer(1, 6 * mm),
        ]
    )
    flow_items = [
        ("1", "选择项目", "指定本地项目或文本资料库"),
        ("2", "扫描材料", "只读识别代码、数据、实验和结论"),
        ("3", "选择选题", "从可以独立验证的问题中选择"),
        ("4", "冻结契约", "确认问题、范围、基线和证据边界"),
        ("5", "执行研究", "显示具体工作、日志和暂停控制"),
        ("6", "研究判定", "支持、反驳或证据仍不足"),
        ("7", "持续补证据", "用户确认任务后创建后继运行"),
        ("8", "缺条件则暂停", "补数据、环境、配置、API 或权限后恢复"),
    ]
    flow_cells = []
    for index in range(0, len(flow_items), 4):
        row = []
        for number, title, detail in flow_items[index : index + 4]:
            row.append(
                [
                    Paragraph(
                        f"<font color='#E85D0F'>{number}</font>　{title}",
                        styles["center"],
                    ),
                    Spacer(1, 2 * mm),
                    Paragraph(detail, styles["small"]),
                ]
            )
        flow_cells.append(row)
    flow = Table(flow_cells, colWidths=[65 * mm] * 4, rowHeights=[37 * mm] * 2)
    flow.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#E5E7EB")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.extend(
        [
            flow,
            Spacer(1, 9 * mm),
            Paragraph(
                "<b>核心原则：</b>“证据不足”不是结束状态，而是进入下一轮补证据计划；系统不会无限自动循环，每一轮都由用户勾选并确认。",
                styles["body"],
            ),
            PageBreak(),
        ]
    )

    step_page(
        story,
        styles,
        "1",
        "选择研究起点",
        "已有代码、数据或结论时，默认从“检查已有项目”进入。",
        "01-首页-选择入口.png",
        "确认选中“检查已有项目”，填写或选择项目文件夹，然后点击“扫描项目”。如果只有一个问题而没有项目材料，改选“验证一个想法”。",
        "项目路径、只读分析说明，以及“扫描项目”主按钮。页面明确提示源项目不会被修改。",
        "系统只读取源项目；运行记录、快照和证据包写入独立目录。外部服务不会静默调用。",
    )
    step_page(
        story,
        styles,
        "2",
        "扫描项目时查看具体工作",
        "执行页不是只显示进度条，而是说明正在做什么、为什么做、会得到什么。",
        "02-执行中-具体工作与暂停.png",
        "等待扫描完成；需要停下时点击“暂停研究”。暂停会在安全检查点生效。",
        "四阶段研究闭环、当前工作摘要、最近执行记录、任务编号与更新时间。",
        "执行过程持续写入可审计事件，不展示模型内部思维。暂停不会删除已经完成的产物。",
    )
    step_page(
        story,
        styles,
        "3",
        "从候选问题中选择选题",
        "扫描结束后展示的是可独立验证的研究问题，而不是工程任务名或泛泛方向。",
        "03-扫描完成-选择候选选题.png",
        "比较每个候选题的核心问题、候选贡献、研究范围和证据基础；选中一个后点击“确认候选选题”。",
        "推荐标记、外部热门信号状态、完整证据链数量，以及现有证据的限制。",
        "公众号与学术热度只帮助发现题材，不计入科学证据。工程冻结任务会被合并进真正的研究问题。",
    )
    step_page(
        story,
        styles,
        "4",
        "确认并冻结研究契约",
        "在执行前，先把“到底判断什么”说清楚，避免看完结果再改口径。",
        "04-确认研究契约.png",
        "核对研究问题、贡献、范围、数据边界、证据类型和禁止升级规则；无误后点击“确认并开始验证”。",
        "契约版本、冻结的选题与证据边界，以及确认后会发生的四项工作。",
        "更换样本、指标、基线或判定门槛会生成新契约版本；后续论文不能改写实验判定。",
    )
    step_page(
        story,
        styles,
        "5",
        "执行四阶段研究闭环",
        "验证过程持续显示当前材料、目标产物、下一步和可审计日志。",
        "05-研究执行-阶段与日志.png",
        "观察当前阶段和最近日志；必要时安全暂停。任务在后台执行，用户可以通过顶部任务入口稍后回来。",
        "发现与收敛、协议与基线、实验与判定、论文与审计四阶段，以及当前所处的具体工作。",
        "系统先绑定协议和实验输出，再判定结论；只有证据允许时才进入论文生产。",
    )
    step_page(
        story,
        styles,
        "6",
        "通过任务列表恢复研究",
        "所有研究任务持久化保存，刷新或重启后仍可从顶部任务入口恢复。",
        "06-研究任务与历史记录.png",
        "点击顶部“研究任务”，选择已完成、执行中或需要处理的任务继续查看。",
        "每项任务的标题、操作类型和状态，包括“已完成”与“需要你处理”。",
        "任务、契约、运行和补证据计划分别保存；切换页面不会丢失研究谱系。",
    )
    step_page(
        story,
        styles,
        "7",
        "证据不足时选择补证据任务",
        "mixed、inconclusive 或 unverifiable 不会结束研究，而是进入补证据工作区。",
        "07-证据不足-选择补证据任务.png",
        "查看每项任务的缺口、方法、预期产物、成本和条件；勾选本轮要做的任务，然后点击“确认本轮计划”。",
        "独立评价、前瞻盲测、文献核验、重新扫描与判定等任务，以及原判定不被覆盖的说明。",
        "系统只执行用户确认的任务。沿用契约也会创建后继运行；改变设计则必须先确认新契约版本。",
    )
    step_page(
        story,
        styles,
        "8",
        "缺少必要条件时安全暂停",
        "无法自动实验通常不是流程结束，而是缺少项目声明的数据、环境、配置、API 或权限。",
        "08-缺少条件-安全暂停并提醒用户.png",
        "按页面要求提供实验配置文件或其他条件，再点击“继续并执行实验”；暂时不做时可选择“本轮跳过”。",
        "缺失条件的类型、具体原因、要填写的输入，以及已经保留的任务状态和运行记录。",
        "系统不会任意运行项目脚本。只有安全、已声明的实验命令才会自动执行；补充条件后从暂停点恢复，并实际验证新产物。",
    )

    story.extend(
        [
            Spacer(1, 10 * mm),
            Paragraph("研究判定与闭环规则", styles["cover_kicker"]),
            Paragraph("系统下一步怎么决定？", styles["cover_title"]),
            Paragraph(
                "研究判定不是看文章写得像不像，而是检查冻结契约、机器可读实验输出、证据门和文献门。",
                styles["cover_subtitle"],
            ),
        ]
    )
    rule_rows = [
        [
            Paragraph("判定结果", styles["label"]),
            Paragraph("默认下一步", styles["label"]),
            Paragraph("是否覆盖历史", styles["label"]),
        ],
        [
            Paragraph("supported / refuted", styles["body"]),
            Paragraph("进入论文资格审查；通过后才生产论文。", styles["body"]),
            Paragraph("否，原运行与证书保持不变。", styles["body"]),
        ],
        [
            Paragraph("mixed / inconclusive / unverifiable", styles["body"]),
            Paragraph("诊断证据缺口，生成补证据计划，等待用户勾选确认。", styles["body"]),
            Paragraph("否，新证据进入独立的后继运行。", styles["body"]),
        ],
        [
            Paragraph("缺数据 / 环境 / 配置 / API / 权限", styles["body"]),
            Paragraph("安全暂停并提出结构化要求；条件补齐后恢复。", styles["body"]),
            Paragraph("否，已完成产物和失败原因均保留。", styles["body"]),
        ],
        [
            Paragraph("任务改变研究设计", styles["body"]),
            Paragraph("生成契约 vNext 草稿，用户确认后才能执行。", styles["body"]),
            Paragraph("否，旧契约和旧判定继续可追溯。", styles["body"]),
        ],
    ]
    rules = Table(rule_rows, colWidths=[58 * mm, 130 * mm, 72 * mm])
    rules.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FFF2E8")),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#E5E7EB")),
                ("INNERGRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#E5E7EB")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.extend(
        [
            rules,
            Spacer(1, 8 * mm),
            Paragraph(
                "<b>一句话理解：</b>用户不是“一键生成论文”，而是在每个关键决策点确认研究边界；系统负责执行、记录、判定和持续补证据，直到证据足够支持明确结论，或用户主动结束归档。",
                styles["body"],
            ),
        ]
    )

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return OUTPUT


if __name__ == "__main__":
    print(build())

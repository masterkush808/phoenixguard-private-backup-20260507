from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path
import re
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "architecture" / "PHOENIXGUARD_V3_MARKET_STUDY_BLUEPRINT.md"
OUTPUT = ROOT / "reports" / "PhoenixGuard_V3_Deep_Market_Study_Blueprint_2026-07-24.pdf"

NAVY = colors.HexColor("#10233F")
BLUE = colors.HexColor("#1E5AA8")
TEAL = colors.HexColor("#0B817D")
GOLD = colors.HexColor("#C88D1A")
INK = colors.HexColor("#172233")
MUTED = colors.HexColor("#536577")
PALE_BLUE = colors.HexColor("#EDF3FA")
PALE_TEAL = colors.HexColor("#E8F5F3")
PALE_GOLD = colors.HexColor("#FFF5E4")
LIGHT_LINE = colors.HexColor("#CAD6E3")


def _register_fonts() -> tuple[str, str, str]:
    regular = Path("C:/Windows/Fonts/arial.ttf")
    bold = Path("C:/Windows/Fonts/arialbd.ttf")
    mono = Path("C:/Windows/Fonts/consola.ttf")
    if regular.exists() and bold.exists():
        pdfmetrics.registerFont(TTFont("PG-Regular", str(regular)))
        pdfmetrics.registerFont(TTFont("PG-Bold", str(bold)))
        if mono.exists():
            pdfmetrics.registerFont(TTFont("PG-Mono", str(mono)))
            return "PG-Regular", "PG-Bold", "PG-Mono"
        return "PG-Regular", "PG-Bold", "Courier"
    return "Helvetica", "Helvetica-Bold", "Courier"


FONT, FONT_BOLD, FONT_MONO = _register_fonts()
BASE = getSampleStyleSheet()
STYLES = {
    "cover_title": ParagraphStyle(
        "cover_title",
        parent=BASE["Title"],
        fontName=FONT_BOLD,
        fontSize=27,
        leading=32,
        textColor=colors.white,
        alignment=TA_LEFT,
        spaceAfter=14,
    ),
    "cover_subtitle": ParagraphStyle(
        "cover_subtitle",
        parent=BASE["BodyText"],
        fontName=FONT,
        fontSize=11.5,
        leading=17,
        textColor=colors.HexColor("#DCE9F7"),
    ),
    "h1": ParagraphStyle(
        "BlueprintH1",
        parent=BASE["Heading1"],
        fontName=FONT_BOLD,
        fontSize=18,
        leading=23,
        textColor=NAVY,
        spaceBefore=14,
        spaceAfter=8,
        keepWithNext=True,
    ),
    "h2": ParagraphStyle(
        "BlueprintH2",
        parent=BASE["Heading2"],
        fontName=FONT_BOLD,
        fontSize=13.2,
        leading=17,
        textColor=BLUE,
        spaceBefore=11,
        spaceAfter=6,
        keepWithNext=True,
    ),
    "h3": ParagraphStyle(
        "BlueprintH3",
        parent=BASE["Heading3"],
        fontName=FONT_BOLD,
        fontSize=10.7,
        leading=14,
        textColor=TEAL,
        spaceBefore=8,
        spaceAfter=4,
        keepWithNext=True,
    ),
    "body": ParagraphStyle(
        "BlueprintBody",
        parent=BASE["BodyText"],
        fontName=FONT,
        fontSize=8.7,
        leading=12.8,
        textColor=INK,
        spaceAfter=5,
    ),
    "small": ParagraphStyle(
        "BlueprintSmall",
        parent=BASE["BodyText"],
        fontName=FONT,
        fontSize=7.4,
        leading=10,
        textColor=MUTED,
    ),
    "code": ParagraphStyle(
        "BlueprintCode",
        parent=BASE["Code"],
        fontName=FONT_MONO,
        fontSize=6.5,
        leading=8.4,
        textColor=INK,
        leftIndent=7,
        rightIndent=7,
        spaceBefore=3,
        spaceAfter=3,
    ),
    "table": ParagraphStyle(
        "BlueprintTable",
        parent=BASE["BodyText"],
        fontName=FONT,
        fontSize=6.8,
        leading=9.1,
        textColor=INK,
    ),
    "table_head": ParagraphStyle(
        "BlueprintTableHead",
        parent=BASE["BodyText"],
        fontName=FONT_BOLD,
        fontSize=6.9,
        leading=9.2,
        textColor=colors.white,
    ),
    "callout": ParagraphStyle(
        "BlueprintCallout",
        parent=BASE["BodyText"],
        fontName=FONT,
        fontSize=8.3,
        leading=12,
        textColor=NAVY,
    ),
    "toc_h": ParagraphStyle(
        "TOCHeading",
        parent=BASE["Heading1"],
        fontName=FONT_BOLD,
        fontSize=20,
        leading=24,
        textColor=NAVY,
        spaceAfter=12,
    ),
}


def _inline(text: str) -> str:
    value = escape(text.strip())
    value = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: (
            f'<link href="{match.group(2)}" color="#1E5AA8">'
            f"{match.group(1)}</link>"
        ),
        value,
    )
    value = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", value)
    value = re.sub(
        r"`([^`]+)`",
        lambda match: f'<font name="{FONT_MONO}">{match.group(1)}</font>',
        value,
    )
    return value


def _paragraph(text: str, style: str = "body") -> Paragraph:
    return Paragraph(_inline(text), STYLES[style])


def _callout(text: str, *, color: colors.Color = TEAL) -> Table:
    background = PALE_TEAL if color == TEAL else PALE_GOLD
    item = Table(
        [[Paragraph(_inline(text), STYLES["callout"])]],
        colWidths=[17.1 * cm],
        hAlign="LEFT",
    )
    item.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), 0.65, color),
                ("LINEBEFORE", (0, 0), (0, -1), 4, color),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return item


def _architecture_overview() -> list[object]:
    box_style = ParagraphStyle(
        "ArchBox",
        parent=STYLES["body"],
        fontName=FONT_BOLD,
        fontSize=7.6,
        leading=10,
        alignment=TA_CENTER,
        textColor=NAVY,
        spaceAfter=0,
    )

    def box(label: str, background: colors.Color) -> Table:
        item = Table([[Paragraph(label, box_style)]], colWidths=[3.1 * cm], rowHeights=[1.25 * cm])
        item.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), background),
                    ("BOX", (0, 0), (-1, -1), 0.7, LIGHT_LINE),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        return item

    arrow = Paragraph("&#8594;", ParagraphStyle("Arrow", parent=box_style, fontSize=14, textColor=BLUE))
    row1 = Table(
        [[
            box("Locked broker<br/>frame", PALE_BLUE),
            arrow,
            box("Proven closed<br/>candle identity", PALE_BLUE),
            arrow,
            box("Candle micro<br/>intelligence", PALE_TEAL),
            arrow,
            box("Swing / rest and<br/>regression study", PALE_TEAL),
        ]],
        colWidths=[3.1 * cm, 0.55 * cm, 3.1 * cm, 0.55 * cm, 3.1 * cm, 0.55 * cm, 3.1 * cm],
        hAlign="LEFT",
    )
    row1.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    row2 = Table(
        [[
            box("Pair DNA<br/>lifelong memory", PALE_GOLD),
            arrow,
            box("Historical sequence<br/>similarity graph", PALE_GOLD),
            arrow,
            box("PG_MARKET_<br/>STUDY_V3", PALE_TEAL),
            arrow,
            box("Major / inner /<br/>directional UI", PALE_BLUE),
        ]],
        colWidths=[3.1 * cm, 0.55 * cm, 3.1 * cm, 0.55 * cm, 3.1 * cm, 0.55 * cm, 3.1 * cm],
        hAlign="LEFT",
    )
    row2.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return [
        _paragraph("Architecture at a glance", "h1"),
        row1,
        Spacer(1, 0.25 * cm),
        row2,
        Spacer(1, 0.2 * cm),
        _callout(
            "Execution permission remains a separate, independently validated contract. "
            "A BUY or SELL study can never grant entry permission by itself.",
            color=GOLD,
        ),
        PageBreak(),
    ]


def _markdown_table(lines: list[str]) -> Table:
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in lines
    ]
    header = rows[0]
    body = rows[2:]
    columns = max(1, len(header))
    widths = [17.1 * cm / columns] * columns
    data = [[Paragraph(_inline(cell), STYLES["table_head"]) for cell in header]]
    for row in body:
        padded = (row + [""] * columns)[:columns]
        data.append([Paragraph(_inline(cell), STYLES["table"]) for cell in padded])
    item = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1)
    item.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE_BLUE]),
                ("GRID", (0, 0), (-1, -1), 0.3, LIGHT_LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return item


def _list_flow(items: Iterable[str], *, ordered: bool) -> ListFlowable:
    flow_items = [
        ListItem(
            Paragraph(_inline(item), STYLES["body"]),
            leftIndent=13,
        )
        for item in items
    ]
    options = {
        "bulletType": "1" if ordered else "bullet",
        "leftIndent": 18,
        "bulletFontName": FONT,
        "bulletFontSize": 7.8,
        "spaceAfter": 5,
    }
    if ordered:
        options["start"] = "1"
    return ListFlowable(flow_items, **options)


def _markdown_story(text: str) -> list[object]:
    lines = text.splitlines()
    story: list[object] = []
    paragraph_lines: list[str] = []
    list_lines: list[str] = []
    list_ordered = False

    def flush_paragraph() -> None:
        if paragraph_lines:
            story.append(_paragraph(" ".join(part.strip() for part in paragraph_lines)))
            paragraph_lines.clear()

    def flush_list() -> None:
        nonlocal list_ordered
        if list_lines:
            story.append(_list_flow(list_lines, ordered=list_ordered))
            list_lines.clear()
        list_ordered = False

    index = 0
    in_code = False
    code_language = ""
    code_lines: list[str] = []
    skipped_document_title = False
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if in_code:
            if stripped.startswith("```"):
                code_text = "\n".join(code_lines)
                label = f"{code_language.upper()} DIAGRAM / CONTRACT" if code_language else "CONTRACT"
                code_table = Table(
                    [[
                        Paragraph(label, STYLES["small"]),
                    ], [
                        Preformatted(code_text, STYLES["code"]),
                    ]],
                    colWidths=[17.1 * cm],
                    hAlign="LEFT",
                )
                code_table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDE8F4")),
                            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F5F8FB")),
                            ("BOX", (0, 0), (-1, -1), 0.45, LIGHT_LINE),
                            ("LEFTPADDING", (0, 0), (-1, -1), 6),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ]
                    )
                )
                story.extend([code_table, Spacer(1, 0.12 * cm)])
                in_code = False
                code_language = ""
                code_lines.clear()
            else:
                code_lines.append(line.rstrip())
            index += 1
            continue

        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            in_code = True
            code_language = stripped[3:].strip()
            index += 1
            continue
        if stripped.startswith("|") and index + 1 < len(lines):
            separator = lines[index + 1].strip()
            if separator.startswith("|") and re.fullmatch(r"[|:\-\s]+", separator):
                flush_paragraph()
                flush_list()
                table_lines = [line, lines[index + 1]]
                index += 2
                while index < len(lines) and lines[index].strip().startswith("|"):
                    table_lines.append(lines[index])
                    index += 1
                story.extend([_markdown_table(table_lines), Spacer(1, 0.15 * cm)])
                continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            if level == 1 and not skipped_document_title:
                skipped_document_title = True
            else:
                story.append(_paragraph(heading.group(2), f"h{level}"))
            index += 1
            continue
        unordered = re.match(r"^[-*]\s+(.+)$", stripped)
        ordered = re.match(r"^\d+\.\s+(.+)$", stripped)
        if unordered or ordered:
            flush_paragraph()
            current_ordered = ordered is not None
            if list_lines and current_ordered != list_ordered:
                flush_list()
            list_ordered = current_ordered
            list_lines.append((ordered or unordered).group(1))
            index += 1
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            story.append(_callout(stripped.lstrip("> ")))
            index += 1
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
            index += 1
            continue
        if list_lines:
            if line.startswith((" ", "\t")):
                list_lines[-1] = f"{list_lines[-1]} {stripped}"
                index += 1
                continue
            flush_list()
        paragraph_lines.append(line)
        index += 1

    flush_paragraph()
    flush_list()
    if code_lines:
        story.append(Preformatted("\n".join(code_lines), STYLES["code"]))
    return story


class BlueprintDocTemplate(BaseDocTemplate):
    def __init__(self, filename: str) -> None:
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=1.75 * cm,
            rightMargin=1.75 * cm,
            topMargin=1.7 * cm,
            bottomMargin=1.55 * cm,
            title="PhoenixGuard V3 Deep Market Study Blueprint",
            author="PhoenixGuard engineering",
            subject="V3 candle intelligence, Pair DNA, similarity, regression, and execution boundary",
        )
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="blueprint",
        )
        self.addPageTemplates(
            [PageTemplate(id="Blueprint", frames=[frame], onPage=self._page)]
        )
        self._heading_counter = 0

    def beforeDocument(self) -> None:
        self._heading_counter = 0

    def _page(self, canvas, doc) -> None:  # type: ignore[no-untyped-def]
        page = canvas.getPageNumber()
        if page > 1:
            canvas.saveState()
            canvas.setStrokeColor(LIGHT_LINE)
            canvas.setLineWidth(0.35)
            canvas.line(1.75 * cm, 28.35 * cm, 19.25 * cm, 28.35 * cm)
            canvas.setFont(FONT_BOLD, 7.2)
            canvas.setFillColor(NAVY)
            canvas.drawString(1.75 * cm, 28.55 * cm, "PHOENIXGUARD V3 MARKET STUDY BLUEPRINT")
            canvas.setFont(FONT, 7)
            canvas.setFillColor(MUTED)
            canvas.drawRightString(19.25 * cm, 1.0 * cm, f"Page {page}")
            canvas.drawString(1.75 * cm, 1.0 * cm, "Study evidence is not execution permission")
            canvas.restoreState()

    def afterFlowable(self, flowable) -> None:  # type: ignore[no-untyped-def]
        if not isinstance(flowable, Paragraph):
            return
        style = flowable.style.name
        if style not in {"BlueprintH1", "BlueprintH2", "BlueprintH3"}:
            return
        level = {"BlueprintH1": 0, "BlueprintH2": 1, "BlueprintH3": 2}[style]
        self._heading_counter += 1
        key = f"heading-{self._heading_counter}"
        text = flowable.getPlainText()
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(text, key, level=level, closed=False)
        self.notify("TOCEntry", (level, text, self.page, key))


def _cover() -> list[object]:
    title_box = Table(
        [[
            Paragraph("PHOENIXGUARD V3", STYLES["cover_subtitle"]),
        ], [
            Paragraph("Deep Market Study<br/>Architecture Blueprint", STYLES["cover_title"]),
        ], [
            Paragraph(
                "Candlestick-by-candlestick intelligence, major and inner regression, "
                "swing/rest behavior, per-pair lifelong memory, historical similarity, "
                "session history, frontend logic, and the independent execution boundary.",
                STYLES["cover_subtitle"],
            ),
        ]],
        colWidths=[17.1 * cm],
        rowHeights=[0.8 * cm, 3.5 * cm, 3.1 * cm],
        hAlign="LEFT",
    )
    title_box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 18),
                ("RIGHTPADDING", (0, 0), (-1, -1), 18),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LINEBELOW", (0, 0), (-1, 0), 1.2, TEAL),
            ]
        )
    )
    metadata = Table(
        [
            [Paragraph("VERSION", STYLES["small"]), _paragraph("V3 only - no V4", "body")],
            [Paragraph("STATUS", STYLES["small"]), _paragraph("Implemented architecture and operating contract", "body")],
            [Paragraph("UPDATED", STYLES["small"]), _paragraph(date(2026, 7, 24).isoformat(), "body")],
            [Paragraph("SOURCE", STYLES["small"]), _paragraph(str(SOURCE.relative_to(ROOT)), "body")],
        ],
        colWidths=[2.2 * cm, 14.9 * cm],
        hAlign="LEFT",
    )
    metadata.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.35, LIGHT_LINE),
                ("BACKGROUND", (0, 0), (0, -1), PALE_BLUE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return [
        Spacer(1, 1.2 * cm),
        title_box,
        Spacer(1, 1.0 * cm),
        _callout(
            "Core doctrine: observation is not execution. The study describes the market; "
            "the independent permission and packet-validation stack decides whether an entry is allowed."
        ),
        Spacer(1, 0.8 * cm),
        metadata,
        Spacer(1, 1.0 * cm),
        _paragraph(
            "Engineering blueprint - not financial advice, a performance guarantee, or an instruction to place a trade.",
            "small",
        ),
        PageBreak(),
    ]


def build() -> Path:
    if not SOURCE.exists():
        raise FileNotFoundError(SOURCE)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            "TOC1",
            parent=STYLES["body"],
            fontName=FONT_BOLD,
            fontSize=9.2,
            leading=13,
            leftIndent=0,
            firstLineIndent=0,
            textColor=NAVY,
            spaceBefore=3,
        ),
        ParagraphStyle(
            "TOC2",
            parent=STYLES["body"],
            fontName=FONT,
            fontSize=8.2,
            leading=11.5,
            leftIndent=14,
            firstLineIndent=0,
            textColor=BLUE,
        ),
        ParagraphStyle(
            "TOC3",
            parent=STYLES["small"],
            leftIndent=28,
            firstLineIndent=0,
        ),
    ]
    story: list[object] = _cover()
    story.extend(
        [
            Paragraph("Contents", STYLES["toc_h"]),
            toc,
            PageBreak(),
            *_architecture_overview(),
            *_markdown_story(SOURCE.read_text(encoding="utf-8")),
        ]
    )
    document = BlueprintDocTemplate(str(OUTPUT))
    document.multiBuild(story)
    return OUTPUT


if __name__ == "__main__":
    print(build())

from __future__ import annotations

import html
import importlib.util
import re
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
MARKDOWN_PATH = ROOT / "docs" / "architecture" / "PhoenixGuard_System_Blueprint.md"
PDF_PATH = ROOT / "docs" / "architecture" / "PhoenixGuard_Architecture.pdf"


def _require_reportlab() -> None:
    if importlib.util.find_spec("reportlab") is None:  # pragma: no cover - missing env deps
        raise SystemExit(
            "Missing dependency: reportlab. Install project dependencies with "
            r"`.\.venv\Scripts\python.exe -m pip install -r requirements.txt`, or install reportlab directly."
        )


def _inline_markup(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r'<font name="Courier">\1</font>', escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    return escaped


def _clean_heading(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


def _table_block(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    header = [part.strip() for part in lines[0].strip().strip("|").split("|")]
    rows: list[list[str]] = []
    for line in lines[2:]:
        cells = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(cells) < len(header):
            cells.extend([""] * (len(header) - len(cells)))
        rows.append(cells[: len(header)])
    return header, rows


def _is_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    current = lines[index].strip()
    separator = lines[index + 1].strip()
    return (
        current.startswith("|")
        and current.endswith("|")
        and separator.startswith("|")
        and separator.endswith("|")
        and set(separator.replace("|", "").replace(":", "").replace("-", "").strip()) == set()
        and "-" in separator
    )


def _consume_until_blank(lines: list[str], index: int) -> tuple[list[str], int]:
    block: list[str] = []
    while index < len(lines) and lines[index].strip():
        block.append(lines[index])
        index += 1
    return block, index


def _extract_title(lines: Iterable[str]) -> str:
    for line in lines:
        heading = _clean_heading(line)
        if heading and heading[0] == 1:
            return heading[1]
    return "PhoenixGuard System Blueprint"


def _extract_toc(lines: Iterable[str]) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    for line in lines:
        heading = _clean_heading(line)
        if heading and 2 <= heading[0] <= 3:
            headings.append(heading)
    return headings


def _build_pdf(markdown_text: str) -> None:
    _require_reportlab()

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import (
        Flowable,
        ListFlowable,
        PageBreak,
        Paragraph,
        Preformatted,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = markdown_text.splitlines()
    title = _extract_title(lines)
    toc = _extract_toc(lines)

    doc = SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=letter,
        rightMargin=0.62 * inch,
        leftMargin=0.62 * inch,
        topMargin=0.68 * inch,
        bottomMargin=0.58 * inch,
        title=title,
        author="PhoenixGuard Project",
        subject="End-to-end system architecture blueprint",
    )

    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "BlueprintTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=24,
            leading=29,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#111827"),
            spaceAfter=18,
        ),
        "subtitle": ParagraphStyle(
            "BlueprintSubtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=11.5,
            leading=15,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#374151"),
            spaceAfter=8,
        ),
        "h1": ParagraphStyle(
            "BlueprintH1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#0f172a"),
            spaceBefore=13,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "BlueprintH2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            textColor=colors.HexColor("#1f2937"),
            spaceBefore=9,
            spaceAfter=6,
        ),
        "h3": ParagraphStyle(
            "BlueprintH3",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=12.2,
            leading=15,
            textColor=colors.HexColor("#334155"),
            spaceBefore=7,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "BlueprintBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.6,
            leading=13.2,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#111827"),
            spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "BlueprintBullet",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.3,
            leading=12.6,
            textColor=colors.HexColor("#111827"),
            leftIndent=12,
            firstLineIndent=0,
            spaceAfter=2.5,
        ),
        "code": ParagraphStyle(
            "BlueprintCode",
            parent=base["Code"],
            fontName="Courier",
            fontSize=7.2,
            leading=8.4,
            leftIndent=6,
            rightIndent=6,
            backColor=colors.HexColor("#f3f4f6"),
            borderColor=colors.HexColor("#d1d5db"),
            borderWidth=0.4,
            borderPadding=5,
            spaceBefore=5,
            spaceAfter=7,
        ),
        "toc": ParagraphStyle(
            "BlueprintTOC",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.4,
            leading=12.4,
            textColor=colors.HexColor("#111827"),
            spaceAfter=2.5,
        ),
    }

    story: list[Flowable] = []

    # Cover
    story.append(Spacer(1, 1.05 * inch))
    story.append(Paragraph(title, styles["title"]))
    story.append(
        Paragraph(
            "Professional end-to-end blueprint for the PhoenixGuard V3 live chart-intelligence, "
            "Model Council, dashboard, and calibrated execution system.",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, 0.18 * inch))
    cover_rows = [
        ["Generated", "2026-06-21"],
        ["Canonical runtime", "PhoenixGuard V3 / FINAL_LIVE"],
        ["Production launcher", "Backend/launch/launch_phoenixguard_live_ready.ps1"],
        ["Primary authority", "PG_EXECUTION_PACKET_V3"],
        ["Rendered source", str(MARKDOWN_PATH.relative_to(ROOT))],
    ]
    cover_table = Table(
        [
            [
                Paragraph(f"<b>{html.escape(left)}</b>", styles["body"]),
                Paragraph(html.escape(right), styles["body"]),
            ]
            for left, right in cover_rows
        ],
        colWidths=[1.85 * inch, 4.15 * inch],
        hAlign="CENTER",
    )
    cover_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#94a3b8")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(cover_table)
    story.append(Spacer(1, 0.3 * inch))
    story.append(
        Paragraph(
            "Design principle: observation is not execution; study is not execution; only a fresh, "
            "validated V3 execution packet can reach calibrated broker actions.",
            styles["subtitle"],
        )
    )
    story.append(PageBreak())

    # Table of contents
    story.append(Paragraph("Contents", styles["h1"]))
    for level, heading_text in toc:
        prefix = "&nbsp;&nbsp;&nbsp;" if level == 3 else ""
        story.append(Paragraph(f"{prefix}{html.escape(heading_text)}", styles["toc"]))
    story.append(PageBreak())

    index = 0
    paragraph_buffer: list[str] = []
    skip_first_h1 = True

    def flush_paragraph() -> None:
        nonlocal paragraph_buffer
        if paragraph_buffer:
            text = " ".join(part.strip() for part in paragraph_buffer if part.strip())
            if text:
                story.append(Paragraph(_inline_markup(text), styles["body"]))
            paragraph_buffer = []

    while index < len(lines):
        raw_line = lines[index]
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            index += 1
            continue

        if stripped == "---":
            flush_paragraph()
            story.append(Spacer(1, 5))
            index += 1
            continue

        heading = _clean_heading(line)
        if heading:
            flush_paragraph()
            level, heading_text = heading
            if level == 1 and skip_first_h1:
                skip_first_h1 = False
                index += 1
                continue
            style_key = "h1" if level <= 2 else "h2" if level == 3 else "h3"
            if level == 2:
                story.append(Spacer(1, 4))
            story.append(Paragraph(html.escape(heading_text), styles[style_key]))
            index += 1
            continue

        if stripped.startswith("```"):
            flush_paragraph()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index].rstrip())
                index += 1
            index += 1
            story.append(Preformatted("\n".join(code_lines), styles["code"]))
            continue

        if _is_table_start(lines, index):
            flush_paragraph()
            table_lines, index = _consume_until_blank(lines, index)
            header, rows = _table_block(table_lines)
            cell_style = styles["body"]
            table_data = [
                [Paragraph(f"<b>{html.escape(cell)}</b>", cell_style) for cell in header],
                *[
                    [Paragraph(_inline_markup(cell), cell_style) for cell in row]
                    for row in rows
                ],
            ]
            usable_width = doc.width
            col_width = usable_width / max(1, len(header))
            table = Table(table_data, colWidths=[col_width] * len(header), repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e7eb")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111827")),
                        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#ffffff")),
                        ("BOX", (0, 0), (-1, -1), 0.55, colors.HexColor("#9ca3af")),
                        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.append(table)
            story.append(Spacer(1, 8))
            continue

        bullet_match = re.match(r"^[-*]\s+(.+)$", stripped)
        number_match = re.match(r"^\d+\.\s+(.+)$", stripped)
        if bullet_match or number_match:
            flush_paragraph()
            ordered = bool(number_match)
            items: list[Flowable] = []
            while index < len(lines):
                current = lines[index].strip()
                match = re.match(r"^\d+\.\s+(.+)$", current) if ordered else re.match(r"^[-*]\s+(.+)$", current)
                if not match:
                    break
                items.append(
                    Paragraph(_inline_markup(match.group(1)), styles["bullet"])
                )
                index += 1
            story.append(
                ListFlowable(
                    items,
                    bulletType="1" if ordered else "bullet",
                    start="1",
                    leftIndent=16,
                    bulletFontName="Helvetica",
                    bulletFontSize=8.2,
                )
            )
            story.append(Spacer(1, 3))
            continue

        paragraph_buffer.append(stripped)
        index += 1

    flush_paragraph()

    def draw_page(canvas: Canvas, doc_obj: SimpleDocTemplate) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#cbd5e1"))
        canvas.setLineWidth(0.4)
        canvas.line(doc_obj.leftMargin, 0.45 * inch, letter[0] - doc_obj.rightMargin, 0.45 * inch)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(doc_obj.leftMargin, 0.28 * inch, "PhoenixGuard System Blueprint")
        canvas.drawRightString(letter[0] - doc_obj.rightMargin, 0.28 * inch, f"Page {doc_obj.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)


def main() -> int:
    if not MARKDOWN_PATH.exists():
        raise SystemExit(f"Blueprint source not found: {MARKDOWN_PATH}")
    markdown_text = MARKDOWN_PATH.read_text(encoding="utf-8")
    _build_pdf(markdown_text)
    print(f"PDF generated: {PDF_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

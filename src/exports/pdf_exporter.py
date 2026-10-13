"""PDF reports with ReportLab."""

from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle)

NAVY = colors.HexColor("#1B2A41")
BLUE = colors.HexColor("#1F4E8C")
GREY = colors.HexColor("#5B6676")
LIGHT = colors.HexColor("#EEF2F7")

_styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=_styles["Heading1"], textColor=NAVY, fontSize=17, spaceAfter=6)
H2 = ParagraphStyle("H2", parent=_styles["Heading2"], textColor=BLUE, fontSize=12.5, spaceBefore=10, spaceAfter=5)
BODY = ParagraphStyle("Body", parent=_styles["BodyText"], fontSize=9, leading=12)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=7.5, leading=9.5, alignment=TA_LEFT)
MUTED = ParagraphStyle("Muted", parent=BODY, textColor=GREY, fontSize=8)
CELL_HEAD = ParagraphStyle("CellHead", parent=SMALL, textColor=colors.white, fontName="Helvetica-Bold")


def _p(text, style=SMALL) -> Paragraph:
    return Paragraph(escape("" if text is None else str(text)).replace("\n", "<br/>"), style)


def _table(headers: list[str], rows: list[list], widths: list[float]) -> LongTable:
    data = [[_p(h, CELL_HEAD) for h in headers]] + [[_p(c) for c in row] for row in rows]
    table = LongTable(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D2DE")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _empty(message: str) -> Paragraph:
    return _p(message, MUTED)


def _footer(app_name: str):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GREY)
        canvas.drawString(15 * mm, 8 * mm, f"{app_name} — AI-assisted analysis. Requires review by a qualified engineer.")
        canvas.drawRightString(doc.pagesize[0] - 15 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def report_pdf(report: dict) -> bytes:
    buf = io.BytesIO()
    pagesize = landscape(A4)
    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm,
                            bottomMargin=14 * mm, title=f"{report['application']['name']} report — {report['document']['title']}",
                            author=report["application"]["name"])
    width = pagesize[0] - 30 * mm
    d = report["document"]
    story = [
        _p(f"{report['application']['name']} · {report['application']['subtitle']}", MUTED),
        _p("Architecture Analysis Report", H1),
        _p(f"{d['title']} — Revision {d['revision']}", H2),
        _table(["Field", "Value"], [
            ["Analysis date", report["generated_at"]], ["Source file", d["filename"]], ["Pages", d["pages"]],
            ["Source status", d["source_status"]], ["Uploaded", d["uploaded_at"]], ["Processed", d["processed_at"] or "—"],
            ["Text chunks / tables / OCR pages", f"{d['chunks']} / {d['tables']} / {d['ocr_pages']}"],
            ["Pages with little text", ", ".join(map(str, d["low_text_pages"])) or "none"],
            ["Embedding model", d["embedding"]], ["SHA-256", d["sha256"]],
        ], [width * 0.25, width * 0.75]),
        _p("Executive summary", H2), _p(report["executive_summary"], BODY),
    ]

    def section(title, headers, rows, widths, empty_msg):
        story.append(_p(title, H2))
        story.append(_table(headers, rows, [width * w for w in widths]) if rows else _empty(empty_msg))

    section("Component inventory", ["ID", "Component", "Description", "Page", "Method", "Review"],
            [[c["external_id"], c["name"], c["description"], c["page_number"], c["extraction_method"], c["review_status"]]
             for c in report["components"]], [0.07, 0.2, 0.47, 0.06, 0.1, 0.1], "No components were extracted.")
    section("Interface inventory", ["Interface", "Type", "Data elements / operations", "Page", "Review"],
            [[i["name"], i["attributes"].get("interface_type", ""), ", ".join(i["attributes"].get("data_elements", [])) or "(none listed)",
              i["page_number"], i["review_status"]] for i in report["interfaces"]],
            [0.2, 0.14, 0.48, 0.06, 0.12], "No interfaces were extracted.")
    section("Port inventory", ["Component", "Port", "Direction", "Interface", "Page", "Review"],
            [[p["component"], p["name"], p["attributes"].get("direction", ""), p["attributes"].get("interface", ""),
              p["page_number"], p["review_status"]] for p in report["ports"]],
            [0.22, 0.22, 0.1, 0.24, 0.06, 0.16], "No ports were extracted.")
    section("Signal inventory", ["Signal", "Interface", "Type", "Unit", "Producer", "Consumer", "Page"],
            [[s["name"]] + [s["attributes"].get(k, "") for k in ("interface", "data_type", "unit", "producer", "consumer")]
             + [s["page_number"]] for s in report["signals"]],
            [0.2, 0.16, 0.1, 0.08, 0.18, 0.22, 0.06], "No signals were extracted.")
    section("Documented dependencies and data flows", ["Source", "Relation", "Target", "Via", "Page", "Method", "Review"],
            [[r["source_name"], r["relation_type"].replace("_", " "), r["target_name"], r["via"], r["page_number"],
              r["extraction_method"], r["review_status"]] for r in report["dependencies"]],
            [0.2, 0.1, 0.2, 0.18, 0.06, 0.14, 0.12], "No dependencies were extracted.")
    section("Functional flows", ["Flow", "Steps", "Page"],
            [[f["name"], f["description"], f["page_number"]] for f in report["functional_flows"]],
            [0.3, 0.64, 0.06], "No functional flows were extracted.")
    story.append(PageBreak())
    section("Analysis findings", ["Code", "Severity", "Category", "Summary", "Pages", "Detection", "Review"],
            [[f["code"], f["severity"], f["category"], f["summary"] + ("\n" + f["reviewer_comment"] if f["reviewer_comment"] else ""),
              ", ".join(map(str, f["pages"])), ("AI suggestion" if f["is_ai_generated"] else f["evidence_kind"].replace("_", " ")),
              f["review_status"].replace("_", " ")] for f in report["findings"]],
            [0.06, 0.07, 0.13, 0.46, 0.07, 0.11, 0.1], "No findings were raised.")
    story.append(KeepTogether([_p("Known limitations", H2)] + [_p("• " + t, BODY) for t in report["limitations"]]))
    story.append(Spacer(1, 6))
    story.append(_p("Findings marked 'missing evidence' indicate that an item could not be located in the extracted data; "
                    "they are not confirmed defects.", MUTED))
    doc.build(story, onFirstPage=_footer(report["application"]["name"]), onLaterPages=_footer(report["application"]["name"]))
    return buf.getvalue()


def comparison_pdf(result: dict, app_name: str, old_name: str, new_name: str, ai_summary: str = "") -> bytes:
    buf = io.BytesIO()
    pagesize = landscape(A4)
    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm,
                            bottomMargin=14 * mm, title=f"{app_name} revision comparison")
    width = pagesize[0] - 30 * mm
    c = result["counts"]
    story = [_p(app_name, MUTED), _p("Revision Comparison Report", H1), _p(f"{old_name}  →  {new_name}", H2),
             _table(["Measure", "Value"], [[k.replace("_", " ").capitalize(), v] for k, v in c.items()]
                    + [["Structural similarity", f"{int(result['structural_similarity'] * 100)}% of section headings match"]],
                    [width * 0.3, width * 0.7])]
    for w in result["warnings"]:
        story.append(_p("Note: " + w, MUTED))
    if ai_summary:
        story += [_p("AI-generated summary (inference — requires review)", H2), _p(ai_summary, BODY)]
    rows = []
    for etype, bucket in result["entity_changes"].items():
        for kind in ("added", "removed", "changed"):
            for item in bucket[kind]:
                details = "; ".join(f"{d['field']}: {d['old']} → {d['new']}" for d in item.get("differences", []))
                rows.append([etype, kind, item["name"], details or item.get("description", ""),
                             item.get("page_old") or "—", item.get("page_new") or "—"])
    story.append(_p("Architecture entity changes (deterministic)", H2))
    story.append(_table(["Type", "Change", "Name", "Details", "Page old", "Page new"], rows,
                        [width * w for w in (0.1, 0.08, 0.2, 0.46, 0.08, 0.08)]) if rows else _empty("No entity changes detected."))
    story.append(_p("Documented relationships involving changed items", H2))
    deps = result["affected_dependencies"]
    story.append(_table(["Source", "Relation", "Target", "Via", "Changed items"],
                        [[d["source"], d["relation"], d["target"], d["via"], d["reasons"]] for d in deps],
                        [width * w for w in (0.18, 0.1, 0.18, 0.16, 0.38)]) if deps else _empty("None."))
    story.append(_p("Section changes (deterministic text comparison)", H2))
    srows = ([["added", s["title"], ", ".join(map(str, s["pages_new"])), s["text"][:600]] for s in result["added_sections"]]
             + [["removed", s["title"], ", ".join(map(str, s["pages_old"])), s["text"][:600]] for s in result["removed_sections"]]
             + [["modified", s["title"], ", ".join(map(str, s["pages_new"])),
                 "\n".join([f"+ {x}" for x in s["added_lines"][:6]] + [f"- {x}" for x in s["removed_lines"][:6]]
                           + [f"~ {x['old']} → {x['new']}" for x in s["changed_lines"][:6]])] for s in result["modified_sections"]])
    story.append(_table(["Change", "Section", "Pages", "Detail"], srows, [width * w for w in (0.08, 0.24, 0.08, 0.6)])
                 if srows else _empty("No section changes."))
    story.append(Spacer(1, 6))
    story.append(_p("A textual change does not imply a defect. Engineering impact must be assessed by a qualified reviewer.", MUTED))
    doc.build(story, onFirstPage=_footer(app_name), onLaterPages=_footer(app_name))
    return buf.getvalue()

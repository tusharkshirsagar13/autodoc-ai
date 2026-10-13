"""Page-level PDF parsing with PyMuPDF.

Produces an ordered list of *elements* per page (headings, paragraphs and
tables) while preserving the 1-based page number of every element. Table
regions are detected with PyMuPDF's table finder so their text is not
duplicated as loose paragraphs.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from src.ingestion.ocr import ocr_page, ocr_status
from src.utils.errors import ExtractionError, OCRUnavailableError
from src.utils.logging_config import get_logger

log = get_logger("pdf_parser")

NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+){0,4})\.?\s+([A-Z][^\n]{1,110})$")
REVISION_PATTERNS = [
    re.compile(r"\b(?:Document\s+)?Revision\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9.\-_]{0,15})", re.I),
    re.compile(r"\bVersion\s*[:\-]\s*([A-Za-z0-9][A-Za-z0-9.\-_]{0,15})", re.I),
    re.compile(r"\bRev\b\.?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9.\-_]{0,10})\b"),
]


@dataclass
class Element:
    kind: str  # heading | paragraph | table
    text: str
    page_number: int
    y: float = 0.0
    level: int = 0
    rows: list[list[str]] = field(default_factory=list)
    section: str = ""


@dataclass
class ParsedPage:
    page_number: int
    char_count: int
    method: str  # text | ocr | empty
    elements: list[Element] = field(default_factory=list)
    low_text: bool = False
    ocr_error: str = ""

    @property
    def text(self) -> str:
        return "\n".join(e.text for e in self.elements)


@dataclass
class ParsedDocument:
    page_count: int
    metadata: dict
    pages: list[ParsedPage]
    sections: list[dict]
    revision_hint: str = ""
    title_hint: str = ""

    @property
    def total_chars(self) -> int:
        return sum(p.char_count for p in self.pages)

    @property
    def low_text_pages(self) -> list[int]:
        return [p.page_number for p in self.pages if p.low_text]

    @property
    def ocr_pages(self) -> list[int]:
        return [p.page_number for p in self.pages if p.method == "ocr"]

    @property
    def table_count(self) -> int:
        return sum(1 for p in self.pages for e in p.elements if e.kind == "table")

    def elements(self):
        for page in self.pages:
            yield from page.elements

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ParsedDocument":
        pages = []
        for p in data.get("pages", []):
            elements = [Element(**e) for e in p.get("elements", [])]
            pages.append(ParsedPage(page_number=p["page_number"], char_count=p["char_count"], method=p["method"],
                                    elements=elements, low_text=p.get("low_text", False),
                                    ocr_error=p.get("ocr_error", "")))
        return cls(page_count=data["page_count"], metadata=data.get("metadata", {}), pages=pages,
                   sections=data.get("sections", []), revision_hint=data.get("revision_hint", ""),
                   title_hint=data.get("title_hint", ""))


def normalize_whitespace(text: str) -> str:
    text = text.replace(" ", " ").replace("­", "").replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # de-hyphenate line breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_IDENTIFIER_FRAGMENT = re.compile(r"^(?:[A-Za-z][a-z0-9]*[A-Z_0-9][A-Za-z0-9_]*)$")


def _clean_cell(value) -> str:
    """Join a table cell's wrapped lines.

    When a CamelCase / snake_case identifier is broken by the column width
    ("WheelSpeedProce" + "ssor"), the fragments are joined without a space.
    Ordinary prose that wraps is joined with a space.
    """
    if value is None:
        return ""
    lines = [ln.strip() for ln in str(value).splitlines() if ln.strip()]
    out = ""
    for line in lines:
        last = out.split(" ")[-1] if out else ""
        if last and _IDENTIFIER_FRAGMENT.match(last) and line[:1].islower() and " " not in line.split(",")[0]:
            out += line
        elif last and last[-1:].isdigit() and line[:1].isdigit() and " " not in line and len(line) <= 4:
            out += line  # a number broken by the column width ("-100..10" + "0")
        else:
            out = f"{out} {line}" if out else line
    return re.sub(r"\s+", " ", out).strip()


def table_to_text(rows: list[list[str]]) -> str:
    return "\n".join(" | ".join(cell for cell in row) for row in rows if any(row))


def _body_font_size(page_dicts: list[dict], table_rects: list[list[tuple]]) -> float:
    """Most common font size (weighted by characters) outside table regions."""
    sizes: Counter = Counter()
    for pd, rects in zip(page_dicts, table_rects):
        for block in pd.get("blocks", []):
            for line in block.get("lines", []):
                if any(_inside(line["bbox"], r) for r in rects):
                    continue
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        sizes[round(span.get("size", 0), 1)] += len(text)
    return sizes.most_common(1)[0][0] if sizes else 10.0


def _heading_level(text: str, size: float, bold: bool, body_size: float) -> int:
    """Return heading level (1..4) or 0 if the line is not a heading."""
    stripped = text.strip()
    if not stripped or len(stripped) > 120:
        return 0
    numbered = NUMBERED_HEADING.match(stripped)
    larger = size >= body_size * 1.15
    if numbered and (bold or larger):
        return min(4, numbered.group(1).count(".") + 1)
    if larger and len(stripped.split()) <= 14 and not stripped.endswith(".") and not stripped[0].islower():
        return 1 if size >= body_size * 1.5 else 2
    if bold and size >= body_size * 0.98 and len(stripped.split()) <= 8 and stripped[0].isupper() \
            and not stripped.endswith((".", ",", ";")):
        return 3
    return 0


def _page_lines(page_dict: dict) -> list[dict]:
    lines = []
    for block in page_dict.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in line["spans"]).strip()
            size = max(s.get("size", 0) for s in spans)
            bold = all(("Bold" in s.get("font", "")) or (s.get("flags", 0) & 16) for s in spans)
            x0, y0, x1, y1 = line["bbox"]
            lines.append({"text": text, "size": size, "bold": bold, "bbox": (x0, y0, x1, y1),
                          "block": id(block)})
    return lines


def _inside(bbox, rect, tolerance: float = 2.0) -> bool:
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return rect[0] - tolerance <= cx <= rect[2] + tolerance and rect[1] - tolerance <= cy <= rect[3] + tolerance


def _extract_tables(page) -> list[tuple[tuple, list[list[str]]]]:
    tables = []
    try:
        found = page.find_tables()
    except Exception as exc:  # table detection is best-effort
        log.debug("Table detection failed on page %s: %s", page.number + 1, exc)
        return tables
    for tab in found.tables:
        try:
            # get_textbox per cell keeps characters such as '_' that sit slightly below the
            # baseline in the right place (tab.extract() can split them onto a new line).
            rows = [[_clean_cell(page.get_textbox(cell)) if cell else "" for cell in row.cells] for row in tab.rows]
        except Exception:
            try:
                rows = [[_clean_cell(c) for c in row] for row in tab.extract()]
            except Exception:
                continue
        rows = [r for r in rows if any(r)]
        if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
            tables.append((tuple(tab.bbox), rows))
    return tables


def _build_elements(page_dict: dict, page_number: int, body_size: float, tables) -> list[Element]:
    elements: list[Element] = []
    for rect, rows in tables:
        elements.append(Element(kind="table", text=table_to_text(rows), page_number=page_number, y=rect[1], rows=rows))
    paragraph: list[str] = []
    para_y = 0.0
    last_block = None
    last_y1 = None

    def flush():
        nonlocal paragraph
        if paragraph:
            text = normalize_whitespace("\n".join(paragraph))
            if text:
                elements.append(Element(kind="paragraph", text=text, page_number=page_number, y=para_y))
        paragraph = []

    for line in _page_lines(page_dict):
        if any(_inside(line["bbox"], rect) for rect, _ in tables):
            continue
        level = _heading_level(line["text"], line["size"], line["bold"], body_size)
        if level:
            flush()
            text = normalize_whitespace(line["text"])
            prev = elements[-1] if elements else None
            # Merge a heading that wraps onto a second line (same level, directly below).
            if (prev is not None and prev.kind == "heading" and prev.level == level and last_y1 is not None
                    and line["bbox"][1] - last_y1 < line["size"] * 0.8 and not NUMBERED_HEADING.match(text)):
                prev.text = f"{prev.text} {text}"
            else:
                elements.append(Element(kind="heading", text=text, page_number=page_number, y=line["bbox"][1],
                                        level=level))
            last_block, last_y1 = None, line["bbox"][3]
            continue
        gap = (line["bbox"][1] - last_y1) if last_y1 is not None else 0
        if paragraph and (line["block"] != last_block and gap > line["size"] * 0.9):
            flush()
        if not paragraph:
            para_y = line["bbox"][1]
        paragraph.append(line["text"])
        last_block, last_y1 = line["block"], line["bbox"][3]
    flush()
    elements.sort(key=lambda e: e.y)
    return elements


def _ocr_elements(text: str, page_number: int) -> list[Element]:
    elements = []
    for block in re.split(r"\n\s*\n", text):
        block = normalize_whitespace(block)
        if not block:
            continue
        kind = "heading" if NUMBERED_HEADING.match(block) and "\n" not in block else "paragraph"
        level = NUMBERED_HEADING.match(block).group(1).count(".") + 1 if kind == "heading" else 0
        elements.append(Element(kind=kind, text=block, page_number=page_number, level=level))
    return elements


def _assign_sections(pages: list[ParsedPage]) -> list[dict]:
    """Attach the active section heading to each element and return a section index."""
    stack: list[tuple[int, str]] = []
    sections: list[dict] = []
    for page in pages:
        for el in page.elements:
            if el.kind == "heading":
                level = el.level or 1
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, el.text))
                sections.append({"title": el.text, "level": level, "page_number": page.page_number})
            el.section = stack[-1][1] if stack else ""
    return sections


def detect_revision_label(text: str) -> str:
    """Find a revision label such as 'Revision: B', 'Version: 1.2' or 'Rev. 03' in the first pages.

    A plausible label contains a digit or is a short upper-case code (A, B, AB); words such as
    'History' in 'Revision History' are ignored.
    """
    head = text[:4000]
    for pattern in REVISION_PATTERNS:
        for m in pattern.finditer(head):
            label = m.group(1).strip(" .:-")
            if label and (any(ch.isdigit() for ch in label) or (label.isupper() and len(label) <= 3)):
                return label
    return ""


def parse_pdf(source: Path | bytes, ocr_enabled: bool = True, ocr_language: str = "eng", ocr_dpi: int = 200,
              tesseract_cmd: str = "", low_text_threshold: int = 40, progress=None) -> ParsedDocument:
    """Parse a PDF into page elements. `progress(fraction, message)` is optional."""
    try:
        doc = pymupdf.open(stream=source, filetype="pdf") if isinstance(source, (bytes, bytearray)) else pymupdf.open(source)
    except Exception as exc:
        raise ExtractionError("The PDF could not be opened for extraction.", str(exc)) from exc

    with doc:
        metadata = {k: v for k, v in (doc.metadata or {}).items() if v}
        page_dicts = []
        for page in doc:
            try:
                page_dicts.append(page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE))
            except Exception as exc:
                log.warning("Text extraction failed on page %s: %s", page.number + 1, exc)
                page_dicts.append({"blocks": []})
        page_tables = [_extract_tables(page) for page in doc]
        body_size = _body_font_size(page_dicts, [[rect for rect, _ in t] for t in page_tables])
        can_ocr = ocr_enabled and ocr_status(tesseract_cmd)["available"]
        pages: list[ParsedPage] = []
        total = doc.page_count
        for index, page in enumerate(doc):
            page_number = index + 1
            if progress:
                progress(index / max(total, 1), f"Extracting page {page_number} of {total}")
            elements = _build_elements(page_dicts[index], page_number, body_size, page_tables[index])
            chars = sum(len(e.text) for e in elements)
            parsed = ParsedPage(page_number=page_number, char_count=chars, method="text" if chars else "empty",
                                elements=elements)
            if chars < low_text_threshold:
                has_images = bool(page.get_images(full=False))
                parsed.low_text = True
                if can_ocr and (has_images or chars == 0):
                    try:
                        text = ocr_page(page, dpi=ocr_dpi, language=ocr_language, tesseract_cmd=tesseract_cmd)
                        ocr_elements = _ocr_elements(text, page_number)
                        ocr_chars = sum(len(e.text) for e in ocr_elements)
                        if ocr_chars > chars:
                            parsed.elements = ocr_elements
                            parsed.char_count = ocr_chars
                            parsed.method = "ocr"
                            parsed.low_text = ocr_chars < low_text_threshold
                    except OCRUnavailableError as exc:
                        parsed.ocr_error = exc.user_message()
            pages.append(parsed)
        sections = _assign_sections(pages)
        first_text = "\n".join(p.text for p in pages[:2])
        title_hint = metadata.get("title", "")
        if not title_hint:
            for el in (pages[0].elements if pages else []):
                if el.kind in {"heading", "paragraph"} and len(el.text) < 150:
                    title_hint = el.text.split("\n")[0]
                    break
        return ParsedDocument(page_count=total, metadata=metadata, pages=pages, sections=sections,
                              revision_hint=detect_revision_label(first_text), title_hint=title_hint)

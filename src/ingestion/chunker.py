"""Section- and page-aware chunking.

Design decisions
* A chunk never spans two pages, so every chunk has exactly one page number
  and citations are precise.
* A chunk never spans two sections; the active section heading is stored in
  the metadata and prepended to the text used for embedding.
* Tables become their own chunks. Long tables are split by rows and the
  header row is repeated in every piece.
* Long paragraphs are split on sentence boundaries with a character overlap.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from src.ingestion.pdf_parser import Element, ParsedDocument, table_to_text

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[(])")


@dataclass
class Chunk:
    text: str
    page_number: int
    section_heading: str
    chunk_index: int
    chunk_type: str  # text | table
    extraction_method: str
    metadata: dict = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha1(self.text.encode("utf-8")).hexdigest()[:16]

    @property
    def embed_text(self) -> str:
        prefix = f"Section: {self.section_heading}\n" if self.section_heading else ""
        return prefix + self.text


def _split_long_text(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    sentences = _SENTENCE_SPLIT.split(text)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > size:  # pathological very long sentence
            head, sentence = sentence[:size], sentence[size - overlap:]
            if current:
                pieces.append(current)
                current = ""
            pieces.append(head)
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= size:
            current = candidate
        else:
            pieces.append(current)
            tail = current[-overlap:] if overlap else ""
            if tail and " " in tail:
                tail = tail[tail.index(" ") + 1:]
            current = f"{tail} {sentence}".strip() if tail else sentence
    if current:
        pieces.append(current)
    return [p for p in pieces if p.strip()]


def _split_table(rows: list[list[str]], size: int) -> list[str]:
    header, body = rows[0], rows[1:]
    header_text = " | ".join(header)
    pieces, current = [], [header]
    for row in body:
        candidate = current + [row]
        if len(table_to_text(candidate)) > size and len(current) > 1:
            pieces.append(table_to_text(current))
            current = [header, row]
        else:
            current = candidate
    if len(current) > 1 or not body:
        pieces.append(table_to_text(current))
    return [f"[Table] {p}" if not p.startswith(header_text) else f"[Table]\n{p}" for p in pieces]


def chunk_document(parsed: ParsedDocument, chunk_size: int = 900, chunk_overlap: int = 150) -> list[Chunk]:
    chunks: list[Chunk] = []

    def emit(text: str, page: int, section: str, kind: str, method: str):
        text = text.strip()
        if len(text) < 3:
            return
        chunks.append(Chunk(text=text, page_number=page, section_heading=section, chunk_index=len(chunks),
                            chunk_type=kind, extraction_method=method))

    for page in parsed.pages:
        method = page.method if page.method != "empty" else "text"
        buffer: list[str] = []
        buffer_section = None

        def flush_buffer():
            nonlocal buffer
            if buffer:
                for piece in _split_long_text("\n".join(buffer), chunk_size, chunk_overlap):
                    emit(piece, page.page_number, buffer_section or "", "text", method)
            buffer = []

        for el in page.elements:
            el: Element
            if el.kind == "table":
                flush_buffer()
                for piece in _split_table(el.rows, chunk_size) if el.rows else [el.text]:
                    emit(piece, page.page_number, el.section, "table", method)
                continue
            if el.section != buffer_section:
                flush_buffer()
                buffer_section = el.section
            text = el.text
            if el.kind == "heading":
                # Headings are kept with the following text so they stay searchable.
                buffer.append(text)
                continue
            projected = len("\n".join(buffer + [text]))
            if projected > chunk_size and buffer:
                flush_buffer()
                buffer_section = el.section
            buffer.append(text)
        flush_buffer()
    return chunks

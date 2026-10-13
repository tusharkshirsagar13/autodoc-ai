"""CSV exports (UTF-8 with BOM so Excel opens them correctly)."""

from __future__ import annotations

import csv
import io
import json

ENTITY_COLUMNS = ["id", "entity_type", "name", "external_id", "component", "description", "page_number", "section",
                  "evidence", "extraction_method", "confidence", "review_status", "reviewer_comment", "attributes"]
RELATIONSHIP_COLUMNS = ["id", "source_name", "relation_type", "target_name", "via", "description", "page_number",
                        "section", "evidence", "extraction_method", "confidence", "review_status"]
FINDING_COLUMNS = ["code", "severity", "category", "summary", "explanation", "evidence", "pages", "detection_method",
                   "is_ai_generated", "evidence_kind", "review_status", "reviewer_comment"]


def _cell(value) -> str:
    if isinstance(value, (list, dict)):
        if isinstance(value, dict):
            value = {k: v for k, v in value.items() if k != "occurrences"}
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    text = str(value)
    # Neutralise spreadsheet formula injection from document-derived text.
    if text and text[0] in "=+-@\t\r":
        text = "'" + text
    return text


def to_csv(rows: list[dict], columns: list[str]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(c)) for c in columns])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def entities_csv(entities: list[dict]) -> bytes:
    return to_csv(entities, ENTITY_COLUMNS)


def relationships_csv(relationships: list[dict]) -> bytes:
    return to_csv(relationships, RELATIONSHIP_COLUMNS)


def findings_csv(findings: list[dict]) -> bytes:
    return to_csv(findings, FINDING_COLUMNS)

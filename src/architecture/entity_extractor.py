"""Architecture entity extraction.

Three layers, applied in order:
1. `table_parser`   — deterministic parsing of component / port / interface /
                      signal tables recognised by their header row.
2. `prose_pattern`  — deterministic patterns in running text (component
                      detail sections, responsibilities, identifier mentions).
3. `llm_structured` — optional, on request: an LLM proposes additional entities
                      as JSON, validated with Pydantic. A proposal is kept only
                      if its name appears verbatim in the source excerpt, and it
                      is always stored as an unreviewed, low-confidence
                      suggestion.

Every entity keeps its page, section, verbatim evidence, extraction method,
confidence label and review status.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from src.ingestion.pdf_parser import Element, ParsedDocument

EntityType = Literal["component", "interface", "port", "signal", "functional_flow", "responsibility"]
RelationType = Literal["depends_on", "sends_to", "provides", "requires", "calls", "uses", "flow_step"]
Confidence = Literal["high", "medium", "low"]

SIGNAL_RE = re.compile(r"\b(?:Sig|SIG|sig)_[A-Za-z0-9_]*[A-Za-z0-9]")
INTERFACE_RE = re.compile(r"\b(?:IF|If|SRI|CSI)_[A-Za-z0-9_]*[A-Za-z0-9]")
PORT_RE = re.compile(r"\b(?:PP|RP|PPort|RPort)_[A-Za-z0-9_]*[A-Za-z0-9]")
IDENT_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*\b")


class ExtractedEntity(BaseModel):
    """Validated structure for every extracted entity."""

    entity_type: EntityType
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    component: str = ""
    external_id: str = ""
    attributes: dict = Field(default_factory=dict)
    page_number: int | None = Field(default=None, ge=1)
    section: str = ""
    evidence: str = ""
    extraction_method: str = "table_parser"
    confidence: Confidence = "medium"

    @field_validator("name", "component", "external_id")
    @classmethod
    def _strip(cls, value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.name)

    def dedup_key(self) -> tuple:
        scoped = self.entity_type in {"port", "responsibility"}
        return (self.entity_type, self.normalized_name, normalize_name(self.component) if scoped else "")


class ExtractedRelationship(BaseModel):
    source_name: str = Field(min_length=1, max_length=200)
    target_name: str = Field(min_length=1, max_length=200)
    relation_type: RelationType = "depends_on"
    via: str = ""
    description: str = ""
    page_number: int | None = Field(default=None, ge=1)
    section: str = ""
    evidence: str = ""
    extraction_method: str = "table_parser"
    confidence: Confidence = "medium"

    @field_validator("source_name", "target_name", "via")
    @classmethod
    def _strip(cls, value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    def dedup_key(self) -> tuple:
        return (normalize_name(self.source_name), normalize_name(self.target_name), self.relation_type,
                normalize_name(self.via))


class LLMExtractionPayload(BaseModel):
    entities: list[dict] = Field(default_factory=list)
    relationships: list[dict] = Field(default_factory=list)


def normalize_name(name: str) -> str:
    """Case-insensitive, whitespace-normalised key. Underscores, digits and suffixes are preserved so
    that e.g. Sig_WheelSpeed_FL and Sig_WheelSpeed_FR remain distinct."""
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def stable_id(revision_id: str, prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join([revision_id, *parts]).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


# ------------------------------------------------------------------ tables
HEADER_ALIASES = {
    "id": {"id", "component id", "swc id", "ref", "identifier"},
    "component": {"component", "software component", "swc", "component name", "name"},
    "description": {"description", "responsibility", "responsibilities", "purpose", "function"},
    "type": {"type", "component type", "swc type", "interface type", "kind"},
    "port": {"port", "port name"},
    "direction": {"direction", "port type", "p/r", "provided/required"},
    "interface": {"interface", "port interface", "interface name"},
    "data": {"data elements", "data element", "elements", "operations", "data elements / operations",
             "data elements/operations", "signals"},
    "signal": {"signal", "signal name", "data element name"},
    "data_type": {"data type", "datatype", "type"},
    "unit": {"unit", "units"},
    "range": {"range", "value range"},
    "producer": {"producer", "sender", "provider", "source component"},
    "consumer": {"consumer", "receiver", "consumers", "receivers"},
    "source": {"source", "from", "client", "dependent component", "source component"},
    "target": {"target", "to", "server", "depends on", "target component", "dependency"},
    "relation": {"relation", "relationship", "dependency type"},
    "via": {"via", "through", "interface / signal", "using"},
}


def _canon(header: str) -> str:
    return re.sub(r"\s+", " ", header.strip().lower())


def _map_headers(header: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header):
        h = _canon(cell)
        for key, aliases in HEADER_ALIASES.items():
            if h in aliases and key not in mapping:
                mapping[key] = idx
                break
    return mapping


def classify_table(header: list[str]) -> str | None:
    cols = _map_headers(header)
    first = _canon(header[0]) if header else ""
    if "source" in cols and "target" in cols:
        return "dependency"
    canon = {_canon(h) for h in header}
    if canon & {"revision", "version", "rev"} and canon & {"date", "author", "description", "change", "changes"}:
        return "revision_history"
    if first in HEADER_ALIASES["signal"] or ("signal" in cols and ("unit" in cols or "data_type" in cols)):
        return "signal"
    if "port" in cols:
        return "port"
    if first in HEADER_ALIASES["interface"] and ("data" in cols or "type" in cols or "description" in cols):
        return "interface"
    if "component" in cols and "description" in cols:
        return "component"
    return None


def _cell(row: list[str], cols: dict[str, int], key: str) -> str:
    idx = cols.get(key)
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def _row_evidence(row: list[str]) -> str:
    return " | ".join(c for c in row)[:400]


def _is_header_row(row: list[str], header: list[str]) -> bool:
    return [_canon(c) for c in row] == [_canon(c) for c in header]


def extract_from_tables(parsed: ParsedDocument) -> tuple[list[ExtractedEntity], list[ExtractedRelationship], list[dict]]:
    entities: list[ExtractedEntity] = []
    rels: list[ExtractedRelationship] = []
    table_log: list[dict] = []
    for el in parsed.elements():
        if el.kind != "table" or not el.rows:
            continue
        header, body = el.rows[0], el.rows[1:]
        kind = classify_table(header)
        table_log.append({"page_number": el.page_number, "section": el.section, "kind": kind or "unrecognised",
                          "rows": len(body), "header": header})
        if not kind or kind == "revision_history":
            continue
        cols = _map_headers(header)
        for row in body:
            if _is_header_row(row, header) or not any(row):
                continue
            common = dict(page_number=el.page_number, section=el.section, evidence=_row_evidence(row),
                          extraction_method="table_parser")
            try:
                if kind == "component":
                    name = _cell(row, cols, "component")
                    if not name:
                        entities.append(ExtractedEntity(entity_type="component", name="(unnamed component)",
                                                        external_id=_cell(row, cols, "id"), confidence="low",
                                                        attributes={"missing_name": True}, **common))
                        continue
                    entities.append(ExtractedEntity(entity_type="component", name=name,
                                                    description=_cell(row, cols, "description"),
                                                    external_id=_cell(row, cols, "id"),
                                                    attributes={"type": _cell(row, cols, "type")},
                                                    confidence="high", **common))
                elif kind == "port":
                    port = _cell(row, cols, "port")
                    comp = _cell(row, cols, "component")
                    iface = _cell(row, cols, "interface")
                    direction = _cell(row, cols, "direction")
                    if port:
                        entities.append(ExtractedEntity(entity_type="port", name=port, component=comp,
                                                        description=f"{direction} port typed by {iface}".strip(),
                                                        attributes={"direction": direction, "interface": iface,
                                                                    "interface_type": _cell(row, cols, "type")},
                                                        confidence="high", **common))
                    if comp and iface and direction:
                        provided = direction.lower().startswith(("p", "provid", "out", "sender", "server"))
                        rels.append(ExtractedRelationship(source_name=comp, target_name=iface,
                                                          relation_type="provides" if provided else "requires",
                                                          via=port, confidence="high", **common))
                elif kind == "interface":
                    name = _cell(row, cols, "interface") or row[0].strip()
                    data = _cell(row, cols, "data")
                    elements = [d.strip() for d in re.split(r"[,;\n]", data) if d.strip() and d.strip() not in {"-", "—", "n/a", "N/A", "TBD"}]
                    entities.append(ExtractedEntity(entity_type="interface", name=name,
                                                    description=_cell(row, cols, "description"),
                                                    attributes={"interface_type": _cell(row, cols, "type"),
                                                                "data_elements": elements,
                                                                "raw_data_elements": data},
                                                    confidence="high", **common))
                elif kind == "signal":
                    name = _cell(row, cols, "signal") or row[0].strip()
                    attrs = {k: _cell(row, cols, k) for k in ("interface", "data_type", "unit", "range", "producer", "consumer")}
                    entities.append(ExtractedEntity(entity_type="signal", name=name, component=attrs["producer"],
                                                    description=", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in attrs.items() if v),
                                                    attributes=attrs, confidence="high", **common))
                    if attrs["producer"] and attrs["consumer"]:
                        for consumer in re.split(r"[,;/]", attrs["consumer"]):
                            if consumer.strip():
                                rels.append(ExtractedRelationship(source_name=attrs["producer"], target_name=consumer.strip(),
                                                                  relation_type="sends_to", via=name,
                                                                  confidence="high", **common))
                elif kind == "dependency":
                    src, tgt = _cell(row, cols, "source"), _cell(row, cols, "target")
                    if src and tgt:
                        relation = _cell(row, cols, "relation").lower().replace(" ", "_") or "depends_on"
                        if relation not in {"depends_on", "sends_to", "provides", "requires", "calls", "uses"}:
                            relation = "depends_on"
                        rels.append(ExtractedRelationship(source_name=src, target_name=tgt, relation_type=relation,
                                                          via=_cell(row, cols, "via"),
                                                          description=_cell(row, cols, "description"),
                                                          confidence="high", **common))
            except ValidationError:
                continue
    return entities, rels, table_log


# ------------------------------------------------------------------- prose
DEP_PATTERNS = [
    (re.compile(r"\b([A-Z][A-Za-z0-9_]{3,})\s+(?:directly\s+)?depends\s+on\s+(?:the\s+)?([A-Z][A-Za-z0-9_]{3,})"), "depends_on", None),
    (re.compile(r"\b([A-Z][A-Za-z0-9_]{3,})\s+(?:requires|needs)\s+(?:data\s+from\s+)?(?:the\s+)?([A-Z][A-Za-z0-9_]{3,})"), "depends_on", None),
    (re.compile(r"\b([A-Z][A-Za-z0-9_]{3,})\s+calls\s+(?:the\s+)?([A-Z][A-Za-z0-9_]{3,})"), "calls", None),
    (re.compile(r"\b([A-Z][A-Za-z0-9_]{3,})\s+(?:sends|transmits|publishes)\s+(\S+)\s+to\s+(?:the\s+)?([A-Z][A-Za-z0-9_]{3,})"), "sends_to", "via"),
    (re.compile(r"\b([A-Z][A-Za-z0-9_]{3,})\s+(?:receives|reads)\s+(\S+)\s+from\s+(?:the\s+)?([A-Z][A-Za-z0-9_]{3,})"), "receives", "via"),
]
FLOW_LINE = re.compile(r"(?:(?P<id>[A-Z]{1,4}-?\d{1,3})\s*[:\-–]?\s*)?(?P<title>[^:\n]{0,80}?)\s*:\s*(?P<chain>[A-Za-z0-9_]+(?:\s*(?:->|→|=>)\s*[A-Za-z0-9_]+)+)")
CHAIN_SPLIT = re.compile(r"\s*(?:->|→|=>)\s*")
COMPONENT_SUFFIX = re.compile(r"(Manager|Handler|Processor|Coordinator|Estimator|Controller|Service|Monitor|Arbiter|Gateway|Adapter|Driver|Calculator|Supervisor)$")


def _sentences(text: str) -> list[str]:
    # Re-join lines wrapped by the PDF layout; keep real list items on their own line.
    text = re.sub(r"\n(?!\s*(?:[-•*▪]|\d+[.)])\s)", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])|\n", text) if s.strip()]


def extract_from_prose(parsed: ParsedDocument, known_components: set[str]) -> tuple[list[ExtractedEntity], list[ExtractedRelationship], list[dict]]:
    """Deterministic prose patterns. `known_components` holds normalised component names from tables."""
    entities: list[ExtractedEntity] = []
    rels: list[ExtractedRelationship] = []
    mentions: list[dict] = []
    comp_lookup = {normalize_name(c): c for c in known_components}

    def is_component(name: str) -> bool:
        return normalize_name(name) in comp_lookup or bool(COMPONENT_SUFFIX.search(name))

    current_component_section: tuple[str, str] | None = None
    for el in parsed.elements():
        el: Element
        if el.kind == "heading":
            current_component_section = None
            for norm, original in comp_lookup.items():
                if re.search(rf"\b{re.escape(original)}\b", el.text):
                    current_component_section = (original, el.text)
                    break
            continue
        text = el.text
        # identifier mentions (used by completeness checks)
        for kind, pattern in (("signal", SIGNAL_RE), ("interface", INTERFACE_RE), ("port", PORT_RE)):
            for m in pattern.finditer(text):
                mentions.append({"kind": kind, "name": m.group(0), "page_number": el.page_number,
                                 "section": el.section, "in_table": el.kind == "table",
                                 "context": text[max(0, m.start() - 80): m.end() + 80].replace("\n", " ")})
        if el.kind == "table":
            continue
        # component mentions in prose referencing components not in any table
        for m in IDENT_RE.finditer(text):
            name = m.group(0)
            if COMPONENT_SUFFIX.search(name) and len(name) > 6:
                mentions.append({"kind": "component", "name": name, "page_number": el.page_number,
                                 "section": el.section, "in_table": False,
                                 "context": text[max(0, m.start() - 80): m.end() + 80].replace("\n", " ")})
        # responsibilities inside a component's own section
        if current_component_section and el.section == current_component_section[1]:
            comp = current_component_section[0]
            for sentence in _sentences(text):
                bullet = re.match(r"^(?:[-•*▪]|\d+[.)])\s+(.+)", sentence)
                lead = re.match(r"^(?:Responsibilit(?:y|ies)|Function)\s*[:\-]\s*(.+)", sentence, re.I)
                content = (bullet or lead)
                if content and len(content.group(1)) > 8:
                    try:
                        entities.append(ExtractedEntity(entity_type="responsibility", name=content.group(1)[:200],
                                                        component=comp, description=content.group(1),
                                                        page_number=el.page_number, section=el.section,
                                                        evidence=sentence[:400], extraction_method="prose_pattern",
                                                        confidence="medium"))
                    except ValidationError:
                        pass
        # dependency sentences
        for sentence in _sentences(text):
            for pattern, relation, via_mode in DEP_PATTERNS:
                for m in pattern.finditer(sentence):
                    if via_mode:
                        src, via, tgt = m.group(1), m.group(2).strip(".,;"), m.group(3)
                    else:
                        src, tgt, via = m.group(1), m.group(2), ""
                    if not (is_component(src) and is_component(tgt)) or normalize_name(src) == normalize_name(tgt):
                        continue
                    rel_type = relation
                    if relation == "receives":  # "A receives X from B" == B sends X to A
                        src, tgt, rel_type = tgt, src, "sends_to"
                    both_known = normalize_name(src) in comp_lookup and normalize_name(tgt) in comp_lookup
                    rels.append(ExtractedRelationship(source_name=comp_lookup.get(normalize_name(src), src),
                                                      target_name=comp_lookup.get(normalize_name(tgt), tgt),
                                                      relation_type=rel_type, via=via, page_number=el.page_number,
                                                      section=el.section, evidence=sentence[:400],
                                                      extraction_method="prose_pattern",
                                                      confidence="medium" if both_known else "low"))
            # functional flows
            for m in FLOW_LINE.finditer(sentence):
                steps = [s for s in CHAIN_SPLIT.split(m.group("chain")) if s]
                if len(steps) < 2 or not any(normalize_name(s) in comp_lookup for s in steps):
                    continue
                flow_name = " ".join(p for p in (m.group("id") or "", (m.group("title") or "").strip()) if p).strip() or " -> ".join(steps)
                entities.append(ExtractedEntity(entity_type="functional_flow", name=flow_name[:200],
                                                description=" → ".join(steps), attributes={"steps": steps},
                                                page_number=el.page_number, section=el.section,
                                                evidence=sentence[:400], extraction_method="prose_pattern",
                                                confidence="medium"))
                for a, b in zip(steps, steps[1:]):
                    rels.append(ExtractedRelationship(source_name=a, target_name=b, relation_type="flow_step",
                                                      via=flow_name[:200], page_number=el.page_number,
                                                      section=el.section, evidence=sentence[:400],
                                                      extraction_method="prose_pattern", confidence="medium"))
    return entities, rels, mentions


# ------------------------------------------------------------------ merging
def merge_entities(items: list[ExtractedEntity]) -> list[ExtractedEntity]:
    """Deduplicate equivalent entities, keeping every occurrence for traceability.

    Equivalence = same type + same normalised name (+ same owning component for
    ports/responsibilities). Similar-but-different names are NOT merged.
    """
    merged: dict[tuple, ExtractedEntity] = {}
    rank = {"high": 3, "medium": 2, "low": 1}
    for item in items:
        key = item.dedup_key()
        occurrence = {"page_number": item.page_number, "section": item.section, "description": item.description,
                      "external_id": item.external_id, "method": item.extraction_method, "evidence": item.evidence[:200]}
        if key not in merged:
            item.attributes = {**item.attributes, "occurrences": [occurrence]}
            merged[key] = item
            continue
        existing = merged[key]
        existing.attributes.setdefault("occurrences", []).append(occurrence)
        if not existing.description and item.description:
            existing.description = item.description
        if not existing.component and item.component:
            existing.component = item.component
        if not existing.external_id and item.external_id:
            existing.external_id = item.external_id
        for k, v in item.attributes.items():
            if k != "occurrences" and v and not existing.attributes.get(k):
                existing.attributes[k] = v
        if rank[item.confidence] > rank[existing.confidence]:
            existing.confidence = item.confidence
    return list(merged.values())


def merge_relationships(items: list[ExtractedRelationship]) -> list[ExtractedRelationship]:
    merged: dict[tuple, ExtractedRelationship] = {}
    for item in items:
        key = item.dedup_key()
        if key in merged:
            # A table-backed relationship outranks a prose one
            if merged[key].extraction_method != "table_parser" and item.extraction_method == "table_parser":
                merged[key] = item
            continue
        merged[key] = item
    return list(merged.values())


def to_records(revision_id: str, entities: list[ExtractedEntity], rels: list[ExtractedRelationship]) -> tuple[list[dict], list[dict]]:
    ent_records = []
    for e in entities:
        scoped = normalize_name(e.component) if e.entity_type in {"port", "responsibility"} else ""
        ent_records.append({
            "id": stable_id(revision_id, "ent", e.entity_type, e.normalized_name, scoped, e.extraction_method),
            "entity_type": e.entity_type, "name": e.name, "normalized_name": e.normalized_name,
            "external_id": e.external_id, "description": e.description, "component": e.component,
            "attributes": e.attributes, "page_number": e.page_number, "section": e.section[:300],
            "evidence": e.evidence, "extraction_method": e.extraction_method, "confidence": e.confidence,
        })
    rel_records = []
    for r in rels:
        rel_records.append({
            "id": stable_id(revision_id, "rel", *map(str, r.dedup_key()), r.extraction_method),
            "source_name": r.source_name, "target_name": r.target_name, "relation_type": r.relation_type,
            "via": r.via, "description": r.description, "page_number": r.page_number, "section": r.section[:300],
            "evidence": r.evidence, "extraction_method": r.extraction_method, "confidence": r.confidence,
        })
    return ent_records, rel_records


def extract_deterministic(parsed: ParsedDocument) -> dict:
    t_entities, t_rels, table_log = extract_from_tables(parsed)
    components = {e.name for e in t_entities if e.entity_type == "component" and not e.attributes.get("missing_name")}
    p_entities, p_rels, mentions = extract_from_prose(parsed, components)
    entities = merge_entities(t_entities + p_entities)
    rels = merge_relationships(t_rels + p_rels)
    return {"entities": entities, "relationships": rels, "tables": table_log, "mentions": mentions}


# --------------------------------------------------------------------- LLM
def _parse_json_object(text: str) -> dict | None:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def validate_llm_extraction(raw_text: str, source_text: str, page_number: int, section: str) -> tuple[list[ExtractedEntity], list[ExtractedRelationship], int]:
    """Validate an LLM JSON response. Returns (entities, relationships, rejected_count).

    Guardrail: a proposed name must occur verbatim (case-insensitive) in the
    excerpt it was extracted from; otherwise it is discarded as unsupported.
    """
    data = _parse_json_object(raw_text)
    if data is None:
        return [], [], 0
    try:
        payload = LLMExtractionPayload.model_validate(data)
    except ValidationError:
        return [], [], 0
    lower_src = source_text.lower()
    entities, rels, rejected = [], [], 0
    for item in payload.entities:
        try:
            ent = ExtractedEntity(entity_type=item.get("entity_type"), name=str(item.get("name", "")),
                                  description=str(item.get("description", ""))[:500],
                                  component=str(item.get("component", "")), page_number=page_number, section=section,
                                  evidence=str(item.get("evidence", ""))[:400], extraction_method="llm_structured",
                                  confidence="low")
        except (ValidationError, TypeError):
            rejected += 1
            continue
        if ent.name.lower() not in lower_src:
            rejected += 1
            continue
        entities.append(ent)
    for item in payload.relationships:
        try:
            rel = ExtractedRelationship(source_name=str(item.get("source", "")), target_name=str(item.get("target", "")),
                                        relation_type=item.get("relation_type", "depends_on"),
                                        via=str(item.get("via", "")), page_number=page_number, section=section,
                                        evidence=str(item.get("evidence", ""))[:400],
                                        extraction_method="llm_structured", confidence="low")
        except (ValidationError, TypeError):
            rejected += 1
            continue
        if rel.source_name.lower() not in lower_src or rel.target_name.lower() not in lower_src:
            rejected += 1
            continue
        rels.append(rel)
    return entities, rels, rejected

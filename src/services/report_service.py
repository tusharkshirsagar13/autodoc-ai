"""Assemble report data from stored results only (no invented content)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from config.settings import APP_NAME, APP_SUBTITLE, APP_VERSION, Settings
from src.database import repository as repo
from src.utils.errors import NotFoundError

KNOWN_LIMITATIONS = [
    "Entities are extracted from text and recognised tables. Content inside diagrams/images is not interpreted.",
    "Findings labelled 'missing evidence' mean an item was not found in the extracted data; it may exist elsewhere.",
    "AI-generated content (answers, suggestions, summaries) is unverified until reviewed by a qualified engineer.",
    "This report supports engineering review. It does not establish safety, AUTOSAR conformance, "
    "regulatory compliance or design approval.",
]


def _entity_rows(entities: list[dict], etype: str) -> list[dict]:
    return [e for e in entities if e["entity_type"] == etype]


def build_report_data(settings: Settings, revision_id: str, include_rejected: bool = False) -> dict:
    revision = repo.get_revision(revision_id, settings=settings)
    if not revision:
        raise NotFoundError("Revision not found.")
    entities = repo.list_entities(revision_id, include_rejected=include_rejected, settings=settings)
    relationships = repo.list_relationships(revision_id, include_rejected=include_rejected, settings=settings)
    findings = repo.list_findings(revision_id, settings=settings)
    type_counts = Counter(e["entity_type"] for e in entities)
    review_counts = Counter(e["review_status"] for e in entities)
    sev_counts = Counter(f["severity"] for f in findings)
    finding_review = Counter(f["review_status"] for f in findings)
    deps = [r for r in relationships if r["relation_type"] in {"depends_on", "sends_to", "calls", "uses"}]

    if entities or findings:
        summary = (
            f"{revision['display_name']} ({revision['page_count']} pages) was processed by {APP_NAME}. "
            f"The extracted inventory contains {type_counts.get('component', 0)} components, "
            f"{type_counts.get('interface', 0)} interfaces, {type_counts.get('port', 0)} ports, "
            f"{type_counts.get('signal', 0)} signals and {len(deps)} documented dependency/data-flow relationships. "
            f"{len(findings)} potential issues were raised "
            f"({', '.join(f'{v} {k}' for k, v in sev_counts.most_common()) or 'none'}); "
            f"{len(findings) - finding_review.get('open', 0)} have been reviewed. "
            f"{review_counts.get('unreviewed', 0)} of {len(entities)} extracted entities have not yet been reviewed."
        )
    else:
        summary = (f"{revision['display_name']} has no extracted architecture data or findings yet. "
                   "Process the document and run extraction before generating a full report.")
    return {
        "application": {"name": APP_NAME, "subtitle": APP_SUBTITLE, "version": APP_VERSION},
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "document": {
            "title": revision["document_title"], "revision": revision["revision_label"],
            "source_status": revision["source_status"], "filename": revision["original_filename"],
            "pages": revision["page_count"], "uploaded_at": revision["uploaded_at"],
            "processed_at": revision["processed_at"], "sha256": revision["sha256"],
            "chunks": revision["chunk_count"], "tables": revision["table_count"],
            "ocr_pages": revision["ocr_page_count"], "low_text_pages": revision["low_text_pages"],
            "embedding": f"{revision['embedding_backend']}:{revision['embedding_model']}",
            "pdf_title": revision["pdf_title"], "pdf_author": revision["pdf_author"],
        },
        "executive_summary": summary,
        "statistics": {"entity_types": dict(type_counts), "entity_review": dict(review_counts),
                       "finding_severity": dict(sev_counts), "finding_review": dict(finding_review)},
        "components": _entity_rows(entities, "component"),
        "interfaces": _entity_rows(entities, "interface"),
        "ports": _entity_rows(entities, "port"),
        "signals": _entity_rows(entities, "signal"),
        "functional_flows": _entity_rows(entities, "functional_flow"),
        "responsibilities": _entity_rows(entities, "responsibility"),
        "dependencies": deps,
        "relationships": relationships,
        "findings": findings,
        "limitations": KNOWN_LIMITATIONS,
    }


def component_report(settings: Settings, revision_id: str, component_name: str) -> dict:
    """Everything the stored inventory says about one component."""
    from src.architecture.entity_extractor import normalize_name

    key = normalize_name(component_name)
    entities = repo.list_entities(revision_id, include_rejected=False, settings=settings)
    rels = repo.list_relationships(revision_id, include_rejected=False, settings=settings)
    component = next((e for e in entities if e["entity_type"] == "component" and e["normalized_name"] == key), None)
    ports = [e for e in entities if e["entity_type"] == "port" and normalize_name(e["component"]) == key]
    interfaces = {normalize_name(p["attributes"].get("interface", "")) for p in ports}
    signals = [e for e in entities if e["entity_type"] == "signal" and (
        normalize_name(e["attributes"].get("producer", "")) == key
        or key in [normalize_name(c) for c in (e["attributes"].get("consumer", "") or "").replace(";", ",").split(",")]
        or normalize_name(e["attributes"].get("interface", "")) in interfaces)]
    responsibilities = [e for e in entities if e["entity_type"] == "responsibility" and normalize_name(e["component"]) == key]
    outgoing = [r for r in rels if normalize_name(r["source_name"]) == key]
    incoming = [r for r in rels if normalize_name(r["target_name"]) == key]
    flows = [e for e in entities if e["entity_type"] == "functional_flow"
             and key in [normalize_name(s) for s in e["attributes"].get("steps", [])]]
    return {"component": component, "ports": ports, "signals": signals, "responsibilities": responsibilities,
            "outgoing": outgoing, "incoming": incoming, "flows": flows,
            "interfaces": [e for e in entities if e["entity_type"] == "interface" and e["normalized_name"] in interfaces]}

"""Audit trail. Events store identifiers and short metadata only — never
document text, prompts, answers or credentials."""

from __future__ import annotations

from config.settings import Settings
from src.database import repository as repo
from src.utils.logging_config import get_logger

log = get_logger("audit")

EVENT_LABELS = {
    "document_uploaded": "Document uploaded",
    "processing_completed": "Processing completed",
    "processing_failed": "Processing failed",
    "processing_needs_ocr": "Processing stopped — OCR needed",
    "question_asked": "Question answered",
    "extraction_run": "Entity extraction run",
    "ai_extraction_run": "AI-assisted extraction run",
    "consistency_check_run": "Consistency checks run",
    "comparison_run": "Revision comparison run",
    "report_generated": "Report / export generated",
    "finding_reviewed": "Finding reviewed",
    "entity_reviewed": "Entity reviewed",
    "relationship_reviewed": "Relationship reviewed",
    "document_deleted": "Revision deleted",
    "settings_changed": "Settings changed",
    "source_status_changed": "Source status changed",
}


def record(event_type: str, target_type: str = "", target_id: str = "", settings: Settings | None = None,
           **details) -> None:
    safe = {k: v for k, v in details.items() if k not in {"text", "answer", "prompt", "api_key"}}
    try:
        repo.add_audit_event(event_type, target_type, target_id, safe, settings=settings)
    except Exception as exc:  # auditing must never break the user action
        log.warning("Could not record audit event %s: %s", event_type, exc)

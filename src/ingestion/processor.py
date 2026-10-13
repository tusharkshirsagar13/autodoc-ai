"""End-to-end ingestion of one stored revision.

Stages: parse → text sufficiency check → chunk → embed + index (ChromaDB) →
deterministic entity extraction → consistency checks → mark processed.

Re-processing is idempotent: existing vectors for the revision are deleted
before new ones are written, chunk ids are deterministic, and reviewed
entities/findings are preserved. If any stage fails the partial vectors are
removed and the revision is marked `failed` (or `needs_ocr`) — never
`processed`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from config.settings import Settings
from src.ai.embeddings import embedder_key
from src.ai.retrieval import VectorStore
from src.analysis.consistency_checker import RULES, run_checks
from src.architecture.entity_extractor import extract_deterministic, to_records
from src.database import repository as repo
from src.database.models import STATUS_FAILED, STATUS_NEEDS_OCR, STATUS_PROCESSED, STATUS_PROCESSING, utcnow
from src.ingestion.chunker import chunk_document
from src.ingestion.ocr import ocr_status
from src.ingestion.pdf_parser import ParsedDocument, parse_pdf
from src.services import audit_service
from src.utils.errors import AutoDocError, InsufficientTextError
from src.utils.file_validation import ensure_within
from src.utils.logging_config import get_logger

log = get_logger("processor")

MIN_DOCUMENT_CHARS = 200


def parsed_cache_path(settings: Settings, revision_id: str) -> Path:
    directory = settings.data_dir / "parsed"
    directory.mkdir(parents=True, exist_ok=True)
    return ensure_within(directory, directory / f"{revision_id}.json")


def save_parsed(settings: Settings, revision_id: str, parsed: ParsedDocument) -> None:
    parsed_cache_path(settings, revision_id).write_text(json.dumps(parsed.to_dict()), encoding="utf-8")


def load_parsed(settings: Settings, revision_id: str) -> ParsedDocument | None:
    path = parsed_cache_path(settings, revision_id)
    if not path.exists():
        return None
    try:
        return ParsedDocument.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def run_extraction_and_checks(settings: Settings, revision_id: str, parsed: ParsedDocument) -> dict:
    """Deterministic extraction + consistency checks for an already-parsed revision."""
    extracted = extract_deterministic(parsed)
    ent_records, rel_records = to_records(revision_id, extracted["entities"], extracted["relationships"])
    repo.replace_extracted(revision_id, ent_records, rel_records, methods={"table_parser", "prose_pattern"},
                           settings=settings)
    entities = repo.list_entities(revision_id, settings=settings)
    relationships = repo.list_relationships(revision_id, settings=settings)
    findings = run_checks(entities, relationships, extracted["mentions"], parsed.low_text_pages, extracted["tables"])
    finding_stats = repo.upsert_findings(revision_id, findings, rule_scope=set(RULES), settings=settings)
    tables_by_kind: dict[str, int] = {}
    for t in extracted["tables"]:
        tables_by_kind[t["kind"]] = tables_by_kind.get(t["kind"], 0) + 1
    return {"entities": len(ent_records), "relationships": len(rel_records), "findings": finding_stats,
            "tables_by_kind": tables_by_kind}


def process_revision(settings: Settings, revision_id: str, progress=None) -> dict:
    """Process a stored revision. Returns a stats dict; raises AutoDocError on failure."""
    revision = repo.get_revision(revision_id, settings=settings)
    if not revision:
        raise AutoDocError("Revision not found.")
    job_id = repo.start_job(revision_id, settings=settings)
    repo.update_revision(revision_id, settings=settings, status=STATUS_PROCESSING, status_message="Processing…")
    started = time.perf_counter()
    store = None

    def report(stage: str, fraction: float, message: str):
        if progress:
            progress(stage, fraction, message)

    try:
        path = ensure_within(settings.upload_dir, settings.upload_dir / revision["stored_filename"])
        if not path.exists():
            raise AutoDocError("The stored PDF file is missing.", "Delete this revision and upload it again.")

        repo.update_job(job_id, settings=settings, stage="extracting")
        parsed = parse_pdf(path, ocr_enabled=settings.ocr_enabled, ocr_language=settings.ocr_language,
                           ocr_dpi=settings.ocr_dpi, tesseract_cmd=settings.tesseract_cmd,
                           low_text_threshold=settings.low_text_threshold,
                           progress=lambda f, m: report("extract", 0.05 + 0.35 * f, m))
        if parsed.total_chars < MIN_DOCUMENT_CHARS:
            status = ocr_status(settings.tesseract_cmd)
            if not settings.ocr_enabled:
                hint = "This looks like a scanned document. Enable OCR on the Settings page and re-process it."
            elif not status["available"]:
                hint = "This looks like a scanned document and OCR is unavailable: " + status["reason"]
            else:
                hint = "OCR ran but recognised almost no text. Check scan quality or the OCR language setting."
            raise InsufficientTextError(
                f"Only {parsed.total_chars} characters of text could be extracted from {parsed.page_count} page(s).", hint)
        save_parsed(settings, revision_id, parsed)

        repo.update_job(job_id, settings=settings, stage="chunking")
        report("chunk", 0.42, "Splitting text into section-aware chunks")
        chunks = chunk_document(parsed, settings.chunk_size, settings.chunk_overlap)
        if not chunks:
            raise InsufficientTextError("No text chunks could be created from the document.")

        repo.update_job(job_id, settings=settings, stage="embedding")
        report("embed", 0.45, "Loading embedding model")
        store = VectorStore(settings)
        store.delete_revision(revision_id)  # idempotent re-processing: no duplicate vectors
        doc_name = revision["document_title"]
        ids, texts, embed_texts, metas = [], [], [], []
        for c in chunks:
            ids.append(f"{revision_id}:{c.chunk_index:05d}:{c.content_hash}")
            texts.append(c.text)
            embed_texts.append(c.embed_text)
            metas.append({
                "document_id": revision["document_id"], "document_name": doc_name, "revision_id": revision_id,
                "revision_label": revision["revision_label"], "page_number": c.page_number,
                "section_heading": c.section_heading[:300], "chunk_index": c.chunk_index, "chunk_type": c.chunk_type,
                "extraction_method": c.extraction_method, "content_hash": c.content_hash,
            })
        store.add_chunks(ids, texts, embed_texts, metas, progress=lambda f, m: report("embed", 0.45 + 0.4 * f, m))

        repo.update_job(job_id, settings=settings, stage="extracting_entities")
        report("entities", 0.88, "Extracting architecture entities and running checks")
        extraction = run_extraction_and_checks(settings, revision_id, parsed)

        backend, model = embedder_key(settings)
        meta = dict(revision.get("metadata") or {})
        meta.update({"pdf_metadata": parsed.metadata, "ocr_errors": sorted({p.ocr_error for p in parsed.pages if p.ocr_error})})
        repo.update_revision(
            revision_id, settings=settings, status=STATUS_PROCESSED, processed_at=utcnow(),
            status_message="Processed successfully" + (
                f" — {len(parsed.low_text_pages)} page(s) with little text need manual review." if parsed.low_text_pages else "."),
            page_count=parsed.page_count, chunk_count=len(chunks), table_count=parsed.table_count,
            ocr_page_count=len(parsed.ocr_pages), embedding_backend=backend, embedding_model=model,
            pdf_title=str(parsed.metadata.get("title", ""))[:300], pdf_author=str(parsed.metadata.get("author", ""))[:200],
            sections_json=parsed.sections, low_text_pages_json=parsed.low_text_pages, metadata_json=meta,
        )
        stats = {"pages": parsed.page_count, "characters": parsed.total_chars, "chunks": len(chunks),
                 "tables": parsed.table_count, "sections": len(parsed.sections), "ocr_pages": len(parsed.ocr_pages),
                 "low_text_pages": parsed.low_text_pages, **extraction,
                 "seconds": round(time.perf_counter() - started, 1), "embedding": f"{backend}:{model}"}
        repo.update_job(job_id, settings=settings, status="completed", stage="done", message="OK", stats=stats)
        audit_service.record("processing_completed", "revision", revision_id, settings=settings,
                             chunks=len(chunks), entities=extraction["entities"])
        report("done", 1.0, "Done")
        return stats
    except Exception as exc:
        if store is not None:
            try:
                store.delete_revision(revision_id)
            except Exception:  # pragma: no cover - best effort cleanup
                log.warning("Could not clean up vectors for %s", revision_id)
        message = exc.user_message() if isinstance(exc, AutoDocError) else f"Unexpected error: {type(exc).__name__}: {exc}"
        status = STATUS_NEEDS_OCR if isinstance(exc, InsufficientTextError) else STATUS_FAILED
        repo.update_revision(revision_id, settings=settings, status=status, status_message=message[:1000], chunk_count=0)
        repo.update_job(job_id, settings=settings, status="failed", message=message[:1000])
        audit_service.record("processing_needs_ocr" if status == STATUS_NEEDS_OCR else "processing_failed",
                             "revision", revision_id, settings=settings, error=type(exc).__name__)
        log.error("Processing failed for %s: %s", revision_id, message)
        if isinstance(exc, AutoDocError):
            raise
        raise AutoDocError(message) from exc

"""Application service layer used by the Streamlit pages."""

from __future__ import annotations

import time

from config.settings import Settings
from src.ai.llm_provider import get_llm_provider
from src.ai.prompts import EXTRACTION_SYSTEM_PROMPT, EXTRACTION_USER_TEMPLATE
from src.ai.retrieval import VectorStore
from src.analysis import consistency_checker
from src.analysis.revision_comparator import ComparisonResult, compare_revisions, impact_findings
from src.architecture.entity_extractor import merge_entities, merge_relationships, to_records, validate_llm_extraction
from src.database import repository as repo
from src.database.models import SOURCE_STATUSES, STATUS_UPLOADED
from src.ingestion import processor
from src.ingestion.pdf_parser import detect_revision_label, parse_pdf
from src.services import audit_service
from src.utils.errors import AutoDocError, LLMProviderError, NotFoundError, ValidationError
from src.utils.file_validation import ensure_within, new_id, safe_storage_path, validate_pdf_upload
from src.utils.logging_config import get_logger

log = get_logger("document_service")


def peek_pdf(data: bytes) -> dict:
    """Quick look at an upload to pre-fill title and revision label (first two pages only)."""
    import pymupdf

    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            meta = doc.metadata or {}
            text = "\n".join(doc[i].get_text() for i in range(min(2, doc.page_count)))
        first_line = next((ln.strip() for ln in text.splitlines() if len(ln.strip()) > 3), "")
        return {"title": (meta.get("title") or first_line)[:200], "revision": detect_revision_label(text)}
    except Exception:
        return {"title": "", "revision": ""}


def upload_revision(settings: Settings, filename: str, data: bytes, mime_type: str | None = None,
                    document_id: str | None = None, new_document_title: str = "", revision_label: str = "",
                    description: str = "") -> dict:
    """Validate and store an uploaded PDF as a new revision. Does not process it."""
    if repo.count_revisions(settings=settings) >= settings.max_documents:
        raise ValidationError(f"The document limit ({settings.max_documents}) has been reached.",
                              "Delete unused revisions or raise AUTODOC_MAX_DOCUMENTS.")
    upload = validate_pdf_upload(filename, data, settings.max_upload_bytes, mime_type, settings.max_pages)
    duplicate = repo.find_revision_by_hash(upload.sha256, settings=settings)
    if duplicate:
        raise ValidationError(f"This exact file is already stored as '{duplicate['display_name']}'.",
                              "Select the existing revision instead of uploading it again.")
    if document_id:
        parent = repo.get_document(document_id, settings=settings)
        if not parent:
            raise NotFoundError("The selected document no longer exists.")
        labels = {r["revision_label"].lower() for r in parent["revisions"]}
    else:
        title = new_document_title.strip() or upload.safe_name.rsplit(".", 1)[0]
        document_id = repo.create_document(title, description, settings=settings)
        labels = set()
    label = (revision_label or "").strip()[:60] or f"R{len(labels) + 1}"
    if label.lower() in labels:
        raise ValidationError(f"Revision label '{label}' already exists for this document.",
                              "Use a distinct revision label (e.g. B, 1.1, 2026-10).")
    revision_id = new_id("rev")
    path = safe_storage_path(settings.upload_dir, revision_id)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        revision = repo.create_revision(document_id, revision_id, settings=settings, revision_label=label,
                                        original_filename=upload.safe_name, stored_filename=path.name,
                                        sha256=upload.sha256, size_bytes=upload.size_bytes,
                                        page_count=upload.page_count, status=STATUS_UPLOADED)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    audit_service.record("document_uploaded", "revision", revision_id, settings=settings,
                         pages=upload.page_count, size_bytes=upload.size_bytes, revision_label=label)
    return revision


def process(settings: Settings, revision_id: str, progress=None) -> dict:
    return processor.process_revision(settings, revision_id, progress=progress)


def delete_revision(settings: Settings, revision_id: str) -> None:
    revision = repo.get_revision(revision_id, settings=settings)
    if not revision:
        raise NotFoundError("Revision not found.")
    # 1. vectors (all embedding collections that might hold this revision)
    try:
        client_store = VectorStore(settings)
        client_store.delete_revision(revision_id)
        for collection in client_store.client.list_collections():
            name = collection if isinstance(collection, str) else collection.name
            if name.startswith("autodoc-"):
                client_store.client.get_collection(name, embedding_function=None).delete(where={"revision_id": revision_id})
    except Exception as exc:
        log.warning("Vector cleanup for %s incomplete: %s", revision_id, exc)
    # 2. files
    for path in (settings.upload_dir / revision["stored_filename"], processor.parsed_cache_path(settings, revision_id)):
        try:
            ensure_within(path.parent, path).unlink(missing_ok=True)
        except (OSError, ValidationError) as exc:
            log.warning("Could not delete %s: %s", path.name, exc)
    # 3. relational rows (cascade)
    repo.delete_revision_row(revision_id, settings=settings)
    audit_service.record("document_deleted", "revision", revision_id, settings=settings,
                         revision_label=revision["revision_label"])


def set_source_status(settings: Settings, revision_id: str, status: str) -> None:
    if status not in SOURCE_STATUSES:
        raise ValidationError("Unknown source status.")
    repo.update_revision(revision_id, settings=settings, source_status=status)
    audit_service.record("source_status_changed", "revision", revision_id, settings=settings, status=status)


def needs_reindex(settings: Settings, revision: dict) -> bool:
    from src.ai.embeddings import embedder_key

    if revision["status"] != "processed":
        return False
    backend, model = embedder_key(settings)
    return (revision.get("embedding_backend"), revision.get("embedding_model")) != (backend, model)


def get_parsed(settings: Settings, revision_id: str):
    parsed = processor.load_parsed(settings, revision_id)
    if parsed is not None:
        return parsed
    revision = repo.get_revision(revision_id, settings=settings)
    if not revision:
        raise NotFoundError("Revision not found.")
    path = ensure_within(settings.upload_dir, settings.upload_dir / revision["stored_filename"])
    parsed = parse_pdf(path, ocr_enabled=settings.ocr_enabled, ocr_language=settings.ocr_language,
                       ocr_dpi=settings.ocr_dpi, tesseract_cmd=settings.tesseract_cmd,
                       low_text_threshold=settings.low_text_threshold)
    processor.save_parsed(settings, revision_id, parsed)
    return parsed


def rerun_extraction(settings: Settings, revision_id: str) -> dict:
    parsed = get_parsed(settings, revision_id)
    stats = processor.run_extraction_and_checks(settings, revision_id, parsed)
    audit_service.record("extraction_run", "revision", revision_id, settings=settings, entities=stats["entities"])
    audit_service.record("consistency_check_run", "revision", revision_id, settings=settings, **stats["findings"])
    repo.record_analysis("consistency_check", revision_id,
                         f"{stats['entities']} entities, {stats['relationships']} relationships; "
                         f"findings: {stats['findings']['new']} new, {stats['findings']['unchanged']} unchanged, "
                         f"{stats['findings']['resolved']} no longer reproduced.", stats, settings=settings)
    return stats


def run_ai_extraction(settings: Settings, revision_id: str, max_chunks: int = 30, progress=None) -> dict:
    """Ask the configured LLM for additional entities, chunk by chunk. Results are unreviewed suggestions."""
    provider = get_llm_provider(settings)  # raises LLMNotConfiguredError with guidance
    store = VectorStore(settings)
    chunks = [c for c in store.get_revision_chunks(revision_id) if c.metadata.get("chunk_type") == "text"][:max_chunks]
    existing = repo.list_entities(revision_id, settings=settings)
    existing_keys = {(e["entity_type"], e["normalized_name"]) for e in existing}
    new_entities, new_rels, rejected, errors = [], [], 0, 0
    for i, chunk in enumerate(chunks):
        if progress:
            progress(i / max(len(chunks), 1), f"Analysing chunk {i + 1} of {len(chunks)}")
        prompt = EXTRACTION_USER_TEMPLATE.format(page=chunk.page_number, text=chunk.text)
        try:
            result = provider.generate(EXTRACTION_SYSTEM_PROMPT, prompt, max_tokens=1200, temperature=0.0)
        except LLMProviderError:
            errors += 1
            if errors >= 3:
                raise
            continue
        ents, rels, rej = validate_llm_extraction(result.text, chunk.text, chunk.page_number, chunk.section)
        rejected += rej
        new_entities.extend(e for e in ents if (e.entity_type, e.normalized_name) not in existing_keys)
        new_rels.extend(rels)
    entities = merge_entities(new_entities)
    rels = merge_relationships(new_rels)
    ent_records, rel_records = to_records(revision_id, entities, rels)
    repo.replace_extracted(revision_id, ent_records, rel_records, methods={"llm_structured"}, settings=settings)
    stats = {"chunks_analysed": len(chunks), "entities_suggested": len(ent_records),
             "relationships_suggested": len(rel_records), "rejected_unsupported": rejected, "provider_errors": errors,
             "provider": provider.name, "model": provider.model}
    audit_service.record("ai_extraction_run", "revision", revision_id, settings=settings, **stats)
    repo.record_analysis("ai_extraction", revision_id,
                         f"AI suggested {len(ent_records)} entities and {len(rel_records)} relationships "
                         f"({rejected} proposals rejected as unsupported by the source text).", stats, settings=settings)
    return stats


def run_ai_review(settings: Settings, revision_id: str) -> dict:
    provider = get_llm_provider(settings)
    entities = repo.list_entities(revision_id, include_rejected=False, settings=settings)
    rels = repo.list_relationships(revision_id, include_rejected=False, settings=settings)
    existing = repo.list_findings(revision_id, settings=settings)
    observations = consistency_checker.ai_observations(provider, entities, rels, existing)
    stats = repo.upsert_findings(revision_id, observations, rule_scope={"ai_observation"}, settings=settings)
    audit_service.record("consistency_check_run", "revision", revision_id, settings=settings, ai=True, **stats)
    return stats


def compare(settings: Settings, old_revision_id: str, new_revision_id: str) -> ComparisonResult:
    old = repo.get_revision(old_revision_id, settings=settings)
    new = repo.get_revision(new_revision_id, settings=settings)
    if not old or not new:
        raise NotFoundError("One of the selected revisions no longer exists.")
    for rev in (old, new):
        if rev["status"] != "processed":
            raise AutoDocError(f"'{rev['display_name']}' has not been processed yet.", "Process it on the Documents page first.")
    started = time.perf_counter()
    result = compare_revisions(
        get_parsed(settings, old_revision_id), get_parsed(settings, new_revision_id),
        repo.list_entities(old_revision_id, settings=settings), repo.list_entities(new_revision_id, settings=settings),
        repo.list_relationships(old_revision_id, settings=settings), repo.list_relationships(new_revision_id, settings=settings),
        old_label=old["revision_label"], new_label=new["revision_label"],
        same_document=old["document_id"] == new["document_id"])
    repo.record_analysis("revision_comparison", new_revision_id, result.summary_text(),
                         {"counts": result.counts(), "old_revision_id": old_revision_id,
                          "seconds": round(time.perf_counter() - started, 2)},
                         other_revision_id=old_revision_id, settings=settings)
    audit_service.record("comparison_run", "revision", new_revision_id, settings=settings, against=old_revision_id)
    return result


def save_impact_findings(settings: Settings, new_revision_id: str, result: ComparisonResult) -> dict:
    return repo.upsert_findings(new_revision_id, impact_findings(result), rule_scope={"revision_change_impact"},
                                settings=settings)

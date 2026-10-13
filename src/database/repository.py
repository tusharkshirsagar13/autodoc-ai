"""Data-access functions. All public functions return plain dicts so callers
(Streamlit pages, exporters) never touch detached ORM objects."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import delete, func, select

from config.settings import Settings
from src.database.initialization import session_scope
from src.database.models import (Analysis, AuditEvent, ChatSession, Document, Entity, Finding,
                                  ProcessingJob, QAMessage, Relationship, ReviewDecision, Revision,
                                  utcnow)
from src.utils.errors import NotFoundError
from src.utils.file_validation import new_id


def _iso(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


def _row(obj, columns: Iterable[str]) -> dict:
    data = {}
    for col in columns:
        value = getattr(obj, col)
        data[col] = _iso(value) if isinstance(value, datetime) else value
    return data


def _cols(model) -> list[str]:
    return [c.name for c in model.__table__.columns]


# ----------------------------------------------------------------- documents
def revision_dict(rev: Revision) -> dict:
    data = _row(rev, _cols(Revision))
    data["document_title"] = rev.document.title if rev.document else ""
    data["display_name"] = f"{data['document_title']} — {rev.revision_label}"
    data["sections"] = rev.sections
    data["low_text_pages"] = rev.low_text_pages
    data["metadata"] = rev.extra_metadata
    return data


def create_document(title: str, description: str = "", settings: Settings | None = None) -> str:
    doc_id = new_id("doc")
    with session_scope(settings) as s:
        s.add(Document(id=doc_id, title=title.strip()[:300] or "Untitled document", description=description))
    return doc_id


def list_documents(settings: Settings | None = None) -> list[dict]:
    with session_scope(settings) as s:
        docs = s.scalars(select(Document).order_by(Document.created_at.desc())).all()
        out = []
        for d in docs:
            item = _row(d, _cols(Document))
            item["revisions"] = [revision_dict(r) for r in d.revisions]
            out.append(item)
        return out


def get_document(document_id: str, settings: Settings | None = None) -> dict | None:
    with session_scope(settings) as s:
        d = s.get(Document, document_id)
        if not d:
            return None
        item = _row(d, _cols(Document))
        item["revisions"] = [revision_dict(r) for r in d.revisions]
        return item


def count_revisions(settings: Settings | None = None) -> int:
    with session_scope(settings) as s:
        return s.scalar(select(func.count(Revision.id))) or 0


def find_revision_by_hash(sha256: str, settings: Settings | None = None) -> dict | None:
    with session_scope(settings) as s:
        rev = s.scalars(select(Revision).where(Revision.sha256 == sha256)).first()
        return revision_dict(rev) if rev else None


def create_revision(document_id: str, revision_id: str, **fields: Any) -> dict:
    settings = fields.pop("settings", None)
    with session_scope(settings) as s:
        if not s.get(Document, document_id):
            raise NotFoundError("Parent document not found.")
        rev = Revision(id=revision_id, document_id=document_id, **fields)
        s.add(rev)
        s.flush()
        s.refresh(rev)
        return revision_dict(rev)


def get_revision(revision_id: str, settings: Settings | None = None) -> dict | None:
    with session_scope(settings) as s:
        rev = s.get(Revision, revision_id)
        return revision_dict(rev) if rev else None


def list_revisions(status: str | None = None, settings: Settings | None = None) -> list[dict]:
    with session_scope(settings) as s:
        query = select(Revision).order_by(Revision.uploaded_at.desc())
        if status:
            query = query.where(Revision.status == status)
        return [revision_dict(r) for r in s.scalars(query).all()]


def update_revision(revision_id: str, settings: Settings | None = None, **fields: Any) -> None:
    with session_scope(settings) as s:
        rev = s.get(Revision, revision_id)
        if not rev:
            raise NotFoundError("Revision not found.")
        for key, value in fields.items():
            if key.endswith("_json") and not isinstance(value, str):
                value = json.dumps(value)
            setattr(rev, key, value)


def delete_revision_row(revision_id: str, settings: Settings | None = None) -> str | None:
    """Delete a revision (cascades). Returns the parent document id if the document became empty."""
    with session_scope(settings) as s:
        rev = s.get(Revision, revision_id)
        if not rev:
            return None
        doc_id = rev.document_id
        s.execute(delete(ReviewDecision).where(ReviewDecision.revision_id == revision_id))
        s.delete(rev)
        s.flush()
        remaining = s.scalar(select(func.count(Revision.id)).where(Revision.document_id == doc_id))
        if remaining == 0:
            doc = s.get(Document, doc_id)
            if doc:
                s.delete(doc)
            return doc_id
        return None


# ---------------------------------------------------------------------- jobs
def start_job(revision_id: str, settings: Settings | None = None) -> str:
    job_id = new_id("job")
    with session_scope(settings) as s:
        s.add(ProcessingJob(id=job_id, revision_id=revision_id, status="running", stage="validating"))
    return job_id


def update_job(job_id: str, settings: Settings | None = None, **fields: Any) -> None:
    with session_scope(settings) as s:
        job = s.get(ProcessingJob, job_id)
        if not job:
            return
        for key, value in fields.items():
            if key == "stats":
                job.stats_json = json.dumps(value, default=str)
            else:
                setattr(job, key, value)
        if fields.get("status") in {"completed", "failed"}:
            job.finished_at = utcnow()


def list_jobs(revision_id: str | None = None, limit: int = 20, settings: Settings | None = None) -> list[dict]:
    with session_scope(settings) as s:
        query = select(ProcessingJob).order_by(ProcessingJob.started_at.desc()).limit(limit)
        if revision_id:
            query = query.where(ProcessingJob.revision_id == revision_id)
        out = []
        for j in s.scalars(query).all():
            d = _row(j, _cols(ProcessingJob))
            d["stats"] = j.stats
            out.append(d)
        return out


# ------------------------------------------------------------------ entities
def entity_dict(e: Entity) -> dict:
    d = _row(e, _cols(Entity))
    d["attributes"] = e.attributes
    return d


def replace_extracted(revision_id: str, entities: list[dict], relationships: list[dict],
                      methods: set[str] | None = None, settings: Settings | None = None) -> None:
    """Replace previously extracted (unreviewed) entities for the given extraction methods.

    Entities/relationships a human has already reviewed are preserved so that
    re-running extraction never discards review work.
    """
    with session_scope(settings) as s:
        ent_q = delete(Entity).where(Entity.revision_id == revision_id, Entity.review_status == "unreviewed")
        rel_q = delete(Relationship).where(Relationship.revision_id == revision_id,
                                           Relationship.review_status == "unreviewed")
        if methods:
            ent_q = ent_q.where(Entity.extraction_method.in_(methods))
            rel_q = rel_q.where(Relationship.extraction_method.in_(methods))
        s.execute(ent_q)
        s.execute(rel_q)
        existing_ids = set(s.scalars(select(Entity.id).where(Entity.revision_id == revision_id)).all())
        existing_rel = set(s.scalars(select(Relationship.id).where(Relationship.revision_id == revision_id)).all())
        for e in entities:
            if e["id"] in existing_ids:
                continue
            data = dict(e)
            data["attributes_json"] = json.dumps(data.pop("attributes", {}) or {})
            s.add(Entity(revision_id=revision_id, **data))
        for r in relationships:
            if r["id"] in existing_rel:
                continue
            s.add(Relationship(revision_id=revision_id, **r))


def list_entities(revision_id: str, entity_type: str | None = None, include_rejected: bool = True,
                  settings: Settings | None = None) -> list[dict]:
    with session_scope(settings) as s:
        query = select(Entity).where(Entity.revision_id == revision_id)
        if entity_type:
            query = query.where(Entity.entity_type == entity_type)
        if not include_rejected:
            query = query.where(Entity.review_status != "rejected")
        query = query.order_by(Entity.entity_type, Entity.name)
        return [entity_dict(e) for e in s.scalars(query).all()]


def list_relationships(revision_id: str, include_rejected: bool = True, settings: Settings | None = None) -> list[dict]:
    with session_scope(settings) as s:
        query = select(Relationship).where(Relationship.revision_id == revision_id)
        if not include_rejected:
            query = query.where(Relationship.review_status != "rejected")
        return [_row(r, _cols(Relationship)) for r in s.scalars(query.order_by(Relationship.source_name)).all()]


def count_entities(settings: Settings | None = None) -> int:
    with session_scope(settings) as s:
        return s.scalar(select(func.count(Entity.id)).where(Entity.review_status != "rejected")) or 0


def review_entity(entity_id: str, status: str, comment: str = "", corrections: dict | None = None,
                  reviewer: str = "", settings: Settings | None = None) -> str:
    with session_scope(settings) as s:
        e = s.get(Entity, entity_id)
        if not e:
            raise NotFoundError("Entity not found.")
        if corrections:
            for key in ("name", "description", "component"):
                if key in corrections and corrections[key] is not None:
                    setattr(e, key, str(corrections[key]).strip())
            e.normalized_name = e.name.lower()
        e.review_status = status
        e.reviewer_comment = comment
        s.add(ReviewDecision(id=new_id("rev"), revision_id=e.revision_id, target_type="entity",
                             target_id=entity_id, decision=status, comment=comment, reviewer=reviewer))
        return e.revision_id


def review_relationship(rel_id: str, status: str, comment: str = "", reviewer: str = "",
                        settings: Settings | None = None) -> str:
    with session_scope(settings) as s:
        r = s.get(Relationship, rel_id)
        if not r:
            raise NotFoundError("Relationship not found.")
        r.review_status = status
        r.reviewer_comment = comment
        s.add(ReviewDecision(id=new_id("rev"), revision_id=r.revision_id, target_type="relationship",
                             target_id=rel_id, decision=status, comment=comment, reviewer=reviewer))
        return r.revision_id


# ------------------------------------------------------------------ findings
def finding_dict(f: Finding) -> dict:
    d = _row(f, _cols(Finding))
    d["pages"] = f.pages
    return d


def upsert_findings(revision_id: str, findings: list[dict], rule_scope: set[str] | None = None,
                    settings: Settings | None = None) -> dict:
    """Insert new findings, keep existing ones (and their review status) by fingerprint,
    and deactivate previously open findings that no longer reproduce."""
    stats = {"new": 0, "unchanged": 0, "resolved": 0}
    with session_scope(settings) as s:
        query = select(Finding).where(Finding.revision_id == revision_id)
        existing = {f.fingerprint: f for f in s.scalars(query).all()}
        seen = set()
        count = s.scalar(select(func.count(Finding.id)).where(Finding.revision_id == revision_id)) or 0
        for item in findings:
            fp = item["fingerprint"]
            seen.add(fp)
            if fp in existing:
                f = existing[fp]
                f.active = True
                f.evidence = item.get("evidence", f.evidence)
                f.pages_json = json.dumps(item.get("pages", []))
                stats["unchanged"] += 1
                continue
            count += 1
            data = dict(item)
            data["pages_json"] = json.dumps(data.pop("pages", []))
            s.add(Finding(id=new_id("fnd"), revision_id=revision_id, code=f"F-{count:03d}", **data))
            stats["new"] += 1
        for fp, f in existing.items():
            if fp in seen:
                continue
            if rule_scope is not None and f.rule_id not in rule_scope:
                continue
            if f.active and f.review_status == "open":
                f.active = False
                stats["resolved"] += 1
    return stats


def list_findings(revision_id: str | None = None, active_only: bool = True, settings: Settings | None = None) -> list[dict]:
    with session_scope(settings) as s:
        query = select(Finding)
        if revision_id:
            query = query.where(Finding.revision_id == revision_id)
        if active_only:
            query = query.where(Finding.active.is_(True))
        query = query.order_by(Finding.code)
        return [finding_dict(f) for f in s.scalars(query).all()]


def review_finding(finding_id: str, status: str, comment: str = "", reviewer: str = "",
                   settings: Settings | None = None) -> str:
    with session_scope(settings) as s:
        f = s.get(Finding, finding_id)
        if not f:
            raise NotFoundError("Finding not found.")
        f.review_status = status
        f.reviewer_comment = comment
        s.add(ReviewDecision(id=new_id("rev"), revision_id=f.revision_id, target_type="finding",
                             target_id=finding_id, decision=status, comment=comment, reviewer=reviewer))
        return f.revision_id


def finding_counts(settings: Settings | None = None) -> dict:
    with session_scope(settings) as s:
        total = s.scalar(select(func.count(Finding.id)).where(Finding.active.is_(True))) or 0
        reviewed = s.scalar(select(func.count(Finding.id)).where(Finding.active.is_(True),
                                                                 Finding.review_status != "open")) or 0
        return {"total": total, "reviewed": reviewed, "open": total - reviewed}


def list_review_decisions(revision_id: str, settings: Settings | None = None) -> list[dict]:
    with session_scope(settings) as s:
        q = select(ReviewDecision).where(ReviewDecision.revision_id == revision_id).order_by(ReviewDecision.created_at.desc())
        return [_row(r, _cols(ReviewDecision)) for r in s.scalars(q).all()]


# ---------------------------------------------------------------------- chat
def get_or_create_chat_session(revision_id: str, settings: Settings | None = None) -> str:
    with session_scope(settings) as s:
        session = s.scalars(select(ChatSession).where(ChatSession.revision_id == revision_id,
                                                      ChatSession.cleared.is_(False))
                            .order_by(ChatSession.created_at.desc())).first()
        if session:
            return session.id
        sid = new_id("chat")
        s.add(ChatSession(id=sid, revision_id=revision_id))
        return sid


def clear_chat_session(session_id: str, settings: Settings | None = None) -> None:
    with session_scope(settings) as s:
        session = s.get(ChatSession, session_id)
        if session:
            s.delete(session)


def add_qa_message(session_id: str, question: str, answer: str, mode: str, citations: list[dict],
                   provider: str = "", model: str = "", latency_ms: int = 0, settings: Settings | None = None) -> str:
    mid = new_id("msg")
    with session_scope(settings) as s:
        s.add(QAMessage(id=mid, session_id=session_id, question=question, answer=answer, mode=mode,
                        citations_json=json.dumps(citations, default=str), provider=provider, model=model,
                        latency_ms=latency_ms))
    return mid


def list_qa_messages(session_id: str, settings: Settings | None = None) -> list[dict]:
    with session_scope(settings) as s:
        q = select(QAMessage).where(QAMessage.session_id == session_id).order_by(QAMessage.created_at)
        out = []
        for m in s.scalars(q).all():
            d = _row(m, _cols(QAMessage))
            d["citations"] = m.citations
            out.append(d)
        return out


def count_questions(settings: Settings | None = None) -> int:
    with session_scope(settings) as s:
        return s.scalar(select(func.count(QAMessage.id))) or 0


# ------------------------------------------------------------------ analyses
def record_analysis(analysis_type: str, revision_id: str, summary: str, details: dict | None = None,
                    other_revision_id: str | None = None, settings: Settings | None = None) -> str:
    aid = new_id("ana")
    with session_scope(settings) as s:
        s.add(Analysis(id=aid, analysis_type=analysis_type, revision_id=revision_id,
                       other_revision_id=other_revision_id, summary=summary[:2000],
                       details_json=json.dumps(details or {}, default=str)))
    return aid


def list_analyses(limit: int = 10, revision_id: str | None = None, settings: Settings | None = None) -> list[dict]:
    with session_scope(settings) as s:
        q = select(Analysis).order_by(Analysis.created_at.desc()).limit(limit)
        if revision_id:
            q = q.where(Analysis.revision_id == revision_id)
        out = []
        for a in s.scalars(q).all():
            d = _row(a, _cols(Analysis))
            rev = s.get(Revision, a.revision_id)
            d["revision_name"] = f"{rev.document.title} — {rev.revision_label}" if rev else a.revision_id
            out.append(d)
        return out


# --------------------------------------------------------------------- audit
def add_audit_event(event_type: str, target_type: str = "", target_id: str = "", details: dict | None = None,
                    settings: Settings | None = None) -> None:
    with session_scope(settings) as s:
        s.add(AuditEvent(event_type=event_type, target_type=target_type, target_id=target_id,
                         details_json=json.dumps(details or {}, default=str)))


def list_audit_events(limit: int = 200, settings: Settings | None = None) -> list[dict]:
    with session_scope(settings) as s:
        q = select(AuditEvent).order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc()).limit(limit)
        out = []
        for e in s.scalars(q).all():
            d = _row(e, _cols(AuditEvent))
            d["details"] = e.details
            out.append(d)
        return out

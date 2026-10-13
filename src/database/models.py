"""SQLAlchemy ORM models for AutoDoc AI.

Deletion behaviour
------------------
* Deleting a Document deletes all of its Revisions.
* Deleting a Revision deletes its processing jobs, entities, relationships,
  findings, chat sessions (and their messages) and analyses.
  The vector records and stored PDF are removed by DocumentService.
* Review decisions are deleted with the revision they belong to.
* Audit events are retained (they hold identifiers and short metadata only,
  never document text) so that deletions themselves remain auditable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class JsonMixin:
    @staticmethod
    def _load(value: str | None, default):
        if not value:
            return default
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default


# Processing statuses
STATUS_UPLOADED = "uploaded"
STATUS_PROCESSING = "processing"
STATUS_PROCESSED = "processed"
STATUS_FAILED = "failed"
STATUS_NEEDS_OCR = "needs_ocr"

# Review statuses
ENTITY_REVIEW_STATUSES = ["unreviewed", "confirmed", "corrected", "rejected"]
FINDING_REVIEW_STATUSES = ["open", "accepted", "rejected", "needs_investigation"]
SOURCE_STATUSES = ["draft", "approved", "superseded"]

ENTITY_TYPES = ["component", "interface", "port", "signal", "functional_flow", "responsibility"]
RELATION_TYPES = ["depends_on", "sends_to", "provides", "requires", "calls", "uses", "flow_step"]


class Document(Base):
    """A logical HLD document; owns one or more revisions."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    revisions: Mapped[list["Revision"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Revision.uploaded_at"
    )


class Revision(Base, JsonMixin):
    """A specific uploaded file (version) of a document."""

    __tablename__ = "revisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    revision_label: Mapped[str] = mapped_column(String(60))
    source_status: Mapped[str] = mapped_column(String(20), default="draft")
    original_filename: Mapped[str] = mapped_column(String(260))
    stored_filename: Mapped[str] = mapped_column(String(80))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=STATUS_UPLOADED)
    status_message: Mapped[str] = mapped_column(Text, default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pdf_title: Mapped[str] = mapped_column(String(300), default="")
    pdf_author: Mapped[str] = mapped_column(String(200), default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    table_count: Mapped[int] = mapped_column(Integer, default=0)
    ocr_page_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_backend: Mapped[str] = mapped_column(String(40), default="")
    embedding_model: Mapped[str] = mapped_column(String(120), default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    sections_json: Mapped[str] = mapped_column(Text, default="[]")
    low_text_pages_json: Mapped[str] = mapped_column(Text, default="[]")

    document: Mapped[Document] = relationship(back_populates="revisions")
    jobs: Mapped[list["ProcessingJob"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    entities: Mapped[list["Entity"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    relationships: Mapped[list["Relationship"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    findings: Mapped[list["Finding"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    chat_sessions: Mapped[list["ChatSession"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)

    @property
    def sections(self) -> list[dict]:
        return self._load(self.sections_json, [])

    @property
    def low_text_pages(self) -> list[int]:
        return self._load(self.low_text_pages_json, [])

    @property
    def extra_metadata(self) -> dict:
        return self._load(self.metadata_json, {})

    @property
    def display_name(self) -> str:
        title = self.document.title if self.document else self.original_filename
        return f"{title} — {self.revision_label}"


class ProcessingJob(Base, JsonMixin):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    stage: Mapped[str] = mapped_column(String(60), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    stats_json: Mapped[str] = mapped_column(Text, default="{}")

    @property
    def stats(self) -> dict:
        return self._load(self.stats_json, {})


class Entity(Base, JsonMixin):
    __tablename__ = "entities"
    __table_args__ = (Index("ix_entity_rev_type", "revision_id", "entity_type"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200), index=True)
    external_id: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    component: Mapped[str] = mapped_column(String(200), default="")
    attributes_json: Mapped[str] = mapped_column(Text, default="{}")
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str] = mapped_column(String(300), default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    extraction_method: Mapped[str] = mapped_column(String(40), default="table_parser")
    confidence: Mapped[str] = mapped_column(String(20), default="medium")
    review_status: Mapped[str] = mapped_column(String(20), default="unreviewed")
    reviewer_comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def attributes(self) -> dict:
        return self._load(self.attributes_json, {})


class Relationship(Base):
    __tablename__ = "relationships"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id", ondelete="CASCADE"), index=True)
    source_name: Mapped[str] = mapped_column(String(200))
    target_name: Mapped[str] = mapped_column(String(200))
    relation_type: Mapped[str] = mapped_column(String(30), default="depends_on")
    via: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str] = mapped_column(String(300), default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    extraction_method: Mapped[str] = mapped_column(String(40), default="table_parser")
    confidence: Mapped[str] = mapped_column(String(20), default="medium")
    review_status: Mapped[str] = mapped_column(String(20), default="unreviewed")
    reviewer_comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    cleared: Mapped[bool] = mapped_column(Boolean, default=False)
    messages: Mapped[list["QAMessage"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="QAMessage.created_at"
    )


class QAMessage(Base, JsonMixin):
    __tablename__ = "qa_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(30), default="llm")
    citations_json: Mapped[str] = mapped_column(Text, default="[]")
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def citations(self) -> list[dict]:
        return self._load(self.citations_json, [])


class Analysis(Base, JsonMixin):
    """A record of an analysis run (comparison, consistency check, report, summary)."""

    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    analysis_type: Mapped[str] = mapped_column(String(40))
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id", ondelete="CASCADE"), index=True)
    other_revision_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    summary: Mapped[str] = mapped_column(Text, default="")
    details_json: Mapped[str] = mapped_column(Text, default="{}")

    @property
    def details(self) -> dict:
        return self._load(self.details_json, {})


class Finding(Base, JsonMixin):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    rule_id: Mapped[str] = mapped_column(String(60))
    category: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(20))
    summary: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    pages_json: Mapped[str] = mapped_column(Text, default="[]")
    detection_method: Mapped[str] = mapped_column(String(40), default="deterministic_rule")
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_kind: Mapped[str] = mapped_column(String(30), default="observed")
    review_status: Mapped[str] = mapped_column(String(30), default="open")
    reviewer_comment: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def pages(self) -> list[int]:
        return self._load(self.pages_json, [])


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[str] = mapped_column(String(40), index=True)
    decision: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(Text, default="")
    reviewer: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditEvent(Base, JsonMixin):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str] = mapped_column(String(30), default="")
    target_id: Mapped[str] = mapped_column(String(40), default="")
    details_json: Mapped[str] = mapped_column(Text, default="{}")

    @property
    def details(self) -> dict:
        return self._load(self.details_json, {})


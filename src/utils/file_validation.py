"""Upload validation and safe file-path handling."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

from src.utils.errors import ValidationError

ALLOWED_EXTENSIONS = {".pdf"}
ALLOWED_MIME_TYPES = {"application/pdf", "application/x-pdf", "application/octet-stream", ""}
PDF_MAGIC = b"%PDF-"


@dataclass
class ValidatedUpload:
    original_name: str
    safe_name: str
    size_bytes: int
    sha256: str
    page_count: int


def sanitize_filename(name: str, max_length: int = 120) -> str:
    """Return a filesystem-safe display filename (no directories, no control chars)."""
    name = unicodedata.normalize("NFKC", name or "")
    # Strip any directory component, regardless of separator style.
    name = re.split(r"[\\/]", name)[-1]
    name = re.sub(r"[^A-Za-z0-9._ \-()]", "_", name).strip(" .")
    name = re.sub(r"_+", "_", name)
    if not name:
        name = "document.pdf"
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, "pdf"
    stem = stem[: max_length - len(ext) - 1] or "document"
    return f"{stem}.{ext.lower()}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_pdf_upload(filename: str, data: bytes, max_bytes: int, mime_type: str | None = None,
                        max_pages: int | None = None) -> ValidatedUpload:
    """Validate an uploaded PDF. Raises ValidationError with an actionable hint."""
    if not filename:
        raise ValidationError("The uploaded file has no name.", "Rename the file and upload it again.")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(f"Unsupported file type '{ext or 'none'}'.", "Only PDF documents (.pdf) are supported.")
    if mime_type is not None and mime_type.lower() not in ALLOWED_MIME_TYPES:
        raise ValidationError(f"Unexpected MIME type '{mime_type}'.", "Upload a genuine PDF document.")
    size = len(data)
    if size == 0:
        raise ValidationError("The uploaded file is empty.", "Check that the file was saved correctly.")
    if size > max_bytes:
        raise ValidationError(
            f"The file is {size / 1_048_576:.1f} MB, above the {max_bytes / 1_048_576:.0f} MB limit.",
            "Split the document or raise AUTODOC_MAX_UPLOAD_MB in your .env file.",
        )
    if not data[:1024].lstrip().startswith(PDF_MAGIC):
        raise ValidationError("The file does not start with a PDF header.", "The file may be corrupted or not a PDF.")

    import pymupdf  # local import keeps module importable without PyMuPDF for docs tooling

    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass:
                raise ValidationError("The PDF is password-protected.", "Remove the password and upload again.")
            page_count = doc.page_count
    except ValidationError:
        raise
    except Exception as exc:  # PyMuPDF raises various error types for broken files
        raise ValidationError("The PDF could not be opened.", f"It may be damaged ({type(exc).__name__}).") from exc
    if page_count == 0:
        raise ValidationError("The PDF contains no pages.")
    if max_pages and page_count > max_pages:
        raise ValidationError(
            f"The PDF has {page_count} pages, above the {max_pages}-page limit.",
            "Raise AUTODOC_MAX_PAGES in .env if your machine can handle it.",
        )
    return ValidatedUpload(
        original_name=filename,
        safe_name=sanitize_filename(filename),
        size_bytes=size,
        sha256=sha256_bytes(data),
        page_count=page_count,
    )


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def safe_storage_path(base_dir: Path, revision_id: str) -> Path:
    """Build a storage path from a generated id and make sure it stays inside base_dir."""
    if not re.fullmatch(r"[A-Za-z0-9_\-]{4,64}", revision_id):
        raise ValidationError("Invalid internal document identifier.")
    base = base_dir.resolve()
    path = (base / f"{revision_id}.pdf").resolve()
    if base not in path.parents:
        raise ValidationError("Refusing to write outside the upload directory.")
    return path


def ensure_within(base_dir: Path, path: Path) -> Path:
    base = base_dir.resolve()
    resolved = path.resolve()
    if resolved != base and base not in resolved.parents:
        raise ValidationError("Refusing to access a file outside the application directory.")
    return resolved

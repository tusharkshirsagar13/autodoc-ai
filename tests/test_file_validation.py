import pymupdf
import pytest

from src.utils.errors import ValidationError
from src.utils.file_validation import (ensure_within, safe_storage_path, sanitize_filename, validate_pdf_upload)

MB = 1024 * 1024


def _pdf_bytes(pages: int = 1, text: str = "Hello") -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd.pdf", "passwd.pdf"),
    ("..\\..\\windows\\evil.PDF", "evil.pdf"),
    ("my design<>|:*?.pdf", "my design_.pdf"),
    ("", "document.pdf"),
])
def test_sanitize_filename_strips_paths_and_unsafe_chars(raw, expected):
    assert sanitize_filename(raw) == expected


def test_valid_pdf_is_accepted(sample_pdfs):
    data = sample_pdfs["A"].read_bytes()
    result = validate_pdf_upload("hld.pdf", data, 10 * MB, "application/pdf")
    assert result.page_count == 5
    assert len(result.sha256) == 64


@pytest.mark.parametrize("name,data,message", [
    ("notes.txt", b"%PDF-1.4 fake", "Unsupported file type"),
    ("empty.pdf", b"", "empty"),
    ("fake.pdf", b"This is not a PDF at all", "PDF header"),
    ("broken.pdf", b"%PDF-1.7\n garbage garbage", "could not be opened"),
])
def test_invalid_files_are_rejected(name, data, message):
    with pytest.raises(ValidationError) as exc:
        validate_pdf_upload(name, data, 10 * MB)
    assert message.lower() in exc.value.user_message().lower()


def test_oversized_file_is_rejected():
    with pytest.raises(ValidationError, match="limit"):
        validate_pdf_upload("big.pdf", _pdf_bytes(), max_bytes=100)


def test_page_limit_enforced():
    with pytest.raises(ValidationError, match="page"):
        validate_pdf_upload("many.pdf", _pdf_bytes(pages=3), 10 * MB, max_pages=2)


def test_wrong_mime_type_rejected():
    with pytest.raises(ValidationError, match="MIME"):
        validate_pdf_upload("x.pdf", _pdf_bytes(), 10 * MB, mime_type="text/html")


def test_safe_storage_path_rejects_traversal(tmp_path):
    assert safe_storage_path(tmp_path, "rev_abc123").parent == tmp_path.resolve()
    for bad in ("../escape", "a/b", "..", "rev 1"):
        with pytest.raises(ValidationError):
            safe_storage_path(tmp_path, bad)


def test_ensure_within(tmp_path):
    inside = tmp_path / "x.pdf"
    assert ensure_within(tmp_path, inside) == inside.resolve()
    with pytest.raises(ValidationError):
        ensure_within(tmp_path / "sub", tmp_path / "other.pdf")


def test_duplicate_upload_and_label_rejected(settings, sample_pdfs):
    from src.services import document_service

    data = sample_pdfs["A"].read_bytes()
    rev = document_service.upload_revision(settings, "a.pdf", data, new_document_title="Doc", revision_label="A")
    with pytest.raises(ValidationError, match="already stored"):
        document_service.upload_revision(settings, "copy.pdf", data, new_document_title="Doc 2")
    with pytest.raises(ValidationError, match="already exists"):
        document_service.upload_revision(settings, "b.pdf", sample_pdfs["B"].read_bytes(),
                                         document_id=rev["document_id"], revision_label="a")
    # stored under a generated id, not the user's filename
    assert rev["stored_filename"].startswith("rev_") and rev["stored_filename"].endswith(".pdf")

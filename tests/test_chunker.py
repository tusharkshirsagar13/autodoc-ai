import pytest

from src.ingestion.chunker import _split_long_text, chunk_document
from src.ingestion.pdf_parser import parse_pdf


@pytest.fixture(scope="module")
def parsed(sample_pdfs):
    return parse_pdf(sample_pdfs["B"], ocr_enabled=False)


def test_chunks_have_complete_metadata(parsed):
    chunks = chunk_document(parsed, chunk_size=900, chunk_overlap=150)
    assert chunks
    for i, c in enumerate(chunks):
        assert c.chunk_index == i
        assert 1 <= c.page_number <= parsed.page_count
        assert c.chunk_type in {"text", "table"}
        assert c.extraction_method in {"text", "ocr"}
        assert len(c.content_hash) == 16
        assert c.text.strip()


def test_chunk_text_comes_from_its_page(parsed):
    """Page-number preservation: every chunk's text must appear on the page it cites."""
    page_text = {p.page_number: p.text for p in parsed.pages}
    for c in chunk_document(parsed, 600, 100):
        sample = c.text.replace("[Table]\n", "").splitlines()[0][:40]
        assert sample in page_text[c.page_number] or c.chunk_type == "table"


def test_section_heading_recorded(parsed):
    chunks = chunk_document(parsed, 900, 150)
    yaw = next(c for c in chunks if "Sig_YawRate" in c.text)
    assert yaw.section_heading == "4.2 VehicleStateEstimator"
    assert yaw.page_number == 2
    assert yaw.embed_text.startswith("Section: 4.2 VehicleStateEstimator")


def test_tables_split_with_repeated_header(parsed):
    chunks = chunk_document(parsed, chunk_size=300, chunk_overlap=0)
    signal_chunks = [c for c in chunks if c.chunk_type == "table" and "Sig_" in c.text and c.page_number == 4
                     and "Data Type" in c.text]
    assert len(signal_chunks) >= 2
    for c in signal_chunks:
        assert "Signal | Interface | Data Type" in c.text


def test_chunk_size_respected_for_text(parsed):
    for c in chunk_document(parsed, chunk_size=400, chunk_overlap=50):
        if c.chunk_type == "text":
            assert len(c.text) <= 400 + 120  # a heading line may be kept with its paragraph


def test_split_long_text_overlap():
    text = " ".join(f"Sentence number {i} describes the BrakeTorqueCoordinator." for i in range(40))
    pieces = _split_long_text(text, size=300, overlap=80)
    assert len(pieces) > 3
    assert all(len(p) <= 300 for p in pieces)
    # consecutive pieces share some words (overlap)
    assert set(pieces[0].split()[-5:]) & set(pieces[1].split()[:15])

import pymupdf
import pytest

from src.ingestion.pdf_parser import ParsedDocument, _clean_cell, detect_revision_label, parse_pdf
from src.utils.errors import InsufficientTextError


@pytest.fixture(scope="module")
def parsed_a(sample_pdfs):
    return parse_pdf(sample_pdfs["A"], ocr_enabled=False)


def test_page_count_and_text(parsed_a):
    assert parsed_a.page_count == 5
    assert len(parsed_a.pages) == 5
    assert parsed_a.total_chars > 3000
    assert [p.page_number for p in parsed_a.pages] == [1, 2, 3, 4, 5]


def test_page_numbers_preserved_for_headings(parsed_a):
    pages = {s["title"]: s["page_number"] for s in parsed_a.sections}
    assert pages["4 Software Components"] == 2
    assert pages["5 Ports and Port Interfaces"] == 3
    assert pages["6 Signals"] == 4
    assert pages["8 Functional Flows"] == 5
    assert pages["4.2 VehicleStateEstimator"] == 2


def test_heading_levels(parsed_a):
    levels = {s["title"]: s["level"] for s in parsed_a.sections}
    assert levels["4 Software Components"] == 1
    assert levels["4.1 WheelSpeedProcessor"] == 2


def test_tables_extracted_with_identifiers_intact(parsed_a):
    tables = [e for e in parsed_a.elements() if e.kind == "table"]
    assert len(tables) == 7
    port_table = next(t for t in tables if t.rows[0][1] == "Port")
    assert port_table.page_number == 3
    assert ["WheelSpeedProcessor", "PP_WheelSpeed", "Provided", "IF_WheelSpeed", "Sender-Receiver"] in port_table.rows
    signal_table = next(t for t in tables if t.rows[0][0] == "Signal")
    producers = {row[5] for row in signal_table.rows[1:]}
    assert "WheelSpeedProcessor" in producers  # wrapped identifier re-joined


def test_elements_carry_section(parsed_a):
    el = next(e for e in parsed_a.elements() if "Sig_YawRate" in e.text)
    assert el.page_number == 2
    assert el.section == "4.2 VehicleStateEstimator"


def test_revision_and_title_detection(parsed_a, sample_pdfs):
    assert parsed_a.revision_hint == "A"
    assert parse_pdf(sample_pdfs["B"], ocr_enabled=False).revision_hint == "B"
    assert "Electronic Brake Control" in parsed_a.title_hint
    assert detect_revision_label("Revision History\nDocument ID 1 Revision: 2.1") == "2.1"


def test_clean_cell_joins_broken_identifiers():
    assert _clean_cell("VehicleStateEstim\nator") == "VehicleStateEstimator"
    assert _clean_cell("Commands\nthe valves") == "Commands the valves"
    assert _clean_cell("-100..10\n0") == "-100..100"
    assert _clean_cell("Revision 2\n2026 baseline") == "Revision 2 2026 baseline"


def test_roundtrip_serialisation(parsed_a):
    clone = ParsedDocument.from_dict(parsed_a.to_dict())
    assert clone.page_count == parsed_a.page_count
    assert clone.sections == parsed_a.sections
    assert [e.text for e in clone.elements()] == [e.text for e in parsed_a.elements()]


def test_low_text_pages_detected(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "1 Overview of the architecture\n" + "Real text. " * 40)
    doc.new_page()  # blank page, like a scanned image without OCR
    path = tmp_path / "mixed.pdf"
    doc.save(path)
    parsed = parse_pdf(path, ocr_enabled=False)
    assert parsed.low_text_pages == [2]
    assert parsed.pages[1].method == "empty"


def test_scanned_document_is_not_marked_processed(settings, tmp_path):
    """A PDF without extractable text must end as 'needs_ocr', never 'processed'."""
    from src.database import repository as repo
    from src.services import document_service

    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page().draw_rect(pymupdf.Rect(50, 50, 300, 300), fill=(0.2, 0.2, 0.2))
    data = doc.tobytes()
    rev = document_service.upload_revision(settings, "scan.pdf", data, new_document_title="Scanned")
    with pytest.raises(InsufficientTextError):
        document_service.process(settings, rev["id"])
    stored = repo.get_revision(rev["id"], settings=settings)
    assert stored["status"] == "needs_ocr"
    assert "OCR" in stored["status_message"]
    assert stored["chunk_count"] == 0


def test_ocr_reads_scanned_page_when_tesseract_available(sample_pdfs, tmp_path):
    from src.ingestion.ocr import ocr_status

    if not ocr_status("")["available"]:
        pytest.skip("Tesseract is not installed")
    # Build an image-only PDF from page 2 of the sample (simulates a scanned document)
    with pymupdf.open(sample_pdfs["A"]) as src:
        png = src[1].get_pixmap(dpi=200).tobytes("png")
    scan = pymupdf.open()
    page = scan.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=png)
    path = tmp_path / "scan.pdf"
    scan.save(path)
    parsed = parse_pdf(path, ocr_enabled=True)
    assert parsed.pages[0].method == "ocr"
    assert "WheelSpeedProcessor" in parsed.pages[0].text
    assert parse_pdf(path, ocr_enabled=False).pages[0].method == "empty"

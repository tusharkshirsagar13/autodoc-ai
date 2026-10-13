import pymupdf
import pytest

from src.analysis.revision_comparator import compare_revisions, impact_findings, section_key
from src.architecture.entity_extractor import extract_deterministic, to_records
from src.ingestion.pdf_parser import parse_pdf


def _prepare(path, rid):
    parsed = parse_pdf(path, ocr_enabled=False)
    x = extract_deterministic(parsed)
    ents, rels = to_records(rid, x["entities"], x["relationships"])
    return parsed, ents, rels


@pytest.fixture(scope="module")
def result(sample_pdfs):
    pa, ea, ra = _prepare(sample_pdfs["A"], "rev_a")
    pb, eb, rb = _prepare(sample_pdfs["B"], "rev_b")
    return compare_revisions(pa, pb, ea, eb, ra, rb, "A", "B")


def test_component_additions_and_removals(result):
    comps = result.entity_changes["component"]
    assert [c["name"] for c in comps["added"]] == ["PedalInputHandler"]
    assert [c["name"] for c in comps["removed"]] == ["HmiWarningManager"]


def test_changed_signal_attributes(result):
    changed = {c["name"]: c for c in result.entity_changes["signal"]["changed"]}
    diffs = {d["field"]: (d["old"], d["new"]) for d in changed["Sig_VehicleSpeed"]["differences"]}
    assert diffs["unit"] == ("km/h", "0.01 km/h")
    assert diffs["data_type"] == ("float32", "uint16")
    added = {s["name"] for s in result.entity_changes["signal"]["added"]}
    assert {"Sig_BrakePedalPosition", "Sig_ActuatorHealth"} <= added
    assert {i["name"] for i in result.entity_changes["interface"]["added"]} == {"IF_ActuatorStatus", "IF_PedalRequest"}


def test_sections_added_and_modified(result):
    assert [s["title"] for s in result.added_sections] == ["9 Degraded Mode Handling"]
    assert result.added_sections[0]["pages_new"] == [5]
    modified = {s["title"] for s in result.modified_sections}
    assert "3 Architecture Overview" in modified
    assert result.unchanged_sections > 0
    assert result.structural_similarity > 0.8 and not result.warnings


def test_relationship_changes_and_impact(result):
    removed = {(r["source"], r["target"]) for r in result.relationship_changes["removed"]}
    assert ("HmiWarningManager", "DiagnosticEventHandler") in removed
    affected = {(d["source"], d["target"], d["relation"]) for d in result.affected_dependencies}
    assert ("BrakeTorqueCoordinator", "PedalInputHandler", "depends_on") in affected
    findings = impact_findings(result)
    assert findings and all(f["rule_id"] == "revision_change_impact" and not f["is_ai_generated"] for f in findings)
    assert all("not mean the revision is defective" in f["explanation"] for f in findings)


def test_renumbered_sections_still_match():
    assert section_key("4.2 VehicleStateEstimator") == section_key("5.1 VehicleStateEstimator")


def test_unrelated_documents_warn(sample_pdfs, tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for title in ("1 Kitchen Layout", "2 Appliances", "3 Lighting Plan"):
        page.insert_text((72, y), title, fontsize=16, fontname="hebo")
        page.insert_text((72, y + 24), "Some unrelated content about a kitchen renovation project.", fontsize=10)
        y += 80
    path = tmp_path / "other.pdf"
    doc.save(path)
    pa, ea, ra = _prepare(sample_pdfs["A"], "rev_a")
    po, eo, ro = _prepare(path, "rev_o")
    res = compare_revisions(pa, po, ea, eo, ra, ro, "A", "X", same_document=False)
    assert any("different documents" in w for w in res.warnings)
    assert any("may be incomplete" in w for w in res.warnings)


def test_service_level_comparison_records_analysis(settings, processed):
    from src.database import repository as repo
    from src.services import document_service

    res = document_service.compare(settings, processed["A"]["id"], processed["B"]["id"])
    assert res.counts()["entities_added"] >= 7
    analyses = repo.list_analyses(settings=settings)
    assert analyses[0]["analysis_type"] == "revision_comparison"
    stats = document_service.save_impact_findings(settings, processed["B"]["id"], res)
    assert stats["new"] > 0
    again = document_service.save_impact_findings(settings, processed["B"]["id"], res)
    assert again["new"] == 0 and again["unchanged"] == stats["new"]

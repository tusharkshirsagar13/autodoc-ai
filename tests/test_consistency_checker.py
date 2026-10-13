from src.analysis.consistency_checker import ai_observations, run_checks
from src.architecture.entity_extractor import extract_deterministic, to_records
from src.ingestion.pdf_parser import parse_pdf
from tests.conftest import FakeProvider


def _findings_for(path):
    parsed = parse_pdf(path, ocr_enabled=False)
    x = extract_deterministic(parsed)
    ents, rels = to_records("rev", x["entities"], x["relationships"])
    return run_checks(ents, rels, x["mentions"], parsed.low_text_pages, x["tables"]), ents, rels, x


def test_expected_issues_found_in_revision_a(sample_pdfs):
    findings, *_ = _findings_for(sample_pdfs["A"])
    by_rule = {}
    for f in findings:
        by_rule.setdefault(f["rule_id"], []).append(f)
    assert "C-05" in by_rule["duplicate_component_id"][0]["summary"]
    assert by_rule["duplicate_component_id"][0]["severity"] == "High"
    assert "IF_DiagEvent" in by_rule["empty_interface_definition"][0]["summary"]
    assert "IF_ActuatorStatus" in by_rule["undefined_interface_reference"][0]["summary"]
    assert "PedalInputHandler" in by_rule["unresolved_dependency"][0]["summary"]
    assert {f["summary"].split()[1] for f in by_rule["signal_not_in_inventory"]} == {"Sig_YawRate", "Sig_BrakePedalPosition"}
    assert "HmiWarningManager" in by_rule["missing_component_description"][0]["summary"]
    assert "VehicleStateEstimator" in by_rule["conflicting_description"][0]["summary"]
    assert by_rule["conflicting_description"][0]["pages"] == [2, 5]


def test_revision_b_resolves_issues(sample_pdfs):
    findings, *_ = _findings_for(sample_pdfs["B"])
    rules = {f["rule_id"] for f in findings}
    assert "duplicate_component_id" not in rules
    assert "unresolved_dependency" not in rules
    assert "undefined_interface_reference" not in rules
    assert {"empty_interface_definition", "conflicting_description", "signal_not_in_inventory"} <= rules


def test_missing_evidence_is_not_called_a_defect(sample_pdfs):
    findings, *_ = _findings_for(sample_pdfs["A"])
    for f in findings:
        assert f["is_ai_generated"] is False
        assert f["detection_method"].startswith("deterministic_rule:")
        if f["evidence_kind"] == "missing_evidence":
            assert "not a confirmed defect" in f["explanation"]
        assert f["fingerprint"] and f["summary"]


def test_rejected_entities_are_excluded(sample_pdfs):
    _, ents, rels, x = _findings_for(sample_pdfs["A"])
    for e in ents:
        if e["name"] == "HmiWarningManager":
            e["review_status"] = "rejected"
    findings = run_checks(ents, rels, x["mentions"], [], x["tables"])
    assert not any("HmiWarningManager has no description" in f["summary"] for f in findings)


def test_review_status_survives_rerun(settings, processed):
    from src.database import repository as repo
    from src.services import document_service

    rid = processed["A"]["id"]
    findings = repo.list_findings(rid, settings=settings)
    target = next(f for f in findings if f["rule_id"] == "duplicate_component_id")
    repo.review_finding(target["id"], "accepted", "Confirmed with architect", "Tester", settings=settings)
    stats = document_service.rerun_extraction(settings, rid)
    assert stats["findings"]["new"] == 0
    again = {f["id"]: f for f in repo.list_findings(rid, settings=settings)}
    assert again[target["id"]]["review_status"] == "accepted"
    assert again[target["id"]]["reviewer_comment"] == "Confirmed with architect"
    decisions = repo.list_review_decisions(rid, settings=settings)
    assert decisions[0]["decision"] == "accepted"


def test_ai_observations_marked_as_suggestions(sample_pdfs):
    _, ents, rels, _ = _findings_for(sample_pdfs["A"])
    provider = FakeProvider('{"observations": [{"summary": "Check whether HmiWarningManager needs ports", '
                            '"explanation": "No ports listed.", "entities": ["HmiWarningManager"], "pages": [2, 99]}]}')
    obs = ai_observations(provider, ents, rels, [])
    assert len(obs) == 1
    assert obs[0]["is_ai_generated"] and obs[0]["severity"] == "Info"
    assert "requires human verification" in obs[0]["explanation"]
    assert obs[0]["pages"] == [2]  # unknown page numbers are dropped
    assert ai_observations(FakeProvider("no json here"), ents, rels, []) == []

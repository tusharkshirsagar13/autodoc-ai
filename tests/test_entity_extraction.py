import pytest
from pydantic import ValidationError

from src.architecture.entity_extractor import (ExtractedEntity, ExtractedRelationship, classify_table,
                                               extract_deterministic, merge_entities, stable_id, to_records,
                                               validate_llm_extraction)
from src.architecture.graph_builder import build_graph
from src.architecture.relationship_extractor import annotate_resolution, derive_interface_links
from src.ingestion.pdf_parser import parse_pdf


@pytest.fixture(scope="module")
def extracted_a(sample_pdfs):
    return extract_deterministic(parse_pdf(sample_pdfs["A"], ocr_enabled=False))


def _names(extracted, etype):
    return {e.name for e in extracted["entities"] if e.entity_type == etype}


def test_components_extracted_with_pages(extracted_a):
    assert _names(extracted_a, "component") == {"WheelSpeedProcessor", "VehicleStateEstimator", "BrakeTorqueCoordinator",
                                                "BrakeActuatorManager", "DiagnosticEventHandler", "HmiWarningManager"}
    wsp = next(e for e in extracted_a["entities"] if e.name == "WheelSpeedProcessor")
    assert wsp.page_number == 2 and wsp.external_id == "C-01" and wsp.extraction_method == "table_parser"
    assert "10 ms" in wsp.description and wsp.evidence


def test_ports_interfaces_signals(extracted_a):
    ports = {(e.component, e.name): e for e in extracted_a["entities"] if e.entity_type == "port"}
    assert ("BrakeActuatorManager", "PP_ActuatorStatus") in ports
    assert ports[("VehicleStateEstimator", "RP_WheelSpeed")].attributes["direction"] == "Required"
    assert _names(extracted_a, "interface") == {"IF_WheelSpeed", "IF_VehicleState", "IF_BrakeTorqueCmd", "IF_DiagEvent"}
    signals = _names(extracted_a, "signal")
    assert {"Sig_WheelSpeed_FL", "Sig_WheelSpeed_FR", "Sig_VehicleSpeed"} <= signals
    vs = next(e for e in extracted_a["entities"] if e.name == "Sig_VehicleSpeed")
    assert vs.attributes["unit"] == "km/h" and vs.page_number == 4


def test_dependencies_and_flows(extracted_a):
    deps = {(r.source_name, r.target_name) for r in extracted_a["relationships"] if r.relation_type == "depends_on"}
    assert ("VehicleStateEstimator", "WheelSpeedProcessor") in deps
    assert ("BrakeTorqueCoordinator", "PedalInputHandler") in deps
    flows = {e.name: e for e in extracted_a["entities"] if e.entity_type == "functional_flow"}
    assert flows["FF-02 Wheel slip control"].attributes["steps"] == [
        "WheelSpeedProcessor", "VehicleStateEstimator", "BrakeTorqueCoordinator", "BrakeActuatorManager"]
    resp = [e for e in extracted_a["entities"] if e.entity_type == "responsibility"]
    assert resp and resp[0].component == "WheelSpeedProcessor"


def test_entity_schema_validation():
    with pytest.raises(ValidationError):
        ExtractedEntity(entity_type="gearbox", name="X")
    with pytest.raises(ValidationError):
        ExtractedEntity(entity_type="component", name="")
    with pytest.raises(ValidationError):
        ExtractedRelationship(source_name="A", target_name="B", relation_type="likes")
    e = ExtractedEntity(entity_type="signal", name="  Sig_A  ", page_number=3)
    assert e.name == "Sig_A"


def test_duplicate_entities_merged_but_distinct_names_kept():
    items = [
        ExtractedEntity(entity_type="component", name="BrakeActuatorManager", description="Drives valves", page_number=2),
        ExtractedEntity(entity_type="component", name="brakeactuatormanager", description="Drives valves", page_number=5),
        ExtractedEntity(entity_type="signal", name="Sig_WheelSpeed_FL", page_number=4),
        ExtractedEntity(entity_type="signal", name="Sig_WheelSpeed_FR", page_number=4),
        ExtractedEntity(entity_type="port", name="RP_Status", component="A", page_number=3),
        ExtractedEntity(entity_type="port", name="RP_Status", component="B", page_number=3),
    ]
    merged = merge_entities(items)
    comps = [e for e in merged if e.entity_type == "component"]
    assert len(comps) == 1 and [o["page_number"] for o in comps[0].attributes["occurrences"]] == [2, 5]
    assert len([e for e in merged if e.entity_type == "signal"]) == 2
    assert len([e for e in merged if e.entity_type == "port"]) == 2  # same port name on different components


def test_stable_ids_are_deterministic(extracted_a):
    first, _ = to_records("rev_1", extracted_a["entities"], extracted_a["relationships"])
    second, _ = to_records("rev_1", extracted_a["entities"], extracted_a["relationships"])
    assert [e["id"] for e in first] == [e["id"] for e in second]
    assert len({e["id"] for e in first}) == len(first)
    assert stable_id("rev_1", "ent", "x") != stable_id("rev_2", "ent", "x")


def test_table_classification():
    assert classify_table(["ID", "Component", "Type", "Description"]) == "component"
    assert classify_table(["Component", "Port", "Direction", "Interface"]) == "port"
    assert classify_table(["Signal", "Interface", "Data Type", "Unit"]) == "signal"
    assert classify_table(["Source", "Relation", "Target"]) == "dependency"
    assert classify_table(["Revision", "Date", "Description"]) == "revision_history"
    assert classify_table(["Foo", "Bar"]) is None


def test_llm_extraction_rejects_unsupported_names():
    source = "The BrakeTorqueCoordinator depends on the VehicleStateEstimator via IF_VehicleState."
    reply = """```json
    {"entities": [{"entity_type": "component", "name": "BrakeTorqueCoordinator", "description": "x"},
                  {"entity_type": "component", "name": "SteeringController", "description": "invented"},
                  {"entity_type": "spaceship", "name": "VehicleStateEstimator"}],
     "relationships": [{"source": "BrakeTorqueCoordinator", "target": "VehicleStateEstimator", "relation_type": "depends_on"},
                       {"source": "BrakeTorqueCoordinator", "target": "GhostModule", "relation_type": "calls"}]}
    ```"""
    ents, rels, rejected = validate_llm_extraction(reply, source, page_number=7, section="3 Overview")
    assert [e.name for e in ents] == ["BrakeTorqueCoordinator"]
    assert ents[0].extraction_method == "llm_structured" and ents[0].confidence == "low" and ents[0].page_number == 7
    assert [(r.source_name, r.target_name) for r in rels] == [("BrakeTorqueCoordinator", "VehicleStateEstimator")]
    assert rejected == 3
    assert validate_llm_extraction("not json", source, 1, "") == ([], [], 0)


def test_graph_contains_only_documented_edges(extracted_a):
    ents, rels = to_records("rev_x", extracted_a["entities"], extracted_a["relationships"])
    graph = build_graph(ents, rels, {"depends_on"})
    expected = {(r["source_name"], r["target_name"]) for r in rels if r["relation_type"] == "depends_on"}
    assert {(u, v) for u, v in graph.edges()} == expected
    assert graph.nodes["PedalInputHandler"]["kind"] == "unresolved"
    assert build_graph(ents, [], {"depends_on"}).number_of_edges() == 0


def test_derived_interface_links_are_labelled(extracted_a):
    ents, rels = to_records("rev_x", extracted_a["entities"], extracted_a["relationships"])
    links = derive_interface_links(ents)
    assert ("VehicleStateEstimator", "WheelSpeedProcessor") in {(l["source_name"], l["target_name"]) for l in links}
    assert all(l["extraction_method"] == "derived_interface_link" for l in links)
    annotated = annotate_resolution(rels, ents)
    pedal = next(r for r in annotated if r["target_name"] == "PedalInputHandler")
    assert pedal["source_resolved"] and not pedal["target_resolved"]

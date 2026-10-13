"""Architecture explorer: entity inventory, component detail, dependency graph and review."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from config.settings import get_settings
from src.ai.llm_provider import llm_status
from src.architecture.graph_builder import build_graph, plotly_figure
from src.architecture.relationship_extractor import annotate_resolution, derive_interface_links
from src.database import repository as repo
from src.database.models import ENTITY_REVIEW_STATUSES
from src.exports.csv_exporter import entities_csv
from src.exports.json_exporter import to_json_bytes
from src.services import audit_service, document_service
from src.services.report_service import component_report
from src.ui.components import (badge, context_bar, empty_state, esc, evidence_block, page_header,
                               require_active_revision, show_error)
from src.ui.resources import get_pipeline
from src.utils.errors import AutoDocError

settings = get_settings()
page_header("Architecture Explorer", "Components, ports, interfaces, signals, dependencies and functional flows "
                                     "extracted from the document — each traceable to its page.")
revision = require_active_revision()
if not revision:
    st.stop()
context_bar(revision)
rid = revision["id"]

entities = repo.list_entities(rid, settings=settings)
relationships = repo.list_relationships(rid, settings=settings)
status = llm_status(settings)

a1, a2, a3 = st.columns([1.3, 1.6, 3])
if a1.button("Re-run extraction", icon=":material/refresh:", width="stretch",
             help="Re-runs deterministic table/prose extraction and the consistency checks. Reviewed items are kept."):
    with st.spinner("Extracting entities…"):
        try:
            stats = document_service.rerun_extraction(settings, rid)
            st.toast(f"{stats['entities']} entities, {stats['relationships']} relationships extracted.")
        except Exception as exc:
            show_error(exc)
    st.rerun()
ai_help = ("Ask the language model to propose additional entities from up to 30 text chunks. Proposals whose names do "
           "not appear verbatim in the source are discarded; the rest are stored as unreviewed, low-confidence suggestions.")
if a2.button("AI-assisted extraction", icon=":material/auto_awesome:", width="stretch", help=ai_help,
             disabled=not status["configured"]):
    bar = st.progress(0.0, text="Starting")
    try:
        stats = document_service.run_ai_extraction(settings, rid, progress=lambda f, m: bar.progress(f, text=m))
        st.session_state["ai_extract_result"] = stats
    except AutoDocError as exc:
        st.error(exc.user_message(), icon=":material/error:")
    st.rerun()
if not status["configured"]:
    a3.caption("AI-assisted extraction needs a configured language model (Settings). Deterministic extraction works without one.")
elif status["external"]:
    a3.caption(f"AI-assisted extraction sends document excerpts to {status['destination']}.")
if res := st.session_state.pop("ai_extract_result", None):
    st.success(f"AI suggested {res['entities_suggested']} entities and {res['relationships_suggested']} relationships from "
               f"{res['chunks_analysed']} chunks; {res['rejected_unsupported']} proposals were rejected as unsupported by the "
               "source text. Suggestions are marked 'llm_structured' and need review.", icon=":material/auto_awesome:")

if not entities:
    empty_state("No architecture entities extracted",
                "No recognised component, port, interface or signal tables were found. Use AI-assisted extraction, "
                "or check the Findings page for unrecognised tables.")
    st.stop()

tabs = st.tabs([":material/widgets: Components", ":material/hub: Dependency graph", ":material/list: Inventory",
                ":material/route: Functional flows", ":material/rate_review: Review"])

# ------------------------------------------------------------ components
with tabs[0]:
    comps = [e for e in entities if e["entity_type"] == "component" and e["review_status"] != "rejected"]
    if not comps:
        empty_state("No components", "No components were extracted for this document.")
    else:
        search = st.text_input("Search components", placeholder="Filter by name or description", key="comp_search")
        shown = [c for c in comps if search.lower() in (c["name"] + " " + c["description"]).lower()]
        st.dataframe(pd.DataFrame([{"ID": c["external_id"], "Component": c["name"], "Description": c["description"],
                                    "Page": c["page_number"], "Confidence": c["confidence"], "Review": c["review_status"]}
                                   for c in shown]), hide_index=True, width="stretch")
        names = sorted({c["name"] for c in comps if not c["attributes"].get("missing_name")})
        selected = st.selectbox("Component detail", names, key="comp_detail")
        if selected:
            rep = component_report(settings, rid, selected)
            comp = rep["component"]
            with st.container(border=True):
                st.markdown(f"### {esc(selected)}")
                if comp:
                    st.markdown(f"{badge(comp['confidence'])} {badge(comp['review_status'])} "
                                f"<span class='ad-muted'>ID {esc(comp['external_id'] or '—')} · page {comp['page_number']} · "
                                f"{esc(comp['section'])} · {esc(comp['extraction_method'])}</span>", unsafe_allow_html=True)
                    st.markdown(comp["description"] or "_No description in the document._")
                    occ = comp["attributes"].get("occurrences", [])
                    if len(occ) > 1:
                        st.caption("Also defined on page(s) " + ", ".join(str(o["page_number"]) for o in occ[1:]) +
                                   ": " + " | ".join(o["description"] for o in occ[1:] if o["description"]))
                d1, d2 = st.columns(2, gap="large")
                with d1:
                    st.markdown("**Responsibilities**")
                    for r in rep["responsibilities"]:
                        st.markdown(f"- {esc(r['description'])} <span class='ad-muted'>(p. {r['page_number']})</span>",
                                    unsafe_allow_html=True)
                    if not rep["responsibilities"]:
                        st.caption("None listed separately.")
                    st.markdown("**Ports**")
                    if rep["ports"]:
                        st.dataframe(pd.DataFrame([{"Port": p["name"], "Direction": p["attributes"].get("direction", ""),
                                                    "Interface": p["attributes"].get("interface", ""), "Page": p["page_number"]}
                                                   for p in rep["ports"]]), hide_index=True, width="stretch")
                    else:
                        st.caption("No ports in the extracted inventory.")
                    st.markdown("**Signals**")
                    if rep["signals"]:
                        st.dataframe(pd.DataFrame([{"Signal": s["name"], "Producer": s["attributes"].get("producer", ""),
                                                    "Consumer": s["attributes"].get("consumer", ""),
                                                    "Unit": s["attributes"].get("unit", ""), "Page": s["page_number"]}
                                                   for s in rep["signals"]]), hide_index=True, width="stretch")
                    else:
                        st.caption("No associated signals found.")
                with d2:
                    st.markdown("**Documented relationships**")
                    rel_rows = [{"Direction": "outgoing", "Relation": r["relation_type"], "Other": r["target_name"],
                                 "Via": r["via"], "Page": r["page_number"], "Method": r["extraction_method"]} for r in rep["outgoing"]]
                    rel_rows += [{"Direction": "incoming", "Relation": r["relation_type"], "Other": r["source_name"],
                                  "Via": r["via"], "Page": r["page_number"], "Method": r["extraction_method"]} for r in rep["incoming"]]
                    if rel_rows:
                        st.dataframe(pd.DataFrame(rel_rows), hide_index=True, width="stretch")
                    else:
                        st.caption("No relationships documented for this component.")
                    st.markdown("**Functional flows**")
                    for f in rep["flows"]:
                        st.markdown(f"- **{esc(f['name'])}**: {esc(f['description'])} <span class='ad-muted'>(p. {f['page_number']})</span>",
                                    unsafe_allow_html=True)
                    if not rep["flows"]:
                        st.caption("Not part of any extracted flow.")
                if st.button("Generate AI component summary with citations", icon=":material/summarize:",
                             disabled=not status["configured"], key="comp_summary_btn"):
                    with st.spinner("Retrieving evidence and summarising…"):
                        result = get_pipeline(settings).answer(
                            f"Summarize the responsibilities, ports, interfaces, signals and documented dependencies of the "
                            f"component {selected}.", [rid])
                    st.session_state[f"comp_summary_{rid}_{selected}"] = result
                    audit_service.record("report_generated", "revision", rid, settings=settings, kind="component_summary")
                result = st.session_state.get(f"comp_summary_{rid}_{selected}")
                if result:
                    st.markdown("**AI summary** — " + ("grounded in cited sources" if result.mode == "answered" else result.mode.replace("_", " ")))
                    st.markdown(result.answer)
                    if result.notice:
                        st.caption(result.notice)
                    st.caption("Cited pages: " + (", ".join(str(c.page_number) for c in result.cited) or "none"))

# ------------------------------------------------------------- graph
with tabs[1]:
    g1, g2 = st.columns([3, 1.4])
    available = sorted({r["relation_type"] for r in relationships} - {"provides", "requires"})
    rel_types = g1.multiselect("Relationship types", available, default=[t for t in available if t != "flow_step"] or available,
                               help="'flow_step' edges come from functional-flow descriptions.")
    include_derived = g2.toggle("Include derived interface links", value=False,
                                help="Adds requirer → provider links derived from the port table (same interface on a "
                                     "required and a provided port). These are labelled 'derived_interface_link'.")
    rels = list(relationships)
    if include_derived:
        rels += derive_interface_links(entities)
        rel_types = list(set(rel_types) | {"uses"})
    graph = build_graph(entities, rels, set(rel_types))
    if graph.number_of_edges() == 0:
        empty_state("No documented relationships to draw",
                    "No dependency, data-flow or call relationships were extracted for the selected types. "
                    "The graph is never filled with assumed edges.")
    else:
        st.plotly_chart(plotly_figure(graph), width="stretch", config={"displaylogo": False})
        unresolved = [n for n, d in graph.nodes(data=True) if d.get("kind") == "unresolved"]
        st.caption(f"{graph.number_of_nodes()} nodes · {graph.number_of_edges()} edges. Hover over the diamond markers on "
                   "edges for the relation, the signal/interface and the source page."
                   + (f" Orange diamonds ({', '.join(unresolved)}) are referenced but not defined as components." if unresolved else ""))
        annotated = annotate_resolution([r for r in rels if r["relation_type"] in set(rel_types)], entities)
        with st.expander("Edge list with evidence"):
            st.dataframe(pd.DataFrame([{"Source": r["source_name"], "Relation": r["relation_type"], "Target": r["target_name"],
                                        "Via": r["via"], "Page": r["page_number"], "Method": r["extraction_method"],
                                        "Target defined": r["target_resolved"], "Evidence": r["evidence"]}
                                       for r in annotated]), hide_index=True, width="stretch")

# ----------------------------------------------------------- inventory
with tabs[2]:
    f1, f2, f3, f4 = st.columns(4)
    types = sorted({e["entity_type"] for e in entities})
    sel_types = f1.multiselect("Type", types, default=types)
    comp_opts = sorted({e["component"] for e in entities if e["component"]})
    sel_comp = f2.selectbox("Component", ["(any)"] + comp_opts)
    sel_review = f3.multiselect("Review status", ENTITY_REVIEW_STATUSES, default=[s for s in ENTITY_REVIEW_STATUSES if s != "rejected"])
    query = f4.text_input("Search", placeholder="name, description…")
    rows = [e for e in entities if e["entity_type"] in sel_types and e["review_status"] in sel_review
            and (sel_comp == "(any)" or e["component"] == sel_comp)
            and query.lower() in (e["name"] + " " + e["description"] + " " + e["component"]).lower()]
    st.dataframe(pd.DataFrame([{"Type": e["entity_type"], "Name": e["name"], "Component": e["component"],
                                "Description": e["description"], "Page": e["page_number"], "Section": e["section"],
                                "Method": e["extraction_method"], "Confidence": e["confidence"], "Review": e["review_status"],
                                "Evidence": e["evidence"]} for e in rows]),
                 hide_index=True, width="stretch", height=420)
    e1, e2, _ = st.columns([1, 1, 3])
    e1.download_button(f"Export {len(rows)} as CSV", entities_csv(rows), file_name=f"autodoc_entities_{revision['revision_label']}.csv",
                       mime="text/csv", icon=":material/download:", width="stretch", disabled=not rows)
    e2.download_button("Export as JSON", to_json_bytes({"document": revision["display_name"], "entities": rows}),
                       file_name=f"autodoc_entities_{revision['revision_label']}.json", mime="application/json",
                       icon=":material/data_object:", width="stretch", disabled=not rows)

# --------------------------------------------------------------- flows
with tabs[3]:
    flows = [e for e in entities if e["entity_type"] == "functional_flow" and e["review_status"] != "rejected"]
    if not flows:
        empty_state("No functional flows extracted",
                    "Flows are recognised from lines such as 'FF-01 Name: A -> B -> C'. Ask the document about flows instead.")
    for f in flows:
        with st.container(border=True):
            st.markdown(f"**{esc(f['name'])}** <span class='ad-muted'>· page {f['page_number']} · {esc(f['section'])}</span>",
                        unsafe_allow_html=True)
            st.markdown(" → ".join(f"`{s}`" for s in f["attributes"].get("steps", [])))
            evidence_block(f["evidence"])

# -------------------------------------------------------------- review
with tabs[4]:
    st.caption("Confirm, correct or reject extracted items. Reviewed items are preserved when extraction is re-run. "
               "Rejected items are excluded from the graph, checks and reports.")
    kind = st.radio("Review", ["Entities", "Relationships"], horizontal=True, label_visibility="collapsed")
    if kind == "Entities":
        only_unreviewed = st.toggle("Show only unreviewed", value=True)
        pool = [e for e in entities if not only_unreviewed or e["review_status"] == "unreviewed"]
        if not pool:
            st.success("Everything has been reviewed.")
        else:
            labels = {e["id"]: f"[{e['entity_type']}] {e['name']}" + (f" ({e['component']})" if e["component"] else "")
                      + f" · p.{e['page_number']} · {e['extraction_method']}" for e in pool}
            eid = st.selectbox("Entity", list(labels), format_func=lambda i: labels[i])
            ent = next(e for e in pool if e["id"] == eid)
            evidence_block(ent["evidence"] or "(no evidence excerpt)")
            with st.form(f"review_{eid}"):
                c1, c2 = st.columns(2)
                new_name = c1.text_input("Name", ent["name"])
                new_comp = c2.text_input("Associated component", ent["component"])
                new_desc = st.text_area("Description", ent["description"], height=80)
                decision = st.radio("Decision", ["confirmed", "corrected", "rejected"], horizontal=True,
                                    help="Choose 'corrected' if you edited the fields above.")
                comment = st.text_input("Comment (optional)")
                reviewer = st.text_input("Reviewer", value=st.session_state.get("reviewer_name", ""))
                if st.form_submit_button("Save review", type="primary"):
                    corrections = {"name": new_name, "component": new_comp, "description": new_desc} if decision == "corrected" else None
                    repo.review_entity(eid, decision, comment, corrections, reviewer, settings=settings)
                    st.session_state["reviewer_name"] = reviewer
                    audit_service.record("entity_reviewed", "entity", eid, settings=settings, decision=decision)
                    st.toast("Review saved.")
                    st.rerun()
    else:
        pool = [r for r in relationships if r["review_status"] == "unreviewed"]
        if not pool:
            st.success("All relationships have been reviewed.")
        else:
            labels = {r["id"]: f"{r['source_name']} —{r['relation_type']}→ {r['target_name']}"
                      + (f" via {r['via']}" if r["via"] else "") + f" · p.{r['page_number']} · {r['extraction_method']}" for r in pool}
            relid = st.selectbox("Relationship", list(labels), format_func=lambda i: labels[i])
            rel = next(r for r in pool if r["id"] == relid)
            evidence_block(rel["evidence"] or "(no evidence excerpt)")
            with st.form(f"rreview_{relid}"):
                decision = st.radio("Decision", ["confirmed", "rejected"], horizontal=True)
                comment = st.text_input("Comment (optional)")
                reviewer = st.text_input("Reviewer", value=st.session_state.get("reviewer_name", ""))
                if st.form_submit_button("Save review", type="primary"):
                    repo.review_relationship(relid, decision, comment, reviewer, settings=settings)
                    st.session_state["reviewer_name"] = reviewer
                    audit_service.record("relationship_reviewed", "relationship", relid, settings=settings, decision=decision)
                    st.toast("Review saved.")
                    st.rerun()

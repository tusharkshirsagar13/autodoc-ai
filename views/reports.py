"""Report generation and structured exports for the active revision."""

from __future__ import annotations

import streamlit as st

from config.settings import get_settings
from src.ai.llm_provider import llm_status
from src.exports.csv_exporter import entities_csv, findings_csv, relationships_csv
from src.exports.excel_exporter import inventory_workbook
from src.exports.json_exporter import report_json
from src.exports.pdf_exporter import report_pdf
from src.services import audit_service
from src.services.report_service import build_report_data
from src.ui.components import context_bar, esc, evidence_block, metric_card, page_header, require_active_revision, show_error
from src.ui.resources import get_pipeline

settings = get_settings()
page_header("Reports & Export", "Download the architecture inventory, findings and a formatted report built from stored results.")
revision = require_active_revision()
if not revision:
    st.stop()
context_bar(revision)
rid = revision["id"]

include_rejected = st.toggle("Include rejected items", value=False,
                             help="By default, entities and relationships rejected during review are left out.")
report = build_report_data(settings, rid, include_rejected=include_rejected)

st.markdown("##### Executive summary")
st.markdown(report["executive_summary"])
c = st.columns(5)
for col, (label, key) in zip(c, [("Components", "components"), ("Interfaces", "interfaces"), ("Ports", "ports"),
                                 ("Signals", "signals"), ("Findings", "findings")]):
    with col:
        metric_card(label, len(report[key]))

st.markdown("##### Downloads")
stem = f"autodoc_{revision['document_title'][:40].replace(' ', '_')}_rev{revision['revision_label']}"
stem = "".join(ch for ch in stem if ch.isalnum() or ch in "_-")

if st.button("Prepare report files", type="primary", icon=":material/build:"):
    try:
        with st.spinner("Building PDF, Excel and JSON…"):
            st.session_state[f"files_{rid}"] = {
                "pdf": report_pdf(report), "xlsx": inventory_workbook(report), "json": report_json(report),
                "entities": entities_csv(report["components"] + report["interfaces"] + report["ports"] + report["signals"]
                                         + report["functional_flows"] + report["responsibilities"]),
                "relationships": relationships_csv(report["relationships"]), "findings": findings_csv(report["findings"]),
            }
        audit_service.record("report_generated", "revision", rid, settings=settings, kind="report_bundle")
    except Exception as exc:
        show_error(exc)
files = st.session_state.get(f"files_{rid}")
if files:
    d = st.columns(3)
    d[0].download_button("PDF report", files["pdf"], f"{stem}.pdf", "application/pdf", icon=":material/picture_as_pdf:", width="stretch")
    d[1].download_button("Excel inventory", files["xlsx"], f"{stem}.xlsx",
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", icon=":material/table:", width="stretch")
    d[2].download_button("JSON (machine-readable)", files["json"], f"{stem}.json", "application/json",
                         icon=":material/data_object:", width="stretch")
    e = st.columns(3)
    e[0].download_button("Entities (CSV)", files["entities"], f"{stem}_entities.csv", "text/csv", icon=":material/download:", width="stretch")
    e[1].download_button("Relationships (CSV)", files["relationships"], f"{stem}_relationships.csv", "text/csv",
                         icon=":material/download:", width="stretch")
    e[2].download_button("Findings (CSV)", files["findings"], f"{stem}_findings.csv", "text/csv", icon=":material/download:", width="stretch")
    st.caption("Files reflect the stored results at the time you pressed 'Prepare report files'.")
else:
    st.caption("Press 'Prepare report files' to build the downloads from the current stored results.")

st.divider()
st.markdown("##### Functional-flow summaries")
flows = report["functional_flows"]
if not flows:
    st.caption("No functional flows were extracted from this document.")
for f in flows:
    st.markdown(f"**{esc(f['name'])}** — {esc(f['description'])} <span class='ad-muted'>(p. {f['page_number']}, "
                f"{esc(f['section'])})</span>", unsafe_allow_html=True)
status = llm_status(settings)
if flows:
    if st.button("Generate AI flow summary with citations", icon=":material/auto_awesome:", disabled=not status["configured"]):
        with st.spinner("Retrieving evidence and summarising…"):
            result = get_pipeline(settings).answer(
                "Summarize each functional flow described in the document: its trigger, the components involved in order, "
                "the signals exchanged and any thresholds or conditions.", [rid], top_k=8)
        st.session_state[f"flow_summary_{rid}"] = result
        audit_service.record("report_generated", "revision", rid, settings=settings, kind="flow_summary")
    if not status["configured"]:
        st.caption("Configure a language model for an AI-written summary; the deterministic flow list above is always available.")
    result = st.session_state.get(f"flow_summary_{rid}")
    if result:
        st.markdown(result.answer)
        if result.notice:
            st.caption(result.notice)
        for cit in result.cited:
            with st.expander(f"[{cit.label}] page {cit.page_number} · {cit.section}"):
                evidence_block(cit.excerpt)

st.divider()
st.markdown("##### Known limitations included in every report")
for item in report["limitations"]:
    st.markdown(f"- {item}")

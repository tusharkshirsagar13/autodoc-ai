"""Compare two processed revisions deterministically, with optional AI summary."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from config.settings import APP_NAME, get_settings
from src.ai.llm_provider import get_llm_provider, llm_status
from src.ai.prompts import COMPARISON_SYSTEM_PROMPT
from src.analysis.revision_comparator import diff_digest
from src.exports.excel_exporter import comparison_workbook
from src.exports.json_exporter import comparison_json
from src.exports.pdf_exporter import comparison_pdf
from src.services import audit_service, document_service
from src.ui.components import empty_state, esc, metric_card, page_header, processed_revisions, show_error
from src.utils.errors import AutoDocError

settings = get_settings()
page_header("Revision Comparison", "Deterministic differences between two revisions — sections, entities and "
                                   "documented relationships — with optional AI-generated impact notes.")

revisions = processed_revisions()
if len(revisions) < 2:
    empty_state("Two processed revisions are needed",
                "Upload a second revision of a document (Documents → 'A new revision of an existing document'), "
                "process it, and return here. The synthetic sample includes revisions A and B.")
    st.page_link("views/documents.py", label="Go to Documents", icon=":material/upload_file:")
    st.stop()

labels = {r["id"]: r["display_name"] for r in revisions}
ids = [r["id"] for r in sorted(revisions, key=lambda r: r["uploaded_at"])]
by_id = {r["id"]: r for r in revisions}
active = st.session_state.get("active_revision_id")
default_new = active if active in ids else ids[-1]
same_doc = [i for i in ids if by_id[i]["document_id"] == by_id[default_new]["document_id"] and i != default_new]
default_old = same_doc[-1] if same_doc else next(i for i in ids if i != default_new)

c1, c2, c3 = st.columns([2, 2, 1])
old_id = c1.selectbox("Baseline (older) revision", ids, index=ids.index(default_old), format_func=lambda i: labels[i])
new_id = c2.selectbox("Compared (newer) revision", ids, index=ids.index(default_new), format_func=lambda i: labels[i])
c3.write("")
run = c3.button("Compare", type="primary", icon=":material/compare_arrows:", width="stretch", disabled=old_id == new_id)
if old_id == new_id:
    st.caption("Select two different revisions.")
if by_id[old_id]["document_id"] != by_id[new_id]["document_id"]:
    st.info("These revisions belong to different documents. The comparison will run, but differences reflect two "
            "documents rather than a change history.", icon=":material/info:")

key = f"cmp_{old_id}_{new_id}"
if run:
    with st.spinner("Comparing revisions…"):
        try:
            comparison = document_service.compare(settings, old_id, new_id)
            st.session_state[key] = comparison.to_dict()
            st.session_state[key + "_obj"] = comparison
            st.session_state[key + "_digest"] = diff_digest(comparison)
            st.session_state.pop(key + "_ai", None)
        except AutoDocError as exc:
            st.error(exc.user_message(), icon=":material/error:")
        except Exception as exc:
            show_error(exc)

result = st.session_state.get(key)
if not result:
    st.stop()

for warning in result["warnings"]:
    st.warning(warning, icon=":material/warning:")
counts = result["counts"]
m = st.columns(5)
with m[0]:
    metric_card("Sections changed", counts["sections_added"] + counts["sections_removed"] + counts["sections_modified"],
                f"+{counts['sections_added']} / -{counts['sections_removed']} / ~{counts['sections_modified']}")
with m[1]:
    metric_card("Entities added", counts["entities_added"])
with m[2]:
    metric_card("Entities removed", counts["entities_removed"])
with m[3]:
    metric_card("Entities changed", counts["entities_changed"])
with m[4]:
    metric_card("Affected relationships", counts["affected_dependencies"], "involve changed items")
st.caption(f"Structural similarity: {int(result['structural_similarity'] * 100)}% of section headings match. "
           "All differences below are computed deterministically from text and extracted inventories.")

tabs = st.tabs([":material/summarize: Summary", ":material/widgets: Entities", ":material/article: Sections",
                ":material/hub: Relationships", ":material/download: Export"])

with tabs[0]:
    st.markdown("##### Direct textual and inventory differences")
    for etype, bucket in result["entity_changes"].items():
        parts = []
        if bucket["added"]:
            parts.append("added " + ", ".join(f"`{i['name']}`" for i in bucket["added"]))
        if bucket["removed"]:
            parts.append("removed " + ", ".join(f"`{i['name']}`" for i in bucket["removed"]))
        if bucket["changed"]:
            parts.append("changed " + ", ".join(f"`{i['name']}`" for i in bucket["changed"]))
        if parts:
            st.markdown(f"- **{etype.replace('_', ' ').capitalize()}s**: " + "; ".join(parts))
    for s in result["added_sections"]:
        st.markdown(f"- Section added: **{esc(s['title'])}** (new p. {', '.join(map(str, s['pages_new']))})")
    for s in result["removed_sections"]:
        st.markdown(f"- Section removed: **{esc(s['title'])}** (old p. {', '.join(map(str, s['pages_old']))})")

    st.markdown("##### Potential engineering impact (AI inference — requires review)")
    status = llm_status(settings)
    if not status["configured"]:
        st.caption("Configure a language model to generate an AI summary of these differences. The deterministic "
                   "results above do not depend on it.")
    else:
        if status["external"]:
            st.caption(f"The difference list (which includes document text) will be sent to {status['destination']}.")
        if st.button("Generate AI summary", icon=":material/auto_awesome:"):
            with st.spinner("Summarising differences…"):
                try:
                    provider = get_llm_provider(settings)
                    digest = st.session_state.get(key + "_digest", "")
                    reply = provider.generate(COMPARISON_SYSTEM_PROMPT, f"<differences>\n{digest}\n</differences>",
                                              temperature=0.0)
                    st.session_state[key + "_ai"] = reply.text
                    audit_service.record("report_generated", "revision", new_id, settings=settings, kind="comparison_ai_summary")
                except AutoDocError as exc:
                    st.error(exc.user_message(), icon=":material/error:")
        if st.session_state.get(key + "_ai"):
            st.info(st.session_state[key + "_ai"], icon=":material/auto_awesome:")

    st.markdown("##### Record change-impact findings")
    st.caption("Creates one reviewable 'Change impact' finding on the newer revision for every documented relationship "
               "that involves a changed item. A change is not treated as a defect.")
    if st.button("Save change-impact findings", icon=":material/fact_check:", disabled=not result["affected_dependencies"]):
        stats = document_service.save_impact_findings(settings, new_id, st.session_state[key + "_obj"])
        audit_service.record("consistency_check_run", "revision", new_id, settings=settings, kind="change_impact", **stats)
        st.success(f"{stats['new']} new finding(s) saved; {stats['unchanged']} already existed.")

with tabs[1]:
    rows = []
    for etype, bucket in result["entity_changes"].items():
        for change in ("added", "removed", "changed"):
            for item in bucket[change]:
                rows.append({"Type": etype, "Change": change, "Name": item["name"], "Component": item.get("component", ""),
                             "Old page": item.get("page_old"), "New page": item.get("page_new"),
                             "Details": "; ".join(f"{d['field']}: {d['old']} → {d['new']}" for d in item.get("differences", []))
                             or item.get("description", "")})
    if rows:
        change_filter = st.multiselect("Change type", ["added", "removed", "changed"], default=["added", "removed", "changed"])
        st.dataframe(pd.DataFrame([r for r in rows if r["Change"] in change_filter]), hide_index=True, width="stretch")
    else:
        st.info("No differences in the extracted entity inventories.")

with tabs[2]:
    for s in result["modified_sections"]:
        with st.expander(f"Modified · {s['title']} (old p. {', '.join(map(str, s['pages_old']))} → new p. "
                         f"{', '.join(map(str, s['pages_new']))}) · similarity {int(s['similarity'] * 100)}%"):
            for line in s["added_lines"]:
                st.markdown(f":green[**+**] {esc(line)}")
            for line in s["removed_lines"]:
                st.markdown(f":red[**−**] {esc(line)}")
            for c in s["changed_lines"]:
                st.markdown(f":orange[**~**] {esc(c['old'])}  \n&nbsp;&nbsp;&nbsp;→ {esc(c['new'])}")
    for s in result["added_sections"]:
        with st.expander(f"Added · {s['title']} (new p. {', '.join(map(str, s['pages_new']))})"):
            st.text(s["text"])
    for s in result["removed_sections"]:
        with st.expander(f"Removed · {s['title']} (old p. {', '.join(map(str, s['pages_old']))})"):
            st.text(s["text"])
    if not (result["modified_sections"] or result["added_sections"] or result["removed_sections"]):
        st.info("No textual section differences.")

with tabs[3]:
    rc = result["relationship_changes"]
    st.markdown(f"**{len(rc.get('added', []))} added · {len(rc.get('removed', []))} removed · {rc.get('unchanged', 0)} unchanged**")
    rel_rows = [{"Change": "added", **r} for r in rc.get("added", [])] + [{"Change": "removed", **r} for r in rc.get("removed", [])]
    if rel_rows:
        st.dataframe(pd.DataFrame(rel_rows), hide_index=True, width="stretch")
    st.markdown("##### Documented relationships involving changed items")
    if result["affected_dependencies"]:
        st.dataframe(pd.DataFrame(result["affected_dependencies"]), hide_index=True, width="stretch")
    else:
        st.caption("None.")

with tabs[4]:
    old_name, new_name = labels[old_id], labels[new_id]
    stem = f"autodoc_comparison_{by_id[old_id]['revision_label']}_vs_{by_id[new_id]['revision_label']}"
    e1, e2, e3 = st.columns(3)
    e1.download_button("JSON", comparison_json(result), file_name=f"{stem}.json", mime="application/json",
                       icon=":material/data_object:", width="stretch")
    e2.download_button("Excel workbook", comparison_workbook(result), file_name=f"{stem}.xlsx", icon=":material/table:",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
    e3.download_button("PDF report", comparison_pdf(result, APP_NAME, old_name, new_name, st.session_state.get(key + "_ai", "")),
                       file_name=f"{stem}.pdf", mime="application/pdf", icon=":material/picture_as_pdf:", width="stretch")

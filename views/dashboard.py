"""Dashboard: real metrics computed from the application database."""

from __future__ import annotations

from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config.settings import APP_TAGLINE, get_settings
from src.database import repository as repo
from src.services.audit_service import EVENT_LABELS
from src.ui.components import badge, empty_state, esc, metric_card, page_header

settings = get_settings()
page_header("Dashboard", APP_TAGLINE)

documents = repo.list_documents(settings=settings)
revisions = [r for d in documents for r in d["revisions"]]
processed = [r for r in revisions if r["status"] == "processed"]
findings = repo.list_findings(settings=settings)
entity_total = repo.count_entities(settings=settings)
questions = repo.count_questions(settings=settings)

cols = st.columns(5)
with cols[0]:
    metric_card("Documents", len(documents), f"{len(revisions)} revision(s) uploaded")
with cols[1]:
    metric_card("Processed", len(processed), f"{len(revisions) - len(processed)} pending or failed")
with cols[2]:
    metric_card("Architecture entities", entity_total, "excluding rejected")
with cols[3]:
    reviewed = sum(1 for f in findings if f["review_status"] != "open")
    metric_card("Findings", len(findings), f"{reviewed} reviewed · {len(findings) - reviewed} open")
with cols[4]:
    metric_card("Questions answered", questions, "stored conversation turns")

st.write("")

if not revisions:
    left, right = st.columns([3, 2], gap="large")
    with left:
        empty_state("Welcome to AutoDoc AI",
                    "No documents have been uploaded yet. Start by uploading a High-Level Design PDF — or the "
                    "synthetic sample in the sample_data folder — on the Documents page.")
    with right:
        st.subheader("Quick start")
        for n, text in enumerate(["Upload an HLD PDF and process it.", "Ask questions and check the cited pages.",
                                  "Explore extracted components and dependencies.",
                                  "Upload a second revision and compare.", "Review findings and export a report."], 1):
            st.markdown(f'<div class="ad-step"><span class="n">{n}</span><span>{esc(text)}</span></div>',
                        unsafe_allow_html=True)
        st.page_link("views/documents.py", label="Upload a document", icon=":material/upload_file:")
    st.stop()

left, right = st.columns([3, 2], gap="large")
with left:
    st.subheader("Documents")
    rows = []
    for r in sorted(revisions, key=lambda r: r["uploaded_at"], reverse=True)[:10]:
        rows.append({"Document": r["document_title"], "Revision": r["revision_label"], "Pages": r["page_count"],
                     "Status": r["status"].replace("_", " "), "Uploaded": r["uploaded_at"],
                     "Processed": r["processed_at"] or "—"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.subheader("Recent analyses")
    analyses = repo.list_analyses(limit=8, settings=settings)
    if analyses:
        for a in analyses:
            st.markdown(f'<div class="ad-muted"><b>{esc(a["analysis_type"].replace("_", " ").capitalize())}</b> · '
                        f'{esc(a["revision_name"])} · {esc(a["created_at"])}</div><div style="font-size:0.9rem">{esc(a["summary"])}</div>',
                        unsafe_allow_html=True)
            st.write("")
    else:
        st.caption("No analyses recorded yet. Comparisons, checks and AI extraction runs appear here.")

with right:
    st.subheader("Shortcuts")
    c1, c2 = st.columns(2)
    with c1:
        st.page_link("views/documents.py", label="Upload document", icon=":material/upload_file:")
        st.page_link("views/document_chat.py", label="Ask questions", icon=":material/forum:")
        st.page_link("views/architecture_explorer.py", label="Explore architecture", icon=":material/account_tree:")
    with c2:
        st.page_link("views/revision_comparison.py", label="Compare revisions", icon=":material/compare:")
        st.page_link("views/findings.py", label="Review findings", icon=":material/fact_check:")
        st.page_link("views/reports.py", label="Generate report", icon=":material/description:")

    st.subheader("Processing status")
    status_counts = Counter(r["status"] for r in revisions)
    st.markdown(" ".join(f"{badge(k)} <b>{v}</b>" for k, v in status_counts.items()), unsafe_allow_html=True)

    if findings:
        st.subheader("Open findings by severity")
        order = ["High", "Medium", "Low", "Info"]
        open_counts = Counter(f["severity"] for f in findings if f["review_status"] == "open")
        fig = go.Figure(go.Bar(x=[open_counts.get(s, 0) for s in order], y=order, orientation="h",
                               marker_color=["#B42318", "#A16207", "#1F4E8C", "#7A8699"]))
        fig.update_layout(height=200, margin=dict(l=10, r=10, t=5, b=5), plot_bgcolor="#fff", paper_bgcolor="#fff",
                          yaxis=dict(autorange="reversed"), xaxis=dict(dtick=1, gridcolor="#EEF2F7"))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    st.subheader("Recent activity")
    events = repo.list_audit_events(limit=8, settings=settings)
    for e in events:
        st.markdown(f'<div class="ad-muted">{esc(e["timestamp"])} · {esc(EVENT_LABELS.get(e["event_type"], e["event_type"]))}</div>',
                    unsafe_allow_html=True)

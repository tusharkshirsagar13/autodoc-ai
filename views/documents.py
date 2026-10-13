"""Document management: upload, processing, revision library, viewer and deletion."""

from __future__ import annotations

import pandas as pd
import pymupdf
import streamlit as st

from config.settings import get_settings
from src.database import repository as repo
from src.database.models import SOURCE_STATUSES
from src.ingestion.ocr import ocr_status
from src.services import document_service
from src.ui.components import (badge, empty_state, esc, page_header, set_active_revision, show_error)
from src.utils.errors import AutoDocError
from src.utils.file_validation import ensure_within

settings = get_settings()
page_header("Documents", "Upload High-Level Design PDFs, track revisions and run the ingestion pipeline.")


def run_processing(revision_id: str) -> bool:
    with st.status("Processing document…", expanded=True) as status:
        bar = st.progress(0.0, text="Starting")

        def progress(stage, fraction, message):
            bar.progress(min(max(fraction, 0.0), 1.0), text=message)

        try:
            stats = document_service.process(settings, revision_id, progress=progress)
        except AutoDocError as exc:
            status.update(label="Processing did not complete", state="error", expanded=True)
            st.error(exc.user_message(), icon=":material/error:")
            return False
        except Exception as exc:  # pragma: no cover - surfaced to user
            status.update(label="Processing failed", state="error", expanded=True)
            show_error(exc)
            return False
        status.update(label="Processing complete", state="complete", expanded=False)
        # Re-run the script so the sidebar's active-document selector includes the new revision;
        # the summary is shown at the top of the page after the rerun.
        set_active_revision(revision_id)
        st.session_state["processing_result"] = {"revision_id": revision_id, **stats}
        st.rerun()
    return True


def show_processing_result() -> None:
    result = st.session_state.pop("processing_result", None)
    if not result:
        return
    revision = repo.get_revision(result["revision_id"], settings=settings)
    name = revision["display_name"] if revision else "the document"
    st.success(
        f"**{name}** processed: {result['chunks']} chunks from {result['pages']} pages ({result['tables']} tables, "
        f"{result['sections']} headings). Extracted {result['entities']} entities and {result['relationships']} "
        f"relationships; {result['findings']['new']} new findings. Took {result['seconds']} s. "
        "It is now the active document.", icon=":material/check_circle:")
    if result["low_text_pages"]:
        st.warning(f"Pages {', '.join(map(str, result['low_text_pages']))} contain little machine-readable text "
                   "(diagrams or scans). Their content may be missing from the analysis.", icon=":material/warning:")
    c1, c2, c3 = st.columns(3)
    c1.page_link("views/document_chat.py", label="Ask questions about it", icon=":material/forum:")
    c2.page_link("views/architecture_explorer.py", label="Explore its architecture", icon=":material/account_tree:")
    c3.page_link("views/findings.py", label="Review findings", icon=":material/fact_check:")


@st.dialog("Delete revision")
def confirm_delete(revision: dict):
    st.write(f"Permanently delete **{revision['display_name']}**?")
    st.caption("This removes the stored PDF, its vector index entries, extracted entities, findings, review decisions "
               "and conversations. The audit log keeps a record that the deletion happened. This cannot be undone.")
    typed = st.text_input("Type DELETE to confirm")
    c1, c2 = st.columns(2)
    if c1.button("Delete permanently", type="primary", disabled=typed != "DELETE", width="stretch"):
        try:
            document_service.delete_revision(settings, revision["id"])
            if st.session_state.get("active_revision_id") == revision["id"]:
                set_active_revision(None)
            st.session_state["flash"] = f"Deleted {revision['display_name']}."
        except Exception as exc:
            show_error(exc)
            return
        st.rerun()
    if c2.button("Cancel", width="stretch"):
        st.rerun()


if msg := st.session_state.pop("flash", None):
    st.toast(msg, icon=":material/check_circle:")
show_processing_result()

tab_upload, tab_library = st.tabs([":material/upload: Upload", ":material/folder_open: Library"])

# ------------------------------------------------------------------- upload
with tab_upload:
    documents = repo.list_documents(settings=settings)
    left, right = st.columns([3, 2], gap="large")
    with left:
        nonce = st.session_state.setdefault("upload_nonce", 0)
        uploaded = st.file_uploader("HLD document (PDF)", type=["pdf"], accept_multiple_files=False, key=f"uploader_{nonce}",
                                    help=f"Maximum {settings.max_upload_mb} MB and {settings.max_pages} pages. "
                                         "Password-protected PDFs are not supported.")
        if uploaded is not None:
            data = uploaded.getvalue()
            peek = st.session_state.get("_peek")
            if not peek or peek.get("name") != uploaded.name or peek.get("size") != len(data):
                peek = {"name": uploaded.name, "size": len(data), **document_service.peek_pdf(data)}
                st.session_state["_peek"] = peek
            with st.form("upload_form", border=True):
                mode = st.radio("This file is", ["A new document", "A new revision of an existing document"],
                                horizontal=True, disabled=not documents,
                                help="Revisions of the same document can be compared on the Revision Comparison page.")
                doc_id = None
                title = ""
                if mode == "A new revision of an existing document" and documents:
                    doc_map = {d["id"]: d["title"] for d in documents}
                    doc_id = st.selectbox("Existing document", list(doc_map), format_func=lambda i: doc_map[i])
                else:
                    title = st.text_input("Document title", value=peek.get("title", "")[:200], max_chars=200)
                label = st.text_input("Revision label", value=peek.get("revision", ""), max_chars=60,
                                      help="Detected from the first pages when possible (e.g. 'Revision: B'). "
                                           "Leave empty to number revisions automatically.")
                description = st.text_area("Notes (optional)", max_chars=1000, height=70) if not doc_id else ""
                process_now = st.checkbox("Process immediately after upload", value=True)
                submitted = st.form_submit_button("Upload", type="primary", icon=":material/upload:")
            if submitted:
                try:
                    revision = document_service.upload_revision(
                        settings, uploaded.name, data, mime_type=uploaded.type, document_id=doc_id,
                        new_document_title=title, revision_label=label, description=description)
                    st.success(f"Stored as {revision['display_name']} ({revision['page_count']} pages).",
                               icon=":material/check_circle:")
                    st.session_state.pop("_peek", None)
                    st.session_state["upload_nonce"] = nonce + 1  # clear the uploader after a successful upload
                    if process_now:
                        run_processing(revision["id"])
                except AutoDocError as exc:
                    st.error(exc.user_message(), icon=":material/error:")
    with right:
        st.markdown("##### What happens on processing")
        for step in ["Validate the file and record a SHA-256 fingerprint",
                     "Extract text, headings and tables page by page (OCR for scanned pages if available)",
                     "Split into section-aware chunks that keep page numbers",
                     "Create embeddings and store them in the local ChromaDB index",
                     "Extract components, ports, interfaces, signals, dependencies and flows",
                     "Run consistency and completeness checks"]:
            st.markdown(f"- {step}")
        ocr = ocr_status(settings.tesseract_cmd)
        if settings.ocr_enabled and ocr["available"]:
            st.caption(f"OCR available (Tesseract {ocr['version']}).")
        elif settings.ocr_enabled:
            st.caption("OCR enabled but unavailable: " + ocr["reason"])
        else:
            st.caption("OCR is disabled in Settings. Scanned PDFs will not be readable.")
        st.caption("Files are stored locally under generated identifiers; nothing is shared publicly.")

# ------------------------------------------------------------------ library
with tab_library:
    documents = repo.list_documents(settings=settings)
    if not documents:
        empty_state("No documents yet", "Uploaded documents and their revisions will be listed here.")
    for doc in documents:
        with st.container(border=True):
            st.markdown(f"#### {esc(doc['title'])}")
            if doc.get("description"):
                st.caption(doc["description"])
            table = pd.DataFrame([{
                "Revision": r["revision_label"], "File": r["original_filename"], "Pages": r["page_count"],
                "Status": r["status"].replace("_", " "), "Source status": r["source_status"],
                "Chunks": r["chunk_count"], "Uploaded": r["uploaded_at"], "Processed": r["processed_at"] or "—",
            } for r in doc["revisions"]])
            st.dataframe(table, hide_index=True, width="stretch")
            rev_map = {r["id"]: r for r in doc["revisions"]}
            selected_id = st.selectbox("Revision", list(rev_map), key=f"sel_{doc['id']}",
                                       format_func=lambda i: f"Revision {rev_map[i]['revision_label']} — {rev_map[i]['original_filename']}")
            rev = rev_map[selected_id]
            st.markdown(f"{badge(rev['status'])} {badge(rev['source_status'])} "
                        f"<span class='ad-muted'>{esc(rev['status_message'])}</span>", unsafe_allow_html=True)
            if rev["status"] == "needs_ocr":
                ocr = ocr_status(settings.tesseract_cmd)
                st.warning("This document has almost no machine-readable text, so it is probably scanned. "
                           + ("OCR is available — enable it on the Settings page if disabled, then re-process."
                              if ocr["available"] else "Install Tesseract to read it: " + ocr["reason"]),
                           icon=":material/document_scanner:")
            if document_service.needs_reindex(settings, rev):
                st.info("This revision was indexed with a different embedding model than the one currently configured. "
                        "Re-process it before asking questions.", icon=":material/sync_problem:")
            c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.6, 1])
            if c1.button("Set as active", key=f"act_{rev['id']}", disabled=rev["status"] != "processed",
                         icon=":material/check:", width="stretch"):
                set_active_revision(rev["id"])
                st.toast(f"Active document: {rev['display_name']}")
                st.rerun()
            label = "Process" if rev["status"] in {"uploaded"} else "Re-process"
            do_process = c2.button(label, key=f"proc_{rev['id']}", icon=":material/play_arrow:", width="stretch")
            new_status = c3.selectbox("Source status", SOURCE_STATUSES, index=SOURCE_STATUSES.index(rev["source_status"]),
                                      key=f"src_{rev['id']}", label_visibility="collapsed",
                                      help="A label for version control. 'Approved source' is recorded by you; "
                                           "AutoDoc AI never approves documents.")
            if new_status != rev["source_status"]:
                document_service.set_source_status(settings, rev["id"], new_status)
                st.rerun()
            if c4.button("Delete", key=f"del_{rev['id']}", icon=":material/delete:", width="stretch"):
                confirm_delete(rev)
            if do_process:
                run_processing(rev["id"])

            with st.expander("Details and page viewer"):
                d1, d2 = st.columns([2, 3], gap="large")
                with d1:
                    st.markdown(f"**Stored file:** `{esc(rev['original_filename'])}` ({rev['size_bytes'] / 1024:.0f} KB)")
                    st.markdown(f"**SHA-256:** `{rev['sha256'][:16]}…`")
                    st.markdown(f"**Tables detected:** {rev['table_count']} · **OCR pages:** {rev['ocr_page_count']}")
                    if rev["embedding_model"]:
                        st.markdown(f"**Embedding:** {esc(rev['embedding_backend'])} · {esc(rev['embedding_model'])}")
                    if rev["low_text_pages"]:
                        st.markdown(f"**Pages with little text:** {', '.join(map(str, rev['low_text_pages']))}")
                    if rev["sections"]:
                        st.markdown("**Detected headings**")
                        outline = "\n".join(f"{'  ' * (s['level'] - 1)}- {s['title']} (p. {s['page_number']})"
                                            for s in rev["sections"][:60])
                        st.markdown(outline)
                    jobs = repo.list_jobs(rev["id"], limit=3, settings=settings)
                    if jobs:
                        st.markdown("**Recent processing runs**")
                        for j in jobs:
                            st.caption(f"{j['started_at']} · {j['status']} · {j['stage']} {('· ' + j['message'][:160]) if j['message'] and j['message'] != 'OK' else ''}")
                with d2:
                    if rev["page_count"]:
                        page_no = st.number_input("Page", 1, rev["page_count"], 1, key=f"pg_{rev['id']}")
                        try:
                            path = ensure_within(settings.upload_dir, settings.upload_dir / rev["stored_filename"])
                            with pymupdf.open(path) as pdf:
                                png = pdf[int(page_no) - 1].get_pixmap(dpi=110).tobytes("png")
                            st.image(png, caption=f"Page {page_no} of {rev['page_count']}", width="stretch")
                        except Exception as exc:
                            st.caption(f"Preview unavailable ({type(exc).__name__}).")

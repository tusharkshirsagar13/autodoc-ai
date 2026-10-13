"""Citation-aware question answering over the active document."""

from __future__ import annotations

import time

import streamlit as st

from config.settings import get_settings
from src.ai.llm_provider import llm_status
from src.database import repository as repo
from src.services import audit_service, document_service
from src.ui.components import (badge, context_bar, esc, evidence_block, page_header, require_active_revision,
                               show_error)
from src.ui.resources import get_pipeline
from src.utils.errors import AutoDocError

settings = get_settings()
page_header("Ask the Document", "Natural-language questions answered from the document, with page citations.")
revision = require_active_revision()
if not revision:
    st.stop()
context_bar(revision)

if document_service.needs_reindex(settings, revision):
    st.warning("This revision was indexed with a different embedding model than the one now configured. "
               "Re-process it on the Documents page before asking questions.", icon=":material/sync_problem:")
    st.stop()

status = llm_status(settings)
if not status["configured"]:
    st.info("**Retrieval-only mode.** No language model is configured, so no AI answers are generated. "
            "You still get the most relevant passages with page citations. " + status["message"],
            icon=":material/info:")
elif status["external"]:
    ack_key = f"ack_external_{status['provider']}_{status['destination']}"
    if not st.session_state.get(ack_key):
        st.warning(f"Questions and the retrieved document excerpts (not the whole file) will be sent to "
                   f"**{status['destination']}** ({status['provider']}, model {status['model']}) to generate answers. "
                   "Only continue if your organisation permits sharing this document with that provider. "
                   "To keep processing on this machine, configure Ollama or a local OpenAI-compatible server.",
                   icon=":material/cloud_upload:")
        if st.button("I understand — enable AI answers for this session", type="primary"):
            st.session_state[ack_key] = True
            st.rerun()
        st.stop()

MODE_LABELS = {
    "answered": ("processed", "Answer grounded in cited sources"),
    "answered_uncited": ("needs_ocr", "Answer without valid citations — verify"),
    "insufficient_evidence": ("needs_ocr", "Insufficient evidence"),
    "retrieval_only": ("uploaded", "Retrieved evidence only — no AI answer"),
    "provider_error": ("failed", "Model error — evidence only"),
}

session_id = repo.get_or_create_chat_session(revision["id"], settings=settings)
messages = repo.list_qa_messages(session_id, settings=settings)

top_left, top_right = st.columns([4, 1])
with top_right:
    if st.button("Clear conversation", icon=":material/delete_sweep:", disabled=not messages, width="stretch"):
        repo.clear_chat_session(session_id, settings=settings)
        st.rerun()


def answer_markdown(msg: dict) -> str:
    lines = [f"# Question\n\n{msg['question']}\n", f"# Answer ({msg['mode'].replace('_', ' ')})\n\n{msg['answer']}\n",
             f"Document: {revision['display_name']}  \nGenerated: {msg['created_at']}"
             + (f" by {msg['provider']}/{msg['model']}" if msg.get("provider") else ""), "\n# Sources\n"]
    for c in msg["citations"]:
        lines.append(f"- [{c['label']}] page {c['page_number']}, section \"{c['section'] or '—'}\" "
                     f"(relevance {c['score']}){' — cited' if c.get('cited_by_answer') else ''}\n\n  > "
                     + c["excerpt"].replace("\n", "\n  > "))
    lines.append("\n_AI-generated content must be verified against the source document by a qualified engineer._")
    return "\n".join(lines)


def render_message(msg: dict, index: int) -> None:
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(msg["question"])
    with st.chat_message("assistant", avatar=":material/smart_toy:"):
        kind, label = MODE_LABELS.get(msg["mode"], ("uploaded", msg["mode"]))
        meta = f" · {esc(msg['provider'])}/{esc(msg['model'])} · {msg['latency_ms']} ms" if msg.get("provider") else ""
        st.markdown(f"{badge(kind, label)} <span class='ad-muted'>{meta}</span>", unsafe_allow_html=True)
        if msg["mode"] in {"answered", "answered_uncited"}:
            st.caption("AI-generated explanation — based on the excerpts below; verify before use.")
        st.markdown(msg["answer"])
        notice = next((c.get("notice") for c in msg["citations"] if c.get("notice")), None)
        if notice:
            st.caption(notice)
        cites = [c for c in msg["citations"] if c.get("label")]
        if cites:
            pages = sorted({c["page_number"] for c in cites if c.get("cited_by_answer")}) or sorted({c["page_number"] for c in cites})
            heading = "Retrieved evidence" + (" — cited pages " if any(c.get("cited_by_answer") for c in cites) else " — pages ") \
                      + ", ".join(map(str, pages))
            with st.expander(heading, expanded=msg["mode"] in {"retrieval_only", "insufficient_evidence", "provider_error"}):
                for c in cites:
                    mark = " · cited in answer" if c.get("cited_by_answer") else ""
                    st.markdown(f"**[{c['label']}] Page {c['page_number']}** · {esc(c['section'] or 'no section heading')} "
                                f"<span class='ad-muted'>· relevance {c['score']} (ranking score, not a probability){mark}</span>",
                                unsafe_allow_html=True)
                    evidence_block(c["excerpt"][:1500])
        st.download_button("Export answer (Markdown)", answer_markdown(msg).encode("utf-8"),
                           file_name=f"autodoc_answer_{index + 1}.md", mime="text/markdown", key=f"dl_{msg['id']}",
                           icon=":material/download:")


if not messages:
    st.markdown("##### Suggested questions")
    suggestions = [
        "What are the major software components described in this document?",
        "Which interfaces or signals are associated with each component?",
        "What dependencies are explicitly documented?",
        "Summarize the functional flows described in the document.",
        "Summarize the responsibilities of each software component.",
        "What information appears to be missing or undefined in this document?",
    ]
    comps = [e["name"] for e in repo.list_entities(revision["id"], "component", include_rejected=False, settings=settings)]
    if comps:
        suggestions[1] = f"Which interfaces or signals are associated with the {comps[min(1, len(comps) - 1)]}?"
        suggestions[4] = f"Summarize the responsibilities of the {comps[0]}."
    picked = st.pills("Suggested questions", suggestions, label_visibility="collapsed", key="suggestion_pill")
    if picked:
        st.session_state["pending_question"] = picked
        del st.session_state["suggestion_pill"]
        st.rerun()

for i, msg in enumerate(messages):
    render_message(msg, i)

question = st.chat_input("Ask about components, interfaces, signals, dependencies or flows…", max_chars=2000)
question = question or st.session_state.pop("pending_question", None)
if question:
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(question)
    with st.chat_message("assistant", avatar=":material/smart_toy:"):
        with st.spinner("Retrieving evidence" + (" and generating an answer…" if status["configured"] else "…")):
            started = time.perf_counter()
            try:
                history = [{"question": m["question"], "answer": m["answer"]} for m in messages
                           if m["mode"] in {"answered", "answered_uncited"}]
                result = get_pipeline(settings).answer(question, [revision["id"]], history=history)
            except AutoDocError as exc:
                show_error(exc)
                st.stop()
            except Exception as exc:
                show_error(exc)
                st.stop()
    citations = [c.to_dict() for c in result.citations]
    if result.notice and citations:
        citations[0]["notice"] = result.notice
    elif result.notice:
        citations = [{"notice": result.notice}]
    repo.add_qa_message(session_id, result.question, result.answer, result.mode, citations, result.provider,
                        result.model, result.latency_ms or int((time.perf_counter() - started) * 1000), settings=settings)
    audit_service.record("question_asked", "revision", revision["id"], settings=settings, mode=result.mode,
                         sources=len(result.citations), provider=result.provider)
    st.rerun()

with st.expander("Find every mention of a term", icon=":material/search:"):
    st.caption("Exact, case-insensitive search across all chunks — useful for 'Which sections mention signal X?'. "
               "This is deterministic and does not use the language model.")
    term = st.text_input("Term or identifier", placeholder="e.g. Sig_VehicleSpeed", key="mention_term")
    if term.strip():
        hits = get_pipeline(settings).retriever.search_text(term.strip(), revision["id"])
        if not hits:
            st.info(f"'{term}' does not occur in the indexed text.")
        else:
            st.markdown(f"**{len(hits)} chunk(s)** on page(s) {', '.join(map(str, sorted({h.page_number for h in hits})))}")
            for h in hits:
                st.markdown(f"**Page {h.page_number}** · {esc(h.section or 'no section heading')}", unsafe_allow_html=True)
                evidence_block(h.text[:800])

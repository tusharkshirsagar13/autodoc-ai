"""Shared Streamlit UI helpers: theme CSS, headers, badges, empty states and
the global "active revision" selection."""

from __future__ import annotations

import html

import streamlit as st

from config.settings import APP_NAME, get_settings
from src.database import repository as repo

CSS = """
<style>
:root {
  --ad-navy: #1B2A41; --ad-blue: #1F4E8C; --ad-blue-2: #3B82C4; --ad-grey: #5B6676;
  --ad-line: #DCE3EC; --ad-bg: #F3F6FA; --ad-green: #2F7D5B; --ad-amber: #A16207; --ad-red: #B42318;
}
.block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1320px; }
h1, h2, h3 { color: var(--ad-navy); letter-spacing: -0.01em; }
h1 { font-size: 1.6rem !important; font-weight: 650 !important; }
h2 { font-size: 1.2rem !important; font-weight: 620 !important; }
h3 { font-size: 1.02rem !important; font-weight: 600 !important; }
[data-testid="stSidebar"] .ad-muted { color: #8EA0B8 !important; }
[data-testid="stSidebarHeader"] img { height: 2.6rem; max-width: 100%; }
.ad-brand { padding: 0.2rem 0 0.9rem 0; border-bottom: 1px solid #24364F; margin-bottom: 0.6rem; }
.ad-brand .name { font-size: 1.15rem; font-weight: 700; color: #FFFFFF !important; letter-spacing: 0.01em; }
.ad-brand .sub { font-size: 0.74rem; color: #8EA0B8 !important; text-transform: uppercase; letter-spacing: 0.06em; }
.ad-header { border-bottom: 1px solid var(--ad-line); padding-bottom: 0.7rem; margin-bottom: 1.1rem; }
.ad-header .title { font-size: 1.5rem; font-weight: 650; color: var(--ad-navy); }
.ad-header .subtitle { color: var(--ad-grey); font-size: 0.92rem; margin-top: 0.15rem; }
.ad-card { border: 1px solid var(--ad-line); border-radius: 8px; padding: 0.9rem 1rem; background: #FFFFFF; height: 100%; }
.ad-card .label { color: var(--ad-grey); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; }
.ad-card .value { color: var(--ad-navy); font-size: 1.65rem; font-weight: 650; line-height: 1.25; }
.ad-card .hint { color: var(--ad-grey); font-size: 0.78rem; }
.ad-badge { display: inline-block; padding: 0.08rem 0.5rem; border-radius: 999px; font-size: 0.74rem; font-weight: 600;
            border: 1px solid transparent; white-space: nowrap; }
.ad-badge.green { background: #E7F4EE; color: var(--ad-green); border-color: #BFE3D1; }
.ad-badge.blue { background: #E8F0FA; color: var(--ad-blue); border-color: #C5D7EF; }
.ad-badge.amber { background: #FDF5E3; color: var(--ad-amber); border-color: #F1DDA9; }
.ad-badge.red { background: #FCEBEA; color: var(--ad-red); border-color: #F4C7C3; }
.ad-badge.grey { background: #F1F3F6; color: var(--ad-grey); border-color: #DCE3EC; }
.ad-empty { border: 1px dashed #C5D0DE; border-radius: 10px; padding: 2rem 1.5rem; text-align: center; background: #FAFBFD; }
.ad-empty .t { font-weight: 650; color: var(--ad-navy); font-size: 1.02rem; margin-bottom: 0.3rem; }
.ad-empty .d { color: var(--ad-grey); font-size: 0.9rem; }
.ad-context { background: var(--ad-bg); border: 1px solid var(--ad-line); border-radius: 8px; padding: 0.5rem 0.8rem;
              font-size: 0.86rem; color: var(--ad-navy); margin-bottom: 0.9rem; }
.ad-evidence { border-left: 3px solid var(--ad-blue-2); background: #F8FAFD; padding: 0.55rem 0.8rem; font-size: 0.86rem;
               white-space: pre-wrap; color: #24324A; border-radius: 0 6px 6px 0; }
.ad-muted { color: var(--ad-grey); font-size: 0.84rem; }
.ad-step { display: flex; gap: 0.6rem; margin: 0.35rem 0; align-items: baseline; }
.ad-step .n { background: var(--ad-blue); color: #fff; border-radius: 50%; width: 1.35rem; height: 1.35rem; font-size: 0.75rem;
              display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; }
div[data-testid="stExpander"] details { border-radius: 8px; border-color: var(--ad-line); }
</style>
"""

STATUS_BADGES = {
    "processed": ("Processed", "green"), "processing": ("Processing", "blue"), "uploaded": ("Not processed", "grey"),
    "failed": ("Failed", "red"), "needs_ocr": ("Needs OCR", "amber"),
    "open": ("Open", "amber"), "accepted": ("Accepted", "green"), "rejected": ("Rejected", "grey"),
    "needs_investigation": ("Investigate", "blue"), "unreviewed": ("Unreviewed", "grey"), "confirmed": ("Confirmed", "green"),
    "corrected": ("Corrected", "blue"), "draft": ("Draft", "grey"), "approved": ("Approved source", "green"),
    "superseded": ("Superseded", "amber"),
    "High": ("High", "red"), "Medium": ("Medium", "amber"), "Low": ("Low", "blue"), "Info": ("Info", "grey"),
    "high": ("High confidence", "green"), "medium": ("Medium confidence", "blue"), "low": ("Low confidence", "amber"),
}


def apply_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def badge(key: str, label: str | None = None) -> str:
    text, color = STATUS_BADGES.get(key, (key.replace("_", " ").capitalize(), "grey"))
    return f'<span class="ad-badge {color}">{esc(label or text)}</span>'


def page_header(title: str, subtitle: str = "") -> None:
    st.set_page_config(page_title=f"{title} · {APP_NAME}")
    st.markdown(f'<div class="ad-header"><div class="title">{esc(title)}</div>'
                f'<div class="subtitle">{esc(subtitle)}</div></div>', unsafe_allow_html=True)


def metric_card(label: str, value, hint: str = "") -> None:
    st.markdown(f'<div class="ad-card"><div class="label">{esc(label)}</div><div class="value">{esc(value)}</div>'
                f'<div class="hint">{esc(hint) if hint else "&nbsp;"}</div></div>', unsafe_allow_html=True)


def empty_state(title: str, description: str) -> None:
    st.markdown(f'<div class="ad-empty"><div class="t">{esc(title)}</div><div class="d">{esc(description)}</div></div>',
                unsafe_allow_html=True)


def evidence_block(text: str) -> None:
    st.markdown(f'<div class="ad-evidence">{esc(text)}</div>', unsafe_allow_html=True)


# ------------------------------------------------------------ active revision
def processed_revisions() -> list[dict]:
    return repo.list_revisions(status="processed", settings=get_settings())


def active_revision_id() -> str | None:
    return st.session_state.get("active_revision_id")


def set_active_revision(revision_id: str | None) -> None:
    st.session_state["active_revision_id"] = revision_id


def sidebar_document_selector() -> None:
    revisions = processed_revisions()
    st.sidebar.markdown("**Active document**")
    if not revisions:
        st.sidebar.caption("No processed documents yet.")
        set_active_revision(None)
        return
    ids = [r["id"] for r in revisions]
    current = active_revision_id()
    if current not in ids:
        current = ids[0]
        set_active_revision(current)
    labels = {r["id"]: r["display_name"] for r in revisions}
    choice = st.sidebar.selectbox("Active document", ids, index=ids.index(current), format_func=lambda i: labels[i],
                                  label_visibility="collapsed", key="sidebar_active_revision",
                                  help="Every analysis page works on this document revision.")
    if choice != current:
        set_active_revision(choice)


def require_active_revision() -> dict | None:
    """Return the active processed revision or render an empty state."""
    rid = active_revision_id()
    revision = repo.get_revision(rid, settings=get_settings()) if rid else None
    if not revision or revision["status"] != "processed":
        empty_state("No processed document selected",
                    "Upload a PDF on the Documents page and process it, then choose it as the active document in the sidebar.")
        st.page_link("views/documents.py", label="Go to Documents", icon=":material/upload_file:")
        return None
    return revision


def context_bar(revision: dict) -> None:
    st.markdown(
        f'<div class="ad-context">Analysing <b>{esc(revision["document_title"])}</b> · revision '
        f'<b>{esc(revision["revision_label"])}</b> · {revision["page_count"]} pages · '
        f'{badge(revision["source_status"])}</div>', unsafe_allow_html=True)


def show_error(exc: Exception) -> None:
    from src.utils.errors import AutoDocError

    if isinstance(exc, AutoDocError):
        st.error(exc.user_message(), icon=":material/error:")
    else:
        st.error(f"Unexpected error: {type(exc).__name__}. See the terminal log for details.", icon=":material/error:")


def app_title(page: str) -> str:
    return f"{page} · {APP_NAME}"

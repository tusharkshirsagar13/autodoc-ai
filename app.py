"""AutoDoc AI — Automotive Architecture Intelligence Platform.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from config.settings import APP_NAME, APP_SUBTITLE, APP_VERSION, get_settings  # noqa: E402
from src.ai.llm_provider import llm_status  # noqa: E402
from src.database.initialization import get_engine  # noqa: E402
from src.ui.components import apply_theme, badge, esc, sidebar_document_selector  # noqa: E402
from src.utils.logging_config import configure_logging  # noqa: E402

st.set_page_config(page_title=f"{APP_NAME} — {APP_SUBTITLE}", page_icon=str(ROOT / "assets" / "icon.svg"), layout="wide",
                   initial_sidebar_state="expanded")

settings = get_settings()
configure_logging(settings.log_level)
get_engine(settings)  # creates directories, database and schema on first start
apply_theme()

st.logo(str(ROOT / "assets" / "logo.svg"), size="large", icon_image=str(ROOT / "assets" / "icon.svg"))

pages = {
    "Overview": [
        st.Page("views/dashboard.py", title="Dashboard", icon=":material/space_dashboard:", default=True),
        st.Page("views/documents.py", title="Documents", icon=":material/upload_file:"),
    ],
    "Analysis": [
        st.Page("views/document_chat.py", title="Ask the Document", icon=":material/forum:"),
        st.Page("views/architecture_explorer.py", title="Architecture Explorer", icon=":material/account_tree:"),
        st.Page("views/revision_comparison.py", title="Revision Comparison", icon=":material/compare:"),
        st.Page("views/findings.py", title="Findings & Review", icon=":material/fact_check:"),
    ],
    "Output": [
        st.Page("views/reports.py", title="Reports & Export", icon=":material/description:"),
        st.Page("views/settings_page.py", title="Settings & Status", icon=":material/settings:"),
    ],
}
navigation = st.navigation(pages, position="sidebar")

with st.sidebar:
    st.write("")
    sidebar_document_selector()
    status = llm_status(get_settings())
    st.markdown("**Language model**")
    if status["configured"]:
        where = "external API" if status["external"] else "local"
        st.markdown(f'{badge("processed", "Configured")} <span class="ad-muted">{esc(status["provider"])} · '
                    f'{esc(status["model"])} · {where}</span>', unsafe_allow_html=True)
    else:
        st.markdown(f'{badge("uploaded", "Not configured")} <span class="ad-muted">retrieval-only mode</span>',
                    unsafe_allow_html=True)
    st.caption(f"v{APP_VERSION} · local prototype — for trusted environments only")

navigation.run()

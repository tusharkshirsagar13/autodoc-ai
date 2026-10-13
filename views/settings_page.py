"""Settings, system status, configuration guide and audit log."""

from __future__ import annotations

import importlib.metadata as md
import importlib.util
import platform

import pandas as pd
import streamlit as st

from config.settings import EMBEDDING_BACKENDS, LLM_PROVIDERS, get_settings
from src.ai.embeddings import embedding_status
from src.ai.llm_provider import OllamaProvider, llm_status
from src.ai.retrieval import get_chroma_client
from src.database import repository as repo
from src.database.initialization import database_status
from src.ingestion.ocr import install_instructions, ocr_status
from src.services import audit_service
from src.services.audit_service import EVENT_LABELS
from src.ui.components import badge, esc, page_header

settings = get_settings()
page_header("Settings & Status", "Configuration, dependency health and the audit trail. API keys are never displayed.")

tab_status, tab_config, tab_guide, tab_audit = st.tabs([":material/monitor_heart: System status", ":material/tune: Configuration",
                                                        ":material/menu_book: Setup guide", ":material/history: Audit log"])


def status_row(label: str, ok: bool | None, detail: str) -> None:
    key = "processed" if ok else ("uploaded" if ok is None else "failed")
    text = "OK" if ok else ("Not configured" if ok is None else "Unavailable")
    st.markdown(f"**{esc(label)}** &nbsp; {badge(key, text)}<br><span class='ad-muted'>{esc(detail)}</span>",
                unsafe_allow_html=True)
    st.write("")


with tab_status:
    left, right = st.columns(2, gap="large")
    with left:
        llm = llm_status(settings)
        if llm["configured"]:
            where = f"external API ({llm['destination']})" if llm["external"] else f"local ({llm['destination']})"
            status_row("Language model", True, f"{llm['provider']} · {llm['model']} · {where}")
        else:
            status_row("Language model", None, llm["message"])
        if llm["key_env"]:
            st.caption(f"{llm['key_env']}: {'set' if llm['key_present'] else 'not set'} (value never shown)")
        if settings.llm_provider == "ollama":
            health = OllamaProvider(settings).health()
            if health["reachable"]:
                st.caption("Ollama reachable. Installed models: " + (", ".join(health["models"]) or "none — run `ollama pull <model>`"))
            else:
                st.caption("Ollama is not reachable. Start it with `ollama serve`.")
        emb = embedding_status(settings)
        status_row("Embedding model", emb["installed"], f"{emb['backend']} · {emb['model']}"
                   + (" · loaded" if emb["loaded"] else " · loads on first use") + (f" · {emb['note']}" if emb["note"] else ""))
        try:
            client = get_chroma_client(settings)
            collections = client.list_collections()
            names = [c if isinstance(c, str) else c.name for c in collections]
            total = sum(client.get_collection(n, embedding_function=None).count() for n in names)
            status_row("Vector database (ChromaDB)", True, f"{settings.chroma_dir} · {len(names)} collection(s) · {total} vectors")
        except Exception as exc:
            status_row("Vector database (ChromaDB)", False, f"{type(exc).__name__}: {exc}")
        db = database_status(settings)
        status_row("Structured database (SQLite)", db["ok"],
                   f"{db['path']} · schema v{db.get('schema_version')} · {db.get('size_bytes', 0) / 1024:.0f} KB" if db["ok"] else db.get("error", ""))
        ocr = ocr_status(settings.tesseract_cmd)
        if not settings.ocr_enabled:
            status_row("OCR (Tesseract)", None, "Disabled in configuration.")
        else:
            status_row("OCR (Tesseract)", ocr["available"], f"Tesseract {ocr['version']}" if ocr["available"] else ocr["reason"])
    with right:
        st.markdown("**Limits and storage**")
        st.dataframe(pd.DataFrame([
            {"Setting": "Upload size limit", "Value": f"{settings.max_upload_mb} MB"},
            {"Setting": "Maximum pages per PDF", "Value": str(settings.max_pages)},
            {"Setting": "Maximum stored revisions", "Value": str(settings.max_documents)},
            {"Setting": "Upload folder", "Value": str(settings.upload_dir)},
            {"Setting": "Data folder", "Value": str(settings.data_dir)},
            {"Setting": "Chunk size / overlap", "Value": f"{settings.chunk_size} / {settings.chunk_overlap} characters"},
            {"Setting": "Retrieved passages (top-k)", "Value": str(settings.retrieval_top_k)},
            {"Setting": "Keyword weight in hybrid search", "Value": str(settings.hybrid_keyword_weight)},
            {"Setting": "Relevance threshold (active backend)", "Value": str(settings.relevance_threshold)},
        ]), hide_index=True, width="stretch")
        st.markdown("**Python packages**")
        rows = []
        for pkg, module in [("streamlit", "streamlit"), ("pymupdf", "pymupdf"), ("chromadb", "chromadb"),
                            ("sentence-transformers", "sentence_transformers"), ("sqlalchemy", "sqlalchemy"),
                            ("pydantic", "pydantic"), ("plotly", "plotly"), ("networkx", "networkx"), ("pandas", "pandas"),
                            ("openpyxl", "openpyxl"), ("reportlab", "reportlab"), ("pytesseract", "pytesseract"),
                            ("openai", "openai"), ("anthropic", "anthropic"), ("httpx", "httpx")]:
            found = importlib.util.find_spec(module) is not None
            try:
                version = md.version(pkg) if found else "—"
            except md.PackageNotFoundError:
                version = "installed"
            rows.append({"Package": pkg, "Status": "installed" if found else "missing", "Version": version})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(f"Python {platform.python_version()} on {platform.system()}")

with tab_config:
    st.caption("These non-secret settings are saved to data/user_settings.json and override .env. "
               "API keys can only be set as environment variables (.env) and are never stored by the application.")
    with st.form("settings_form"):
        st.markdown("##### Language model")
        c1, c2 = st.columns(2)
        providers = list(LLM_PROVIDERS)
        provider = c1.selectbox("Provider", providers, index=providers.index(settings.llm_provider),
                                format_func=lambda p: LLM_PROVIDERS[p])
        model = c2.text_input("Model name", settings.llm_model,
                              help="Exact model identifier for the provider, e.g. an Ollama tag such as 'llama3.1:8b'.")
        base_url = st.text_input("Base URL (optional)", settings.llm_base_url,
                                 help="For an OpenAI-compatible local server (e.g. http://localhost:1234/v1) or a custom Ollama host.")
        c3, c4, c5 = st.columns(3)
        temperature = c3.slider("Temperature", 0.0, 1.0, float(settings.llm_temperature), 0.05)
        max_tokens = c4.number_input("Max answer tokens", 128, 8000, int(settings.llm_max_tokens), 64)
        timeout = c5.number_input("Timeout (seconds)", 5, 600, int(settings.llm_timeout_seconds), 5)

        st.markdown("##### Embeddings and retrieval")
        c6, c7 = st.columns(2)
        backends = list(EMBEDDING_BACKENDS)
        backend = c6.selectbox("Embedding backend", backends, index=backends.index(settings.embedding_backend),
                               format_func=lambda b: EMBEDDING_BACKENDS[b])
        emb_model = c7.text_input("Sentence Transformers model", settings.embedding_model,
                                  help="e.g. BAAI/bge-small-en-v1.5 (default), intfloat/e5-small-v2, sentence-transformers/all-MiniLM-L6-v2")
        c8, c9, c10 = st.columns(3)
        chunk_size = c8.number_input("Chunk size (characters)", 200, 4000, int(settings.chunk_size), 50)
        chunk_overlap = c9.number_input("Chunk overlap (characters)", 0, 2000, int(settings.chunk_overlap), 25)
        top_k = c10.number_input("Retrieved passages (top-k)", 1, 20, int(settings.retrieval_top_k))
        c11, c12, c13 = st.columns(3)
        kw = c11.slider("Keyword weight (hybrid search)", 0.0, 1.0, float(settings.hybrid_keyword_weight), 0.05)
        thr = c12.slider("Relevance threshold (semantic)", 0.0, 1.0, float(settings.min_relevance_score), 0.01)
        thr_h = c13.slider("Relevance threshold (hashing)", 0.0, 1.0, float(settings.min_relevance_score_hashing), 0.01)

        st.markdown("##### OCR")
        c14, c15 = st.columns(2)
        ocr_enabled = c14.toggle("Enable OCR for scanned pages", settings.ocr_enabled)
        ocr_lang = c15.text_input("OCR language code(s)", settings.ocr_language, help="Tesseract codes, e.g. eng or eng+deu")
        saved = st.form_submit_button("Save settings", type="primary", icon=":material/save:")
    if saved:
        changes = {"llm_provider": provider, "llm_model": model.strip(), "llm_base_url": base_url.strip(),
                   "llm_temperature": temperature, "llm_max_tokens": max_tokens, "llm_timeout_seconds": timeout,
                   "embedding_backend": backend, "embedding_model": emb_model.strip(), "chunk_size": chunk_size,
                   "chunk_overlap": chunk_overlap, "retrieval_top_k": top_k, "hybrid_keyword_weight": kw,
                   "min_relevance_score": thr, "min_relevance_score_hashing": thr_h, "ocr_enabled": ocr_enabled,
                   "ocr_language": ocr_lang.strip() or "eng"}
        embedding_changed = (backend, emb_model.strip()) != (settings.embedding_backend, settings.embedding_model)
        chunking_changed = (chunk_size, chunk_overlap) != (settings.chunk_size, settings.chunk_overlap)
        settings.save_overrides(changes)
        get_settings(reload=True)
        audit_service.record("settings_changed", settings=settings, fields=sorted(changes))
        st.success("Settings saved.", icon=":material/check_circle:")
        if embedding_changed:
            st.warning("The embedding model changed. Existing documents must be re-processed before they can be "
                       "searched with the new model (Documents → Re-process).", icon=":material/sync_problem:")
        if chunking_changed:
            st.info("Chunking changes apply to documents processed from now on.", icon=":material/info:")
        st.rerun()

with tab_guide:
    st.markdown(f"""
#### Configure a language model (optional)
AutoDoc AI works without a language model: upload, extraction, retrieval with citations, comparison, checks and exports
all run locally. A model adds written answers, summaries and AI suggestions.

Edit the `.env` file in the project folder (copy `.env.example` first) and restart the app.

| Option | `.env` settings | Data leaves this computer? |
|---|---|---|
| **Ollama (local)** | `LLM_PROVIDER=ollama` · `LLM_MODEL=llama3.1:8b` (any pulled model) | No |
| **Local OpenAI-compatible server** (LM Studio, llama.cpp, vLLM) | `LLM_PROVIDER=openai` · `LLM_BASE_URL=http://localhost:1234/v1` · `LLM_MODEL=<served model>` | No |
| **OpenAI API** | `LLM_PROVIDER=openai` · `LLM_MODEL=<model id>` · `OPENAI_API_KEY=...` | Yes — question and retrieved excerpts |
| **Anthropic API** | `LLM_PROVIDER=anthropic` · `LLM_MODEL=<model id>` · `ANTHROPIC_API_KEY=...` | Yes — question and retrieved excerpts |

For hosted APIs, check the provider's documentation for current model identifiers. Before excerpts are sent to an
external provider, the chat page asks for confirmation once per session.

#### Embedding model
The default `sentence-transformers` backend downloads **{esc(settings.embedding_model)}** (~130 MB) from Hugging Face on first
use and caches it. Without internet access, select the **offline hashing** backend (lexical matching only, lower quality).
Changing the embedding model requires re-processing documents.

#### OCR for scanned PDFs
{install_instructions()} Then make sure OCR is enabled above and re-process the document.

#### Security notes
This is a local prototype without user authentication. Run it only on a trusted machine or network and do not expose
it to the internet without adding access control.
""")

with tab_audit:
    events = repo.list_audit_events(limit=300, settings=settings)
    if events:
        st.dataframe(pd.DataFrame([{"Time (UTC)": e["timestamp"], "Event": EVENT_LABELS.get(e["event_type"], e["event_type"]),
                                    "Target": f"{e['target_type']} {e['target_id']}".strip(),
                                    "Details": ", ".join(f"{k}={v}" for k, v in e["details"].items())} for e in events]),
                     hide_index=True, width="stretch", height=480)
        st.caption("Audit events contain identifiers and short metadata only — never document text, prompts, answers or keys.")
    else:
        st.caption("No events recorded yet.")

# AutoDoc AI — Implementation Notes

Technical decisions, how each case-study requirement was addressed, and known limitations.

## Requirements traceability (Case Study 1)

| Requirement (case study, pp. 4–7) | Implementation | Status |
|---|---|---|
| PDF and supported document ingestion | `validate_pdf_upload`, `upload_revision`, `processor.process_revision` | Done (PDF only) |
| OCR for scanned pages | `ingestion/ocr.py`, low-text detection, *Needs OCR* status with install guidance | Done when Tesseract is installed |
| Section, table and metadata extraction | `pdf_parser.py` (headings by font metrics, PyMuPDF table finder, PDF metadata, revision label) | Done |
| Architecture entity recognition | `entity_extractor.py`: tables + prose patterns + optional validated LLM extraction | Done |
| Interface and dependency mapping | Port/interface/signal/dependency tables, prose dependencies, flows, derived interface links, graph | Done |
| Natural-language Q&A with citations | `rag_pipeline.py`, `views/document_chat.py` | Done (LLM optional) |
| Cited summaries and component reports | Component detail + AI component summary; flow summaries; PDF/Excel reports | Done |
| Revision comparison | `revision_comparator.py`, `views/revision_comparison.py` | Done |
| Potential mismatch and completeness checks | `consistency_checker.py` (13 rules + change impact + AI suggestions) | Done |
| Export of structured findings | CSV, Excel, JSON, PDF | Done |
| Engineer review, correction, export | Entity/relationship/finding review workflow, review history | Done |
| Risk control: page-level citations and review queues | Citations everywhere; unreviewed status and review tab | Done |
| Risk control: restrict answers to evidence, show confidence | Relevance gating, citation validation, confidence labels on entities | Done |
| Risk control: project isolation / RBAC | Revision-scoped retrieval; **no authentication/RBAC** | Partial — see limitations |
| Risk control: version labels and approved-source status | Revision labels, SHA-256, draft/approved/superseded label | Done |
| Audit logs | `audit_events` table and Settings → Audit log | Done |

## Key decisions

* **Streamlit only, no FastAPI.** The pilot has one user and no external API consumers; a separate server would add
  deployment complexity without benefit. Business logic sits in `src/services` and is UI-independent.
* **`views/` instead of `pages/`.** Streamlit auto-discovers a `pages/` folder (legacy multipage mode). With explicit
  `st.navigation`, opening a deep link such as `/findings` in a new tab rendered the page outside the app shell. Renaming
  the folder fixed it.
* **Deterministic first, AI second.** Extraction, comparison and checks are deterministic and work without an LLM. AI
  only proposes (extraction suggestions, observations, summaries) and its output is labelled and reviewable.
* **Verbatim-evidence guardrail for LLM extraction.** A proposed entity/relationship is discarded unless its name(s)
  appear in the excerpt it came from. This blocks a common hallucination mode (invented component names) but cannot
  verify that a relationship is semantically correct, so results stay *unreviewed / low confidence*.
* **Hybrid retrieval.** Engineering questions often name exact identifiers (`Sig_VehicleSpeed`) that dense embeddings
  handle poorly. BM25 over identifier-aware tokens (`Sig_VehicleSpeed` → `sig_vehiclespeed`, `vehicle`, `speed`) is
  blended in. The BM25 part is scaled by idf-weighted query coverage so a single incidental word cannot look like strong
  evidence.
* **Relevance gate before the LLM.** If nothing passes the threshold, the app answers "insufficient evidence" without
  calling the model, which avoids answers built on irrelevant context and saves cost.
* **Citation validation.** The model must cite `[S#]` labels; labels that were not supplied are removed and uncited
  answers are flagged.
* **Page-pure chunks.** Chunks never cross a page boundary so every citation is a single page; tables are separate
  chunks with the header repeated when split.
* **Table cells read with `get_textbox`.** `Table.extract()` sometimes moved underscores in identifiers to a new line
  (`PP WheelSpeed\n_`); reading each cell's bounding box keeps `PP_WheelSpeed` intact. Identifiers broken by narrow
  columns (`WheelSpeedProce` + `ssor`) are re-joined when the fragment is CamelCase/snake_case and the next line starts in
  lower case.
* **Heading detection relative to body text outside tables.** Body font size is computed from non-table lines; otherwise
  small table fonts made normal paragraphs look like headings.
* **Stable IDs and fingerprints.** Entity/relationship IDs hash revision + type + normalised name (+ component), and
  findings use rule fingerprints, so re-runs update instead of duplicating and review decisions persist.
* **One Chroma collection per embedding model.** Vectors of different dimensions never mix; a revision indexed with
  another model is flagged for re-processing.
* **Per-backend relevance thresholds.** Semantic models give unrelated text higher cosine similarity than the lexical
  hashing embedder, so the thresholds differ (0.35 vs 0.15). The semantic default is a heuristic starting point; it
  could not be calibrated in the build environment (see below).
* **Offline hashing embedder.** Lets the app and the test-suite run without downloading a model. It must be chosen
  explicitly; the app never switches embedding models silently.
* **Secrets.** API keys are read from the environment at call time, never stored on the settings object, never written
  to `user_settings.json`, and redacted from logs and error messages.

## Build-environment constraints (honest notes)

* The build sandbox could not reach Hugging Face, so the default `BAAI/bge-small-en-v1.5` model could not be downloaded
  there. The Sentence-Transformers code path was verified with a tiny randomly initialised model created locally in the
  tests (`test_sentence_transformer_backend_end_to_end`), and the full pipeline and UI were verified with the hashing
  backend. Retrieval *quality* with BGE and the 0.35 threshold has not been measured.
* No LLM API key or local model was available, so real LLM inference was not executed. Provider classes were tested
  with mocked SDK clients and mocked HTTP (`tests/test_llm_provider.py`), and the RAG pipeline with a fake provider.
* OCR: Tesseract 5.3 was available in the build environment. `test_ocr_reads_scanned_page_when_tesseract_available`
  converts a sample page into an image-only PDF and checks that OCR recovers the text; it is skipped automatically on
  machines without Tesseract. The other tests run with OCR disabled to stay fast and deterministic.

## Known limitations

* No authentication, users or roles; intended for a trusted local machine.
* Diagram content (images, vector drawings) is not interpreted; such pages are flagged.
* Table recognition depends on header names; unusual headers yield an *unrecognised tables* finding.
* Prose patterns cover common phrasings ("depends on", "requires", "calls", "sends X to", "receives X from",
  `FF-01 Name: A -> B`); other phrasing is only searchable, not extracted, unless AI extraction is used.
* Conflicting-description detection uses string similarity (difflib ratio < 0.6) and can flag paraphrases.
* Only the last three Q&A turns are passed as context; the conversation is not a multi-step agent.
* Processing is synchronous within the Streamlit session.
* Only English prompts and patterns were designed and tested.

## Possible next steps

Background job queue; FastAPI with auth/RBAC and project workspaces; evaluation set with precision/recall per entity
type; ARXML import; vision-model diagram parsing; Neo4j traceability to requirements and tests; calibrating thresholds
on real HLDs.

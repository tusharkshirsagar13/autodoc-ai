# AutoDoc AI — Testing

## How to run

```bash
python -m pytest              # full suite
python -m pytest -v           # with test names
python -m pytest tests/test_retrieval.py -k isolation
```

No API key, network access or GPU is needed. Tests use:

* the synthetic sample PDFs, regenerated into a temporary folder for each test session;
* an isolated temporary data/upload/ChromaDB directory per test;
* the offline **hashing** embedder (plus one test that builds a tiny Sentence-Transformers model locally);
* a `FakeProvider` and mocked SDK/HTTP clients instead of real LLMs.

## Latest result

Executed on 2026-10-13 in the build environment (Linux, Python 3.13.16, CPU only):

```
91 passed in 246.46s (0:04:06)
```

No tests were skipped in that run (Tesseract was available, so the OCR test ran). On a machine without Tesseract,
`test_ocr_reads_scanned_page_when_tesseract_available` is skipped automatically; the tiny Sentence-Transformers test
is skipped if `torch`/`transformers` are not installed.

| File | Tests | Result |
|---|---|---|
| `test_file_validation.py` | 15 | passed |
| `test_pdf_parser.py` | 11 | passed |
| `test_chunker.py` | 6 | passed |
| `test_embeddings.py` | 4 | passed |
| `test_retrieval.py` | 14 | passed |
| `test_entity_extraction.py` | 10 | passed |
| `test_revision_comparison.py` | 7 | passed |
| `test_consistency_checker.py` | 6 | passed |
| `test_exports.py` | 6 | passed |
| `test_llm_provider.py` | 10 | passed |
| `test_ui_smoke.py` | 2 | passed |

## Test catalogue

| Area (requirement) | Test(s) | Expected result |
|---|---|---|
| Valid PDF ingestion | `test_valid_pdf_is_accepted`, `test_processing_stats` | 5 pages accepted; processing yields chunks, entities and no low-text pages |
| Invalid / empty files | `test_invalid_files_are_rejected` (wrong extension, empty, non-PDF content, corrupt PDF), `test_oversized_file_is_rejected`, `test_page_limit_enforced`, `test_wrong_mime_type_rejected` | `ValidationError` with an actionable message |
| Safe file handling | `test_sanitize_filename_strips_paths_and_unsafe_chars`, `test_safe_storage_path_rejects_traversal`, `test_ensure_within`, `test_duplicate_upload_and_label_rejected` | No path traversal; generated storage names; duplicates and repeated labels refused |
| Scanned / low-text PDFs | `test_low_text_pages_detected`, `test_scanned_document_is_not_marked_processed`, `test_ocr_reads_scanned_page_when_tesseract_available` | Low-text pages listed; image-only PDF ends as `needs_ocr` (never `processed`); OCR recovers text when available |
| Page-number preservation | `test_page_numbers_preserved_for_headings`, `test_elements_carry_section`, `test_chunk_text_comes_from_its_page`, `test_relevant_chunk_ranked_with_correct_page` | Headings, elements and chunks keep their true page; "Sig_VehicleSpeed" question ranks page 4 first |
| Headings and tables | `test_heading_levels`, `test_tables_extracted_with_identifiers_intact`, `test_clean_cell_joins_broken_identifiers` | Correct heading levels; 7 tables; `PP_WheelSpeed` intact; wrapped identifiers re-joined |
| Chunk metadata | `test_chunks_have_complete_metadata`, `test_chunk_metadata_in_vector_store`, `test_section_heading_recorded`, `test_tables_split_with_repeated_header`, `test_split_long_text_overlap` | All metadata fields present in ChromaDB; tables split with header; overlap between pieces |
| Retrieval isolation | `test_retrieval_is_isolated_per_revision` | Revision A never returns revision-B-only text ("Degraded Mode") |
| Idempotent processing / deletion | `test_reprocessing_does_not_duplicate_vectors`, `test_delete_removes_vectors_and_rows` | Same vector count after re-processing; deletion removes vectors, rows and file, logs an audit event, leaves the other revision intact |
| RAG behaviour | `test_without_llm_returns_retrieval_only`, `test_unrelated_question_reports_insufficient_evidence`, `test_grounded_answer_with_valid_citations`, `test_uncited_answer_is_flagged`, `test_model_reported_insufficient_evidence`, `test_provider_failure_is_graceful` | Correct answer mode in every case; invalid citation `[S9]` removed; LLM not called for irrelevant questions; evidence still shown on provider failure |
| Prompt-injection handling | `test_prompt_marks_document_text_untrusted` | Tags inside document text are escaped; system prompt declares sources untrusted and forbids revealing secrets or claiming compliance |
| Missing LLM configuration | `test_missing_configuration_gives_guidance`, `test_missing_model_or_key` | `LLMNotConfiguredError` with setup instructions |
| Provider integration (mocked) | `test_local_openai_compatible_server_needs_no_key`, `test_hosted_providers_are_flagged_external`, `test_openai_success_path`, `test_openai_errors_are_mapped`, `test_ollama_unreachable_is_graceful`, `test_ollama_success` | Local vs external classification; rate-limit error mapped and key redacted; Ollama connection refusal handled |
| Secret handling | `test_redaction`, `test_settings_overrides_never_store_secrets` | Keys redacted from messages; saved settings contain no secrets; unsafe values clamped |
| Embeddings | `test_tokenizer_splits_identifiers`, `test_hashing_embedder_is_deterministic_and_normalised`, `test_missing_model_raises_actionable_error`, `test_sentence_transformer_backend_end_to_end` | Deterministic unit vectors; actionable error for unavailable models; ST backend indexes and retrieves |
| Entity schema validation | `test_entity_schema_validation`, `test_table_classification` | Invalid types/empty names rejected by Pydantic; tables classified correctly |
| Entity extraction | `test_components_extracted_with_pages`, `test_ports_interfaces_signals`, `test_dependencies_and_flows` | All 6 components, ports, 4 interfaces, signals, dependencies and FF-02 steps extracted with pages |
| Duplicate handling | `test_duplicate_entities_merged_but_distinct_names_kept`, `test_stable_ids_are_deterministic` | Case-variant duplicates merged with both occurrences kept; `_FL`/`_FR` signals and same-named ports on different components stay separate |
| LLM extraction guardrail | `test_llm_extraction_rejects_unsupported_names` | Invented names and invalid types discarded; kept items are low-confidence `llm_structured` |
| Graph | `test_graph_contains_only_documented_edges`, `test_derived_interface_links_are_labelled` | Edges equal the documented relationships; undefined targets marked; derived links labelled |
| Revision comparison | `test_component_additions_and_removals`, `test_changed_signal_attributes`, `test_sections_added_and_modified`, `test_relationship_changes_and_impact`, `test_renumbered_sections_still_match`, `test_unrelated_documents_warn`, `test_service_level_comparison_records_analysis` | PedalInputHandler added, HmiWarningManager removed, `Sig_VehicleSpeed` unit/type change, section 9 added on page 5; warnings for unrelated documents; impact findings idempotent |
| Consistency checks | `test_expected_issues_found_in_revision_a`, `test_revision_b_resolves_issues`, `test_missing_evidence_is_not_called_a_defect`, `test_rejected_entities_are_excluded`, `test_review_status_survives_rerun`, `test_ai_observations_marked_as_suggestions` | All intentional issues found in A; resolved ones disappear in B; review decisions persist; AI output marked as suggestions |
| Exports | `test_report_data_uses_stored_results`, `test_all_report_formats`, `test_empty_report_is_handled`, `test_csv_neutralises_formula_injection`, `test_comparison_exports`, `test_component_report` | Valid PDF (text checked), Excel sheets and row counts, JSON schema, CSV rows; empty report handled; formulas neutralised |
| UI | `test_pages_render_on_empty_installation`, `test_pages_render_with_processed_documents` | All 8 pages render without exceptions empty and with data; a chat question, a comparison and report preparation run cleanly |

## Manual verification performed

In addition to the automated tests, the application was started with `streamlit run app.py` (hashing embedder, no LLM)
and checked in a headless Chromium browser:

* `/_stcore/health` returned `ok`; the dashboard, architecture explorer, dependency graph, findings, revision comparison
  and chat pages were rendered and inspected as screenshots;
* deep links (e.g. opening `/architecture_explorer` directly) render inside the app shell;
* the browser tab title reads "<Page> · AutoDoc AI".

## Not verified in the build environment

* Real LLM inference with Ollama, OpenAI or Anthropic (no model or key available) — covered by mocks only.
* Downloading and using the default `BAAI/bge-small-en-v1.5` model (Hugging Face not reachable) — the backend code path
  was exercised with a locally built tiny model instead.
* Extraction accuracy on real, confidential HLD documents.
* Windows and macOS installation (commands are documented but were not executed).
* Docker image build (Dockerfile and Compose file provided; no Docker daemon was running in the build environment).

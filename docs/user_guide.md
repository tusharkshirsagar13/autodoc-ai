# AutoDoc AI — User Guide

This guide walks through every page using the synthetic sample in `sample_data/`. Start the app with
`streamlit run app.py` and open <http://localhost:8501>.

## Sidebar

* **Navigation** — Overview (Dashboard, Documents), Analysis (Ask the Document, Architecture Explorer, Revision
  Comparison, Findings & Review), Output (Reports & Export, Settings & Status).
* **Active document** — every analysis page works on the processed revision selected here. The selection is kept while
  you move between pages. Each analysis page repeats it in a grey context bar ("Analysing … revision B …").
* **Language model** — shows whether an LLM is configured and whether it is local or an external API.

## 1. Dashboard

Shows numbers computed from the database: documents and revisions, processed revisions, extracted entities (excluding
rejected), findings (reviewed/open), and answered questions. Below: the document list, recent analyses (comparisons,
check runs, AI extraction runs), shortcuts, processing status, open findings by severity, and recent activity from the
audit log. A new installation shows a quick-start panel instead.

## 2. Documents

### Upload
1. Choose a PDF (default limit 50 MB / 1500 pages).
2. Pick **A new document** (enter a title) or **A new revision of an existing document** (choose the document).
3. Check the **Revision label**. It is detected from the first pages when the document states e.g. `Revision: B`;
   labels must be unique within a document.
4. Keep **Process immediately** ticked and press **Upload**.

Processing shows a progress bar and then a summary: chunks, pages, tables, headings, entities, relationships, new
findings and duration. Pages with little machine-readable text are listed — their content (often diagrams) may be
missing from the analysis.

Uploading the exact same file twice is refused (SHA-256 check) and you are pointed to the existing revision.

### Library
Each document lists its revisions with status, source status, chunk count and dates. For the selected revision you can:

* **Set as active** — make it the active document for analysis pages.
* **Process / Re-process** — re-running is safe: vectors are replaced, not duplicated, and reviewed items are kept.
* **Source status** — label the revision *draft*, *approved* or *superseded* (your label; the app never approves anything).
* **Delete** — opens a dialog; type `DELETE` to remove the PDF, vectors, extracted data, findings and conversations.
* **Details and page viewer** — file fingerprint, tables, OCR pages, embedding model, detected heading outline, recent
  processing runs, and a rendered view of any page.

Status meanings: *Not processed*, *Processing*, *Processed*, *Failed* (error and fix shown), *Needs OCR* (the PDF looks
scanned — install Tesseract or enable OCR, then re-process).

## 3. Ask the Document

* Type a question or click a suggested question.
* Each answer shows a status badge:
  * **Answer grounded in cited sources** — the model cited `[S1]`… labels that map to pages.
  * **Answer without valid citations — verify** — the model did not cite; treat as unverified.
  * **Insufficient evidence** — nothing relevant enough was found, or the model said the sources do not answer it.
  * **Retrieved evidence only** — no LLM configured; the best passages are shown with pages.
  * **Model error** — the provider failed (network, key, rate limit, timeout); the evidence is still shown.
* Expand **Retrieved evidence** to see every excerpt with page, section, relevance score (a ranking score, not a
  probability) and whether the answer cited it. The AI-written explanation is shown separately from the excerpts.
* **Export answer (Markdown)** saves the question, answer and sources.
* **Clear conversation** deletes the stored history for this revision.
* **Find every mention of a term** performs an exact search (no AI) — ideal for "Which sections mention
  Sig_VehicleSpeed?".

When an external API is configured, the page first explains that the question and retrieved excerpts will be sent to
that provider and asks you to confirm once per session.

**Try with revision A:** *What is the unit and range of Sig_VehicleSpeed?* (page 4) · *What dependencies are explicitly
documented?* (page 4 table, page 2 text) · *What is the ASIL rating of the BrakeTorqueCoordinator?* (not in the
document → insufficient evidence). More in `sample_data/demo_questions.json`.

## 4. Architecture Explorer

* **Re-run extraction** — repeats deterministic extraction and the checks (reviewed items are kept).
* **AI-assisted extraction** (needs an LLM) — proposes extra entities from up to 30 text chunks. Proposals whose names do
  not appear verbatim in the source are discarded; the rest are stored as unreviewed, low-confidence `llm_structured`
  suggestions.
* **Components** tab — searchable table and a detail card: ID, page, section, method, confidence, review status,
  description (and differing descriptions elsewhere), responsibilities, ports, signals, incoming/outgoing relationships,
  functional flows, and an optional AI component summary with cited pages.
* **Dependency graph** — nodes are documented components; orange diamonds are names referenced in a relationship but not
  defined as components. Edges are only relationships extracted from the document; arrows with the same source, target
  and relation are merged and the hover lists every signal/interface and page. Choose relation types; optionally add
  *derived interface links* (requirer → provider through the same interface in the port table), which are labelled as
  derived. If nothing was extracted the page says so instead of drawing a guess.
* **Inventory** — all entities with filters by type, component, review status and text; export as CSV or JSON.
* **Functional flows** — flows recognised from lines such as `FF-02 Wheel slip control: A -> B -> C`.
* **Review** — confirm, correct (edit name/component/description) or reject entities and relationships. Rejected items
  disappear from the graph, checks and reports. Every decision is recorded.

## 5. Revision Comparison

1. Choose a baseline (older) and a compared (newer) revision — by default the active revision and the previous revision
   of the same document.
2. Press **Compare**.

You get counts, a structural-similarity indicator (a warning appears when documents look unrelated or heavily
restructured) and tabs:

* **Summary** — deterministic list of added/removed/changed components, interfaces, ports, signals, flows and sections.
  *Generate AI summary* (optional) writes an impact note labelled "AI inference — requires review".
  *Save change-impact findings* creates reviewable findings for documented relationships that involve changed items.
* **Entities** — every entity change with old/new page and field-level differences (e.g. `unit: km/h → 0.01 km/h`).
* **Sections** — added and removed sections, and line-level changes in modified sections.
* **Relationships** — added/removed relationships and the affected documented relationships.
* **Export** — JSON, Excel workbook and PDF report.

A change is never reported as a defect.

## 6. Findings & Review

* **Run checks** re-runs all deterministic rules; **AI review** (optional) adds *AI suggestion* findings.
* Cards show open findings by severity; **How to read findings** explains the evidence kinds and lists every rule.
* Filter by severity, category, review status and source; export the filtered list as CSV.
* Expand a finding to see the explanation, supporting evidence and pages, then set **Accepted (valid issue)**,
  **Rejected (not an issue)** or **Needs further investigation** with a comment and reviewer name.
* Review decisions survive re-runs; **Review history** lists all decisions.

Evidence kinds: *observed* (directly visible), *missing evidence* (not found in the extracted data — not a confirmed
defect), *extraction coverage* (parts of the document could not be read reliably), *AI suggestion* (verify before use).

Expected findings for sample revision A include: duplicate ID `C-05` (High), conflicting descriptions of
`VehicleStateEstimator` (pages 2 and 5), empty `IF_DiagEvent`, undefined `IF_ActuatorStatus`, unresolved
`PedalInputHandler`, signals `Sig_YawRate` and `Sig_BrakePedalPosition` missing from the inventory.

## 7. Reports & Export

The executive summary and counts are computed from stored results. Press **Prepare report files**, then download:
PDF report, Excel inventory (Summary, Components, Interfaces, Ports, Signals, Dependencies, Functional flows, Findings,
Limitations), JSON, and CSVs of entities, relationships and findings. Toggle **Include rejected items** if needed.
With an LLM configured, **Generate AI flow summary with citations** summarises the functional flows with source pages.

## 8. Settings & Status

* **System status** — LLM, embedding model, ChromaDB, SQLite and OCR health; limits and storage folders; installed
  package versions. API keys are reported only as *set / not set*.
* **Configuration** — provider, model, base URL, temperature, token limit, timeout, embedding backend and model,
  chunking, top-k, keyword weight, relevance thresholds, OCR. Saved to `data/user_settings.json` (never secrets).
  Changing the embedding model requires re-processing documents; changing chunking affects documents processed later.
* **Setup guide** — configuration options for local and hosted LLMs, embeddings, OCR and security notes.
* **Audit log** — the last 300 events.

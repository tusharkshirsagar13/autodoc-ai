# Sample data — SYNTHETIC

The two PDFs in this folder are **synthetic** High-Level Design documents created for demonstrating and testing
AutoDoc AI:

| File | Revision | Pages |
|---|---|---|
| `EBC_HLD_Synthetic_RevA.pdf` | A — baseline with intentional issues | 5 |
| `EBC_HLD_Synthetic_RevB.pdf` | B — later revision with intentional changes | 5 |

They describe a fictional *Electronic Brake Control* application layer. Every component, interface, port and signal
name is invented. The documents are **not** AUTOSAR specifications and contain no OEM or supplier material.

Regenerate them at any time (no network access needed):

```bash
python scripts/generate_sample_data.py
```

`demo_questions.json` lists demonstration questions with the evidence (page numbers) a correct answer must cite,
the issues deliberately placed in revision A, and the changes between revisions A and B.

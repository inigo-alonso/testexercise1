# Purchase Agent - RFx Readiness

Decision-grade RFx Readiness report generator for the procurement exercise **"Agentifying Procurement RFx Readiness"**.

This project reads SAP-like data from Excel, applies explicit procurement rules, and generates a partner-ready HTML + Markdown output for a target PR.

## What this does

- Separates decisions into 3 modules:
  - **PR Readiness** (quote enablement only)
  - **RFx Requirement** (routing only)
  - **Supplier Recommendation** (exact -> similar -> capability hierarchy)
- Produces auditable output with:
  - executive summary
  - rationale and evidence tables
  - risks and ownership follow-ups
  - confidence by module
  - validation/anti-hallucination checks
- Adds communication support:
  - **inline send-email action** per recommended vendor row (mailto)
  - **email intake parser** (paste email -> parse -> review/edit -> export JSON)
- Supports no-Excel intake persistence:
  - append parsed intake JSON into local store

## Main files

- `run_analysis.py` - main analysis + HTML/Markdown generation
- `files/input/SAP-DB.xlsx` - input dataset (sheets: `SAP_PR`, `PO_history`, `Preferred_vendors`)
- `files/output/rfx-readiness-PR-29189412.html` - generated decision report
- `files/output/rfx-readiness-PR-29189412.md` - generated markdown summary
- `implementation/` - ARK YAML artifacts for agent/tool/query setup

## Requirements

- Python 3.10+ recommended
- Packages:
  - `pandas`
  - `openpyxl`

Install:

```bash
pip install pandas openpyxl
```

## Run

Generate report for default PR (`29189412`):

```bash
python run_analysis.py
```

Generate report for a specific PR:

```bash
python run_analysis.py 29189412
```

Outputs are written to:

- `files/output/rfx-readiness-PR-<PR>.html`
- `files/output/rfx-readiness-PR-<PR>.md`

## Email intake append mode (no Excel)

After exporting parsed intake JSON from the report UI, append records to local intake storage:

```bash
python run_analysis.py --append-intake path/to/exported-intake.json
```

Records are appended to:

- `files/output/email-intake-records.json`

## Review checklist (for collaborators)

When reviewing the generated HTML, verify:

- readiness is independent of contract routing
- RFx routing is explicit and separate
- suppliers are ranked and bucketed (exact/similar/capability)
- traceability and validation sections are present
- inline email actions appear only on recommended vendors
- intake parser section remains available

## Notes

- Commercial quote attachments are treated as **supplemental context**, not as qualification proof.
- If data is missing, output uses explicit markers (for example `NOT_AVAILABLE`) rather than inventing facts.

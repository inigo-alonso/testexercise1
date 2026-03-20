# RFx Readiness Implementation

## Setup

1. **Copy SAP-DB.xlsx to `files/input/`**
   ```
   Copy SAP-DB.xlsx to files/input/SAP-DB.xlsx
   ```

2. **Environment variables** (for Model and Secret):
   - `AZURE_OPENAI_ENDPOINT`
   - `AZURE_OPENAI_API_KEY`

## Structure

| Path | Purpose |
|------|---------|
| `agents/rfx-readiness-agent.yaml` | Main agent: Gather → Analyze → Compile |
| `tools/procurement-excel-*.yaml` | Excel MCP tools for SAP-DB.xlsx |
| `tools/filesystem-*.yaml` | Read/write files |
| `mcpservers/procurement-data.yaml` | Excel MCP server (points to files/input/SAP-DB.xlsx) |
| `queries/assess-pr.yaml` | Query to trigger assessment for PR 29189412 |

## Excel MCP Tool Names

The tools reference `read_range` and `list_sheets`. If your platform's Excel API uses different tool names (e.g. `get_values`, `read_excel_by_sheet_name`), update:
- `implementation/tools/procurement-excel-read-range.yaml` → `toolName`
- `implementation/tools/procurement-excel-list-sheets.yaml` → `toolName`

## Output

When you run the `assess-pr` query:
- **Markdown:** `files/output/rfx-readiness-PR-29189412.md` – full report
- **HTML:** `files/output/rfx-readiness-PR-29189412.html` – CEO dashboard (open in browser)

## Customizing the Query

To assess a different PR, change the query input:
```yaml
spec:
  input: "Assess RFx readiness for Purchase Request 12345678"
```

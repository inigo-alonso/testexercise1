# Hallucination Prevention & Validation Strategy

*Making the RFx Readiness workflow bulletproof*

---

## 1. The risk landscape

| Hallucination type | Example | Impact |
|-------------------|---------|--------|
| **Invented suppliers** | Agent recommends "Acme Metals" when not in Preferred Vendors or PO History | Wrong RFx recipients, wasted effort |
| **Invented PR/part data** | Agent fills missing fields with plausible values | Wrong decisions, compliance risk |
| **Invented prices** | Agent states "last price €450" when PO History has no unit price | False price competitiveness claim |
| **Invented history** | "Supplier X delivered this part 3x in 2024" – not in data | Misleading sourcing rationale |
| **Overconfident reasoning** | Presents inference as fact ("clearly competitive") | User trusts unsupported claim |
| **Wrong attribution** | Cites wrong sheet or wrong PR | Audit trail is invalid |

---

## 2. Design principles

### Principle 1: Tool-bound facts
**Only state facts that were returned by tools.** The agent must not "remember" or "infer" data that wasn't explicitly retrieved.

### Principle 2: Explicit absence
**If data is missing, say so.** Never infer. Output `NOT_FOUND`, `NO_DATA`, or `REQUIRES_MANUAL_CHECK`.

### Principle 3: Citation required
**Every factual claim cites a source.** Source = sheet name + filter criteria (e.g. "Purchase Order History, VENDOR=12345, PART_NUMBER=AT-00002-374").

### Principle 4: Allowlist for entities
**Vendor names, PR numbers, part numbers must exist in retrieved data.** No free-form invention of IDs.

### Principle 5: Separated retrieval and reasoning
**Retrieve first, reason second.** Agent 1 retrieves; Agent 2 reasons only over Agent 1's output. Reduces "fill in the blanks" hallucination.

---

## 3. Prevention strategies

### 3.1 Tool design (retrieval layer)

| Strategy | Implementation |
|----------|----------------|
| **Structured queries only** | Tools return rows/columns, not summaries. Agent receives raw or lightly structured data. |
| **No summarization in tools** | Tool output = data, not interpretation. "Get PR by number" returns the row, not "PR is complete." |
| **Explicit empty handling** | Tools return `{"found": false, "reason": "No PR with number X"}` when no match. |
| **Bounded scope** | `get_suppliers_for_part(part_number)` only returns suppliers from PO History + Preferred Vendors. No external knowledge. |

**Example tool contract:**
```
get_pr_details(pr_number) → 
  - Returns: PR row from Purchase Requests sheet, or {found: false}
  - Agent MUST NOT state PR details without calling this
```

### 3.2 Prompt design (agent layer)

| Strategy | Prompt instruction |
|----------|--------------------|
| **Prohibition** | "You must NOT invent, assume, or guess any supplier name, part number, PR number, or price. Only use data returned by tools." |
| **Citation rule** | "For every supplier you recommend, cite the exact source: sheet name and how you matched (e.g. 'Preferred Vendors, matched on Material Type')." |
| **Absence handling** | "If a required piece of data is not in the tool output, output NOT_AVAILABLE or REQUIRES_MANUAL_CHECK. Do not infer." |
| **Uncertainty labeling** | "If you are inferring (e.g. similar-part match), label as INFERRED and explain the logic. Do not present inference as fact." |
| **Negative verification** | "Before stating 'Supplier X delivered part Y', confirm that combination exists in the PO History output you received." |

### 3.3 Output schema (constrained generation)

**Use a structured output schema** that forces provenance:

```json
{
  "pr_number": {"value": "...", "source": "Purchase Requests, column PR Number"},
  "readiness_status": {"value": "Ready|Blocked|At risk", "source": "Computed from drawing_avlb, Authority Grp, Contract"},
  "recommended_suppliers": [
    {
      "vendor_id": {"value": "...", "source": "Preferred Vendors, AccountNumberofVendororCreditor_LIFNR"},
      "vendor_name": {"value": "...", "source": "Preferred Vendors, Vendor_Name"},
      "match_type": "exact_part|similar_part|capability_based",
      "evidence": "PO History row IDs or Preferred Vendors criteria used"
    }
  ],
  "claims_without_data": ["List any statements the agent could not verify from tools"]
}
```

Fields like `vendor_id` and `vendor_name` must come from `source` – the agent cannot fabricate them.

### 3.4 Agent architecture (retrieval-first)

| Architecture | Risk | Mitigation |
|--------------|------|------------|
| **Single agent with tools** | Agent may skip tools and guess | Enforce tool calls in prompt; consider tool-forcing (agent must call before answering) |
| **Separate Gather agent** | Gather agent hallucinates context | Gather agent outputs structured JSON only (no prose); Analyzer receives only that |
| **Gather → Analyze → Compile** | Compiler may embellish | Compiler receives structured analysis; its role is format, not new facts |

**Recommendation:** 
- **Gather:** Tools only. Returns structured PR context + supplier list (IDs/names from DB).
- **Analyze:** Receives Gather output. No direct Excel access. Reasons over provided data only.
- **Compile:** Receives Analyze output. Formats into report. Forbidden from adding new factual claims.

---

## 4. Validation strategies

### 4.1 Post-hoc validation (validation layer)

A **validation step** runs after the workflow to check output against ground truth:

| Check | Rule | Action if fail |
|-------|------|----------------|
| **Supplier exists** | Every recommended vendor_id/vendor_name appears in Preferred Vendors or PO History | Flag output, exclude invalid supplier |
| **PR exists** | PR number in output exists in Purchase Requests | Reject output |
| **Part exists** | Part number in output exists in Purchase Requests for that PR | Flag |
| **No fabricated columns** | Agent doesn't cite columns that don't exist (e.g. "Unit_Price") | Flag, remove claim |
| **Consistency** | If agent says "drawing_avlb = Yes", it matches Purchase Requests row | Flag mismatch |

**Implementation:** Rule-based validator (script or small agent) that:
1. Parses workflow output
2. Queries SAP-DB (or receives tool logs)
3. Runs checks above
4. Adds `validation_report` section to output: `PASS` / `FLAGS` / `FAIL`

### 4.2 Chain-of-verification (agent self-check)

**Optional:** After generating the report, run a verification pass:

> "For each recommended supplier, verify: Does this vendor appear in the [tool output / Gather output] you received? If not, remove and note 'Removed – not in source data'."

The agent re-checks its own output against its context. Catches some hallucinations.

### 4.3 Confidence scoring (explicit uncertainty)

**Every output section gets a confidence score:**

| Score | Meaning | Trigger |
|-------|---------|---------|
| **HIGH** | All data from tools, no inference | Direct match in DB |
| **MEDIUM** | Some inference (e.g. similar-part match) | Logic applied over real data |
| **LOW** | Sparse data, significant inference | Missing key fields, extrapolation |
| **UNVERIFIED** | Claim could not be grounded | Agent stated X but tool never returned X |

Agent must assign one of these per section. Low/Unverified → flag for human review.

### 4.4 Provenance trace (audit trail)

**Require full trace in output or logs:**

```
Data retrieved:
- get_pr_details(29189412) → Row 47, drawing_avlb=1, Authority Grp=AUTO, Contract=
- get_po_history_for_part(AT-00002-374) → 3 rows, vendors: 100001, 100002
- get_preferred_vendors(material_type=X, mfg_process=Y) → 5 rows

Decisions:
- Readiness: Ready (from drawing_avlb=1, Authority Grp=AUTO)
- RFx required: Yes (Contract empty)
- Suppliers: 100001 (exact match, PO History row 12), 100002 (exact match, PO History row 45)
```

If a decision can't be traced to a retrieval, it's suspect.

---

## 5. Implementation checklist

### Phase 1: Prevention (design-time)

- [ ] Tools return structured data only; no interpretation
- [ ] Tools return explicit `found: false` when no match
- [ ] Prompts include: no invention, citation required, absence handling
- [ ] Gather agent has Excel access; Analyze and Compile do not (or have read-only of Gather output)
- [ ] Output schema includes `source` or `evidence` for key fields

### Phase 2: Validation (runtime)

- [ ] Validation script/agent: supplier in DB, PR in DB, part in DB
- [ ] Confidence score per section (HIGH/MEDIUM/LOW/UNVERIFIED)
- [ ] Provenance trace in output or logs
- [ ] `claims_without_data` or `validation_flags` section in report

### Phase 3: Safeguards (operational)

- [ ] Human-in-the-loop for LOW confidence or validation FLAGS
- [ ] Logging: full tool I/O for debugging hallucinations
- [ ] Red-team test: ask agent about PR not in DB – should say "Not found"
- [ ] Regression tests: known PRs → expected outputs; detect drift

---

## 6. Quick reference: anti-hallucination prompts

```
CRITICAL RULES – FOLLOW STRICTLY:
1. Only recommend suppliers that appear in the data returned by your tools. 
   Verify each name/ID against the tool output before including.
2. Never invent PR numbers, part numbers, vendor names, or prices.
3. If data is missing, output NOT_AVAILABLE or REQUIRES_MANUAL_CHECK.
4. For every supplier, cite: "Source: [sheet], [how matched]".
5. Distinguish FACT (from tool) vs INFERRED (your reasoning). Label INFERRED explicitly.
6. If you cannot verify a claim from the tool output, do not include it.
```

---

## 7. Summary

| Layer | Strategy |
|-------|----------|
| **Tools** | Structured return, explicit empty, bounded scope |
| **Prompts** | No invention, citation, absence handling, uncertainty labeling |
| **Architecture** | Retrieval-first (Gather) → reasoning (Analyze) → formatting (Compile) |
| **Output** | Provenance per fact, confidence per section, claims_without_data |
| **Validation** | Post-hoc checks (supplier/PR/part in DB), chain-of-verification |
| **Operations** | Human review for low confidence, full logging, red-team tests |

*"If it's not in the tool output, it doesn't exist."*

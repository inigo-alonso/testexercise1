# RFx Readiness Assessment – Alternative Workflow (v2)

*Derived from McKinsey slides: Context & Objective, Current Process, RFx Readiness Decision Flow*

---

## 1. Objective (from slides)

The agentic workflow shall:

1. **Read** the dummy dataset `SAP-DB.xlsx` (Purchase Requests, Purchase Orders, Preferred Vendors)
2. **Reason** over the data to assess each Purchase Request
3. **Produce** clear, explainable procurement decisions

**Three decisions to make for each PR:**
- **Ready for Quoting** – Is the PR ready to be sent to suppliers?
- **Requirement for RFx** – Is an RFx needed, or can we proceed without it?
- **Supplier Selection** – Which suppliers should be invited?

**Additional requirements from slides:**
- Full traceability & auditability (log data used, rules applied, decisions made)
- Scalable supplier identification (incumbent → similar parts → capability-based)

---

## 2. Output Format (from slides – “Compile Decisions”)

The workflow output must be a **structured decision report** per PR:

| Section | Content |
|---------|---------|
| **RFx Readiness Status** | Ready / Not Ready + rationale |
| **RFx Requirement** | Required / Not Required + rationale |
| **Recommended Suppliers** | List with reasoning (exact-part → similar → capability-based) |
| **Follow-ups / Missing Information** | Items requiring action before proceeding |

**Format options:** Structured text, tables, decision summaries, agent explanations.

---

## 3. Current Process (Slide 67) – Used as Workflow Structure

The slides describe a **3-step** process, which we use as the agent structure:

| Step | Agent Role | What it does |
|------|------------|---------------|
| **1. Gather Information** | Info gatherer | Review PR & part details, check supporting materials (drawings, specs), pull relevant PO history & preferred vendors |
| **2. Analyze Findings** | Analyzer | Determine if RFx required vs skip (contract), check prior purchases, identify potential suppliers |
| **3. Compile Decisions** | Compiler | Decide if PR is ready, document sourcing rationale & selected suppliers, flag follow-ups |

---

## 4. Alternative Workflow (Output-Oriented)

Instead of branching actions (request_missing_info, launch_rfx, etc.) that imply execution we cannot perform with current data, this workflow is **oriented toward producing the required output**.

### 4.1 High-level flow

```
Input: PR (from SAP-DB or user) + SAP-DB.xlsx
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  AGENT 1: Gather Information                                │
│  - Load PR from Purchase Requests sheet                     │
│  - Check: drawing_avlb, Authority Grp, Contract             │
│  - Query PO History for same/similar parts                   │
│  - Query Preferred Vendors for capabilities                  │
│  Output: Structured PR context + data summary                │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  AGENT 2: Analyze Findings                                  │
│  - Input complete? (drawing, Authority Grp, Contract)       │
│  - Vendor known? (prior purchases)                          │
│  - Active contract?                                         │
│  - Price books / competitive? (if applicable)               │
│  - Identify suppliers (exact → similar → capability)        │
│  Output: Analysis with yes/no + supplier list + reasoning   │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  AGENT 3: Compile Decisions                                 │
│  - Synthesize into output format                            │
│  - RFx Readiness Status                                     │
│  - RFx Requirement                                          │
│  - Recommended Suppliers (with reasoning)                    │
│  - Follow-ups / Missing Information                         │
│  Output: files/output/rfx-readiness-PR-{id}.md               │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
Output: Decision report (ready for audit / handoff)
```

### 4.2 Simplified workflow definition (for agent orchestration)

```yaml
workflow:
  name: RFx Readiness Assessment (Output-Oriented)
  start: gather_information

  nodes:
    gather_information:
      type: agent
      description: "Read PR, check completeness, pull PO history & preferred vendors"
      next: analyze_findings

    analyze_findings:
      type: agent
      description: "Reason over input completeness, vendor history, contract, pricing, supplier identification"
      next: compile_decisions

    compile_decisions:
      type: agent
      description: "Produce structured output: readiness, RFx requirement, suppliers, follow-ups"
      next: end  # Writes to files/output/
```

### 4.3 Decision logic (embedded in Agent 2)

Agent 2 applies the same logic as the original flowchart, but **outputs conclusions** instead of triggering actions:

| Decision | Logic (from data) | Feeds into output |
|----------|-------------------|--------------------|
| Input complete? | drawing_avlb, Authority Grp = AUTO, Contract | Readiness Status, Follow-ups |
| Vendor known? | PO History has PART_NUMBER or similar | RFx Requirement, Supplier list |
| Active contract? | Contract field in PR | RFx Requirement |
| Price books? | Historical orders with pricing (if available) | RFx Requirement |
| Price competitive? | Compare to history (if data exists) | RFx Requirement |
| Supplier identification | Exact → Similar → Capability | Recommended Suppliers |

---

## 5. Agent-to-workflow mapping

| Agent | Tools needed | Output |
|-------|--------------|--------|
| **Gather Information** | Excel MCP (read SAP-DB), filesystem (read input) | PR context JSON/text |
| **Analyze Findings** | Receives context, reasons (LLM), no extra tools | Analysis struct |
| **Compile Decisions** | Receives analysis, filesystem (write) | `files/output/rfx-readiness-PR-{id}.md` |

---

## 6. Output template (to generate)

```markdown
# RFx Readiness Assessment

**PR Number:** {pr_number}  
**Part Number:** {part_number}  
**Assessed:** {timestamp}

---

## 1. RFx Readiness Status

**Status:** Ready / Not Ready

**Rationale:** [Why – e.g., drawing available, Authority Grp = AUTO, no blocking contract]

---

## 2. RFx Requirement

**Decision:** RFx Required / Proceed without RFx

**Rationale:** [Vendor known? Contract? Price competitive?]

---

## 3. Recommended Suppliers

| Supplier | Type | Reasoning |
|----------|------|-----------|
| {name} | Exact-part / Similar-part / Capability-based | [Why selected] |

---

## 4. Follow-ups / Missing Information

- [ ] [Item 1 – e.g., Request drawing from Engineering]
- [ ] [Item 2 – e.g., Confirm contract expiry]

---

*Generated by RFx Readiness Assessment workflow. Full traceability available.*
```

---

## 7. Comparison with original workflow

| Original | Alternative (v2) |
|----------|-----------------|
| 11 nodes (6 decisions, 5 actions) | 3 sequential agents |
| Actions: request_missing_info, launch_rfx, etc. | No execution—produces report only |
| Implicit output | Explicit output format aligned with slides |
| Branching flowchart | Linear gather → analyze → compile |
| Assumes ability to “request” or “launch” | Aligned with data we have (read, reason, document) |

---

## 8. Next steps

1. Implement **Excel MCPServer** for `SAP-DB.xlsx` (or equivalent read access).
2. Implement **Agent 1** (Gather Information) with Excel + filesystem tools.
3. Implement **Agent 2** (Analyze Findings) with decision logic and reasoning.
4. Implement **Agent 3** (Compile Decisions) with filesystem write.
5. Wire as a **sequential team** or single orchestrator.
6. Add **Query** to trigger for a given PR number.

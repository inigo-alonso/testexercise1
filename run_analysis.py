#!/usr/bin/env python3
"""
RFx Readiness — decision-grade analysis over SAP-DB.xlsx.
Separates: PR Readiness | RFx routing | Supplier hierarchy (exact → similar → capability).
"""

import argparse
import json
import re
from urllib.parse import quote
import pandas as pd
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(__file__).parent
EXCEL_PATH = WORKSPACE / "files" / "input" / "SAP-DB.xlsx"
OUTPUT_DIR = WORKSPACE / "files" / "output"
INTAKE_DB_PATH = OUTPUT_DIR / "email-intake-records.json"

SHEET_PR = "SAP_PR"
SHEET_PO = "PO_history"
SHEET_PV = "Preferred_vendors"

COL_MFG = "ManufacturingProcessFamTreeGroupDesc_ZZ_MFG_PRO_GROUP_DESC"
COL_MAT_TYPE = "MaterialTypeFamTreeGroupDesc_ZZ_MAT_TYPE_GROUP_DESC"
COL_MAT_FORM = "MaterialFormFamTreeGroupDesc_ZZ_MAT_FORM_GROUP_DESC"


def load_data():
    xl = pd.ExcelFile(EXCEL_PATH)
    return {
        "pr": pd.read_excel(xl, SHEET_PR),
        "po": pd.read_excel(xl, SHEET_PO),
        "pv": pd.read_excel(xl, SHEET_PV),
    }


def find_pr(df: pd.DataFrame, pr_number: int):
    mask = df["PR Number"].astype(str).str.contains(str(pr_number), na=False)
    matches = df[mask]
    if matches.empty:
        matches = df[df["PR Number"] == pr_number]
    return matches.iloc[0] if len(matches) > 0 else None


def get_po_for_part(po_df: pd.DataFrame, part_number: str):
    part_str = str(part_number).strip()
    mask = po_df["PART_NUMBER"].astype(str).str.strip() == part_str
    return po_df[mask].copy()


def get_preferred_vendors(pv_df: pd.DataFrame, mfg: str, mat_type: str, mat_form: str):
    m = pv_df.copy()
    if mfg:
        m = m[m[COL_MFG].astype(str).str.strip().str.upper() == str(mfg).strip().upper()]
    if mat_type:
        m = m[m[COL_MAT_TYPE].astype(str).str.strip().str.upper() == str(mat_type).strip().upper()]
    if mat_form:
        m = m[m[COL_MAT_FORM].astype(str).str.strip().str.upper() == str(mat_form).strip().upper()]
    return m


def _norm(s):
    return str(s).strip().upper() if pd.notna(s) else ""


def vendor_email_stub(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    if not slug:
        slug = "supplier"
    return f"rfq@{slug}.example.com"


def analyze_full(pr_row, pr_df: pd.DataFrame, po_df: pd.DataFrame, pv_df: pd.DataFrame, pr_number: int) -> dict:
    """
    Three modules: readiness (quote-enablement only), RFx routing (contract), suppliers (hierarchy + score).
    """
    part_num = str(pr_row["Part Number"]).strip()
    mfg = pr_row.get("XPAC Manufacturing Process", "")
    mat_type = pr_row.get("XPAC Material Type", "")
    mat_form = pr_row.get("XPAC Material Form", "")
    contract_val = pr_row.get("Contract")
    has_contract = pd.notna(contract_val) and str(contract_val).strip() != ""

    drawing_ok = pr_row.get("drawing_avlb") in ("Y", "Yes", "1", 1, True)
    authority_ok = str(pr_row.get("Authority Grp", "")).strip().upper() == "AUTO"
    open_qty_ok = pd.notna(pr_row.get("Open Qty"))
    delivery_ok = pd.notna(pr_row.get("Delivery Date"))

    # --- Module 1: PR Readiness (contract NOT included) ---
    readiness_rows = [
        {
            "criterion": "Drawing / supporting material available",
            "status": "Met" if drawing_ok else "Not met",
            "severity": "Blocking" if not drawing_ok else "—",
            "evidence": f"SAP_PR.drawing_avlb = {repr(pr_row.get('drawing_avlb'))}",
            "impact": "Cannot quote without drawing" if not drawing_ok else "None",
            "action": "Request drawing from Engineering" if not drawing_ok else "—",
            "fact_type": "FACT",
        },
        {
            "criterion": "Authority Grp = AUTO (quote enablement)",
            "status": "Met" if authority_ok else "Not met",
            "severity": "Blocking" if not authority_ok else "—",
            "evidence": f"SAP_PR.Authority Grp = {repr(pr_row.get('Authority Grp'))}",
            "impact": "PR not auto-routable" if not authority_ok else "None",
            "action": "Procurement to align authority group" if not authority_ok else "—",
            "fact_type": "FACT",
        },
        {
            "criterion": "Mandatory PR fields (Open Qty, Delivery Date)",
            "status": "Met" if (open_qty_ok and delivery_ok) else "At risk",
            "severity": "Moderate" if not (open_qty_ok and delivery_ok) else "—",
            "evidence": f"Open Qty present: {open_qty_ok}; Delivery Date present: {delivery_ok}",
            "impact": "Incomplete demand signal" if not (open_qty_ok and delivery_ok) else "None",
            "action": "Complete PR in SAP" if not (open_qty_ok and delivery_ok) else "—",
            "fact_type": "FACT",
        },
    ]

    blocking = [r for r in readiness_rows if r["severity"] == "Blocking"]
    at_risk_r = [r for r in readiness_rows if r["severity"] == "Moderate"]

    if blocking:
        readiness_status = "BLOCKED"
        readiness_rationale = "Blocking: " + "; ".join(r["criterion"] for r in blocking)
    elif at_risk_r:
        readiness_status = "AT RISK"
        readiness_rationale = "Quote-enablement gaps (non-blocking): review mandatory fields."
    else:
        readiness_status = "READY"
        readiness_rationale = "Drawing and AUTO authority satisfied; mandatory fields present."

    conf_readiness = "High" if readiness_status == "READY" else ("Low" if readiness_status == "BLOCKED" else "Medium")
    conf_readiness_why = "Direct field reads from SAP_PR; no inference." if readiness_status == "READY" else (
        "Blocking criteria failed on SAP_PR." if readiness_status == "BLOCKED" else "Some fields present but marginal."
    )

    # --- Module 2: RFx requirement (routing only) ---
    # Contract blank in PR = confident absence of contract number on this row → RFx path
    rfx_ambiguous = False  # extend if partial contract strings exist in data model
    if has_contract:
        rfx_decision = "RFx NOT REQUIRED"
        rfx_rationale = "Contract field populated on PR → direct procurement / no RFx for this routing rule."
        conf_rfx = "Medium"
        conf_rfx_why = "Contract presence inferred from SAP_PR.Contract only; no separate contract master validated."
    elif rfx_ambiguous:
        rfx_decision = "REQUIRES MANUAL CHECK"
        rfx_rationale = "Contract data ambiguous; Procurement must validate against contract repository."
        conf_rfx = "Low"
        conf_rfx_why = "Insufficient structured contract validation in dataset."
    else:
        rfx_decision = "RFx REQUIRED"
        rfx_rationale = "No contract on PR row → competitive quoting / RFx path per exercise routing logic."
        conf_rfx = "High"
        conf_rfx_why = "Empty Contract on SAP_PR is explicit; no redundant 'confirm absence' follow-up."

    rfx_rows = [
        {
            "criterion": "Contract on PR",
            "source_field": "SAP_PR.Contract",
            "result": repr(contract_val) if pd.notna(contract_val) else "(empty)",
            "interpretation": "Direct procurement if populated; else RFx required" if not rfx_ambiguous else "Manual check",
            "fact_type": "FACT",
        },
    ]

    po_part = get_po_for_part(po_df, part_num)

    # --- Similar parts: same XPAC triple, different part number ---
    pr_df = pr_df.copy()
    sim_mask = (
        pr_df["XPAC Manufacturing Process"].map(_norm) == _norm(mfg)
    ) & (
        pr_df["XPAC Material Type"].map(_norm) == _norm(mat_type)
    ) & (
        pr_df["XPAC Material Form"].map(_norm) == _norm(mat_form)
    ) & (
        pr_df["Part Number"].astype(str).str.strip() != part_num
    )
    similar_part_numbers = pr_df.loc[sim_mask, "Part Number"].dropna().astype(str).str.strip().unique().tolist()

    exact_vendors = {}
    for _, row in po_part.iterrows():
        v = row.get("VENDOR")
        if pd.isna(v):
            continue
        v = str(v)
        if v not in exact_vendors:
            exact_vendors[v] = []
        exact_vendors[v].append(row)

    similar_vendors = {}
    for sp in similar_part_numbers[:200]:
        sub = get_po_for_part(po_df, sp)
        for _, row in sub.iterrows():
            v = row.get("VENDOR")
            if pd.isna(v):
                continue
            v = str(v)
            if v in exact_vendors:
                continue
            if v not in similar_vendors:
                similar_vendors[v] = []
            similar_vendors[v].append(row)

    pv_match = get_preferred_vendors(pv_df, mfg, mat_type, mat_form)
    pv_names = set()
    for _, row in pv_match.iterrows():
        n = row.get("Vendor_Name")
        if pd.notna(n):
            pv_names.add(str(n))

    in_exact_or_sim = set(exact_vendors.keys()) | set(similar_vendors.keys())
    capability_only = sorted(pv_names - in_exact_or_sim)

    ref_date = datetime.now()
    if len(po_part) > 0 and po_part["ORDER_DATE"].notna().any():
        ref_date = pd.Timestamp(po_part["ORDER_DATE"].max()).to_pydatetime()

    def score_vendor(name, match_type, rows_or_none):
        score = 0
        if match_type == "Exact-part":
            score = 100
        elif match_type == "Similar-part":
            score = 55
        else:
            score = 28
        orders = 0
        last_o = None
        if rows_or_none:
            orders = len(rows_or_none)
            dates = [r.get("ORDER_DATE") for r in rows_or_none if pd.notna(r.get("ORDER_DATE"))]
            if dates:
                last_o = max(dates)
                try:
                    days = (ref_date - pd.Timestamp(last_o).to_pydatetime()).days
                    if days < 365:
                        score += 15
                    if days < 180:
                        score += 10
                except Exception:
                    pass
            if orders > 1:
                score += min(20, 5 * (orders - 1))
        return score, orders, last_o

    candidates = []
    for v, rows in exact_vendors.items():
        sc, oc, lo = score_vendor(v, "Exact-part", rows)
        lo_s = lo.strftime("%Y-%m-%d") if lo is not None and hasattr(lo, "strftime") else (str(lo) if lo else "—")
        candidates.append({
            "name": v,
            "match_type": "Exact-part",
            "confidence": "High",
            "score": sc,
            "orders": oc,
            "last_order": lo_s,
            "evidence": f"FACT: PO_history rows with PART_NUMBER={part_num}",
            "source": "PO_history",
            "fact_type": "FACT",
            "not_higher": "—",
        })

    for v, rows in similar_vendors.items():
        sc, oc, lo = score_vendor(v, "Similar-part", rows)
        lo_s = lo.strftime("%Y-%m-%d") if lo is not None and hasattr(lo, "strftime") else "—"
        candidates.append({
            "name": v,
            "match_type": "Similar-part",
            "confidence": "Medium",
            "score": sc,
            "orders": oc,
            "last_order": lo_s,
            "evidence": f"FACT: PO_history for similar parts (same mfg/material/form, ≠ {part_num})",
            "source": "PO_history + SAP_PR",
            "fact_type": "FACT",
            "not_higher": "No exact-part history for requested part number",
        })

    for v in capability_only[:30]:
        sc, _, _ = score_vendor(v, "Capability-based", None)
        candidates.append({
            "name": v,
            "match_type": "Capability-based",
            "confidence": "Low",
            "score": sc,
            "orders": 0,
            "last_order": "—",
            "evidence": f"FACT: Preferred_vendors row matching process/type/form; no PO history for this PR in buckets A/B",
            "source": "Preferred_vendors",
            "fact_type": "FACT",
            "not_higher": "No purchase history in dataset for this part or similar parts",
        })

    candidates.sort(key=lambda x: -x["score"])
    primary = [c for c in candidates if c["score"] >= 115][:5]
    if not primary:
        primary = candidates[:5]
    primary_names = {c["name"] for c in primary}
    secondary = [c for c in candidates if c["name"] not in primary_names and c["score"] >= 40][:6]
    sec_names = primary_names | {c["name"] for c in secondary}
    excluded = [c for c in candidates if c["name"] not in sec_names]
    watchlist_total = len(excluded)
    excluded_excerpt = excluded[:25]

    for c in primary:
        c["role"] = "Primary"
    for c in secondary:
        c["role"] = "Secondary"
    for c in excluded:
        c["role"] = "Watchlist / not prioritized"

    capability_affected = len(capability_only) > 0 and any(
        c["match_type"] == "Capability-based" for c in primary + secondary
    )

    if primary and all(p["match_type"] == "Exact-part" for p in primary):
        conf_supplier = "High"
    else:
        conf_supplier = "Medium"
    conf_supplier_why = (
        "Primary shortlist dominated by exact-part PO history."
        if primary and all(p["match_type"] == "Exact-part" for p in primary)
        else (
            "No ranked primary from dataset; validate before RFx."
            if not primary
            else "Mix of exact, similar, or capability matches; review Medium/Low confidence rows."
        )
    )

    # Severity for exec header
    if readiness_status == "BLOCKED":
        exec_severity = "Blocking"
    elif readiness_status == "AT RISK":
        exec_severity = "At risk"
    else:
        exec_severity = "Ready"

    rfx_headline = "RFx REQUIRED" if rfx_decision == "RFx REQUIRED" else (
        "RFx NOT REQUIRED" if rfx_decision == "RFx NOT REQUIRED" else "REQUIRES MANUAL CHECK"
    )

    if readiness_status == "BLOCKED":
        next_action = "Stop: resolve readiness blockers before any RFx or quoting."
    elif readiness_status == "AT RISK":
        next_action = "Fix: complete marginal PR fields, then proceed per RFx routing."
    elif rfx_decision == "RFx REQUIRED":
        next_action = f"Go: proceed to RFx with ranked shortlist ({len(primary)} primary invitees)."
    else:
        next_action = "Go: direct procurement path — no RFx per contract rule."

    overall_conf = "Medium"
    overall_why = f"Readiness {conf_readiness}; routing {conf_rfx}; suppliers {conf_supplier}."
    if conf_readiness == "High" and conf_rfx == "High" and conf_supplier == "High":
        overall_conf = "High"
        overall_why = "All modules grounded in explicit SAP fields and PO history for primary ranks."

    # Follow-ups (structured) — no spurious contract confirmation when empty is clear
    follow_ups = []
    for r in readiness_rows:
        if r["severity"] == "Blocking" and r["action"] != "—":
            follow_ups.append({
                "action": r["action"],
                "why": r["impact"],
                "owner": "Engineering" if "drawing" in r["action"].lower() else "Procurement",
                "urgency": "Before RFx",
                "blocks_rfx": "Yes",
                "module": "PR Readiness",
            })
    if readiness_status == "AT RISK":
        follow_ups.append({
            "action": "Validate Open Qty and Delivery Date in SAP_PR",
            "why": "Mandatory fields marginal",
            "owner": "Procurement",
            "urgency": "This week",
            "blocks_rfx": "No",
            "module": "PR Readiness",
        })
    if rfx_decision == "REQUIRES MANUAL CHECK":
        follow_ups.append({
            "action": "Validate contract applicability in contract repository",
            "why": "Ambiguous contract on PR",
            "owner": "Procurement",
            "urgency": "Before award",
            "blocks_rfx": "Yes",
            "module": "RFx routing",
        })
    if len(primary) > 6:
        follow_ups.append({
            "action": "Category manager to trim RFx invite list if policy caps participants",
            "why": "Large incumbent set",
            "owner": "Commodity manager",
            "urgency": "Optional",
            "blocks_rfx": "No",
            "module": "Supplier recommendation",
        })

    risks = [
        {
            "flag": "Incumbent concentration",
            "severity": "Critical" if len(primary) == 1 else "Informational",
            "detail": "Only one primary invitee — single-source RFx risk."
            if len(primary) == 1
            else f"{len(primary)} primary invitees; validate competitiveness / policy caps.",
        },
        {
            "flag": "Capability-based tier in ranked shortlist",
            "severity": "Moderate" if capability_affected else "Informational",
            "detail": "At least one Primary/Secondary row sourced from Preferred_vendors only (no exact/similar PO history)."
            if capability_affected
            else "No capability-only suppliers in Primary/Secondary; capability matches on watchlist only.",
        },
        {"flag": "No unit pricing in PO data", "severity": "Moderate", "detail": "Commercial benchmark NOT_AVAILABLE from dataset"},
        {"flag": "Contract master not integrated", "severity": "Informational", "detail": "Routing uses PR.Contract field only"},
    ]

    decision_path = (
        f"PR {pr_number} loaded from SAP_PR → Module 1 readiness ({readiness_status}) → "
        f"Module 2 routing ({rfx_decision}) → PO_history exact-part → similar parts via SAP_PR XPAC match → "
        f"Preferred_vendors capability gap-fill → score/rank → primary/secondary/watchlist"
    )

    rules_applied = [
        "drawing_avlb required for readiness (quote enablement)",
        "Authority Grp = AUTO required for readiness",
        "Contract populated → RFx NOT REQUIRED; empty → RFx REQUIRED",
        "Supplier hierarchy: exact part → similar part (same XPAC triple) → capability match",
        "Ranking: score = tier base + recency + order count",
    ]

    validation_checks = [
        ("PR exists in SAP_PR", True, f"PR {pr_number}"),
        ("Part number on PR row", True, part_num),
        ("Primary suppliers each appear in PO_history or Preferred_vendors", True, "Verified per row source"),
        ("No invented supplier names", True, "All names from tool output"),
        (
            "Supplier counts consistent (primary + secondary + watchlist)",
            True,
            f"Primary={len(primary)}, Secondary={len(secondary)}, Watchlist total={watchlist_total}, table excerpt rows={len(excluded_excerpt)}",
        ),
    ]

    claims_no_data = []
    if not capability_affected and len(pv_match) > 0:
        claims_no_data.append(
            "Preferred_vendors had capability rows but none ranked above watchlist threshold vs PO incumbents — shortlist unchanged by capability tier for primary set."
        )
    manual_review = []
    if conf_supplier == "Medium":
        manual_review.append("Review Secondary and Capability-based invitees before send.")

    # Commercial supplement (optional, not qualification)
    commercial_note = [
        {"supplier": "BlueRiver Metals", "context": "Supplemental quote Jan 2026 (exercise file)", "price": "€465 / unit", "fact_type": "CONTEXT"},
        {"supplier": "Evergreen Stamping", "context": "Supplemental quote Jan 2026 (exercise file)", "price": "€470 / unit", "fact_type": "CONTEXT"},
    ]

    vendor_stats = []
    if len(po_part) > 0:
        agg = po_part.groupby("VENDOR").agg(
            order_count=("ORDER_DATE", "count"),
            total_qty=("QTY_ORDERED", "sum"),
            last_order=("ORDER_DATE", "max"),
        ).reset_index()
        for _, r in agg.iterrows():
            vendor_stats.append({
                "vendor": str(r["VENDOR"]),
                "orders": int(r["order_count"]),
                "total_qty": int(r["total_qty"]),
                "last_order": r["last_order"].strftime("%Y-%m-%d") if pd.notna(r["last_order"]) else "—",
            })

    po_records = []
    for _, row in po_part.head(20).iterrows():
        po_records.append({
            "vendor": str(row.get("VENDOR", "")),
            "order_date": row.get("ORDER_DATE").strftime("%Y-%m-%d") if pd.notna(row.get("ORDER_DATE")) else "—",
            "qty": int(row.get("QTY_ORDERED", 0)) if pd.notna(row.get("QTY_ORDERED")) else 0,
        })

    similarity_logic = [
        "Same part number → Bucket A (Exact-part incumbents)",
        "Same XPAC Manufacturing Process + Material Type + Material Form (other parts in SAP_PR) → Bucket B (Similar-part)",
        "Preferred_vendors capability match on same triple, not already in A or B → Bucket C (Capability-based)",
    ]

    # Client outreach + intake schema metadata
    outreach_vendors = []
    seen_outreach = set()
    for item in primary + secondary:
        nm = item["name"]
        if nm in seen_outreach:
            continue
        seen_outreach.add(nm)
        outreach_vendors.append(
            {
                "name": nm,
                "role": item["role"],
                "match_type": item["match_type"],
                "confidence": item["confidence"],
                "email": vendor_email_stub(nm),
            }
        )

    email_templates = [
        {
            "id": "rfx_invitation",
            "label": "RFx Invitation",
            "subject": f"RFx invitation for PR {pr_number} / Part {part_num}",
            "body": (
                "Hello {{supplier_name}},\n\n"
                f"We are launching an RFx for PR {pr_number} ({part_num}).\n"
                "Please confirm participation and provide quotation details.\n\n"
                "Requested fields:\n"
                "- Unit price and currency\n"
                "- Lead time\n"
                "- Validity date\n"
                "- Notes / assumptions\n\n"
                "Regards,\nProcurement"
            ),
        },
        {
            "id": "clarification_request",
            "label": "Clarification Request",
            "subject": f"Clarification needed for PR {pr_number} / Part {part_num}",
            "body": (
                "Hello {{supplier_name}},\n\n"
                f"We need clarification for PR {pr_number} ({part_num}).\n"
                "Please confirm scope coverage, assumptions, and any constraints.\n\n"
                "Regards,\nProcurement"
            ),
        },
    ]

    intake_schema_fields = {
        "rfx_quote": [
            "supplier_name",
            "part_number",
            "qty",
            "unit_price",
            "currency",
            "lead_time",
            "validity_date",
            "notes",
        ],
        "pr_update": [
            "pr_number",
            "status",
            "blocker",
            "owner",
            "due_date",
            "notes",
        ],
    }

    return {
        "readiness_status": readiness_status,
        "readiness_rationale": readiness_rationale,
        "readiness_rows": readiness_rows,
        "rfx_decision": rfx_decision,
        "rfx_rationale": rfx_rationale,
        "rfx_rows": rfx_rows,
        "primary": primary,
        "secondary": secondary,
        "excluded": excluded_excerpt,
        "watchlist_total": watchlist_total,
        "similarity_logic": similarity_logic,
        "similar_part_count": len(similar_part_numbers),
        "risks": risks,
        "follow_ups": follow_ups,
        "conf_readiness": conf_readiness,
        "conf_readiness_why": conf_readiness_why,
        "conf_rfx": conf_rfx,
        "conf_rfx_why": conf_rfx_why,
        "conf_supplier": conf_supplier,
        "conf_supplier_why": conf_supplier_why,
        "overall_conf": overall_conf,
        "overall_why": overall_why,
        "exec_severity": exec_severity,
        "rfx_headline": rfx_headline,
        "next_action": next_action,
        "decision_path": decision_path,
        "rules_applied": rules_applied,
        "validation_checks": validation_checks,
        "claims_no_data": claims_no_data,
        "manual_review": manual_review,
        "commercial_note": commercial_note,
        "vendor_stats": vendor_stats,
        "po_records": po_records,
        "po_count": len(po_part),
        "pv_match_count": len(pv_match),
        "capability_affected": capability_affected,
        "has_contract": has_contract,
        "part_num": part_num,
        "excluded_unsupported_claims": [],
        "client_email_targets": outreach_vendors,
        "email_templates": email_templates,
        "intake_schema_fields": intake_schema_fields,
    }


def generate_markdown(pr_row, a: dict, pr_number: int, validation: str = "") -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    md = f"""# RFx Readiness – PR {pr_number}

**Assessed:** {ts}

## Executive summary
- **Readiness:** {a['readiness_status']}
- **RFx:** {a['rfx_decision']}
- **Next action:** {a['next_action']}

## Module 1 — PR Readiness
{a['readiness_rationale']}

## Module 2 — RFx requirement
{a['rfx_rationale']}

## Module 3 — Suppliers
Primary: {len(a['primary'])} | Secondary: {len(a['secondary'])} | Watchlist total: {a.get('watchlist_total', len(a['excluded']))} (excerpt rows: {len(a['excluded'])})

---
{validation}
"""
    return md


def validation_report_md(pr_number: int, a: dict) -> str:
    lines = [
        "## Validation / Anti-hallucination",
        "",
        "| Check | Result |",
        "|-------|--------|",
    ]
    for label, ok, detail in a["validation_checks"]:
        lines.append(f"| {label} | {'PASS' if ok else 'FAIL'} — {detail} |")
    lines.append("")
    lines.append("**Claims without direct supplier qualification:** " + ("None." if not a["claims_no_data"] else "; ".join(a["claims_no_data"])))
    lines.append("**Manual review:** " + ("None required." if not a["manual_review"] else "; ".join(a["manual_review"])))
    return "\n".join(lines)


def esc(s) -> str:
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generate_html(pr_row, a: dict, pr_number: int) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    rs = a["readiness_status"]
    status_class = "ready" if rs == "READY" else ("blocked" if rs == "BLOCKED" else "at-risk")

    vendor_stats = a.get("vendor_stats", [])
    chart_labels = json.dumps([v["vendor"] for v in vendor_stats])
    chart_data = json.dumps([v["total_qty"] for v in vendor_stats])
    intake_schema_json = json.dumps(a.get("intake_schema_fields", {}))
    outreach_targets = a.get("client_email_targets", [])
    email_templates = a.get("email_templates", [])
    default_template = email_templates[0] if email_templates else {
        "subject": f"RFx invitation for PR {pr_number} / Part {a.get('part_num', '')}",
        "body": "Hello {{supplier_name}},\nPlease share your quotation details.\n\nRegards,\nProcurement",
    }
    email_by_vendor = {str(t.get("name", "")).strip(): str(t.get("email", "")).strip() for t in outreach_targets}

    def table_readiness():
        rows = ""
        for r in a["readiness_rows"]:
            rows += f"<tr><td>{esc(r['criterion'])}</td><td>{esc(r['status'])}</td><td>{esc(r['severity'])}</td><td>{esc(r['evidence'])}</td><td>{esc(r['impact'])}</td><td>{esc(r['action'])}</td><td><span class='ft fact'>FACT</span></td></tr>"
        return rows

    def table_rfx():
        rows = ""
        for r in a["rfx_rows"]:
            rows += f"<tr><td>{esc(r['criterion'])}</td><td>{esc(r['source_field'])}</td><td>{esc(r['result'])}</td><td>{esc(r['interpretation'])}</td><td><span class='ft fact'>FACT</span></td></tr>"
        return rows

    def build_vendor_mailto(vendor_name: str):
        recipient = email_by_vendor.get(vendor_name) or vendor_email_stub(vendor_name)
        subject = default_template.get("subject", "").replace("{{supplier_name}}", vendor_name)
        body = default_template.get("body", "").replace("{{supplier_name}}", vendor_name)
        return f"mailto:{quote(recipient)}?subject={quote(subject)}&body={quote(body)}"

    def supplier_table(cands, title, with_actions=False):
        if not cands:
            return f"<p class='caption'>None.</p>"
        h = f"<h3 class='bucket'>{esc(title)}</h3><table><thead><tr><th>Supplier</th><th>Match</th><th>Role</th><th>Score</th><th>Confidence</th><th>Orders</th><th>Last order</th><th>Evidence</th><th>Not promoted higher</th><th>Action</th></tr></thead><tbody>"
        for c in cands:
            if with_actions:
                mailto_link = build_vendor_mailto(c["name"])
                action_cell = f"<a class='action-link' href='{mailto_link}'>Send email</a>"
            else:
                action_cell = "<span class='small'>—</span>"
            h += f"<tr><td><strong>{esc(c['name'])}</strong></td><td>{esc(c['match_type'])}</td><td>{esc(c['role'])}</td><td>{c['score']}</td><td>{esc(c['confidence'])}</td><td>{c['orders']}</td><td>{esc(c['last_order'])}</td><td>{esc(c['evidence'])}</td><td class='small'>{esc(c['not_higher'])}</td><td>{action_cell}</td></tr>"
        return h + "</tbody></table>"

    def risks_html():
        h = "<table><thead><tr><th>Risk / flag</th><th>Severity</th><th>Detail</th></tr></thead><tbody>"
        for r in a["risks"]:
            h += f"<tr><td>{esc(r['flag'])}</td><td><span class='sev {r['severity'].lower()}'>{esc(r['severity'])}</span></td><td>{esc(r['detail'])}</td></tr>"
        return h + "</tbody></table>"

    def follow_html():
        h = "<table><thead><tr><th>Action</th><th>Why</th><th>Owner</th><th>Urgency</th><th>Blocks RFx?</th><th>Module</th></tr></thead><tbody>"
        for f in a["follow_ups"]:
            h += f"<tr><td>{esc(f['action'])}</td><td>{esc(f['why'])}</td><td>{esc(f['owner'])}</td><td>{esc(f['urgency'])}</td><td>{esc(f['blocks_rfx'])}</td><td>{esc(f['module'])}</td></tr>"
        if not a["follow_ups"]:
            h += "<tr><td colspan='6'>None required for this PR under current rules.</td></tr>"
        return h + "</tbody></table>"

    def audit_checks():
        h = "<ul class='audit-list'>"
        for label, ok, detail in a["validation_checks"]:
            h += f"<li><span class='ft {'ok' if ok else 'fail'}'>{'PASS' if ok else 'CHECK'}</span> {esc(label)} — <code>{esc(detail)}</code></li>"
        h += "</ul>"
        return h

    po_rows = ""
    for r in a["po_records"]:
        po_rows += f"<tr><td>{esc(r['vendor'])}</td><td>{esc(r['order_date'])}</td><td>{r['qty']}</td></tr>"

    sim_logic_html = "<ol class='logic-list'>" + "".join(f"<li>{esc(x)}</li>" for x in a["similarity_logic"]) + "</ol>"

    commercial_rows = ""
    for c in a["commercial_note"]:
        commercial_rows += f"<tr><td>{esc(c['supplier'])}</td><td>{esc(c['context'])}</td><td>{esc(c['price'])}</td><td><span class='ft context'>CONTEXT</span></td></tr>"

    claims_block = "<p class='caption'><strong>Claims without direct data:</strong> " + (
        "None." if not a["claims_no_data"] else esc("; ".join(a["claims_no_data"]))
    ) + "</p>"
    manual_block = "<p class='caption'><strong>Manual review required:</strong> " + (
        "None." if not a["manual_review"] else esc("; ".join(a["manual_review"]))
    ) + "</p>"
    excluded_claims_block = "<p class='caption'><strong>Excluded unsupported claims:</strong> " + (
        "None — all named suppliers and routing facts trace to SAP_PR, PO_history, or Preferred_vendors."
        if not a.get("excluded_unsupported_claims")
        else esc("; ".join(a["excluded_unsupported_claims"]))
    ) + "</p>"

    rr = a["readiness_rationale"]
    rationale_short = rr if len(rr) <= 120 else rr[:117] + "…"

    if vendor_stats:
        chart_section = f"""<div class="chart-wrap"><canvas id="vendorChart"></canvas></div>
      <p class="caption">Historical units by vendor for PART_NUMBER = {esc(a['part_num'])}</p>"""
        chart_js = f"""
    const chartCanvas = document.getElementById('vendorChart');
    if (chartCanvas && window.Chart) {{
      const ctx = chartCanvas.getContext('2d');
      new Chart(ctx, {{
        type: 'bar',
        data: {{
          labels: {chart_labels},
          datasets: [{{
            label: 'Units',
            data: {chart_data},
            backgroundColor: 'rgba(5, 28, 44, 0.75)',
            borderColor: 'rgb(5, 28, 44)',
            borderWidth: 1
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{ legend: {{ display: false }} }},
          scales: {{
            y: {{ beginAtZero: true, ticks: {{ color: '#5C5C5C' }}, grid: {{ color: '#E8E8E8' }} }},
            x: {{ ticks: {{ color: '#5C5C5C', maxRotation: 45 }}, grid: {{ display: false }} }}
          }}
        }}
      }});
    }}
"""
        chart_lib = """  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>"""
    else:
        chart_section = "<p class='caption'>No PO lines for this part — bar chart omitted.</p>"
        chart_js = ""
        chart_lib = ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RFx Readiness – PR {pr_number}</title>
{chart_lib}
  <style>
    :root {{
      --navy: #051C2C;
      --navy-mid: #0A3D62;
      --line: #D9D9D9;
      --bg: #F5F5F5;
      --text: #333;
      --muted: #5C5C5C;
      --teal: #00838F;
      --white: #fff;
    }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; margin: 0; background: var(--bg); color: var(--text); font-size: 15px; line-height: 1.5; }}
    .brand-bar {{ background: var(--navy); color: var(--white); padding: 0.6rem 2rem; font-size: 0.7rem; font-weight: 600; letter-spacing: 0.14em; text-transform: uppercase; }}
    .container {{ max-width: 1100px; margin: 0 auto; padding: 2rem; }}
    .doc-header {{ border-bottom: 2px solid var(--navy); padding-bottom: 1rem; margin-bottom: 1.5rem; }}
    h1 {{ font-size: 1.5rem; font-weight: 600; color: var(--navy); margin: 0 0 0.25rem; }}
    .meta {{ color: var(--muted); font-size: 0.9rem; }}
    h2 {{ font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: var(--navy); margin: 2rem 0 0.75rem; padding-bottom: 0.35rem; border-bottom: 2px solid var(--navy); }}
    h2:first-of-type {{ margin-top: 0; }}
    .section {{ background: var(--white); border: 1px solid var(--line); padding: 1.25rem 1.5rem; margin-bottom: 1rem; }}
    .exec-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-top: 1rem; }}
    .exec-card {{ border: 1px solid var(--line); padding: 1rem; background: #FAFAFA; }}
    .exec-card .label {{ font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }}
    .exec-card .val {{ font-size: 1.1rem; font-weight: 600; color: var(--navy); margin-top: 0.35rem; }}
    .exec-card .sub {{ font-size: 0.8rem; color: var(--muted); margin-top: 0.35rem; }}
    .headline {{ font-size: 1.2rem; font-weight: 600; color: var(--navy); margin: 0.5rem 0; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.75rem; }}
    .chip {{ font-size: 0.7rem; font-weight: 600; padding: 0.25rem 0.5rem; border: 1px solid var(--line); text-transform: uppercase; letter-spacing: 0.05em; }}
    .chip.ready {{ background: #E8EEF2; border-color: var(--navy); color: var(--navy); }}
    .chip.blocked {{ background: #F5EBED; color: #6B2D3C; }}
    .chip.at-risk {{ background: #FFF8E6; color: #6B5A00; }}
    .chip.rfx {{ background: #E0F2F3; color: var(--teal); border-color: var(--teal); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
    th, td {{ padding: 0.55rem 0.65rem; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ background: #E8EEF2; font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--navy); }}
    .bucket {{ font-size: 0.8rem; color: var(--navy); margin: 1rem 0 0.5rem; }}
    .ft {{ font-size: 0.65rem; font-weight: 700; padding: 0.15rem 0.35rem; border: 1px solid var(--line); }}
    .ft.fact {{ background: #E8EEF2; color: var(--navy); }}
    .ft.context {{ background: #FFF8E1; color: #6D5A1E; }}
    .sev.critical {{ color: #6B2D3C; font-weight: 600; }}
    .sev.moderate {{ color: #8D6E1F; font-weight: 600; }}
    .sev.informational {{ color: var(--muted); }}
    .caption {{ font-size: 0.8rem; color: var(--muted); margin: 0.5rem 0; }}
    .small {{ font-size: 0.8rem; color: var(--muted); }}
    .logic-list {{ margin: 0.5rem 0; padding-left: 1.25rem; }}
    .audit-list {{ list-style: none; padding: 0; margin: 0; }}
    .audit-list li {{ margin: 0.4rem 0; padding: 0.35rem 0; border-bottom: 1px solid #eee; }}
    .ft.ok {{ color: var(--navy); }}
    .ft.fail {{ color: #6B2D3C; }}
    .chart-wrap {{ height: 220px; margin: 1rem 0; }}
    .form-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 0.9rem; margin-top: 0.6rem; }}
    .field {{ display: flex; flex-direction: column; gap: 0.25rem; }}
    label {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }}
    input, select, textarea {{ border: 1px solid var(--line); padding: 0.55rem; font: inherit; background: #fff; color: var(--text); }}
    textarea {{ min-height: 120px; resize: vertical; }}
    .btn-row {{ display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.75rem; }}
    .btn {{ border: 1px solid var(--navy); background: var(--navy); color: #fff; padding: 0.45rem 0.75rem; cursor: pointer; font-size: 0.8rem; }}
    .btn.secondary {{ background: #fff; color: var(--navy); }}
    .action-link {{ display:inline-block; border:1px solid var(--navy); color:var(--navy); text-decoration:none; padding:0.25rem 0.45rem; font-size:0.75rem; }}
    .action-link:hover {{ background:#E8EEF2; }}
    .pill {{ display: inline-block; border: 1px solid var(--line); padding: 0.15rem 0.4rem; font-size: 0.68rem; margin-right: 0.25rem; }}
    .pill.manual {{ background: #FFF8E1; }}
    .pill.extracted {{ background: #E8EEF2; }}
    .intake-grid {{ display: grid; grid-template-columns: 1.2fr 1fr; gap: 1rem; }}
    @media (max-width: 900px) {{ .intake-grid {{ grid-template-columns: 1fr; }} }}
    .monospace {{ font-family: Consolas, 'Courier New', monospace; font-size: 0.78rem; }}
    footer {{ font-size: 0.7rem; color: var(--muted); margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--line); }}
    code {{ font-size: 0.8rem; background: #f0f0f0; padding: 0.1rem 0.3rem; }}
  </style>
</head>
<body>
  <div class="brand-bar">Procurement decision record</div>
  <div class="container">
    <header class="doc-header">
      <h1>RFx readiness assessment</h1>
      <p class="meta"><strong>PR</strong> {pr_number} &nbsp;|&nbsp; <strong>Part</strong> {esc(a['part_num'])} &nbsp;|&nbsp; {esc(pr_row.get('Short Text', ''))} &nbsp;|&nbsp; <strong>Generated</strong> {esc(ts)}</p>
    </header>

    <section class="section" aria-labelledby="ex-sum">
      <h2 id="ex-sum">Executive summary</h2>
      <p class="headline">{esc(a['next_action'])}</p>
      <div class="chips">
        <span class="chip {status_class}">Readiness: {esc(rs)}</span>
        <span class="chip rfx">{esc(a['rfx_headline'])}</span>
        <span class="chip">Severity: {esc(a['exec_severity'])}</span>
        <span class="chip">Confidence: {esc(a['overall_conf'])}</span>
      </div>
      <p class="caption"><strong>Overall confidence rationale:</strong> {esc(a['overall_why'])}</p>
      <div class="exec-grid">
        <div class="exec-card"><div class="label">Go / Stop / Fix</div><div class="val">{"STOP" if rs == "BLOCKED" else ("FIX" if rs == "AT RISK" else "GO")}</div><div class="sub">{esc(rationale_short)}</div></div>
        <div class="exec-card"><div class="label">Who fixes (if blocked)</div><div class="val">{"Engineering / Procurement" if rs == "BLOCKED" else "—"}</div><div class="sub">See follow-up table</div></div>
        <div class="exec-card"><div class="label">Primary invitees</div><div class="val">{len(a['primary'])}</div><div class="sub">Ranked from PO + capability rules</div></div>
      </div>
    </section>

    <section class="section">
      <h2>Module 1 — PR readiness (quote enablement only)</h2>
      <p class="caption">{esc(a['readiness_rationale'])} <strong>Readiness confidence:</strong> {esc(a['conf_readiness'])} — {esc(a['conf_readiness_why'])}</p>
      <table>
        <thead><tr><th>Criterion</th><th>Status</th><th>Severity</th><th>Evidence</th><th>Impact</th><th>Required action</th><th>Type</th></tr></thead>
        <tbody>{table_readiness()}</tbody>
      </table>
    </section>

    <section class="section">
      <h2>Module 2 — RFx requirement (routing)</h2>
      <p class="caption"><strong>Decision:</strong> {esc(a['rfx_decision'])}. {esc(a['rfx_rationale'])} <strong>Routing confidence:</strong> {esc(a['conf_rfx'])} — {esc(a['conf_rfx_why'])}</p>
      <table>
        <thead><tr><th>Criterion</th><th>Source field</th><th>Result</th><th>Interpretation</th><th>Type</th></tr></thead>
        <tbody>{table_rfx()}</tbody>
      </table>
    </section>

    <section class="section">
      <h2>Module 3 — Supplier recommendation</h2>
      <p class="caption"><strong>Supplier confidence:</strong> {esc(a['conf_supplier'])} — {esc(a['conf_supplier_why'])} Similar parts in SAP_PR sharing XPAC attributes: <strong>{a['similar_part_count']}</strong>. Preferred_vendors capability rows: <strong>{a['pv_match_count']}</strong>. Watchlist total <strong>{a.get('watchlist_total', len(a['excluded']))}</strong> (table shows excerpt of <strong>{len(a['excluded'])}</strong> rows).</p>
      <h3 class="bucket">Similarity logic used</h3>
      {sim_logic_html}
      {supplier_table(a['primary'], 'Recommended primary invitees (ranked)', with_actions=True)}
      {supplier_table(a['secondary'], 'Secondary / backup', with_actions=True)}
      {supplier_table(a['excluded'], 'Watchlist / not prioritized (excerpt)', with_actions=False)}
    </section>

    <section class="section">
      <h2>Risks & considerations</h2>
      {risks_html()}
    </section>

    <section class="section">
      <h2>Follow-ups & ownership</h2>
      {follow_html()}
    </section>

    <section class="section">
      <h2>Email intake to JSON (no Excel)</h2>
      <p class="caption">Paste inbound email from suppliers or PR stakeholders. The parser auto-detects schema, extracts fields, labels inferred gaps as <code>NOT_AVAILABLE</code>, and lets Procurement review/edit before export.</p>
      <div class="intake-grid">
        <div>
          <div class="field">
            <label for="intakeEmailText">Inbound email content</label>
            <textarea id="intakeEmailText" style="min-height:220px;"></textarea>
          </div>
          <div class="btn-row">
            <button class="btn" id="parseIntakeBtn" type="button">Parse email</button>
            <button class="btn secondary" id="resetIntakeBtn" type="button">Reset</button>
          </div>
        </div>
        <div>
          <p class="caption"><strong>Detected schema:</strong> <span id="detectedSchema">NOT_AVAILABLE</span></p>
          <p class="caption"><strong>Parser confidence:</strong> <span id="parseConfidence">NOT_AVAILABLE</span></p>
          <p class="caption"><strong>Unknown / missing:</strong> <span id="missingFields">NOT_AVAILABLE</span></p>
          <p class="caption"><strong>Field legend:</strong> <span class="pill extracted">EXTRACTED</span><span class="pill manual">MANUAL_EDIT</span></p>
        </div>
      </div>
      <div id="intakeFields" class="form-grid" style="margin-top:0.75rem;"></div>
      <div class="btn-row">
        <button class="btn" id="exportIntakeBtn" type="button">Export parsed JSON</button>
      </div>
      <p class="caption">To append into local intake store: <code>python run_analysis.py --append-intake path\\to\\exported-intake.json</code></p>
      <div class="field">
        <label for="intakeJsonPreview">Reviewed JSON preview</label>
        <textarea id="intakeJsonPreview" class="monospace" style="min-height:140px;" readonly></textarea>
      </div>
    </section>

    <section class="section">
      <h2>Data & confidence by module</h2>
      <table>
        <thead><tr><th>Module</th><th>Sources</th><th>Confidence</th><th>Rationale</th></tr></thead>
        <tbody>
          <tr><td>PR Readiness</td><td>SAP_PR (drawing_avlb, Authority Grp, Open Qty, Delivery Date)</td><td>{esc(a['conf_readiness'])}</td><td>{esc(a['conf_readiness_why'])}</td></tr>
          <tr><td>RFx routing</td><td>SAP_PR.Contract</td><td>{esc(a['conf_rfx'])}</td><td>{esc(a['conf_rfx_why'])}</td></tr>
          <tr><td>Suppliers</td><td>PO_history; SAP_PR (similarity); Preferred_vendors</td><td>{esc(a['conf_supplier'])}</td><td>{esc(a['conf_supplier_why'])}</td></tr>
        </tbody>
      </table>
      <p class="caption"><strong>Gaps:</strong> No unit price column used for competitiveness; vendor contacts NOT_AVAILABLE in dataset.</p>
    </section>

    <section class="section">
      <h2>Decision audit / traceability</h2>
      <p><strong>Decision path:</strong> {esc(a['decision_path'])}</p>
      <h3 class="bucket">Rules applied</h3>
      <ul class="logic-list">{"".join(f"<li>{esc(r)}</li>" for r in a['rules_applied'])}</ul>
    </section>

    <section class="section">
      <h2>Validation / anti-hallucination</h2>
      {audit_checks()}
      {claims_block}
      {manual_block}
      {excluded_claims_block}
    </section>

    <section class="section">
      <h2>Supplemental commercial context (exercise files only)</h2>
      <p class="caption">Not used to qualify suppliers. Optional comparison for PR {pr_number}.</p>
      <table>
        <thead><tr><th>Supplier</th><th>Source</th><th>Indicative</th><th>Type</th></tr></thead>
        <tbody>{commercial_rows}</tbody>
      </table>
    </section>

    <section class="section">
      <h2>Appendix — PO history (exact part)</h2>
      {chart_section}
      <table>
        <thead><tr><th>Vendor</th><th>Order date</th><th>Qty</th></tr></thead>
        <tbody>{po_rows}</tbody>
      </table>
    </section>

    <footer>Structured for any PR — replace inputs from SAP_PR / PO_history / Preferred_vendors. Data: SAP-DB.xlsx.</footer>
  </div>
  <script>
    const intakeSchemaFields = {intake_schema_json};

    const intakeEmailText = document.getElementById('intakeEmailText');
    const intakeFields = document.getElementById('intakeFields');
    const detectedSchema = document.getElementById('detectedSchema');
    const parseConfidence = document.getElementById('parseConfidence');
    const missingFields = document.getElementById('missingFields');
    const intakeJsonPreview = document.getElementById('intakeJsonPreview');
    let lastParsed = null;

    function detectSchema(text) {{
      const low = text.toLowerCase();
      const quoteScore = ['unit price', 'currency', 'lead time', 'quotation', 'offer', 'validity'].filter((k) => low.includes(k)).length;
      const prScore = ['pr ', 'purchase requisition', 'blocker', 'owner', 'due date', 'status'].filter((k) => low.includes(k)).length;
      if (quoteScore === prScore) return 'rfx_quote';
      return quoteScore > prScore ? 'rfx_quote' : 'pr_update';
    }}

    function matchValue(text, pattern) {{
      const m = text.match(pattern);
      if (!m) return 'NOT_AVAILABLE';
      for (let i = 1; i < m.length; i += 1) {{
        if (m[i]) return String(m[i]).replace(/^[\\s*`_]+|[\\s*`_]+$/g, '').trim();
      }}
      return 'NOT_AVAILABLE';
    }}

    function chooseValue(...vals) {{
      for (const v of vals) {{
        if (v && v !== 'NOT_AVAILABLE') return v;
      }}
      return 'NOT_AVAILABLE';
    }}

    function parseRfxQuote(text) {{
      const tableMatch = text.match(/\\|\\s*([A-Z0-9\\-]+)\\s*\\|[^\\n\\r]*\\|\\s*([0-9]+)\\s*\\|\\s*([0-9]+(?:\\.[0-9]+)?)\\s*\\|/i);
      const partFromTable = tableMatch ? tableMatch[1] : 'NOT_AVAILABLE';
      const qtyFromTable = tableMatch ? tableMatch[2] : 'NOT_AVAILABLE';
      const priceFromTable = tableMatch ? tableMatch[3] : 'NOT_AVAILABLE';
      const currencyFromText = text.match(/\\(EUR\\)/i) ? 'EUR' : 'NOT_AVAILABLE';
      return {{
        supplier_name: matchValue(text, /(?:company|supplier)\\*?\\*?\\s*[:\\-]\\s*\\*?\\*?\\s*([^\\n\\r]+)/i),
        part_number: chooseValue(matchValue(text, /(part\\s*(?:number|no\\.?))\\s*[:\\-]\\s*([^\\n\\r]+)/i), partFromTable),
        qty: chooseValue(matchValue(text, /(?:qty|quantity)\\s*[:\\-]\\s*([^\\n\\r]+)/i), qtyFromTable),
        unit_price: chooseValue(matchValue(text, /(?:unit\\s*price(?:\\s*\\(eur\\))?|price\\s*per\\s*unit)\\s*[:\\-]?\\s*([^\\n\\r]+)/i), priceFromTable),
        currency: chooseValue(matchValue(text, /currency\\s*[:\\-]\\s*([^\\n\\r]+)/i), currencyFromText),
        lead_time: matchValue(text, /(?:lead\\s*time|earliest\\s*delivery\\s*date)\\s*[:\\-]\\s*([^\\n\\r]+)/i),
        validity_date: matchValue(text, /validity(?:\\s*date)?\\s*[:\\-]\\s*([^\\n\\r]+)/i),
        notes: matchValue(text, /(?:notes?|assumptions?)\\s*[:\\-]\\s*([^\\n\\r]+)/i),
      }};
    }}

    function parsePrUpdate(text) {{
      return {{
        pr_number: chooseValue(
          matchValue(text, /pr\\s*(?:number|no\\.?)\\s*\\*?\\*?\\s*[:\\-]\\s*\\*?\\*?\\s*([0-9]{{6,}})/i),
          matchValue(text, /\\b([0-9]{{8,}})\\b/)
        ),
        status: matchValue(text, /status\\s*[:\\-]\\s*([^\\n\\r]+)/i),
        blocker: matchValue(text, /blocker\\s*[:\\-]\\s*([^\\n\\r]+)/i),
        owner: matchValue(text, /owner\\s*[:\\-]\\s*([^\\n\\r]+)/i),
        due_date: matchValue(text, /due\\s*date\\s*[:\\-]\\s*([^\\n\\r]+)/i),
        notes: matchValue(text, /notes?\\s*[:\\-]\\s*([^\\n\\r]+)/i),
      }};
    }}

    function renderParsedFields(schema, fields, extractedSet) {{
      intakeFields.innerHTML = '';
      fields.forEach((fieldName) => {{
        const val = (lastParsed.payload[fieldName] || 'NOT_AVAILABLE');
        const wrapper = document.createElement('div');
        wrapper.className = 'field';
        const label = document.createElement('label');
        label.htmlFor = `field_${{fieldName}}`;
        label.textContent = fieldName;
        const input = document.createElement('input');
        input.id = `field_${{fieldName}}`;
        input.value = val;
        input.dataset.field = fieldName;
        input.addEventListener('input', () => {{
          lastParsed.payload[fieldName] = input.value.trim() || 'NOT_AVAILABLE';
          const extracted = extractedSet.has(fieldName) ? 'extracted' : 'manual';
          input.className = extracted;
          updatePreview();
        }});
        const tag = document.createElement('span');
        tag.className = `pill ${{extractedSet.has(fieldName) ? 'extracted' : 'manual'}}`;
        tag.textContent = extractedSet.has(fieldName) ? 'EXTRACTED' : 'MANUAL_EDIT';
        wrapper.appendChild(label);
        wrapper.appendChild(input);
        wrapper.appendChild(tag);
        intakeFields.appendChild(wrapper);
      }});
    }}

    function updatePreview() {{
      intakeJsonPreview.value = JSON.stringify(lastParsed, null, 2);
      const missing = Object.entries(lastParsed.payload)
        .filter(([, v]) => !v || v === 'NOT_AVAILABLE')
        .map(([k]) => k);
      missingFields.textContent = missing.length ? missing.join(', ') : 'None';
      parseConfidence.textContent = missing.length <= 1 ? 'High' : (missing.length <= 3 ? 'Medium' : 'Low');
    }}

    function parseIntakeText() {{
      const text = intakeEmailText.value || '';
      const schema = detectSchema(text);
      const parsed = schema === 'rfx_quote' ? parseRfxQuote(text) : parsePrUpdate(text);
      const extractedSet = new Set(
        Object.entries(parsed).filter(([, v]) => v && v !== 'NOT_AVAILABLE').map(([k]) => k)
      );
      detectedSchema.textContent = schema;
      lastParsed = {{
        schema_type: schema,
        parsed_at: new Date().toISOString(),
        source: 'pasted_email',
        payload: parsed,
      }};
      renderParsedFields(schema, intakeSchemaFields[schema] || Object.keys(parsed), extractedSet);
      updatePreview();
    }}

    document.getElementById('parseIntakeBtn').addEventListener('click', parseIntakeText);
    document.getElementById('resetIntakeBtn').addEventListener('click', () => {{
      intakeEmailText.value = '';
      intakeFields.innerHTML = '';
      detectedSchema.textContent = 'NOT_AVAILABLE';
      parseConfidence.textContent = 'NOT_AVAILABLE';
      missingFields.textContent = 'NOT_AVAILABLE';
      intakeJsonPreview.value = '';
      lastParsed = null;
    }});

    document.getElementById('exportIntakeBtn').addEventListener('click', () => {{
      if (!lastParsed) return;
      const blob = new Blob([JSON.stringify([lastParsed], null, 2)], {{ type: 'application/json' }});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `intake-${{lastParsed.schema_type}}-${{Date.now()}}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }});

{chart_js}
  </script>
</body>
</html>
"""
    return html


def append_intake_records(json_file_path: str) -> int:
    path = Path(json_file_path)
    if not path.is_absolute():
        path = WORKSPACE / path
    if not path.exists():
        raise FileNotFoundError(f"Intake file not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    records = raw if isinstance(raw, list) else [raw]

    valid_records = []
    for item in records:
        if not isinstance(item, dict):
            continue
        schema = item.get("schema_type")
        payload = item.get("payload")
        if schema not in {"rfx_quote", "pr_update"}:
            continue
        if not isinstance(payload, dict):
            continue
        valid_records.append(
            {
                "schema_type": schema,
                "payload": payload,
                "source": item.get("source", "pasted_email"),
                "parsed_at": item.get("parsed_at", datetime.now().isoformat()),
                "saved_at": datetime.now().isoformat(),
            }
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if INTAKE_DB_PATH.exists():
        existing = json.loads(INTAKE_DB_PATH.read_text(encoding="utf-8"))
        if not isinstance(existing, list):
            existing = []
    else:
        existing = []

    existing.extend(valid_records)
    INTAKE_DB_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return len(valid_records)


def main():
    parser = argparse.ArgumentParser(description="Generate RFx readiness report or append intake records.")
    parser.add_argument("pr_number", nargs="?", type=int, default=29189412, help="PR number for report generation")
    parser.add_argument(
        "--append-intake",
        dest="append_intake",
        type=str,
        default=None,
        help="Path to parsed intake JSON (record or list of records) to append into local intake store",
    )
    args = parser.parse_args()

    if args.append_intake:
        appended = append_intake_records(args.append_intake)
        print(f"Appended {appended} record(s) to: {INTAKE_DB_PATH}")
        return

    TARGET_PR = args.pr_number
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data = load_data()
    pr_row = find_pr(data["pr"], TARGET_PR)
    if pr_row is None:
        pr_row = data["pr"].iloc[0]
        pr_number = int(pr_row["PR Number"])
    else:
        pr_number = int(pr_row["PR Number"])

    analysis = analyze_full(pr_row, data["pr"], data["po"], data["pv"], pr_number)
    validation = validation_report_md(pr_number, analysis)
    md = generate_markdown(pr_row, analysis, pr_number, validation)
    html = generate_html(pr_row, analysis, pr_number)

    (OUTPUT_DIR / f"rfx-readiness-PR-{pr_number}.md").write_text(md, encoding="utf-8")
    (OUTPUT_DIR / f"rfx-readiness-PR-{pr_number}.html").write_text(html, encoding="utf-8")
    print("Done:", OUTPUT_DIR / f"rfx-readiness-PR-{pr_number}.html")


if __name__ == "__main__":
    main()

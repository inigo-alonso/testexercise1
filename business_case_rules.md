# Exercise: Agentifying Procurement RFx Readiness

The goal is to simulate how a procurement team decides:
- Whether a Purchase Request (PR) is ready to be sent to suppliers
- Whether an RFx is required
- Which suppliers should be invited

Your solution should read procurement data, reason over it, and produce **clear, explainable sourcing decisions**.

This is not a data-engineering exercise. Focus on **reasoning, decision logic, and agent collaboration**.

---

## Business Context

RFx readiness assessment is typically:
- Mandatory before supplier outreach
- Highly manual and judgment-driven
- Repeated at scale, under time pressure

Agentifying this process allows:
- Faster readiness checks
- Consistent application of sourcing rules
- Scalable supplier identification
- Transparent and auditable decisions

---

## Data Provided

You will use a simplified procurement dataset provided as an Excel file:

**`SAP-DB.xlsx`**

The file contains multiple sheets representing different data required for the procurement process.

### 1. Purchase Requests / Raw Data
This sheet contains the following columns:
- PR Number
- Part Number
- Short Text
- TrackingNo
- Authority Grp
- Replinishment
- Plant
- Open Qty
- Delivery Date
- XPAC Manufacturing Process
- XPAC Material Type
- XPAC Material Form
- Contract
- drawing_avlb

This is your **starting point** for each sourcing decision.

---

### 2. Purchase Order History
This sheet represents **historical sourcing decisions**, including:
- Which suppliers previously delivered the same or similar parts
- Timing and frequency of purchases
- Supplier recurrence over time

This sheet contains the following columns:
- DATA_TS
- PO_HEADER
- PO_ITEM
- PO_SCHEDULE_LINE
- ACCOUNT_ASSIGNMENT_CATEGORY
- PURCHASING_DOCUMENT_CATEGORY
- PROGRAM
- DOCUMENT_TYPE_GROUP
- DOCUMENT_TYPE
- DIRECT_SHIP_PO
- ORDER_PRIORITY
- PLANT
- VENDOR
- SITE_CODE
- PART_NUMBER
- PART_NOUN
- QTY_ORDERED
- QTY_RECEIVED
- QTY_REMAINING
- BASE_UNIT
- ORDER_DATE
- STATISTICAL_DELIVERY_DATE
- EXPECTED_DELIVERY_DATE
- ESTIMATED_ON_TIME_RECEIPT_DATE
- PO_ITEM_MAX_RECEIPT_DATE
- STOCK_INDICATOR
- DEMAND_CLASS
- PSV
- PRODUCTION_IND
- MAJOR_MODELS
- EARLY_DELIVERY_DATE_MIN
- EARLY_DELIVERY_DATE_MAX
- QTY_EARLY
- QTY_EARLY_CONTESTED
- QTY_EARLY_UNCONTESTED
- LATE_DELIVERY_DATE_MIN
- LATE_DELIVERY_DATE_MAX
- QTY_LATE
- QTY_LATE_CONTESTED
- QTY_LATE_UNCONTESTED

This data is useful to:
- Identify incumbent suppliers
- Look back 2–3 years for similar parts
- Establish sourcing precedence

---

### 3. Preferred Vendors / Supplier Capabilities
This sheet describes:
- Supplier capabilities by material type
- Manufacturing process families
- Part family or commodity group coverage

This sheet contains the following columns:
- Vendor_Name
- Commodity_ZZ_COMMODITY
- SAPCommodityCodeDesc_ZZ_COMMODITY_DESC
- ManufacturingProcessFamTreeGroup_ZZ_MFG_PRO_GROUP
- ManufacturingProcessFamTreeGroupDesc_ZZ_MFG_PRO_GROUP_DESC
- MaterialTypeFamTreeGroup_ZZ_MAT_TYPE_GROUP
- MaterialTypeFamTreeGroupDesc_ZZ_MAT_TYPE_GROUP_DESC
- MaterialFormFamTreeGroup_ZZ_MAT_FORM_GROUP
- MaterialFormFamTreeGroupDesc_ZZ_MAT_FORM_GROUP_DESC
- LengthDiameter_ZZ_SIZE_TYPE
- SizeTypeDesc
- Size_ZZ_SIZE_GROUP
- SizeGroupDesc
- Assetclassmarkedfordeletion_LOEKZ
- AccountNumberofVendororCreditor_LIFNR
- CountryKey_LAND1
- StartDate_ZZ_START_DATE
- EndDate_ZZ_END_DATE
- SuspendDate_ZZ_SUSPEND_DATE

This data is useful to:
- Capability-based supplier expansion
- Identification of suppliers even if the exact part was never purchased before

---

## Core Decision Logic (What Your Agents Should Reason About)

### Step 1: RFx Readiness Check
For each PR, assess whether it is ready for sourcing.

Considerations are:
- Is required supporting material available (i.e., drawing available)? If not, reach out to engineering to request drawing
- Is the Authority Grp = AUTO?
- Is there no contract available (If a contract is available, direct procurement -> no RFx required)

If not ready, your workflow should clearly explain **why** and **what is missing**.

---

### Step 2: Supplier Identification (Key Reasoning Challenge)

Suppliers can be identified using a **hierarchy of logic**. For example:

1. **Exact-part suppliers**  
   Suppliers that previously delivered the same part number.

2. **Similar-part suppliers**  
   Suppliers that delivered parts with similar:
   - Material type
   - Manufacturing process
   - Material form

3. **Capability-based suppliers**  
   Suppliers that have the required capabilities, even if no historical purchase exists.

---

## Defining “Similar Parts” (Important)

There is no single correct definition of similarity.

Your agents may define similarity using one or more of:
- Manufacturing process family
- Material type (e.g., steel, aluminum, rubber)
- Material form (e.g., sheet, bar, casting)
- Part family or commodity group
- Short text or description similarity

The important part is:
- Make your logic **explicit**
- Be consistent
- Be able to explain *why* a supplier was selected

---

## Expected Output

Your agentic workflow should produce, for each PR:
- RFx readiness status (Ready / Not Ready)
- RFx requirement decision
- Recommended suppliers (with reasoning)
- Any required follow-ups or missing information

Outputs can be:
- Structured text
- Tables
- Decision summaries
- Agent explanations

---


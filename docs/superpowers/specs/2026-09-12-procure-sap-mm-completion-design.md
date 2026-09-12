# UNG-PROCURE SAP-MM Completion Design

## Goal
Extend the existing UNG-PROCURE service so it covers the missing procurement chain expected from the SAP-MM mapping without duplicating responsibilities already owned by UNG-VECTOR or UNG-MIDAS.

## Current Baseline
UNG-PROCURE already provides requisitions, vendors, bids, awards, purchase orders, JANUS authorization, NEXUS messaging, a MIDAS handoff, a VECTOR receiving handoff, and an acceptance probe.

## Ownership Boundaries
- UNG-PROCURE owns procurement workflow state: supplier purchasing profile, requisitions, RFQs/bids, purchase orders, PO lines, delivery schedules, matching state, exceptions, and procurement approvals.
- UNG-VECTOR owns physical warehouse receipt, stock, lot/serial handling, and inventory truth.
- UNG-MIDAS owns supplier invoice accounting, tax, payment status, AP/GL posting, and financial truth.
- NEXUS remains the transport/event backbone between systems.
- JANUS remains the authorization authority.

## Recommended Architecture
Use an additive modular extension to the current FastAPI service. Preserve all existing routes and event contracts. Add focused modules instead of expanding app.py further.

### Module 1: Supplier Purchasing Profile
Add supplier procurement metadata alongside the existing vendor record:
- supplier code
- legal/tax identifiers
- payment terms
- currency
- lead time
- approved/blocked status
- bank/payment reference metadata without storing banking secrets
- purchasing contact fields
- risk and compliance status

This is procurement-facing supplier data only. Canonical enterprise identity may later federate with MDM.

### Module 2: Requisition and RFQ Detail
Extend requisitions from header-only records into line-based documents.

Requisition lines:
- SKU/material reference
- description
- quantity
- UOM
- target delivery date
- requested location
- estimated unit price
- cost center / account assignment reference

RFQ support:
- RFQ header and invited suppliers
- requested lines
- quote validity
- supplier responses
- line-level quoted price and lead time
- award comparison

Existing bid functionality remains compatible and can be bridged to RFQ responses.

### Module 3: Purchase Order Detail and Scheduling
Add PO lines and delivery schedules while preserving the current procure_orders header.

PO lines:
- line number
- SKU/material reference
- description
- ordered quantity
- UOM
- unit price
- currency
- tax code/reference
- receiving location
- status

Delivery schedules:
- PO line
- scheduled quantity
- due date
- received quantity
- status

A PO award creates the PO header plus its approved lines and schedules.

### Module 4: Goods Receipt Matching
PROCURE does not become the warehouse system. VECTOR remains the source of truth for actual receipt.

PROCURE stores receipt references received from VECTOR through NEXUS:
- receipt id
- PO id
- PO line id
- SKU
- quantity received
- receipt timestamp
- location
- lot/serial references when supplied by VECTOR

PROCURE updates cumulative received quantities and PO line/schedule status from those events.

### Module 5: Supplier Invoice Matching
MIDAS remains the source of truth for invoice accounting. PROCURE stores only the invoice matching projection needed for procurement controls:
- invoice id/reference
- supplier
- PO id / PO line
- invoiced quantity
- unit price
- tax amount
- total amount
- currency
- invoice status

MIDAS sends invoice-created/updated events through NEXUS.

### Module 6: Three-Way Match
Implement PO ↔ goods receipt ↔ supplier invoice matching at PO-line level.

Checks:
- supplier must match PO supplier
- SKU/material must match the PO line
- invoiced quantity must not exceed permitted received/ordered quantity unless tolerance allows it
- invoice unit price must be within configured tolerance of PO unit price
- currency must match unless explicitly converted upstream
- duplicate invoice references are rejected

Outcomes:
- matched
- matched_with_tolerance
- blocked_quantity_variance
- blocked_price_variance
- blocked_supplier_mismatch
- blocked_duplicate_invoice
- pending_receipt

A passed match emits a NEXUS event to MIDAS authorizing accounting/payment progression. A blocked match emits a procurement exception event and does not authorize payment progression.

## Tolerances
Store explicit procurement matching tolerance configuration:
- quantity percentage tolerance
- price percentage tolerance
- absolute price tolerance
- over-delivery allowance
- under-delivery close threshold

Defaults should be conservative and configurable. No hidden tolerance behavior.

## Data Integrity
- Use NUMERIC/Decimal for money and unit prices, not floating point.
- Use database uniqueness constraints for document numbers and duplicate invoice protection.
- Use foreign keys where practical inside PROCURE-owned tables.
- Use transactional updates for award, receipt projection, invoice projection, and three-way-match state transitions.
- Matching decisions are append-only in a procurement audit/match history table; current status may be projected separately.

## Event Contracts
Outbound through NEXUS:
- PROCURE.PURCHASE_ORDER.ISSUED
- PROCURE.PURCHASE_ORDER.RECEIVING_EXPECTED
- PROCURE.MATCH.PASSED
- PROCURE.MATCH.BLOCKED

Inbound through the existing service integration layer:
- VECTOR.GOODS_RECEIPT.POSTED
- VECTOR.GOODS_RECEIPT.REVERSED
- MIDAS.SUPPLIER_INVOICE.POSTED
- MIDAS.SUPPLIER_INVOICE.CANCELLED
- MIDAS.PAYMENT.STATUS

Existing award events remain supported for backward compatibility.

## Authorization
Reuse JANUS and add procurement-scoped permissions only where required:
- procure.suppliers.read/write
- procure.rfq.read/write
- procure.orders.read/write
- procure.receipts.read
- procure.invoices.read
- procure.match.read/write
- procure.config.read/write

ung.admin remains the administrative override.

## Error Handling
- 400 for invalid document state/transitions
- 404 for missing supplier/requisition/PO/line/invoice references
- 409 for duplicate documents, overmatch conflicts, or invalid concurrent state
- 422 for malformed business values
- 503 when JANUS or required integration dependencies are unavailable

Inbound NEXUS processing must be idempotent using source event/message identifiers.

## Testing
Use TDD for each module. Required coverage includes:
- supplier creation/validation
- requisition line creation
- RFQ quote comparison
- PO line and schedule creation
- partial and full receipts
- receipt reversal
- invoice projection
- exact 3-way match
- quantity variance block
- price variance block
- tolerance pass
- duplicate invoice rejection
- repeated inbound event idempotency
- unauthorized access
- transaction rollback on failure

Deployment acceptance must verify startup, schema initialization, /health 200, /ready database connectivity, route mounting, no 5xx startup errors, and at least one controlled end-to-end PO → receipt → invoice match flow where credentials permit.

## Delivery Sequence
1. Supplier purchasing profile + line-based requisitions/RFQs.
2. PO lines + delivery schedules + VECTOR receipt projection.
3. MIDAS invoice projection + three-way match + tolerance/exceptions.
4. Integration acceptance and production validation.

## Non-Goals
- Do not duplicate VECTOR inventory quantities or warehouse stock ledger.
- Do not make PROCURE an accounting ledger.
- Do not implement MIDAS payment execution inside PROCURE.
- Do not replace JANUS or NEXUS.
- Do not redesign unrelated existing procurement routes.

## Success Criteria
UNG-PROCURE can trace one purchasing transaction from requisition through supplier selection, PO issuance, scheduled delivery, VECTOR goods receipt, MIDAS invoice, three-way match, and payment authorization/blocking while preserving clear source-of-truth ownership across PROCURE, VECTOR, and MIDAS.

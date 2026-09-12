# UNG-PROCURE SAP-MM Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend UNG-PROCURE from its current requisition/vendor/bid/award flow into a complete procurement chain with supplier purchasing profiles, line-based requisitions/RFQs, PO lines and schedules, VECTOR receipt projection, MIDAS invoice projection, and line-level three-way matching.

**Architecture:** Preserve the current FastAPI service, current routes, JANUS authorization, and NEXUS transport. Add focused modules for purchasing master data/documents and matching instead of expanding `app.py`; keep VECTOR as physical inventory truth and MIDAS as accounting/payment truth while PROCURE owns procurement workflow and match decisions.

**Tech Stack:** Python 3, FastAPI, Pydantic, PostgreSQL via psycopg, JANUS bearer authorization, NEXUS events, pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-procure-sap-mm-completion-design.md`

## Global Constraints

- Preserve all existing UNG-PROCURE routes and existing award/integration event contracts.
- UNG-VECTOR remains authoritative for physical receipts, inventory, lot/serial state, and warehouse truth.
- UNG-MIDAS remains authoritative for invoice accounting, tax, AP/GL, payment status, and financial truth.
- Use `NUMERIC`/`Decimal` for money and unit prices; do not introduce new floating-point monetary fields.
- NEXUS remains the inter-system event backbone and JANUS remains the authorization authority.
- Inbound integration processing must be idempotent by source event/message id.
- Three-way match decisions are append-only in history; current status may be projected separately.
- Do not implement inventory stock ledgers, AP ledgers, payment execution, or unrelated refactors in PROCURE.

---

## File Structure

- Create `supplier_profiles.py` — supplier purchasing profile schema, validation, routes, and DB initialization.
- Create `purchasing_documents.py` — requisition lines, RFQs, quotes, PO lines, and delivery schedules.
- Create `procure_matching.py` — receipt projection, invoice projection, tolerances, three-way matching, idempotency, match history, and match events.
- Modify `app.py` — initialize the new modules, bump version, and advertise new capabilities.
- Modify `entrypoint.py` — mount the three new routers.
- Create `tests/test_supplier_profiles.py` — supplier profile validation and duplicate protection.
- Create `tests/test_purchasing_documents.py` — requisition/RFQ/PO/schedule behavior.
- Create `tests/test_procure_matching.py` — receipt, invoice, idempotency, tolerance, match/block, and duplicate invoice behavior.
- Create `tests/test_procure_integration_flow.py` — controlled PO → receipt → invoice → match flow using local test doubles for external event delivery.

---

### Task 1: Supplier Purchasing Profiles

**Files:**
- Create: `supplier_profiles.py`
- Create: `tests/test_supplier_profiles.py`
- Modify: `app.py`
- Modify: `entrypoint.py`

**Interfaces:**
- Consumes: existing `conn()` and `auth(permission, authorization)` from `app.py`.
- Produces: `init_supplier_profiles(conn)`, `router`, `SupplierProfileIn`, `GET /v1/suppliers`, `POST /v1/suppliers`, `PATCH /v1/suppliers/{supplier_code}`.

- [ ] **Step 1: Write the failing supplier profile tests**

```python
from supplier_profiles import validate_supplier_profile


def test_supplier_profile_rejects_negative_lead_time():
    try:
        validate_supplier_profile('SUP-001', 'USD', -1, 'approved')
        assert False, 'expected ValueError'
    except ValueError as e:
        assert str(e) == 'lead_time_days_cannot_be_negative'


def test_supplier_profile_rejects_invalid_status():
    try:
        validate_supplier_profile('SUP-001', 'USD', 5, 'unknown')
        assert False, 'expected ValueError'
    except ValueError as e:
        assert str(e) == 'invalid_supplier_status'
```

- [ ] **Step 2: Run the tests and verify RED**

Run:
```bash
pytest tests/test_supplier_profiles.py -v
```
Expected: FAIL because `supplier_profiles` does not exist.

- [ ] **Step 3: Implement supplier profile validation and schema**

Create `supplier_profiles.py` with:

```python
from datetime import datetime, timezone
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix='/v1/suppliers', tags=['suppliers'])
VALID_STATUSES = {'approved', 'blocked', 'pending_review'}


def now():
    return datetime.now(timezone.utc)


def validate_supplier_profile(supplier_code: str, currency: str, lead_time_days: int, status: str):
    if not supplier_code.strip():
        raise ValueError('supplier_code_required')
    if len(currency.strip()) != 3:
        raise ValueError('currency_must_be_iso3')
    if lead_time_days < 0:
        raise ValueError('lead_time_days_cannot_be_negative')
    if status not in VALID_STATUSES:
        raise ValueError('invalid_supplier_status')
    return True


class SupplierProfileIn(BaseModel):
    vendor_id: str
    supplier_code: str
    legal_name: str
    tax_id: str | None = None
    payment_terms: str = 'NET30'
    currency: str = 'USD'
    lead_time_days: int = 0
    status: str = 'approved'
    purchasing_email: str | None = None
    purchasing_phone: str | None = None
    compliance_status: str = 'clear'
    payment_reference: str | None = None


def init_supplier_profiles(conn):
    with conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS procure_supplier_profiles(
          vendor_id UUID PRIMARY KEY,
          supplier_code TEXT UNIQUE NOT NULL,
          legal_name TEXT NOT NULL,
          tax_id TEXT NULL,
          payment_terms TEXT NOT NULL,
          currency TEXT NOT NULL,
          lead_time_days INTEGER NOT NULL DEFAULT 0 CHECK(lead_time_days>=0),
          status TEXT NOT NULL,
          purchasing_email TEXT NULL,
          purchasing_phone TEXT NULL,
          compliance_status TEXT NOT NULL,
          payment_reference TEXT NULL,
          created_at TIMESTAMPTZ NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL)''')
```

Use `procure.suppliers.read/write` in the router. `POST` must verify the referenced `procure_vendors.id` exists before insert and return `404 vendor_not_found` otherwise. Duplicate `supplier_code` returns `409 supplier_code_exists`.

- [ ] **Step 4: Run the supplier tests and verify GREEN**

Run:
```bash
pytest tests/test_supplier_profiles.py -v
```
Expected: PASS.

- [ ] **Step 5: Wire module initialization and router mounting**

In `app.py` import `init_supplier_profiles`, call it during startup after core tables are created, bump service version from `1.2.0` to `1.3.0`, and add `supplier-purchasing-profiles` to `/v1/system` capabilities.

In `entrypoint.py` add:

```python
from supplier_profiles import router as supplier_profiles_router
app.include_router(supplier_profiles_router)
```

- [ ] **Step 6: Run all Task 1 tests and compile check**

Run:
```bash
pytest tests/test_supplier_profiles.py -v
python -m py_compile app.py entrypoint.py supplier_profiles.py
```
Expected: all PASS; compile exits 0.

- [ ] **Step 7: Commit**

```bash
git add supplier_profiles.py tests/test_supplier_profiles.py app.py entrypoint.py
git commit -m "feat: add procurement supplier profiles"
```

---

### Task 2: Line-Based Requisitions, RFQs, PO Lines, and Delivery Schedules

**Files:**
- Create: `purchasing_documents.py`
- Create: `tests/test_purchasing_documents.py`
- Modify: `app.py`
- Modify: `entrypoint.py`

**Interfaces:**
- Consumes: existing `procure_requests`, `procure_vendors`, `procure_orders`, `conn()`, `auth()`.
- Produces: `init_purchasing_documents(conn)`, `router`, helper `validate_positive_decimal(value, field)`, requisition-line/RFQ/quote/PO-line/schedule routes.

- [ ] **Step 1: Write the failing document validation tests**

```python
from decimal import Decimal
from purchasing_documents import validate_positive_decimal


def test_rejects_zero_quantity():
    try:
        validate_positive_decimal(Decimal('0'), 'quantity')
        assert False
    except ValueError as e:
        assert str(e) == 'quantity_must_be_positive'


def test_accepts_positive_unit_price():
    assert validate_positive_decimal(Decimal('12.50'), 'unit_price') is True
```

- [ ] **Step 2: Run RED**

```bash
pytest tests/test_purchasing_documents.py -v
```
Expected: FAIL because `purchasing_documents` does not exist.

- [ ] **Step 3: Implement procurement document tables**

Create initialization for:

```sql
CREATE TABLE IF NOT EXISTS procure_request_lines(
  id UUID PRIMARY KEY,
  request_id UUID NOT NULL REFERENCES procure_requests(id) ON DELETE CASCADE,
  line_no INTEGER NOT NULL,
  sku TEXT NOT NULL,
  description TEXT NOT NULL,
  quantity NUMERIC(18,4) NOT NULL CHECK(quantity>0),
  uom TEXT NOT NULL,
  target_delivery_date DATE NULL,
  requested_location TEXT NULL,
  estimated_unit_price NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(estimated_unit_price>=0),
  cost_center_ref TEXT NULL,
  account_assignment_ref TEXT NULL,
  UNIQUE(request_id,line_no)
);

CREATE TABLE IF NOT EXISTS procure_rfqs(
  id UUID PRIMARY KEY,
  rfq_code TEXT UNIQUE NOT NULL,
  request_id UUID NOT NULL REFERENCES procure_requests(id),
  status TEXT NOT NULL,
  valid_until DATE NULL,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS procure_rfq_vendors(
  rfq_id UUID NOT NULL REFERENCES procure_rfqs(id) ON DELETE CASCADE,
  vendor_id UUID NOT NULL REFERENCES procure_vendors(id),
  PRIMARY KEY(rfq_id,vendor_id)
);

CREATE TABLE IF NOT EXISTS procure_rfq_quotes(
  id UUID PRIMARY KEY,
  rfq_id UUID NOT NULL REFERENCES procure_rfqs(id) ON DELETE CASCADE,
  vendor_id UUID NOT NULL REFERENCES procure_vendors(id),
  request_line_id UUID NOT NULL REFERENCES procure_request_lines(id),
  unit_price NUMERIC(18,4) NOT NULL CHECK(unit_price>=0),
  currency TEXT NOT NULL,
  lead_time_days INTEGER NOT NULL DEFAULT 0 CHECK(lead_time_days>=0),
  status TEXT NOT NULL,
  submitted_at TIMESTAMPTZ NOT NULL,
  UNIQUE(rfq_id,vendor_id,request_line_id)
);

CREATE TABLE IF NOT EXISTS procure_order_lines(
  id UUID PRIMARY KEY,
  order_id UUID NOT NULL REFERENCES procure_orders(id) ON DELETE CASCADE,
  line_no INTEGER NOT NULL,
  sku TEXT NOT NULL,
  description TEXT NOT NULL,
  ordered_quantity NUMERIC(18,4) NOT NULL CHECK(ordered_quantity>0),
  received_quantity NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(received_quantity>=0),
  uom TEXT NOT NULL,
  unit_price NUMERIC(18,4) NOT NULL CHECK(unit_price>=0),
  currency TEXT NOT NULL,
  tax_code TEXT NULL,
  receiving_location TEXT NULL,
  status TEXT NOT NULL,
  UNIQUE(order_id,line_no)
);

CREATE TABLE IF NOT EXISTS procure_delivery_schedules(
  id UUID PRIMARY KEY,
  order_line_id UUID NOT NULL REFERENCES procure_order_lines(id) ON DELETE CASCADE,
  schedule_no INTEGER NOT NULL,
  scheduled_quantity NUMERIC(18,4) NOT NULL CHECK(scheduled_quantity>0),
  received_quantity NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(received_quantity>=0),
  due_date DATE NOT NULL,
  status TEXT NOT NULL,
  UNIQUE(order_line_id,schedule_no)
);
```

- [ ] **Step 4: Implement document routes**

Required routes:

```text
POST /v1/requests/{request_id}/lines
GET  /v1/requests/{request_id}/lines
POST /v1/rfqs
POST /v1/rfqs/{rfq_id}/vendors/{vendor_id}
POST /v1/rfqs/{rfq_id}/quotes
GET  /v1/rfqs/{rfq_id}/comparison
POST /v1/orders/{order_id}/lines
GET  /v1/orders/{order_id}/lines
POST /v1/orders/{order_id}/lines/{line_id}/schedules
GET  /v1/orders/{order_id}/schedules
```

RFQ comparison must order by `unit_price ASC, lead_time_days ASC` per request line and include supplier/vendor ids. Routes use existing scopes where possible: `procure.requests.read/write`, `procure.bids.read/write`, `procure.orders.read/write`.

- [ ] **Step 5: Extend tests for duplicate lines and RFQ comparison**

Add tests proving:
- duplicate `(request_id,line_no)` is rejected;
- duplicate quote for `(rfq,vendor,line)` is rejected;
- comparison ranks lower price first, then lower lead time;
- schedule quantity must be positive.

- [ ] **Step 6: Run Task 2 tests and compile check**

```bash
pytest tests/test_purchasing_documents.py -v
python -m py_compile purchasing_documents.py app.py entrypoint.py
```
Expected: PASS and exit 0.

- [ ] **Step 7: Wire router and capability flags**

In `entrypoint.py`:

```python
from purchasing_documents import router as purchasing_documents_router
app.include_router(purchasing_documents_router)
```

In `/v1/system`, add:

```text
requisition-lines
rfqs
rfq-quotes
purchase-order-lines
delivery-schedules
```

- [ ] **Step 8: Commit**

```bash
git add purchasing_documents.py tests/test_purchasing_documents.py app.py entrypoint.py
git commit -m "feat: add procurement document lines and schedules"
```

---

### Task 3: VECTOR Goods-Receipt Projection and Idempotent Inbound Processing

**Files:**
- Create: `procure_matching.py`
- Create: `tests/test_procure_matching.py`
- Modify: `entrypoint.py`
- Modify: `app.py`

**Interfaces:**
- Consumes: `procure_order_lines`, `procure_delivery_schedules`, `conn()`, `auth()`, existing NEXUS service identity pattern.
- Produces: `init_procure_matching(conn)`, `router`, `apply_goods_receipt(conn, event_id, payload) -> dict`, `POST /v1/inbound/vector/goods-receipt`, `GET /v1/receipts`.

- [ ] **Step 1: Write failing receipt projection tests**

```python
from decimal import Decimal
from procure_matching import allocate_receipt


def test_allocate_receipt_caps_at_open_quantity():
    assert allocate_receipt(Decimal('10'), Decimal('6'), Decimal('7')) == Decimal('4')


def test_allocate_receipt_accepts_partial_quantity():
    assert allocate_receipt(Decimal('10'), Decimal('2'), Decimal('3')) == Decimal('3')
```

- [ ] **Step 2: Run RED**

```bash
pytest tests/test_procure_matching.py -v
```
Expected: FAIL because module/function does not exist.

- [ ] **Step 3: Implement receipt projection schema and helpers**

Create:

```sql
CREATE TABLE IF NOT EXISTS procure_inbound_events(
  source_system TEXT NOT NULL,
  source_event_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY(source_system,source_event_id)
);

CREATE TABLE IF NOT EXISTS procure_receipts(
  id UUID PRIMARY KEY,
  source_event_id TEXT UNIQUE NOT NULL,
  receipt_id TEXT NOT NULL,
  order_id UUID NOT NULL REFERENCES procure_orders(id),
  order_line_id UUID NOT NULL REFERENCES procure_order_lines(id),
  sku TEXT NOT NULL,
  quantity NUMERIC(18,4) NOT NULL CHECK(quantity>0),
  location_code TEXT NULL,
  lot_code TEXT NULL,
  serial_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,
  status TEXT NOT NULL,
  received_at TIMESTAMPTZ NOT NULL
);
```

Implement:

```python
def allocate_receipt(ordered_quantity, already_received, incoming_quantity):
    open_quantity = ordered_quantity - already_received
    return min(open_quantity, incoming_quantity)
```

`apply_goods_receipt` must:
1. reject duplicate source event ids by returning the existing projection without double-updating quantities;
2. verify PO line and SKU;
3. insert receipt projection;
4. atomically increment `procure_order_lines.received_quantity`;
5. update line status to `partially_received` or `received`;
6. update schedules oldest-due-first without exceeding scheduled quantity;
7. record the source event id in `procure_inbound_events` in the same transaction.

- [ ] **Step 4: Add reversal behavior tests**

Add tests for `VECTOR.GOODS_RECEIPT.REVERSED` proving quantities are reduced but never below zero and the same reversal event cannot apply twice.

- [ ] **Step 5: Implement inbound routes**

```text
POST /v1/inbound/vector/goods-receipt
POST /v1/inbound/vector/goods-receipt-reversed
GET  /v1/receipts
```

Require the existing PROCURE service bearer identity for inbound system calls; do not accept anonymous integration writes.

- [ ] **Step 6: Run Task 3 tests and compile check**

```bash
pytest tests/test_procure_matching.py -v -k receipt
python -m py_compile procure_matching.py entrypoint.py app.py
```
Expected: PASS and exit 0.

- [ ] **Step 7: Wire router and capabilities**

In `entrypoint.py`:

```python
from procure_matching import router as procure_matching_router
app.include_router(procure_matching_router)
```

Add `goods-receipt-projection` and `idempotent-inbound-events` to `/v1/system`.

- [ ] **Step 8: Commit**

```bash
git add procure_matching.py tests/test_procure_matching.py app.py entrypoint.py
git commit -m "feat: project vector goods receipts into procure"
```

---

### Task 4: MIDAS Invoice Projection, Tolerances, and Three-Way Match

**Files:**
- Modify: `procure_matching.py`
- Modify: `tests/test_procure_matching.py`
- Modify: `app.py`

**Interfaces:**
- Consumes: Task 3 `apply_goods_receipt`, `procure_receipts`, `procure_order_lines`, existing `emit(target, message_type, payload)` from `app.py`.
- Produces: `evaluate_three_way_match(po_line, receipt_qty, invoice, tolerance) -> dict`, invoice projection routes, tolerance routes, append-only match history, NEXUS pass/block events.

- [ ] **Step 1: Write failing exact-match and variance tests**

```python
from decimal import Decimal
from procure_matching import evaluate_three_way_match


def test_exact_three_way_match_passes():
    result = evaluate_three_way_match(
        {'vendor_id':'V1','sku':'SKU1','ordered_quantity':Decimal('10'),'unit_price':Decimal('5.00'),'currency':'USD'},
        Decimal('10'),
        {'vendor_id':'V1','sku':'SKU1','quantity':Decimal('10'),'unit_price':Decimal('5.00'),'currency':'USD'},
        {'quantity_pct':Decimal('0'),'price_pct':Decimal('0'),'absolute_price':Decimal('0')},
    )
    assert result['status'] == 'matched'


def test_price_variance_blocks():
    result = evaluate_three_way_match(
        {'vendor_id':'V1','sku':'SKU1','ordered_quantity':Decimal('10'),'unit_price':Decimal('5.00'),'currency':'USD'},
        Decimal('10'),
        {'vendor_id':'V1','sku':'SKU1','quantity':Decimal('10'),'unit_price':Decimal('6.00'),'currency':'USD'},
        {'quantity_pct':Decimal('0'),'price_pct':Decimal('1'),'absolute_price':Decimal('0')},
    )
    assert result['status'] == 'blocked_price_variance'
```

- [ ] **Step 2: Run RED**

```bash
pytest tests/test_procure_matching.py -v -k 'three_way or variance'
```
Expected: FAIL because `evaluate_three_way_match` is missing.

- [ ] **Step 3: Add invoice, tolerance, current-match, and history tables**

```sql
CREATE TABLE IF NOT EXISTS procure_invoice_projection(
  id UUID PRIMARY KEY,
  source_event_id TEXT UNIQUE NOT NULL,
  invoice_ref TEXT UNIQUE NOT NULL,
  vendor_id UUID NOT NULL REFERENCES procure_vendors(id),
  order_id UUID NOT NULL REFERENCES procure_orders(id),
  order_line_id UUID NOT NULL REFERENCES procure_order_lines(id),
  sku TEXT NOT NULL,
  quantity NUMERIC(18,4) NOT NULL CHECK(quantity>0),
  unit_price NUMERIC(18,4) NOT NULL CHECK(unit_price>=0),
  tax_amount NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(tax_amount>=0),
  total_amount NUMERIC(18,4) NOT NULL CHECK(total_amount>=0),
  currency TEXT NOT NULL,
  status TEXT NOT NULL,
  posted_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS procure_match_tolerances(
  id SMALLINT PRIMARY KEY DEFAULT 1 CHECK(id=1),
  quantity_pct NUMERIC(9,4) NOT NULL DEFAULT 0,
  price_pct NUMERIC(9,4) NOT NULL DEFAULT 0,
  absolute_price NUMERIC(18,4) NOT NULL DEFAULT 0,
  over_delivery_pct NUMERIC(9,4) NOT NULL DEFAULT 0,
  under_delivery_close_pct NUMERIC(9,4) NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS procure_match_current(
  invoice_id UUID PRIMARY KEY REFERENCES procure_invoice_projection(id),
  status TEXT NOT NULL,
  reason TEXT NULL,
  evaluated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS procure_match_history(
  sequence BIGSERIAL PRIMARY KEY,
  id UUID UNIQUE NOT NULL,
  invoice_id UUID NOT NULL REFERENCES procure_invoice_projection(id),
  order_line_id UUID NOT NULL REFERENCES procure_order_lines(id),
  status TEXT NOT NULL,
  reason TEXT NULL,
  detail JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);
```

Create a database trigger preventing UPDATE/DELETE on `procure_match_history`.

- [ ] **Step 4: Implement deterministic match evaluation**

`evaluate_three_way_match` must return exactly one of:

```text
matched
matched_with_tolerance
blocked_quantity_variance
blocked_price_variance
blocked_supplier_mismatch
blocked_duplicate_invoice
pending_receipt
```

Evaluation order:
1. supplier mismatch → `blocked_supplier_mismatch`;
2. SKU mismatch → `blocked_quantity_variance` with reason `sku_mismatch`;
3. currency mismatch → `blocked_price_variance` with reason `currency_mismatch`;
4. zero/insufficient receipt → `pending_receipt`;
5. quantity outside tolerance → `blocked_quantity_variance`;
6. price outside both percentage and absolute tolerance → `blocked_price_variance`;
7. exact quantity and price → `matched`;
8. otherwise within configured tolerance → `matched_with_tolerance`.

Use `Decimal` throughout.

- [ ] **Step 5: Implement MIDAS invoice inbound and matching routes**

```text
POST /v1/inbound/midas/supplier-invoice
POST /v1/inbound/midas/supplier-invoice-cancelled
GET  /v1/invoices
GET  /v1/matches
POST /v1/matches/{invoice_id}/evaluate
GET  /v1/match-config
PUT  /v1/match-config
```

On successful match emit:

```python
emit('UNG-MIDAS', 'PROCURE.MATCH.PASSED', payload)
```

On blocked match emit:

```python
emit('UNG-MIDAS', 'PROCURE.MATCH.BLOCKED', payload)
```

Do not emit payment authorization for `pending_receipt`.

- [ ] **Step 6: Add tests for tolerance, duplicate invoice, and idempotency**

Required test cases:
- exact match → `matched`;
- within tolerance → `matched_with_tolerance`;
- quantity over tolerance → blocked;
- price over tolerance → blocked;
- supplier mismatch → blocked;
- no receipt → pending;
- same `invoice_ref` twice → duplicate blocked/rejected;
- same inbound `source_event_id` twice → no duplicate projection/history;
- cancelled invoice does not remain payable/matched.

- [ ] **Step 7: Run full matching tests**

```bash
pytest tests/test_procure_matching.py -v
python -m py_compile procure_matching.py app.py
```
Expected: PASS and exit 0.

- [ ] **Step 8: Add capabilities and commit**

Add to `/v1/system`:

```text
supplier-invoice-projection
three-way-match
match-tolerances
match-exceptions
```

Commit:

```bash
git add procure_matching.py tests/test_procure_matching.py app.py
git commit -m "feat: add procure three-way matching"
```

---

### Task 5: End-to-End Integration Acceptance and Production Readiness

**Files:**
- Create: `tests/test_procure_integration_flow.py`
- Modify: `app.py`
- Modify: `entrypoint.py`

**Interfaces:**
- Consumes: all prior task routes/helpers and existing NEXUS `emit()` mechanism.
- Produces: regression coverage for complete purchasing flow and final service capability/version state.

- [ ] **Step 1: Write the end-to-end flow test**

The test must construct one controlled flow:
1. vendor + supplier profile;
2. requisition header + line;
3. RFQ + quote;
4. existing award/order header;
5. PO line + schedule;
6. simulated VECTOR receipt projection;
7. simulated MIDAS invoice projection;
8. match evaluation;
9. assert final match is `matched` and the outgoing event type is `PROCURE.MATCH.PASSED`.

Use local dependency/test doubles for NEXUS delivery; do not call live production endpoints from unit tests.

- [ ] **Step 2: Run the end-to-end test and verify RED/GREEN as implementation is completed**

```bash
pytest tests/test_procure_integration_flow.py -v
```
Expected final state: PASS.

- [ ] **Step 3: Run the full PROCURE test suite**

```bash
pytest -v
```
Expected: zero failures.

- [ ] **Step 4: Run compile validation**

```bash
python -m py_compile app.py entrypoint.py supplier_profiles.py purchasing_documents.py procure_matching.py inbound_layers.py procure_kpis.py
```
Expected: exit 0.

- [ ] **Step 5: Verify service metadata**

Bump final service version to `1.3.0` everywhere it is returned or used in User-Agent metadata. Confirm `/v1/system` contains all new capability names while retaining existing capabilities.

- [ ] **Step 6: Commit final integration state**

```bash
git add app.py entrypoint.py tests/test_procure_integration_flow.py
git commit -m "test: validate procure purchase-to-match flow"
```

- [ ] **Step 7: Create PR and review before merge**

PR title:
```text
Complete UNG-PROCURE SAP-MM purchasing chain
```

PR summary must state that PROCURE coordinates purchasing and matching while VECTOR and MIDAS remain system-of-record owners for inventory and accounting respectively.

- [ ] **Step 8: Merge only after review and green verification**

Before merge verify:
```bash
pytest -v
python -m py_compile app.py entrypoint.py supplier_profiles.py purchasing_documents.py procure_matching.py
```
Expected: zero test failures and compile exit 0.

- [ ] **Step 9: Validate production deployment**

After Railway deploys main:
- deployment status = `SUCCESS`;
- `/health` = HTTP 200 and reports `UNG-PROCURE` version `1.3.0`;
- `/ready` reports database connected;
- startup logs contain no real exceptions or 5xx startup failures;
- deployed source commit equals the merged PR commit;
- route mounting is confirmed from deployed source/OpenAPI where possible;
- run one controlled authenticated PO → receipt → invoice → match acceptance flow if service credentials are available.

---

## Self-Review Checklist

- Spec coverage: supplier profile, requisition lines, RFQ, quote comparison, PO lines, schedules, VECTOR receipts/reversals, MIDAS invoices/cancellation, tolerances, 3-way match, idempotency, audit history, JANUS scopes, NEXUS pass/block events, and production validation are all assigned to tasks.
- Placeholder scan: no TBD/TODO/"implement later" steps remain.
- Type consistency: monetary and quantity fields introduced by this plan use `Decimal`/PostgreSQL `NUMERIC`; route/module names are consistent across tasks.
- Source-of-truth boundaries remain unchanged: PROCURE = purchasing/match state, VECTOR = physical inventory/receipts, MIDAS = accounting/payment.

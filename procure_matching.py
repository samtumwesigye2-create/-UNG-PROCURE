from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
import hmac, json
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
try:
    from psycopg.errors import UniqueViolation
except Exception:
    class UniqueViolation(Exception):
        pass

router = APIRouter(tags=['procure-matching'])


def now():
    return datetime.now(timezone.utc)


def allocate_receipt(ordered_quantity, already_received, incoming_quantity):
    ordered_quantity = Decimal(ordered_quantity)
    already_received = Decimal(already_received)
    incoming_quantity = Decimal(incoming_quantity)
    if incoming_quantity <= 0:
        raise ValueError('receipt_quantity_must_be_positive')
    return min(max(Decimal('0'), ordered_quantity - already_received), incoming_quantity)


def _pct_diff(actual, expected):
    actual, expected = Decimal(actual), Decimal(expected)
    if expected == 0:
        return Decimal('0') if actual == 0 else Decimal('1000000')
    return abs(actual - expected) / abs(expected) * Decimal('100')


def evaluate_three_way_match(po_line, receipt_qty, invoice, tolerance):
    ordered = Decimal(po_line['ordered_quantity'])
    po_price = Decimal(po_line['unit_price'])
    receipt = Decimal(receipt_qty)
    invoice_qty = Decimal(invoice['quantity'])
    invoice_price = Decimal(invoice['unit_price'])
    qty_tol = Decimal(tolerance.get('quantity_pct', 0))
    price_tol = Decimal(tolerance.get('price_pct', 0))
    abs_tol = Decimal(tolerance.get('absolute_price', 0))
    if str(invoice['vendor_id']) != str(po_line['vendor_id']):
        return {'status':'blocked_supplier_mismatch','reason':'supplier_mismatch'}
    if invoice['sku'] != po_line['sku']:
        return {'status':'blocked_quantity_variance','reason':'sku_mismatch'}
    if invoice['currency'].upper() != po_line['currency'].upper():
        return {'status':'blocked_price_variance','reason':'currency_mismatch'}
    if receipt <= 0:
        return {'status':'pending_receipt','reason':'no_receipt'}
    if invoice_qty > receipt and _pct_diff(invoice_qty, receipt) > qty_tol:
        return {'status':'blocked_quantity_variance','reason':'invoice_exceeds_received'}
    price_delta = abs(invoice_price - po_price)
    if _pct_diff(invoice_price, po_price) > price_tol and price_delta > abs_tol:
        return {'status':'blocked_price_variance','reason':'price_variance'}
    if invoice_qty == ordered and receipt >= ordered and invoice_price == po_price:
        return {'status':'matched','reason':None}
    return {'status':'matched_with_tolerance','reason':'within_tolerance'}


class ReceiptEvent(BaseModel):
    source_event_id: str
    receipt_id: str
    order_id: str
    order_line_id: str
    sku: str
    quantity: Decimal
    location_code: str | None = None
    lot_code: str | None = None
    serial_numbers: list[str] = Field(default_factory=list)
    received_at: datetime | None = None


class ReceiptReverseEvent(BaseModel):
    source_event_id: str
    original_receipt_id: str
    order_line_id: str
    quantity: Decimal


class InvoiceEvent(BaseModel):
    source_event_id: str
    invoice_ref: str
    vendor_id: str
    order_id: str
    order_line_id: str
    sku: str
    quantity: Decimal
    unit_price: Decimal
    tax_amount: Decimal = Decimal('0')
    total_amount: Decimal
    currency: str = 'USD'
    posted_at: datetime | None = None


class InvoiceCancelEvent(BaseModel):
    source_event_id: str
    invoice_ref: str


class ToleranceIn(BaseModel):
    quantity_pct: Decimal = Decimal('0')
    price_pct: Decimal = Decimal('0')
    absolute_price: Decimal = Decimal('0')
    over_delivery_pct: Decimal = Decimal('0')
    under_delivery_close_pct: Decimal = Decimal('0')


def init_procure_matching(conn):
    with conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS procure_inbound_events(
          source_system TEXT NOT NULL, source_event_id TEXT NOT NULL, event_type TEXT NOT NULL,
          processed_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(source_system,source_event_id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS procure_receipts(
          id UUID PRIMARY KEY, source_event_id TEXT UNIQUE NOT NULL, receipt_id TEXT NOT NULL,
          order_id UUID NOT NULL REFERENCES procure_orders(id),
          order_line_id UUID NOT NULL REFERENCES procure_order_lines(id), sku TEXT NOT NULL,
          quantity NUMERIC(18,4) NOT NULL CHECK(quantity>0), location_code TEXT NULL,
          lot_code TEXT NULL, serial_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,
          status TEXT NOT NULL, received_at TIMESTAMPTZ NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS procure_invoice_projection(
          id UUID PRIMARY KEY, source_event_id TEXT UNIQUE NOT NULL, invoice_ref TEXT UNIQUE NOT NULL,
          vendor_id UUID NOT NULL REFERENCES procure_vendors(id), order_id UUID NOT NULL REFERENCES procure_orders(id),
          order_line_id UUID NOT NULL REFERENCES procure_order_lines(id), sku TEXT NOT NULL,
          quantity NUMERIC(18,4) NOT NULL CHECK(quantity>0), unit_price NUMERIC(18,4) NOT NULL CHECK(unit_price>=0),
          tax_amount NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(tax_amount>=0), total_amount NUMERIC(18,4) NOT NULL CHECK(total_amount>=0),
          currency TEXT NOT NULL, status TEXT NOT NULL, posted_at TIMESTAMPTZ NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS procure_match_tolerances(
          id SMALLINT PRIMARY KEY DEFAULT 1 CHECK(id=1), quantity_pct NUMERIC(9,4) NOT NULL DEFAULT 0,
          price_pct NUMERIC(9,4) NOT NULL DEFAULT 0, absolute_price NUMERIC(18,4) NOT NULL DEFAULT 0,
          over_delivery_pct NUMERIC(9,4) NOT NULL DEFAULT 0, under_delivery_close_pct NUMERIC(9,4) NOT NULL DEFAULT 0,
          updated_at TIMESTAMPTZ NOT NULL)''')
        c.execute('INSERT INTO procure_match_tolerances(id,updated_at) VALUES(1,%s) ON CONFLICT(id) DO NOTHING', (now(),))
        c.execute('''CREATE TABLE IF NOT EXISTS procure_match_current(
          invoice_id UUID PRIMARY KEY REFERENCES procure_invoice_projection(id), status TEXT NOT NULL,
          reason TEXT NULL, evaluated_at TIMESTAMPTZ NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS procure_match_history(
          sequence BIGSERIAL PRIMARY KEY, id UUID UNIQUE NOT NULL,
          invoice_id UUID NOT NULL REFERENCES procure_invoice_projection(id),
          order_line_id UUID NOT NULL REFERENCES procure_order_lines(id), status TEXT NOT NULL,
          reason TEXT NULL, detail JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL)''')
        c.execute("""CREATE OR REPLACE FUNCTION procure_match_history_immutable() RETURNS trigger AS $$
          BEGIN RAISE EXCEPTION 'procure_match_history_is_append_only'; END; $$ LANGUAGE plpgsql""")
        c.execute('DROP TRIGGER IF EXISTS trg_procure_match_history_immutable ON procure_match_history')
        c.execute('''CREATE TRIGGER trg_procure_match_history_immutable BEFORE UPDATE OR DELETE ON procure_match_history
          FOR EACH ROW EXECUTE FUNCTION procure_match_history_immutable()''')


def _deps():
    from app import conn, auth, emit, PROCURE_SERVICE_TOKEN
    return conn, auth, emit, PROCURE_SERVICE_TOKEN


def require_service_identity(authorization, expected):
    if not expected:
        raise HTTPException(503, 'procure_service_identity_not_configured')
    if not authorization or not authorization.lower().startswith('bearer '):
        raise HTTPException(401, 'service_bearer_required')
    token = authorization.split(' ', 1)[1]
    if not hmac.compare_digest(token, expected):
        raise HTTPException(401, 'invalid_service_identity')


def apply_goods_receipt(conn, event_id, payload):
    with conn() as c:
        if c.execute("SELECT 1 FROM procure_inbound_events WHERE source_system='UNG-VECTOR' AND source_event_id=%s", (event_id,)).fetchone():
            return c.execute('SELECT * FROM procure_receipts WHERE source_event_id=%s', (event_id,)).fetchone()
        line = c.execute('''SELECT l.*,o.vendor_id FROM procure_order_lines l
                            JOIN procure_orders o ON o.id=l.order_id
                            WHERE l.id=%s AND l.order_id=%s FOR UPDATE''',
                         (payload.order_line_id, payload.order_id)).fetchone()
        if not line:
            raise HTTPException(404, 'order_line_not_found')
        if line['sku'] != payload.sku:
            raise HTTPException(409, 'sku_mismatch')
        applied = allocate_receipt(line['ordered_quantity'], line['received_quantity'], payload.quantity)
        if applied <= 0:
            raise HTTPException(409, 'order_line_already_fully_received')
        row = c.execute('''INSERT INTO procure_receipts
          (id,source_event_id,receipt_id,order_id,order_line_id,sku,quantity,location_code,lot_code,serial_numbers,status,received_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'posted',%s) RETURNING *''',
          (str(uuid4()),event_id,payload.receipt_id,payload.order_id,payload.order_line_id,payload.sku,applied,
           payload.location_code,payload.lot_code,json.dumps(payload.serial_numbers),payload.received_at or now())).fetchone()
        new_received = Decimal(line['received_quantity']) + applied
        line_status = 'received' if new_received >= Decimal(line['ordered_quantity']) else 'partially_received'
        c.execute('UPDATE procure_order_lines SET received_quantity=%s,status=%s WHERE id=%s',
                  (new_received,line_status,payload.order_line_id))
        remaining = applied
        schedules = c.execute('''SELECT * FROM procure_delivery_schedules WHERE order_line_id=%s
                                 ORDER BY due_date,schedule_no FOR UPDATE''', (payload.order_line_id,)).fetchall()
        for schedule in schedules:
            if remaining <= 0:
                break
            open_qty = Decimal(schedule['scheduled_quantity']) - Decimal(schedule['received_quantity'])
            take = min(open_qty, remaining)
            if take > 0:
                updated = Decimal(schedule['received_quantity']) + take
                status = 'received' if updated >= Decimal(schedule['scheduled_quantity']) else 'partially_received'
                c.execute('UPDATE procure_delivery_schedules SET received_quantity=%s,status=%s WHERE id=%s',
                          (updated,status,schedule['id']))
                remaining -= take
        c.execute("INSERT INTO procure_inbound_events VALUES('UNG-VECTOR',%s,'VECTOR.GOODS_RECEIPT.POSTED',%s)", (event_id,now()))
        return row


def _evaluate_invoice(conn, invoice_id, emit_fn):
    with conn() as c:
        invoice = c.execute('SELECT * FROM procure_invoice_projection WHERE id=%s', (invoice_id,)).fetchone()
        if not invoice:
            raise HTTPException(404, 'invoice_not_found')
        line = c.execute('''SELECT l.*,o.vendor_id FROM procure_order_lines l
                            JOIN procure_orders o ON o.id=l.order_id WHERE l.id=%s''',
                         (invoice['order_line_id'],)).fetchone()
        receipt_qty = c.execute("SELECT COALESCE(sum(quantity),0) q FROM procure_receipts WHERE order_line_id=%s AND status='posted'",
                                (invoice['order_line_id'],)).fetchone()['q']
        tolerance = c.execute('SELECT * FROM procure_match_tolerances WHERE id=1').fetchone()
        result = evaluate_three_way_match(line, receipt_qty, invoice, tolerance)
        t = now()
        c.execute('''INSERT INTO procure_match_current(invoice_id,status,reason,evaluated_at)
          VALUES(%s,%s,%s,%s) ON CONFLICT(invoice_id) DO UPDATE SET
          status=EXCLUDED.status,reason=EXCLUDED.reason,evaluated_at=EXCLUDED.evaluated_at''',
          (invoice_id,result['status'],result.get('reason'),t))
        detail = {'receipt_qty':str(receipt_qty),'invoice_ref':invoice['invoice_ref'],
                  'po_unit_price':str(line['unit_price']),'invoice_unit_price':str(invoice['unit_price'])}
        c.execute('''INSERT INTO procure_match_history(id,invoice_id,order_line_id,status,reason,detail,created_at)
          VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s)''',
          (str(uuid4()),invoice_id,invoice['order_line_id'],result['status'],result.get('reason'),json.dumps(detail),t))
    if result['status'] in {'matched','matched_with_tolerance'}:
        emit_fn('UNG-MIDAS','PROCURE.MATCH.PASSED',{'invoice_id':str(invoice_id),'invoice_ref':invoice['invoice_ref'],'status':result['status']})
    elif result['status'] != 'pending_receipt':
        emit_fn('UNG-MIDAS','PROCURE.MATCH.BLOCKED',{'invoice_id':str(invoice_id),'invoice_ref':invoice['invoice_ref'],
                                                    'status':result['status'],'reason':result.get('reason')})
    return result


@router.post('/v1/inbound/vector/goods-receipt')
def inbound_receipt(b: ReceiptEvent, authorization: str | None = Header(None)):
    conn, _, _, token = _deps()
    require_service_identity(authorization, token)
    return apply_goods_receipt(conn, b.source_event_id, b)


@router.post('/v1/inbound/vector/goods-receipt-reversed')
def reverse_receipt(b: ReceiptReverseEvent, authorization: str | None = Header(None)):
    conn, _, _, token = _deps()
    require_service_identity(authorization, token)
    with conn() as c:
        if c.execute("SELECT 1 FROM procure_inbound_events WHERE source_system='UNG-VECTOR' AND source_event_id=%s", (b.source_event_id,)).fetchone():
            return {'status':'already_processed'}
        receipt = c.execute("SELECT * FROM procure_receipts WHERE receipt_id=%s AND order_line_id=%s AND status='posted' FOR UPDATE",
                            (b.original_receipt_id,b.order_line_id)).fetchone()
        if not receipt:
            raise HTTPException(404, 'receipt_not_found')
        qty = min(Decimal(b.quantity), Decimal(receipt['quantity']))
        line = c.execute('SELECT * FROM procure_order_lines WHERE id=%s FOR UPDATE', (b.order_line_id,)).fetchone()
        new_received = max(Decimal('0'), Decimal(line['received_quantity']) - qty)
        status = 'open' if new_received == 0 else 'partially_received'
        c.execute('UPDATE procure_order_lines SET received_quantity=%s,status=%s WHERE id=%s', (new_received,status,b.order_line_id))
        c.execute("UPDATE procure_receipts SET status='reversed' WHERE id=%s", (receipt['id'],))
        c.execute("INSERT INTO procure_inbound_events VALUES('UNG-VECTOR',%s,'VECTOR.GOODS_RECEIPT.REVERSED',%s)", (b.source_event_id,now()))
        return {'status':'reversed','quantity':qty}


@router.get('/v1/receipts')
def receipts(authorization: str | None = Header(None)):
    conn, auth, _, _ = _deps()
    auth('procure.receipts.read', authorization)
    with conn() as c:
        return c.execute('SELECT * FROM procure_receipts ORDER BY received_at DESC').fetchall()


@router.post('/v1/inbound/midas/supplier-invoice', status_code=201)
def inbound_invoice(b: InvoiceEvent, authorization: str | None = Header(None)):
    conn, _, emit, token = _deps()
    require_service_identity(authorization, token)
    if b.quantity <= 0 or b.unit_price < 0 or b.tax_amount < 0 or b.total_amount < 0:
        raise HTTPException(422, 'invalid_invoice_values')
    with conn() as c:
        if c.execute("SELECT 1 FROM procure_inbound_events WHERE source_system='UNG-MIDAS' AND source_event_id=%s", (b.source_event_id,)).fetchone():
            return c.execute('SELECT * FROM procure_invoice_projection WHERE source_event_id=%s', (b.source_event_id,)).fetchone()
        try:
            row = c.execute('''INSERT INTO procure_invoice_projection
              (id,source_event_id,invoice_ref,vendor_id,order_id,order_line_id,sku,quantity,unit_price,tax_amount,total_amount,currency,status,posted_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'posted',%s) RETURNING *''',
              (str(uuid4()),b.source_event_id,b.invoice_ref,b.vendor_id,b.order_id,b.order_line_id,b.sku,b.quantity,
               b.unit_price,b.tax_amount,b.total_amount,b.currency.upper(),b.posted_at or now())).fetchone()
        except UniqueViolation:
            raise HTTPException(409, 'duplicate_invoice')
        c.execute("INSERT INTO procure_inbound_events VALUES('UNG-MIDAS',%s,'MIDAS.SUPPLIER_INVOICE.POSTED',%s)", (b.source_event_id,now()))
    _evaluate_invoice(conn, row['id'], emit)
    return row


@router.post('/v1/inbound/midas/supplier-invoice-cancelled')
def cancel_invoice(b: InvoiceCancelEvent, authorization: str | None = Header(None)):
    conn, _, _, token = _deps()
    require_service_identity(authorization, token)
    with conn() as c:
        if c.execute("SELECT 1 FROM procure_inbound_events WHERE source_system='UNG-MIDAS' AND source_event_id=%s", (b.source_event_id,)).fetchone():
            return {'status':'already_processed'}
        row = c.execute("UPDATE procure_invoice_projection SET status='cancelled' WHERE invoice_ref=%s RETURNING *", (b.invoice_ref,)).fetchone()
        if not row:
            raise HTTPException(404, 'invoice_not_found')
        c.execute("""INSERT INTO procure_match_current(invoice_id,status,reason,evaluated_at)
          VALUES(%s,'cancelled','invoice_cancelled',%s) ON CONFLICT(invoice_id) DO UPDATE SET
          status='cancelled',reason='invoice_cancelled',evaluated_at=EXCLUDED.evaluated_at""", (row['id'],now()))
        c.execute("INSERT INTO procure_match_history(id,invoice_id,order_line_id,status,reason,detail,created_at) VALUES(%s,%s,%s,'cancelled','invoice_cancelled','{}'::jsonb,%s)",
                  (str(uuid4()),row['id'],row['order_line_id'],now()))
        c.execute("INSERT INTO procure_inbound_events VALUES('UNG-MIDAS',%s,'MIDAS.SUPPLIER_INVOICE.CANCELLED',%s)", (b.source_event_id,now()))
        return row


@router.get('/v1/invoices')
def invoices(authorization: str | None = Header(None)):
    conn, auth, _, _ = _deps()
    auth('procure.invoices.read', authorization)
    with conn() as c:
        return c.execute('SELECT * FROM procure_invoice_projection ORDER BY posted_at DESC').fetchall()


@router.get('/v1/matches')
def matches(authorization: str | None = Header(None)):
    conn, auth, _, _ = _deps()
    auth('procure.match.read', authorization)
    with conn() as c:
        return c.execute('''SELECT m.*,i.invoice_ref FROM procure_match_current m
                            JOIN procure_invoice_projection i ON i.id=m.invoice_id
                            ORDER BY evaluated_at DESC''').fetchall()


@router.post('/v1/matches/{invoice_id}/evaluate')
def evaluate(invoice_id: str, authorization: str | None = Header(None)):
    conn, auth, emit, _ = _deps()
    auth('procure.match.write', authorization)
    return _evaluate_invoice(conn, invoice_id, emit)


@router.get('/v1/match-config')
def match_config(authorization: str | None = Header(None)):
    conn, auth, _, _ = _deps()
    auth('procure.config.read', authorization)
    with conn() as c:
        return c.execute('SELECT * FROM procure_match_tolerances WHERE id=1').fetchone()


@router.put('/v1/match-config')
def put_match_config(b: ToleranceIn, authorization: str | None = Header(None)):
    conn, auth, _, _ = _deps()
    auth('procure.config.write', authorization)
    if min(b.quantity_pct,b.price_pct,b.absolute_price,b.over_delivery_pct,b.under_delivery_close_pct) < 0:
        raise HTTPException(422, 'tolerances_cannot_be_negative')
    with conn() as c:
        return c.execute('''UPDATE procure_match_tolerances SET quantity_pct=%s,price_pct=%s,absolute_price=%s,
                            over_delivery_pct=%s,under_delivery_close_pct=%s,updated_at=%s WHERE id=1 RETURNING *''',
                         (b.quantity_pct,b.price_pct,b.absolute_price,b.over_delivery_pct,b.under_delivery_close_pct,now())).fetchone()

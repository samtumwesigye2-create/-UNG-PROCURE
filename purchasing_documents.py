from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
try:
    from psycopg.errors import UniqueViolation
except Exception:
    class UniqueViolation(Exception):
        pass

router = APIRouter(tags=['purchasing-documents'])


def now():
    return datetime.now(timezone.utc)


def validate_positive_decimal(value: Decimal, field: str):
    if value <= 0:
        raise ValueError(f'{field}_must_be_positive')
    return True


def rank_quotes(quotes):
    return sorted(quotes, key=lambda q: (Decimal(q['unit_price']), int(q['lead_time_days'])))


class RequestLineIn(BaseModel):
    line_no: int
    sku: str
    description: str = ''
    quantity: Decimal
    uom: str = 'EA'
    target_delivery_date: date | None = None
    requested_location: str | None = None
    estimated_unit_price: Decimal = Decimal('0')
    cost_center_ref: str | None = None
    account_assignment_ref: str | None = None


class RFQIn(BaseModel):
    rfq_code: str
    request_id: str
    valid_until: date | None = None


class QuoteIn(BaseModel):
    vendor_id: str
    request_line_id: str
    unit_price: Decimal
    currency: str = 'USD'
    lead_time_days: int = 0


class OrderLineIn(BaseModel):
    line_no: int
    sku: str
    description: str = ''
    ordered_quantity: Decimal
    uom: str = 'EA'
    unit_price: Decimal
    currency: str = 'USD'
    tax_code: str | None = None
    receiving_location: str | None = None


class ScheduleIn(BaseModel):
    schedule_no: int
    scheduled_quantity: Decimal
    due_date: date


def init_purchasing_documents(conn):
    with conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS procure_request_lines(
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
          UNIQUE(request_id,line_no))''')
        c.execute('''CREATE TABLE IF NOT EXISTS procure_rfqs(
          id UUID PRIMARY KEY,
          rfq_code TEXT UNIQUE NOT NULL,
          request_id UUID NOT NULL REFERENCES procure_requests(id),
          status TEXT NOT NULL,
          valid_until DATE NULL,
          created_at TIMESTAMPTZ NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS procure_rfq_vendors(
          rfq_id UUID NOT NULL REFERENCES procure_rfqs(id) ON DELETE CASCADE,
          vendor_id UUID NOT NULL REFERENCES procure_vendors(id),
          PRIMARY KEY(rfq_id,vendor_id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS procure_rfq_quotes(
          id UUID PRIMARY KEY,
          rfq_id UUID NOT NULL REFERENCES procure_rfqs(id) ON DELETE CASCADE,
          vendor_id UUID NOT NULL REFERENCES procure_vendors(id),
          request_line_id UUID NOT NULL REFERENCES procure_request_lines(id),
          unit_price NUMERIC(18,4) NOT NULL CHECK(unit_price>=0),
          currency TEXT NOT NULL,
          lead_time_days INTEGER NOT NULL DEFAULT 0 CHECK(lead_time_days>=0),
          status TEXT NOT NULL,
          submitted_at TIMESTAMPTZ NOT NULL,
          UNIQUE(rfq_id,vendor_id,request_line_id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS procure_order_lines(
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
          UNIQUE(order_id,line_no))''')
        c.execute('''CREATE TABLE IF NOT EXISTS procure_delivery_schedules(
          id UUID PRIMARY KEY,
          order_line_id UUID NOT NULL REFERENCES procure_order_lines(id) ON DELETE CASCADE,
          schedule_no INTEGER NOT NULL,
          scheduled_quantity NUMERIC(18,4) NOT NULL CHECK(scheduled_quantity>0),
          received_quantity NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK(received_quantity>=0),
          due_date DATE NOT NULL,
          status TEXT NOT NULL,
          UNIQUE(order_line_id,schedule_no))''')


def _deps():
    from app import conn, auth
    return conn, auth


@router.post('/v1/requests/{request_id}/lines', status_code=201)
def add_request_line(request_id: str, b: RequestLineIn, authorization: str | None = Header(None)):
    conn, auth = _deps()
    auth('procure.requests.write', authorization)
    try:
        validate_positive_decimal(b.quantity, 'quantity')
    except ValueError as e:
        raise HTTPException(422, str(e))
    if b.estimated_unit_price < 0:
        raise HTTPException(422, 'estimated_unit_price_cannot_be_negative')
    with conn() as c:
        if not c.execute('SELECT id FROM procure_requests WHERE id=%s', (request_id,)).fetchone():
            raise HTTPException(404, 'request_not_found')
        try:
            return c.execute('''INSERT INTO procure_request_lines
              (id,request_id,line_no,sku,description,quantity,uom,target_delivery_date,requested_location,estimated_unit_price,cost_center_ref,account_assignment_ref)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
              (str(uuid4()),request_id,b.line_no,b.sku,b.description,b.quantity,b.uom,b.target_delivery_date,b.requested_location,b.estimated_unit_price,b.cost_center_ref,b.account_assignment_ref)).fetchone()
        except UniqueViolation:
            raise HTTPException(409, 'request_line_exists')


@router.get('/v1/requests/{request_id}/lines')
def get_request_lines(request_id: str, authorization: str | None = Header(None)):
    conn, auth = _deps()
    auth('procure.requests.read', authorization)
    with conn() as c:
        return c.execute('SELECT * FROM procure_request_lines WHERE request_id=%s ORDER BY line_no', (request_id,)).fetchall()


@router.post('/v1/rfqs', status_code=201)
def create_rfq(b: RFQIn, authorization: str | None = Header(None)):
    conn, auth = _deps()
    auth('procure.bids.write', authorization)
    with conn() as c:
        if not c.execute('SELECT id FROM procure_requests WHERE id=%s', (b.request_id,)).fetchone():
            raise HTTPException(404, 'request_not_found')
        try:
            return c.execute('INSERT INTO procure_rfqs VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',
                             (str(uuid4()), b.rfq_code, b.request_id, 'open', b.valid_until, now())).fetchone()
        except UniqueViolation:
            raise HTTPException(409, 'rfq_code_exists')


@router.post('/v1/rfqs/{rfq_id}/vendors/{vendor_id}', status_code=201)
def invite_vendor(rfq_id: str, vendor_id: str, authorization: str | None = Header(None)):
    conn, auth = _deps()
    auth('procure.bids.write', authorization)
    with conn() as c:
        if not c.execute('SELECT id FROM procure_rfqs WHERE id=%s', (rfq_id,)).fetchone():
            raise HTTPException(404, 'rfq_not_found')
        if not c.execute('SELECT id FROM procure_vendors WHERE id=%s', (vendor_id,)).fetchone():
            raise HTTPException(404, 'vendor_not_found')
        try:
            c.execute('INSERT INTO procure_rfq_vendors VALUES(%s,%s)', (rfq_id, vendor_id))
            return {'rfq_id': rfq_id, 'vendor_id': vendor_id, 'status': 'invited'}
        except UniqueViolation:
            raise HTTPException(409, 'vendor_already_invited')


@router.post('/v1/rfqs/{rfq_id}/quotes', status_code=201)
def create_quote(rfq_id: str, b: QuoteIn, authorization: str | None = Header(None)):
    conn, auth = _deps()
    auth('procure.bids.write', authorization)
    if b.unit_price < 0 or b.lead_time_days < 0:
        raise HTTPException(422, 'invalid_quote_values')
    with conn() as c:
        if not c.execute('SELECT 1 FROM procure_rfq_vendors WHERE rfq_id=%s AND vendor_id=%s', (rfq_id, b.vendor_id)).fetchone():
            raise HTTPException(400, 'vendor_not_invited')
        if not c.execute('''SELECT 1 FROM procure_request_lines rl JOIN procure_rfqs r ON r.request_id=rl.request_id
                            WHERE rl.id=%s AND r.id=%s''', (b.request_line_id, rfq_id)).fetchone():
            raise HTTPException(404, 'request_line_not_in_rfq')
        try:
            return c.execute('''INSERT INTO procure_rfq_quotes
              (id,rfq_id,vendor_id,request_line_id,unit_price,currency,lead_time_days,status,submitted_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
              (str(uuid4()),rfq_id,b.vendor_id,b.request_line_id,b.unit_price,b.currency.upper(),b.lead_time_days,'submitted',now())).fetchone()
        except UniqueViolation:
            raise HTTPException(409, 'quote_exists')


@router.get('/v1/rfqs/{rfq_id}/comparison')
def compare_rfq(rfq_id: str, authorization: str | None = Header(None)):
    conn, auth = _deps()
    auth('procure.bids.read', authorization)
    with conn() as c:
        rows = c.execute('''SELECT q.*,v.name vendor_name FROM procure_rfq_quotes q
                            JOIN procure_vendors v ON v.id=q.vendor_id
                            WHERE q.rfq_id=%s
                            ORDER BY q.request_line_id,q.unit_price ASC,q.lead_time_days ASC''', (rfq_id,)).fetchall()
        return {'rfq_id': rfq_id, 'quotes': rows}


@router.post('/v1/orders/{order_id}/lines', status_code=201)
def add_order_line(order_id: str, b: OrderLineIn, authorization: str | None = Header(None)):
    conn, auth = _deps()
    auth('procure.orders.write', authorization)
    try:
        validate_positive_decimal(b.ordered_quantity, 'ordered_quantity')
    except ValueError as e:
        raise HTTPException(422, str(e))
    if b.unit_price < 0:
        raise HTTPException(422, 'unit_price_cannot_be_negative')
    with conn() as c:
        if not c.execute('SELECT id FROM procure_orders WHERE id=%s', (order_id,)).fetchone():
            raise HTTPException(404, 'order_not_found')
        try:
            return c.execute('''INSERT INTO procure_order_lines
              (id,order_id,line_no,sku,description,ordered_quantity,received_quantity,uom,unit_price,currency,tax_code,receiving_location,status)
              VALUES(%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,'open') RETURNING *''',
              (str(uuid4()),order_id,b.line_no,b.sku,b.description,b.ordered_quantity,b.uom,b.unit_price,b.currency.upper(),b.tax_code,b.receiving_location)).fetchone()
        except UniqueViolation:
            raise HTTPException(409, 'order_line_exists')


@router.get('/v1/orders/{order_id}/lines')
def get_order_lines(order_id: str, authorization: str | None = Header(None)):
    conn, auth = _deps()
    auth('procure.orders.read', authorization)
    with conn() as c:
        return c.execute('SELECT * FROM procure_order_lines WHERE order_id=%s ORDER BY line_no', (order_id,)).fetchall()


@router.post('/v1/orders/{order_id}/lines/{line_id}/schedules', status_code=201)
def add_schedule(order_id: str, line_id: str, b: ScheduleIn, authorization: str | None = Header(None)):
    conn, auth = _deps()
    auth('procure.orders.write', authorization)
    try:
        validate_positive_decimal(b.scheduled_quantity, 'scheduled_quantity')
    except ValueError as e:
        raise HTTPException(422, str(e))
    with conn() as c:
        if not c.execute('SELECT id FROM procure_order_lines WHERE id=%s AND order_id=%s', (line_id, order_id)).fetchone():
            raise HTTPException(404, 'order_line_not_found')
        try:
            return c.execute("INSERT INTO procure_delivery_schedules VALUES(%s,%s,%s,%s,0,%s,'open') RETURNING *",
                             (str(uuid4()),line_id,b.schedule_no,b.scheduled_quantity,b.due_date)).fetchone()
        except UniqueViolation:
            raise HTTPException(409, 'schedule_exists')


@router.get('/v1/orders/{order_id}/schedules')
def get_schedules(order_id: str, authorization: str | None = Header(None)):
    conn, auth = _deps()
    auth('procure.orders.read', authorization)
    with conn() as c:
        return c.execute('''SELECT s.* FROM procure_delivery_schedules s
                            JOIN procure_order_lines l ON l.id=s.order_line_id
                            WHERE l.order_id=%s ORDER BY l.line_no,s.schedule_no''', (order_id,)).fetchall()

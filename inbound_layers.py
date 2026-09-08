from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app import auth, conn

router = APIRouter(prefix="/v1/inbound", tags=["Inbound Logistics"])


def now():
    return datetime.now(timezone.utc)


def ensure_schema():
    with conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS procure_supplier_metrics(
            vendor_id UUID PRIMARY KEY,
            deliveries_total INTEGER NOT NULL DEFAULT 0,
            deliveries_on_time INTEGER NOT NULL DEFAULT 0,
            exceptions_total INTEGER NOT NULL DEFAULT 0,
            avg_lead_time_days DOUBLE PRECISION NOT NULL DEFAULT 0,
            reliability_score DOUBLE PRECISION NOT NULL DEFAULT 100,
            updated_at TIMESTAMPTZ NOT NULL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS procure_asns(
            id UUID PRIMARY KEY,
            order_id UUID NOT NULL,
            vendor_id UUID NOT NULL,
            asn_number TEXT UNIQUE NOT NULL,
            carrier TEXT,
            eta TIMESTAMPTZ,
            status TEXT NOT NULL,
            exception BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS procure_dock_appointments(
            id UUID PRIMARY KEY,
            order_id UUID NOT NULL,
            asn_id UUID,
            facility_code TEXT NOT NULL,
            dock_code TEXT NOT NULL,
            slot_start TIMESTAMPTZ NOT NULL,
            slot_end TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL,
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_procure_asns_order ON procure_asns(order_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_procure_asns_vendor ON procure_asns(vendor_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_procure_docks_slot ON procure_dock_appointments(facility_code,dock_code,slot_start,slot_end)')


class SupplierMetricIn(BaseModel):
    on_time: bool
    lead_time_days: float = Field(ge=0, le=3650)
    exception: bool = False


class ASNIn(BaseModel):
    order_id: str
    asn_number: str = Field(min_length=3, max_length=80)
    carrier: str = Field(default="", max_length=120)
    eta: datetime | None = None


class ASNStatusIn(BaseModel):
    status: str = Field(min_length=2, max_length=40)
    exception: bool | None = None


class DockAppointmentIn(BaseModel):
    order_id: str
    asn_id: str | None = None
    facility_code: str = Field(min_length=2, max_length=64)
    dock_code: str = Field(min_length=1, max_length=64)
    slot_start: datetime
    slot_end: datetime
    notes: str = Field(default="", max_length=500)


class DockStatusIn(BaseModel):
    status: str = Field(min_length=2, max_length=40)


def _vendor_for_order(order_id: str):
    with conn() as c:
        return c.execute('SELECT vendor_id FROM procure_orders WHERE id=%s', (order_id,)).fetchone()


def _score(total: int, on_time: int, exceptions: int, avg_lead: float) -> float:
    if total <= 0:
        return 100.0
    on_time_rate = on_time / total
    exception_rate = exceptions / total
    lead_penalty = min(avg_lead / 60.0, 1.0)
    score = 100.0 * (0.70 * on_time_rate + 0.20 * (1.0 - exception_rate) + 0.10 * (1.0 - lead_penalty))
    return round(max(0.0, min(100.0, score)), 2)


@router.post('/suppliers/{vendor_id}/performance')
def record_supplier_performance(vendor_id: str, body: SupplierMetricIn, authorization: str | None = Header(None)):
    auth('procure.vendors.write', authorization)
    ensure_schema()
    with conn() as c:
        if not c.execute('SELECT id FROM procure_vendors WHERE id=%s', (vendor_id,)).fetchone():
            raise HTTPException(404, 'vendor_not_found')
        row = c.execute('SELECT * FROM procure_supplier_metrics WHERE vendor_id=%s', (vendor_id,)).fetchone()
        total = (row['deliveries_total'] if row else 0) + 1
        on_time = (row['deliveries_on_time'] if row else 0) + (1 if body.on_time else 0)
        exceptions = (row['exceptions_total'] if row else 0) + (1 if body.exception else 0)
        previous_avg = float(row['avg_lead_time_days']) if row else 0.0
        avg = ((previous_avg * (total - 1)) + body.lead_time_days) / total
        score = _score(total, on_time, exceptions, avg)
        return c.execute('''INSERT INTO procure_supplier_metrics(vendor_id,deliveries_total,deliveries_on_time,exceptions_total,avg_lead_time_days,reliability_score,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(vendor_id) DO UPDATE SET deliveries_total=EXCLUDED.deliveries_total,deliveries_on_time=EXCLUDED.deliveries_on_time,
            exceptions_total=EXCLUDED.exceptions_total,avg_lead_time_days=EXCLUDED.avg_lead_time_days,reliability_score=EXCLUDED.reliability_score,updated_at=EXCLUDED.updated_at
            RETURNING *''', (vendor_id,total,on_time,exceptions,avg,score,now())).fetchone()


@router.get('/suppliers/{vendor_id}/reliability')
def supplier_reliability(vendor_id: str, authorization: str | None = Header(None)):
    auth('procure.vendors.read', authorization)
    ensure_schema()
    with conn() as c:
        vendor = c.execute('SELECT id,name,status,risk_level FROM procure_vendors WHERE id=%s', (vendor_id,)).fetchone()
        if not vendor: raise HTTPException(404, 'vendor_not_found')
        metrics = c.execute('SELECT * FROM procure_supplier_metrics WHERE vendor_id=%s', (vendor_id,)).fetchone()
        if not metrics:
            metrics = {'vendor_id': vendor_id, 'deliveries_total': 0, 'deliveries_on_time': 0, 'exceptions_total': 0, 'avg_lead_time_days': 0.0, 'reliability_score': 100.0, 'updated_at': None}
        return {'vendor': vendor, 'metrics': metrics}


@router.get('/suppliers/reliability')
def supplier_reliability_list(authorization: str | None = Header(None)):
    auth('procure.vendors.read', authorization)
    ensure_schema()
    with conn() as c:
        return c.execute('''SELECT v.id vendor_id,v.name,v.status,v.risk_level,
            COALESCE(m.deliveries_total,0) deliveries_total,COALESCE(m.deliveries_on_time,0) deliveries_on_time,
            COALESCE(m.exceptions_total,0) exceptions_total,COALESCE(m.avg_lead_time_days,0) avg_lead_time_days,
            COALESCE(m.reliability_score,100) reliability_score,m.updated_at
            FROM procure_vendors v LEFT JOIN procure_supplier_metrics m ON m.vendor_id=v.id
            ORDER BY reliability_score DESC,v.name''').fetchall()


@router.post('/asns', status_code=201)
def create_asn(body: ASNIn, authorization: str | None = Header(None)):
    auth('procure.orders.read', authorization)
    ensure_schema()
    order = _vendor_for_order(body.order_id)
    if not order: raise HTTPException(404, 'order_not_found')
    ts = now()
    try:
        with conn() as c:
            return c.execute('''INSERT INTO procure_asns(id,order_id,vendor_id,asn_number,carrier,eta,status,exception,created_at,updated_at)
                VALUES(%s,%s,%s,%s,%s,%s,'asn_sent',FALSE,%s,%s) RETURNING *''',
                (str(uuid4()),body.order_id,order['vendor_id'],body.asn_number.strip(),body.carrier.strip(),body.eta,ts,ts)).fetchone()
    except Exception as exc:
        if 'unique' in str(exc).lower(): raise HTTPException(409, 'asn_number_exists')
        raise


@router.patch('/asns/{asn_id}')
def update_asn(asn_id: str, body: ASNStatusIn, authorization: str | None = Header(None)):
    auth('procure.orders.read', authorization)
    ensure_schema()
    allowed = {'asn_sent','in_transit','at_consolidation','received','cancelled'}
    status = body.status.strip().lower().replace(' ','_')
    if status not in allowed: raise HTTPException(422, 'invalid_asn_status')
    with conn() as c:
        current = c.execute('SELECT * FROM procure_asns WHERE id=%s', (asn_id,)).fetchone()
        if not current: raise HTTPException(404, 'asn_not_found')
        exception = current['exception'] if body.exception is None else body.exception
        return c.execute('UPDATE procure_asns SET status=%s,exception=%s,updated_at=%s WHERE id=%s RETURNING *', (status,exception,now(),asn_id)).fetchone()


@router.get('/asns')
def list_asns(order_id: str | None = None, authorization: str | None = Header(None)):
    auth('procure.orders.read', authorization)
    ensure_schema()
    with conn() as c:
        if order_id:
            return c.execute('SELECT * FROM procure_asns WHERE order_id=%s ORDER BY created_at DESC', (order_id,)).fetchall()
        return c.execute('SELECT * FROM procure_asns ORDER BY created_at DESC LIMIT 250').fetchall()


@router.post('/dock-appointments', status_code=201)
def create_dock_appointment(body: DockAppointmentIn, authorization: str | None = Header(None)):
    auth('procure.orders.read', authorization)
    ensure_schema()
    if body.slot_end <= body.slot_start: raise HTTPException(422, 'slot_end_must_be_after_slot_start')
    if not _vendor_for_order(body.order_id): raise HTTPException(404, 'order_not_found')
    if body.asn_id:
        with conn() as c:
            asn = c.execute('SELECT id,order_id FROM procure_asns WHERE id=%s', (body.asn_id,)).fetchone()
            if not asn: raise HTTPException(404, 'asn_not_found')
            if str(asn['order_id']) != body.order_id: raise HTTPException(409, 'asn_order_mismatch')
    with conn() as c:
        overlap = c.execute('''SELECT id FROM procure_dock_appointments WHERE facility_code=%s AND dock_code=%s
            AND status NOT IN ('cancelled','completed') AND slot_start < %s AND slot_end > %s LIMIT 1''',
            (body.facility_code,body.dock_code,body.slot_end,body.slot_start)).fetchone()
        if overlap: raise HTTPException(409, 'dock_slot_conflict')
        ts = now()
        return c.execute('''INSERT INTO procure_dock_appointments(id,order_id,asn_id,facility_code,dock_code,slot_start,slot_end,status,notes,created_at,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,'scheduled',%s,%s,%s) RETURNING *''',
            (str(uuid4()),body.order_id,body.asn_id,body.facility_code.strip(),body.dock_code.strip(),body.slot_start,body.slot_end,body.notes.strip(),ts,ts)).fetchone()


@router.patch('/dock-appointments/{appointment_id}')
def update_dock_appointment(appointment_id: str, body: DockStatusIn, authorization: str | None = Header(None)):
    auth('procure.orders.read', authorization)
    ensure_schema()
    status = body.status.strip().lower().replace(' ','_')
    if status not in {'scheduled','arrived','loading','completed','cancelled','no_show'}: raise HTTPException(422, 'invalid_dock_status')
    with conn() as c:
        row = c.execute('UPDATE procure_dock_appointments SET status=%s,updated_at=%s WHERE id=%s RETURNING *', (status,now(),appointment_id)).fetchone()
        if not row: raise HTTPException(404, 'dock_appointment_not_found')
        return row


@router.get('/dock-appointments')
def list_dock_appointments(facility_code: str | None = None, authorization: str | None = Header(None)):
    auth('procure.orders.read', authorization)
    ensure_schema()
    with conn() as c:
        if facility_code:
            return c.execute('SELECT * FROM procure_dock_appointments WHERE facility_code=%s ORDER BY slot_start', (facility_code,)).fetchall()
        return c.execute('SELECT * FROM procure_dock_appointments ORDER BY slot_start DESC LIMIT 250').fetchall()


@router.get('/summary')
def inbound_summary(authorization: str | None = Header(None)):
    auth('procure.orders.read', authorization)
    ensure_schema()
    with conn() as c:
        return {
            'open_asns': c.execute("SELECT COUNT(*) n FROM procure_asns WHERE status NOT IN ('received','cancelled')").fetchone()['n'],
            'asn_exceptions': c.execute('SELECT COUNT(*) n FROM procure_asns WHERE exception=TRUE').fetchone()['n'],
            'scheduled_docks': c.execute("SELECT COUNT(*) n FROM procure_dock_appointments WHERE status='scheduled'").fetchone()['n'],
            'supplier_score_avg': float(c.execute('SELECT COALESCE(AVG(reliability_score),100) n FROM procure_supplier_metrics').fetchone()['n']),
        }

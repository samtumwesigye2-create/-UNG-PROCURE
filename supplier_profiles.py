from datetime import datetime, timezone
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
try:
    from psycopg.errors import UniqueViolation
except Exception:
    class UniqueViolation(Exception):
        pass

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


class SupplierProfilePatch(BaseModel):
    legal_name: str | None = None
    tax_id: str | None = None
    payment_terms: str | None = None
    currency: str | None = None
    lead_time_days: int | None = None
    status: str | None = None
    purchasing_email: str | None = None
    purchasing_phone: str | None = None
    compliance_status: str | None = None
    payment_reference: str | None = None


def init_supplier_profiles(conn):
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS procure_supplier_profiles(
          vendor_id UUID PRIMARY KEY REFERENCES procure_vendors(id),
          supplier_code TEXT UNIQUE NOT NULL,
          legal_name TEXT NOT NULL,
          tax_id TEXT NULL,
          payment_terms TEXT NOT NULL,
          currency TEXT NOT NULL,
          lead_time_days INTEGER NOT NULL DEFAULT 0 CHECK(lead_time_days>=0),
          status TEXT NOT NULL CHECK(status IN ('approved','blocked','pending_review')),
          purchasing_email TEXT NULL,
          purchasing_phone TEXT NULL,
          compliance_status TEXT NOT NULL,
          payment_reference TEXT NULL,
          created_at TIMESTAMPTZ NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL)""")


def _deps():
    from app import conn, auth
    return conn, auth


@router.get('')
def list_suppliers(authorization: str | None = Header(None)):
    conn, auth = _deps()
    auth('procure.suppliers.read', authorization)
    with conn() as c:
        return c.execute('SELECT * FROM procure_supplier_profiles ORDER BY supplier_code').fetchall()


@router.post('', status_code=201)
def create_supplier(b: SupplierProfileIn, authorization: str | None = Header(None)):
    conn, auth = _deps()
    auth('procure.suppliers.write', authorization)
    try:
        validate_supplier_profile(b.supplier_code, b.currency, b.lead_time_days, b.status)
    except ValueError as e:
        raise HTTPException(422, str(e))
    t = now()
    with conn() as c:
        if not c.execute('SELECT id FROM procure_vendors WHERE id=%s', (b.vendor_id,)).fetchone():
            raise HTTPException(404, 'vendor_not_found')
        try:
            return c.execute("""INSERT INTO procure_supplier_profiles
              (vendor_id,supplier_code,legal_name,tax_id,payment_terms,currency,lead_time_days,status,purchasing_email,purchasing_phone,compliance_status,payment_reference,created_at,updated_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
              (b.vendor_id,b.supplier_code.strip(),b.legal_name,b.tax_id,b.payment_terms,b.currency.upper(),b.lead_time_days,b.status,b.purchasing_email,b.purchasing_phone,b.compliance_status,b.payment_reference,t,t)).fetchone()
        except UniqueViolation:
            raise HTTPException(409, 'supplier_code_exists')


@router.patch('/{supplier_code}')
def patch_supplier(supplier_code: str, b: SupplierProfilePatch, authorization: str | None = Header(None)):
    conn, auth = _deps()
    auth('procure.suppliers.write', authorization)
    updates = b.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, 'no_changes')
    with conn() as c:
        current = c.execute('SELECT * FROM procure_supplier_profiles WHERE supplier_code=%s', (supplier_code,)).fetchone()
        if not current:
            raise HTTPException(404, 'supplier_not_found')
        currency = updates.get('currency', current['currency'])
        lead = updates.get('lead_time_days', current['lead_time_days'])
        status = updates.get('status', current['status'])
        try:
            validate_supplier_profile(supplier_code, currency, lead, status)
        except ValueError as e:
            raise HTTPException(422, str(e))
        allowed = {'legal_name','tax_id','payment_terms','currency','lead_time_days','status','purchasing_email','purchasing_phone','compliance_status','payment_reference'}
        fields=[]
        values=[]
        for k,v in updates.items():
            if k in allowed:
                fields.append(f'{k}=%s')
                values.append(v.upper() if k=='currency' and v else v)
        fields.append('updated_at=%s')
        values.append(now())
        values.append(supplier_code)
        return c.execute(f"UPDATE procure_supplier_profiles SET {','.join(fields)} WHERE supplier_code=%s RETURNING *", values).fetchone()

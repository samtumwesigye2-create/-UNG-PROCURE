import json, os, urllib.request
from datetime import datetime, timezone
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from app import auth, conn
from inbound_layers import ensure_schema

router=APIRouter(prefix='/v1/kpis',tags=['Supply Chain KPIs'])
NOVA_BASE_URL=os.getenv('NOVA_BASE_URL','https://ung-nova-production.up.railway.app').rstrip('/')

def now(): return datetime.now(timezone.utc)

def ensure():
 ensure_schema()
 with conn() as c:
  c.execute('''CREATE TABLE IF NOT EXISTS procure_supplier_governance(vendor_id UUID PRIMARY KEY,responsible_sourcing_assessed BOOLEAN NOT NULL DEFAULT FALSE,critical_supplier BOOLEAN NOT NULL DEFAULT FALSE,risk_review_current BOOLEAN NOT NULL DEFAULT FALSE,updated_at TIMESTAMPTZ NOT NULL)''')
  c.execute('''CREATE TABLE IF NOT EXISTS procure_supplier_quality(vendor_id UUID PRIMARY KEY,receipts_assessed INTEGER NOT NULL DEFAULT 0,defective_receipts INTEGER NOT NULL DEFAULT 0,updated_at TIMESTAMPTZ NOT NULL)''')

class GovernanceIn(BaseModel):
 responsible_sourcing_assessed:bool=False
 critical_supplier:bool=False
 risk_review_current:bool=False
class QualityIn(BaseModel): defective:bool=False

@router.put('/suppliers/{vendor_id}/governance')
def set_governance(vendor_id:str,b:GovernanceIn,authorization:str|None=Header(None)):
 auth('procure.vendors.write',authorization);ensure()
 with conn() as c:
  if not c.execute('SELECT id FROM procure_vendors WHERE id=%s',(vendor_id,)).fetchone():raise HTTPException(404,'vendor_not_found')
  return c.execute('''INSERT INTO procure_supplier_governance(vendor_id,responsible_sourcing_assessed,critical_supplier,risk_review_current,updated_at) VALUES(%s,%s,%s,%s,%s)
   ON CONFLICT(vendor_id) DO UPDATE SET responsible_sourcing_assessed=EXCLUDED.responsible_sourcing_assessed,critical_supplier=EXCLUDED.critical_supplier,risk_review_current=EXCLUDED.risk_review_current,updated_at=EXCLUDED.updated_at RETURNING *''',(vendor_id,b.responsible_sourcing_assessed,b.critical_supplier,b.risk_review_current,now())).fetchone()

@router.post('/suppliers/{vendor_id}/quality')
def record_quality(vendor_id:str,b:QualityIn,authorization:str|None=Header(None)):
 auth('procure.vendors.write',authorization);ensure()
 with conn() as c:
  if not c.execute('SELECT id FROM procure_vendors WHERE id=%s',(vendor_id,)).fetchone():raise HTTPException(404,'vendor_not_found')
  row=c.execute('SELECT * FROM procure_supplier_quality WHERE vendor_id=%s',(vendor_id,)).fetchone(); assessed=(row['receipts_assessed'] if row else 0)+1; defects=(row['defective_receipts'] if row else 0)+(1 if b.defective else 0)
  return c.execute('''INSERT INTO procure_supplier_quality(vendor_id,receipts_assessed,defective_receipts,updated_at) VALUES(%s,%s,%s,%s)
   ON CONFLICT(vendor_id) DO UPDATE SET receipts_assessed=EXCLUDED.receipts_assessed,defective_receipts=EXCLUDED.defective_receipts,updated_at=EXCLUDED.updated_at RETURNING *''',(vendor_id,assessed,defects,now())).fetchone()

def snapshot():
 ensure(); out=[]
 with conn() as c:
  r=c.execute('SELECT COALESCE(SUM(deliveries_total),0) total,COALESCE(SUM(deliveries_on_time),0) ontime FROM procure_supplier_metrics').fetchone()
  if r['total']:out.append(('supplier_on_time_delivery',100*r['ontime']/r['total']))
  q=c.execute('SELECT COALESCE(SUM(receipts_assessed),0) total,COALESCE(SUM(defective_receipts),0) defects FROM procure_supplier_quality').fetchone()
  if q['total']:out.append(('supplier_defect_rate',100*q['defects']/q['total']))
  v=c.execute('SELECT COUNT(*) n FROM procure_vendors').fetchone()['n']; g=c.execute('SELECT COUNT(*) n FROM procure_supplier_governance WHERE responsible_sourcing_assessed=TRUE').fetchone()['n']
  if v:out.append(('responsible_sourcing_coverage',100*g/v))
  cr=c.execute('SELECT COUNT(*) n FROM procure_supplier_governance WHERE critical_supplier=TRUE').fetchone()['n']; rr=c.execute('SELECT COUNT(*) n FROM procure_supplier_governance WHERE critical_supplier=TRUE AND risk_review_current=TRUE').fetchone()['n']
  if cr:out.append(('critical_supplier_risk_coverage',100*rr/cr))
 return out

@router.get('/snapshot')
def kpi_snapshot(authorization:str|None=Header(None)):
 auth('procure.vendors.read',authorization);vals=snapshot();return {'source_system':'UNG-PROCURE','observations':[{'kpi_key':k,'value':round(v,4)} for k,v in vals],'generated_at':now()}

@router.post('/publish')
def publish(authorization:str|None=Header(None)):
 auth('procure.vendors.read',authorization); vals=snapshot()
 body={'observations':[{'kpi_key':k,'value':v,'source_system':'UNG-PROCURE','measured_at':now().isoformat()} for k,v in vals]}
 if not vals:return {'status':'no-source-data','published':0}
 req=urllib.request.Request(NOVA_BASE_URL+'/v1/supply-chain/observations/bulk',data=json.dumps(body).encode(),method='POST',headers={'Content-Type':'application/json','X-UNG-Permissions':'nova.datasets.write','User-Agent':'UNG-PROCURE/1.3'})
 try:
  with urllib.request.urlopen(req,timeout=8) as r:return {'status':'published','published':len(vals),'nova_status':r.status}
 except Exception as e:raise HTTPException(502,f'nova_publish_failed:{type(e).__name__}')

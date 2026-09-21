from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from uuid import uuid4
from datetime import datetime, timezone
import json, os, psycopg, urllib.error, urllib.request
from psycopg.rows import dict_row
from supplier_profiles import init_supplier_profiles
from purchasing_documents import init_purchasing_documents
from procure_matching import init_procure_matching
from demand_intelligence import init_demand_intelligence, classify_demand, forecast_demand, calculate_replenishment
from production_planning import init_planning, install_planning_routes
from manufacturing_operations import init_production, install_production_routes

app=FastAPI(title='UNG-PROCURE',version='1.4.0')
DB=os.getenv('DATABASE_URL','')
JANUS_BASE_URL=os.getenv('JANUS_BASE_URL','https://ung-iam-production.up.railway.app').rstrip('/')
NEXUS_BASE_URL=os.getenv('NEXUS_BASE_URL','https://ung-nexus-production.up.railway.app').rstrip('/')
MIDAS_BASE_URL=os.getenv('MIDAS_BASE_URL','https://ung-midas-production.up.railway.app').rstrip('/')
VECTOR_BASE_URL=os.getenv('VECTOR_BASE_URL','https://ung-vector-production.up.railway.app').rstrip('/')
PROCURE_SERVICE_TOKEN=os.getenv('UNG_PROCURE_SERVICE_TOKEN','').strip()

def conn(): return psycopg.connect(DB,row_factory=dict_row)
def auth(permission,authorization):
    if not authorization or not authorization.lower().startswith('bearer '): raise HTTPException(401,'JANUS bearer token required')
    req=urllib.request.Request(JANUS_BASE_URL+'/v1/auth/introspect',data=b'',method='POST',headers={'Authorization':authorization})
    try:
        with urllib.request.urlopen(req,timeout=5) as r:data=json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401,403): raise HTTPException(401,'JANUS token invalid or expired')
        raise HTTPException(503,'JANUS authorization unavailable')
    except Exception: raise HTTPException(503,'JANUS authorization unavailable')
    principal=data.get('principal') or {}; perms=set(principal.get('permissions') or [])
    if permission not in perms and 'ung.admin' not in perms: raise HTTPException(403,f'Missing JANUS permission: {permission}')
    return principal

def service_authorization():
    return f'Bearer {PROCURE_SERVICE_TOKEN}' if PROCURE_SERVICE_TOKEN else ''

def emit(target,message_type,payload):
    if not NEXUS_BASE_URL:return {'status':'disabled'}
    authorization=service_authorization()
    if not authorization:return {'status':'failed','error':'procure_service_token_missing'}
    body=json.dumps({'source_system':'UNG-PROCURE','target_system':target,'message_type':message_type,'payload':payload}).encode()
    req=urllib.request.Request(NEXUS_BASE_URL+'/v1/messages',data=body,method='POST',headers={'Authorization':authorization,'Content-Type':'application/json','User-Agent':'UNG-PROCURE/1.4.0'})
    try:
        with urllib.request.urlopen(req,timeout=8) as r:return {'status':'delivered','response_code':r.status,'response':json.loads(r.read().decode() or '{}')}
    except urllib.error.HTTPError as e:
        return {'status':'failed','response_code':e.code,'error':f'http_{e.code}'}
    except Exception as e:return {'status':'failed','error':type(e).__name__}

def fetch_vector_material_master(sku):
    authorization=service_authorization()
    if not authorization:
        raise HTTPException(503,'procure_service_token_missing')
    url=VECTOR_BASE_URL+f'/v1/materials/{sku}/master'
    req=urllib.request.Request(url,method='GET',headers={'Authorization':authorization,'User-Agent':'UNG-PROCURE/1.4.0'})
    try:
        with urllib.request.urlopen(req,timeout=8) as r:
            return json.loads(r.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        if e.code==404: return None
        if e.code in (401,403): raise HTTPException(503,'vector_service_identity_rejected')
        raise HTTPException(503,f'vector_material_master_http_{e.code}')
    except Exception as e:
        raise HTTPException(503,f'vector_material_master_unavailable:{type(e).__name__}')

def run_acceptance_probe():
    if not DB:return
    probe_id=str(uuid4()); now=datetime.now(timezone.utc)
    payload={'acceptance_probe_id':probe_id,'order_id':f'ACCEPTANCE-{probe_id[:8]}','request_id':'acceptance-probe','vendor_id':'acceptance-probe','amount':0.0,'currency':'USD','status':'acceptance_test'}
    targets=[('UNG-MIDAS','PROCURE.PURCHASE_ORDER.AWARDED'),('UNG-VECTOR','PROCURE.PURCHASE_ORDER.RECEIVING_EXPECTED')]
    for target,mtype in targets:
        result=emit(target,mtype,payload)
        try:
            with conn() as c:
                c.execute('INSERT INTO procure_acceptance_checks VALUES(%s,%s,%s,%s,%s,%s,%s)',(str(uuid4()),probe_id,target,mtype,result.get('status'),json.dumps(result),now))
        except Exception: pass

@app.on_event('startup')
def init():
    if DB:
        with conn() as c:
            c.execute('CREATE TABLE IF NOT EXISTS procure_requests(id UUID PRIMARY KEY,title TEXT,description TEXT,requester TEXT,status TEXT,priority TEXT,estimated_value DOUBLE PRECISION,currency TEXT,created_at TIMESTAMPTZ,updated_at TIMESTAMPTZ)')
            c.execute('CREATE TABLE IF NOT EXISTS procure_vendors(id UUID PRIMARY KEY,name TEXT,email TEXT,status TEXT,risk_level TEXT,created_at TIMESTAMPTZ)')
            c.execute('CREATE TABLE IF NOT EXISTS procure_bids(id UUID PRIMARY KEY,request_id UUID,vendor_id UUID,amount DOUBLE PRECISION,currency TEXT,score DOUBLE PRECISION,status TEXT,submitted_at TIMESTAMPTZ)')
            c.execute('CREATE TABLE IF NOT EXISTS procure_orders(id UUID PRIMARY KEY,request_id UUID,vendor_id UUID,amount DOUBLE PRECISION,currency TEXT,status TEXT,issued_at TIMESTAMPTZ,updated_at TIMESTAMPTZ)')
            c.execute('CREATE TABLE IF NOT EXISTS procure_integration_events(id UUID PRIMARY KEY,order_id UUID,target_system TEXT,message_type TEXT,status TEXT,response JSONB,created_at TIMESTAMPTZ)')
            c.execute('CREATE TABLE IF NOT EXISTS procure_acceptance_checks(id UUID PRIMARY KEY,probe_id TEXT,target_system TEXT,message_type TEXT,status TEXT,response JSONB,created_at TIMESTAMPTZ)')
        init_supplier_profiles(conn)
        init_purchasing_documents(conn)
        init_procure_matching(conn)
        init_demand_intelligence(conn)
        init_planning(conn)
        init_production(conn)
        run_acceptance_probe()

class RequestIn(BaseModel): title:str; description:str=''; requester:str; priority:str='normal'; estimated_value:float=0; currency:str='USD'
class VendorIn(BaseModel): name:str; email:str; risk_level:str='normal'
class BidIn(BaseModel): request_id:str; vendor_id:str; amount:float; currency:str='USD'; score:float=0
class AwardIn(BaseModel): bid_id:str
class DemandHistoryIn(BaseModel): period_start:datetime; quantity:float
class DemandAnalysisIn(BaseModel):
    on_hand:float=0
    inbound:float=0
    allocated:float=0
    supplier_lead_time_days:float=0
    review_period_days:float=30
    service_level_factor:float=1.65

@app.get('/')
def root(): return {'service':'UNG-PROCURE','status':'online','version':'1.4.0','nexus':NEXUS_BASE_URL,'midas':MIDAS_BASE_URL,'vector':VECTOR_BASE_URL}
@app.get('/health')
def health(): return {'status':'ok','service':'UNG-PROCURE','version':'1.4.0'}
@app.get('/ready')
def ready():
    try:
        with conn() as c:c.execute('SELECT 1')
        return {'status':'ready','database':'connected','janus':JANUS_BASE_URL,'nexus':NEXUS_BASE_URL,'midas':MIDAS_BASE_URL,'vector':VECTOR_BASE_URL,'service_identity_configured':bool(PROCURE_SERVICE_TOKEN)}
    except Exception:return {'status':'degraded','database':'unavailable','janus':JANUS_BASE_URL}
@app.get('/v1/system')
def system(): return {'system_id':'UNG-PROCURE','domain':'procurement','capabilities':['requisitions','vendors','bids','awards','purchase-orders','janus-bearer-auth','procure-service-identity','nexus-events','midas-finance-handoff','vector-receiving-handoff','full-chain-acceptance-probe','supplier-purchasing-profiles','requisition-lines','rfqs','rfq-quotes','purchase-order-lines','delivery-schedules','goods-receipt-projection','idempotent-inbound-events','supplier-invoice-projection','three-way-match','match-tolerances','match-exceptions','demand-classification','baseline-demand-forecasting','replenishment-recommendations','procurement-plans','demand-plans','demand-plan-approval','sop','sop-approval','bom','mps','mps-release','mrp','mrp-lot-sizing','mrp-lead-time-offset','vector-material-master-integration','approved-source-aware-mrp','mrp-planned-orders','planned-buy-to-requisition','planned-make-to-production-order','rfq-source-selection','budget-gated-po-release','acceptance-budget-150','capacity-planning','production-schedule-adherence','production-orders','shop-floor-execution-tracking']}
@app.get('/v1/integration/acceptance')
def acceptance_status():
    try:
        with conn() as c:
            rows=c.execute('SELECT probe_id,target_system,message_type,status,response,created_at FROM procure_acceptance_checks ORDER BY created_at DESC LIMIT 2').fetchall()
        passed=len(rows)==2 and all(r['status']=='delivered' for r in rows)
        return {'service':'UNG-PROCURE','status':'passed' if passed else 'failed','checks':rows}
    except Exception as e: raise HTTPException(503,f'acceptance_status_unavailable:{type(e).__name__}')
@app.get('/v1/requests')
def list_requests(authorization:str|None=Header(None)):
    auth('procure.requests.read',authorization)
    with conn() as c:return c.execute('SELECT * FROM procure_requests ORDER BY created_at DESC').fetchall()
@app.post('/v1/requests',status_code=201)
def create_request(b:RequestIn,authorization:str|None=Header(None)):
    auth('procure.requests.write',authorization); now=datetime.now(timezone.utc)
    with conn() as c:return c.execute('INSERT INTO procure_requests VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(str(uuid4()),b.title,b.description,b.requester,'draft',b.priority,b.estimated_value,b.currency,now,now)).fetchone()
@app.post('/v1/requests/{request_id}/submit')
def submit_request(request_id:str,authorization:str|None=Header(None)):
    auth('procure.requests.write',authorization)
    with conn() as c:
        row=c.execute("UPDATE procure_requests SET status='submitted',updated_at=%s WHERE id=%s RETURNING *",(datetime.now(timezone.utc),request_id)).fetchone()
        if not row: raise HTTPException(404,'request_not_found')
        return row
@app.post('/v1/vendors',status_code=201)
def create_vendor(b:VendorIn,authorization:str|None=Header(None)):
    auth('procure.vendors.write',authorization)
    with conn() as c:return c.execute('INSERT INTO procure_vendors VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',(str(uuid4()),b.name,b.email,'active',b.risk_level,datetime.now(timezone.utc))).fetchone()
@app.get('/v1/vendors')
def list_vendors(authorization:str|None=Header(None)):
    auth('procure.vendors.read',authorization)
    with conn() as c:return c.execute('SELECT * FROM procure_vendors ORDER BY created_at DESC').fetchall()
@app.post('/v1/bids',status_code=201)
def create_bid(b:BidIn,authorization:str|None=Header(None)):
    auth('procure.bids.write',authorization)
    if b.score<0 or b.score>100: raise HTTPException(422,'score_must_be_0_to_100')
    with conn() as c:
        if not c.execute('SELECT id FROM procure_requests WHERE id=%s',(b.request_id,)).fetchone(): raise HTTPException(404,'request_not_found')
        if not c.execute('SELECT id FROM procure_vendors WHERE id=%s',(b.vendor_id,)).fetchone(): raise HTTPException(404,'vendor_not_found')
        return c.execute('INSERT INTO procure_bids VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(str(uuid4()),b.request_id,b.vendor_id,b.amount,b.currency,b.score,'submitted',datetime.now(timezone.utc))).fetchone()
@app.get('/v1/bids/{request_id}')
def list_bids(request_id:str,authorization:str|None=Header(None)):
    auth('procure.bids.read',authorization)
    with conn() as c:return c.execute('SELECT * FROM procure_bids WHERE request_id=%s ORDER BY score DESC, amount ASC',(request_id,)).fetchall()
@app.post('/v1/awards')
def award(b:AwardIn,authorization:str|None=Header(None)):
    auth('procure.awards.write',authorization); now=datetime.now(timezone.utc)
    with conn() as c:
        bid=c.execute('SELECT * FROM procure_bids WHERE id=%s',(b.bid_id,)).fetchone()
        if not bid: raise HTTPException(404,'bid_not_found')
        c.execute("UPDATE procure_bids SET status='awarded' WHERE id=%s",(b.bid_id,))
        c.execute("UPDATE procure_requests SET status='awarded',updated_at=%s WHERE id=%s",(now,bid['request_id']))
        order=c.execute("""INSERT INTO procure_orders
          (id,request_id,vendor_id,amount,currency,status,issued_at,updated_at,budget_scope,released_at)
          VALUES(%s,%s,%s,%s,%s,'draft',%s,%s,NULL,NULL) RETURNING *""",
          (str(uuid4()),bid['request_id'],bid['vendor_id'],bid['amount'],bid['currency'],now,now)).fetchone()
    return {'order':order,'status':'draft_pending_release'}
@app.get('/v1/orders')
def orders(authorization:str|None=Header(None)):
    auth('procure.orders.read',authorization)
    with conn() as c:return c.execute('SELECT * FROM procure_orders ORDER BY issued_at DESC').fetchall()
@app.get('/v1/integration/events')
def integration_events(authorization:str|None=Header(None)):
    auth('procure.orders.read',authorization)
    with conn() as c:return c.execute('SELECT * FROM procure_integration_events ORDER BY created_at DESC LIMIT 100').fetchall()
@app.get('/v1/summary')
def summary(authorization:str|None=Header(None)):
    auth('procure.requests.read',authorization)
    with conn() as c:return {'requests':c.execute('SELECT COUNT(*) n FROM procure_requests').fetchone()['n'],'vendors':c.execute('SELECT COUNT(*) n FROM procure_vendors').fetchone()['n'],'bids':c.execute('SELECT COUNT(*) n FROM procure_bids').fetchone()['n'],'orders':c.execute('SELECT COUNT(*) n FROM procure_orders').fetchone()['n']}

@app.post('/v1/demand/history',status_code=201)
def add_demand_history(b:DemandHistoryIn,sku:str,authorization:str|None=Header(None)):
    auth('procure.demand.write',authorization)
    if b.quantity < 0: raise HTTPException(422,'demand_must_be_non_negative')
    now=datetime.now(timezone.utc)
    with conn() as c:
        return c.execute('INSERT INTO demand_history(id,sku,period_start,quantity,created_at) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(sku,period_start) DO UPDATE SET quantity=EXCLUDED.quantity,created_at=EXCLUDED.created_at RETURNING *',(str(uuid4()),sku,b.period_start,b.quantity,now)).fetchone()

@app.get('/v1/demand/history/{sku}')
def get_demand_history(sku:str,authorization:str|None=Header(None)):
    auth('procure.demand.read',authorization)
    with conn() as c:return c.execute('SELECT * FROM demand_history WHERE sku=%s ORDER BY period_start ASC',(sku,)).fetchall()

@app.post('/v1/demand/analyze/{sku}')
def analyze_demand(sku:str,b:DemandAnalysisIn,authorization:str|None=Header(None)):
    auth('procure.demand.write',authorization)
    if min(b.on_hand,b.inbound,b.allocated,b.supplier_lead_time_days,b.service_level_factor) < 0 or b.review_period_days <= 0:
        raise HTTPException(422,'invalid_replenishment_inputs')
    with conn() as c:
        rows=c.execute('SELECT quantity FROM demand_history WHERE sku=%s ORDER BY period_start ASC',(sku,)).fetchall()
    if not rows: raise HTTPException(404,'demand_history_not_found')
    history=[float(r['quantity']) for r in rows]
    classification=classify_demand(history)
    forecast=forecast_demand(history,classification['demand_class'])
    replenishment=calculate_replenishment(history,forecast['forecast_quantity'],b.on_hand,b.inbound,b.allocated,b.supplier_lead_time_days,b.review_period_days,b.service_level_factor)
    now=datetime.now(timezone.utc); recommendation_id=str(uuid4())
    payload={'recommendation_id':recommendation_id,'sku':sku,**classification,**forecast,**replenishment,'on_hand':b.on_hand,'inbound':b.inbound,'allocated':b.allocated,'supplier_lead_time_days':b.supplier_lead_time_days}
    with conn() as c:
        c.execute('INSERT INTO demand_recommendations(id,sku,demand_class,adi,cv2,forecast_quantity,forecast_model,mae,wape,mape,wmape,forecast_bias,on_hand,inbound,allocated,supplier_lead_time_days,safety_stock,reorder_point,recommended_order_quantity,stockout_risk,status,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(recommendation_id,sku,classification['demand_class'],classification['adi'],classification['cv2'],forecast['forecast_quantity'],forecast['forecast_model'],forecast['mae'],forecast['wape'],forecast['mape'],forecast['wmape'],forecast['forecast_bias'],b.on_hand,b.inbound,b.allocated,b.supplier_lead_time_days,replenishment['safety_stock'],replenishment['reorder_point'],replenishment['recommended_order_quantity'],replenishment['stockout_risk'],replenishment['status'],now))
    integration={'status':'not_required'}
    if replenishment['status']=='reorder':
        integration=emit('UNG-VECTOR','PROCURE.REPLENISHMENT.RECOMMENDED',payload)
    return {'recommendation':payload,'integration':integration}

@app.get('/v1/demand/recommendations')
def demand_recommendations(authorization:str|None=Header(None)):
    auth('procure.demand.read',authorization)
    with conn() as c:return c.execute('SELECT * FROM demand_recommendations ORDER BY created_at DESC LIMIT 100').fetchall()


install_planning_routes(app, conn, auth, emit, fetch_vector_material_master)

install_production_routes(app, conn, auth)

from datetime import date, datetime, timezone
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router=APIRouter(prefix="/v1/strategic-sourcing",tags=["Strategic Sourcing, Contracts and TCO"])

def now(): return datetime.now(timezone.utc)

SOURCING_MODES={"strategic","transactional","global","local","single","multiple"}
RFP_STATUSES={"draft","open","evaluation","awarded","closed","cancelled"}

class SourcingStrategyIn(BaseModel):
    name:str
    mode:str
    category:str
    rationale:str=""
    target_supplier_count:int=Field(default=1,ge=1)
    local_preference:bool=False
    global_allowed:bool=True
    risk_tolerance:str="medium"

class RFPIn(BaseModel):
    code:str
    title:str
    description:str=""
    category:str="general"
    due_date:date|None=None
    sourcing_strategy_id:str|None=None

class RFPInviteIn(BaseModel):
    vendor_id:str

class RFPEvaluationIn(BaseModel):
    vendor_id:str
    cost:float=Field(ge=0,le=100)
    quality:float=Field(ge=0,le=100)
    delivery:float=Field(ge=0,le=100)
    capability:float=Field(ge=0,le=100)
    sustainability:float=Field(ge=0,le=100)
    relationship:float=Field(ge=0,le=100)
    note:str=""

class ContractIn(BaseModel):
    vendor_id:str
    contract_code:str
    title:str
    start_date:date
    end_date:date|None=None
    currency:str="USD"
    ceiling_value:float=Field(default=0,ge=0)
    payment_terms:str="NET30"
    sla_terms:str=""
    renewal_notice_days:int=Field(default=30,ge=0)
    owner:str=""

class ContractAmendmentIn(BaseModel):
    summary:str
    value_delta:float=0
    effective_date:date|None=None

class ContractReviewIn(BaseModel):
    sla_attainment_pct:float=Field(ge=0,le=100)
    service_quality_pct:float=Field(ge=0,le=100)
    relationship_score:float=Field(ge=0,le=100)
    innovation_score:float=Field(ge=0,le=100)
    note:str=""

class TCOIn(BaseModel):
    vendor_id:str
    sku:str
    quantity:float=Field(gt=0)
    unit_price:float=Field(ge=0)
    freight:float=Field(default=0,ge=0)
    duties:float=Field(default=0,ge=0)
    handling:float=Field(default=0,ge=0)
    service_lifecycle:float=Field(default=0,ge=0)
    quality_cost:float=Field(default=0,ge=0)
    financing_cost:float=Field(default=0,ge=0)
    currency:str="USD"

def weighted_supplier_score(b:RFPEvaluationIn):
    weights={"cost":0.25,"quality":0.20,"delivery":0.15,"capability":0.15,"sustainability":0.10,"relationship":0.15}
    return round(sum(getattr(b,k)*w for k,w in weights.items()),2)

def init_strategic_sourcing(conn):
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS procure_sourcing_strategies(
          id UUID PRIMARY KEY,name TEXT NOT NULL,mode TEXT NOT NULL,category TEXT NOT NULL,
          rationale TEXT NOT NULL,target_supplier_count INTEGER NOT NULL,local_preference BOOLEAN NOT NULL,
          global_allowed BOOLEAN NOT NULL,risk_tolerance TEXT NOT NULL,status TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_rfps(
          id UUID PRIMARY KEY,code TEXT UNIQUE NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,
          category TEXT NOT NULL,due_date DATE NULL,sourcing_strategy_id UUID NULL,
          status TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL,awarded_vendor_id UUID NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_rfp_vendors(
          rfp_id UUID NOT NULL REFERENCES procure_rfps(id) ON DELETE CASCADE,
          vendor_id UUID NOT NULL REFERENCES procure_vendors(id),PRIMARY KEY(rfp_id,vendor_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_supplier_evaluations(
          id UUID PRIMARY KEY,rfp_id UUID NOT NULL REFERENCES procure_rfps(id) ON DELETE CASCADE,
          vendor_id UUID NOT NULL REFERENCES procure_vendors(id),cost NUMERIC NOT NULL,quality NUMERIC NOT NULL,
          delivery NUMERIC NOT NULL,capability NUMERIC NOT NULL,sustainability NUMERIC NOT NULL,
          relationship NUMERIC NOT NULL,total_score NUMERIC NOT NULL,note TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL,UNIQUE(rfp_id,vendor_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_contracts(
          id UUID PRIMARY KEY,vendor_id UUID NOT NULL REFERENCES procure_vendors(id),contract_code TEXT UNIQUE NOT NULL,
          title TEXT NOT NULL,start_date DATE NOT NULL,end_date DATE NULL,currency TEXT NOT NULL,
          ceiling_value NUMERIC NOT NULL,payment_terms TEXT NOT NULL,sla_terms TEXT NOT NULL,
          renewal_notice_days INTEGER NOT NULL,owner TEXT NOT NULL,status TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL,updated_at TIMESTAMPTZ NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_contract_amendments(
          id UUID PRIMARY KEY,contract_id UUID NOT NULL REFERENCES procure_contracts(id) ON DELETE CASCADE,
          amendment_no INTEGER NOT NULL,summary TEXT NOT NULL,value_delta NUMERIC NOT NULL,
          effective_date DATE NULL,created_at TIMESTAMPTZ NOT NULL,UNIQUE(contract_id,amendment_no))""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_supplier_relationship_reviews(
          id UUID PRIMARY KEY,vendor_id UUID NOT NULL REFERENCES procure_vendors(id),contract_id UUID NULL,
          sla_attainment_pct NUMERIC NOT NULL,service_quality_pct NUMERIC NOT NULL,
          relationship_score NUMERIC NOT NULL,innovation_score NUMERIC NOT NULL,note TEXT NOT NULL,
          reviewed_at TIMESTAMPTZ NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_tco_assessments(
          id UUID PRIMARY KEY,vendor_id UUID NOT NULL REFERENCES procure_vendors(id),sku TEXT NOT NULL,
          quantity NUMERIC NOT NULL,unit_price NUMERIC NOT NULL,freight NUMERIC NOT NULL,duties NUMERIC NOT NULL,
          handling NUMERIC NOT NULL,service_lifecycle NUMERIC NOT NULL,quality_cost NUMERIC NOT NULL,
          financing_cost NUMERIC NOT NULL,total_cost NUMERIC NOT NULL,cost_per_unit NUMERIC NOT NULL,
          currency TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL)""")

def install_strategic_sourcing_routes(app,conn,auth):
    @router.post("/strategies",status_code=201)
    def create_strategy(b:SourcingStrategyIn,authorization:str|None=Header(None)):
        auth("procure.vendors.write",authorization)
        mode=b.mode.lower()
        if mode not in SOURCING_MODES: raise HTTPException(422,"unsupported_sourcing_mode")
        with conn() as c:
            return c.execute("""INSERT INTO procure_sourcing_strategies VALUES(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s) RETURNING *""",
              (str(uuid4()),b.name,mode,b.category,b.rationale,b.target_supplier_count,b.local_preference,
               b.global_allowed,b.risk_tolerance,now())).fetchone()

    @router.get("/strategies")
    def list_strategies(authorization:str|None=Header(None)):
        auth("procure.vendors.read",authorization)
        with conn() as c:return c.execute("SELECT * FROM procure_sourcing_strategies ORDER BY created_at DESC").fetchall()

    @router.post("/rfps",status_code=201)
    def create_rfp(b:RFPIn,authorization:str|None=Header(None)):
        auth("procure.bids.write",authorization)
        with conn() as c:
            return c.execute("""INSERT INTO procure_rfps VALUES(
              %s,%s,%s,%s,%s,%s,%s,'open',%s,NULL) RETURNING *""",
              (str(uuid4()),b.code,b.title,b.description,b.category,b.due_date,b.sourcing_strategy_id,now())).fetchone()

    @router.post("/rfps/{rfp_id}/invite",status_code=201)
    def invite_rfp(rfp_id:str,b:RFPInviteIn,authorization:str|None=Header(None)):
        auth("procure.bids.write",authorization)
        with conn() as c:
            if not c.execute("SELECT 1 FROM procure_rfps WHERE id=%s",(rfp_id,)).fetchone(): raise HTTPException(404,"rfp_not_found")
            if not c.execute("SELECT 1 FROM procure_vendors WHERE id=%s",(b.vendor_id,)).fetchone(): raise HTTPException(404,"vendor_not_found")
            c.execute("INSERT INTO procure_rfp_vendors VALUES(%s,%s) ON CONFLICT DO NOTHING",(rfp_id,b.vendor_id))
        return {"rfp_id":rfp_id,"vendor_id":b.vendor_id,"status":"invited"}

    @router.post("/rfps/{rfp_id}/evaluate")
    def evaluate_rfp(rfp_id:str,b:RFPEvaluationIn,authorization:str|None=Header(None)):
        auth("procure.awards.write",authorization); score=weighted_supplier_score(b)
        with conn() as c:
            if not c.execute("SELECT 1 FROM procure_rfp_vendors WHERE rfp_id=%s AND vendor_id=%s",(rfp_id,b.vendor_id)).fetchone():
                raise HTTPException(409,"vendor_not_invited")
            row=c.execute("""INSERT INTO procure_supplier_evaluations(
              id,rfp_id,vendor_id,cost,quality,delivery,capability,sustainability,relationship,total_score,note,created_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(rfp_id,vendor_id) DO UPDATE SET cost=EXCLUDED.cost,quality=EXCLUDED.quality,
              delivery=EXCLUDED.delivery,capability=EXCLUDED.capability,sustainability=EXCLUDED.sustainability,
              relationship=EXCLUDED.relationship,total_score=EXCLUDED.total_score,note=EXCLUDED.note,
              created_at=EXCLUDED.created_at RETURNING *""",
              (str(uuid4()),rfp_id,b.vendor_id,b.cost,b.quality,b.delivery,b.capability,b.sustainability,
               b.relationship,score,b.note,now())).fetchone()
            c.execute("UPDATE procure_rfps SET status='evaluation' WHERE id=%s",(rfp_id,))
        return row

    @router.get("/rfps/{rfp_id}/ranking")
    def rfp_ranking(rfp_id:str,authorization:str|None=Header(None)):
        auth("procure.bids.read",authorization)
        with conn() as c:return c.execute("""SELECT e.*,v.name vendor_name FROM procure_supplier_evaluations e
          JOIN procure_vendors v ON v.id=e.vendor_id WHERE e.rfp_id=%s
          ORDER BY e.total_score DESC""",(rfp_id,)).fetchall()

    @router.get("/contracts")
    def list_contracts(status:str|None=None,authorization:str|None=Header(None)):
        auth("procure.orders.read",authorization)
        with conn() as c:
            if status:
                return c.execute("""SELECT * FROM procure_contracts
                  WHERE status=%s ORDER BY end_date NULLS LAST,created_at DESC LIMIT 500""",(status,)).fetchall()
            return c.execute("""SELECT * FROM procure_contracts
              ORDER BY end_date NULLS LAST,created_at DESC LIMIT 500""").fetchall()

    @router.post("/contracts",status_code=201)
    def create_contract(b:ContractIn,authorization:str|None=Header(None)):
        auth("procure.orders.write",authorization)
        if b.end_date and b.end_date<b.start_date: raise HTTPException(422,"end_before_start")
        t=now()
        with conn() as c:return c.execute("""INSERT INTO procure_contracts VALUES(
          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s) RETURNING *""",
          (str(uuid4()),b.vendor_id,b.contract_code,b.title,b.start_date,b.end_date,b.currency.upper(),
           b.ceiling_value,b.payment_terms,b.sla_terms,b.renewal_notice_days,b.owner,t,t)).fetchone()

    @router.post("/contracts/{contract_id}/amendments",status_code=201)
    def amend_contract(contract_id:str,b:ContractAmendmentIn,authorization:str|None=Header(None)):
        auth("procure.orders.write",authorization)
        with conn() as c:
            contract=c.execute("SELECT * FROM procure_contracts WHERE id=%s FOR UPDATE",(contract_id,)).fetchone()
            if not contract: raise HTTPException(404,"contract_not_found")
            n=c.execute("SELECT COUNT(*) n FROM procure_contract_amendments WHERE contract_id=%s",(contract_id,)).fetchone()["n"]+1
            row=c.execute("""INSERT INTO procure_contract_amendments VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
              (str(uuid4()),contract_id,n,b.summary,b.value_delta,b.effective_date,now())).fetchone()
            c.execute("UPDATE procure_contracts SET ceiling_value=ceiling_value+%s,updated_at=%s WHERE id=%s",(b.value_delta,now(),contract_id))
        return row

    @router.get("/contracts/renewals")
    def renewal_watch(days:int=60,authorization:str|None=Header(None)):
        auth("procure.orders.read",authorization)
        if days<0 or days>3650: raise HTTPException(422,"days_out_of_range")
        with conn() as c:return c.execute("""SELECT * FROM procure_contracts WHERE status='active'
          AND end_date IS NOT NULL AND end_date<=CURRENT_DATE+(%s * INTERVAL '1 day')
          ORDER BY end_date""",(days,)).fetchall()

    @router.post("/contracts/{contract_id}/review")
    def contract_review(contract_id:str,b:ContractReviewIn,authorization:str|None=Header(None)):
        auth("procure.vendors.write",authorization)
        with conn() as c:
            ct=c.execute("SELECT * FROM procure_contracts WHERE id=%s",(contract_id,)).fetchone()
            if not ct: raise HTTPException(404,"contract_not_found")
            return c.execute("""INSERT INTO procure_supplier_relationship_reviews VALUES(
              %s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
              (str(uuid4()),ct["vendor_id"],contract_id,b.sla_attainment_pct,b.service_quality_pct,
               b.relationship_score,b.innovation_score,b.note,now())).fetchone()

    @router.post("/tco",status_code=201)
    def calculate_tco(b:TCOIn,authorization:str|None=Header(None)):
        auth("procure.bids.read",authorization)
        total=b.quantity*b.unit_price+b.freight+b.duties+b.handling+b.service_lifecycle+b.quality_cost+b.financing_cost
        cpu=total/b.quantity
        with conn() as c:
            row=c.execute("""INSERT INTO procure_tco_assessments VALUES(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
              (str(uuid4()),b.vendor_id,b.sku,b.quantity,b.unit_price,b.freight,b.duties,b.handling,
               b.service_lifecycle,b.quality_cost,b.financing_cost,total,cpu,b.currency.upper(),now())).fetchone()
        return {"assessment":row,"total_cost":round(total,2),"cost_per_unit":round(cpu,4)}

    app.include_router(router)

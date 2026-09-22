from datetime import date, datetime, timezone
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router=APIRouter(prefix="/v1/supply-planning",tags=["Demand and Supply Planning"])

def now(): return datetime.now(timezone.utc)

SIGNAL_TYPES={"customer_order","sales_forecast","historical_demand","market_trend","promotion","new_product_launch","customer_commitment"}

class DemandSignalIn(BaseModel):
    sku:str
    signal_type:str
    period_start:date
    quantity:float=Field(ge=0)
    confidence_pct:float=Field(default=100,ge=0,le=100)
    source_ref:str=""
    note:str=""

class SupplierCapacityIn(BaseModel):
    supplier_code:str
    sku:str
    period_start:date
    period_end:date
    maximum_units:float=Field(ge=0)
    committed_units:float=Field(default=0,ge=0)
    minimum_order_qty:float=Field(default=0,ge=0)
    lead_time_days:int=Field(default=0,ge=0)

class SupplyPlanIn(BaseModel):
    sku:str
    period_start:date
    period_end:date
    demand_units:float=Field(ge=0)
    on_hand_units:float=Field(default=0,ge=0)
    inbound_units:float=Field(default=0,ge=0)
    safety_stock_units:float=Field(default=0,ge=0)
    production_units:float=Field(default=0,ge=0)
    procurement_units:float=Field(default=0,ge=0)
    logistics_capacity_units:float=Field(default=0,ge=0)
    total_cost:float=Field(default=0,ge=0)
    risk_score:float=Field(default=0,ge=0,le=100)
    owner:str=""

class SupplyPlanDecision(BaseModel):
    note:str=""

def init_supply_planning(conn):
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS procure_demand_signals(
          id UUID PRIMARY KEY,sku TEXT NOT NULL,signal_type TEXT NOT NULL,period_start DATE NOT NULL,
          quantity NUMERIC NOT NULL,confidence_pct NUMERIC NOT NULL,source_ref TEXT NOT NULL,note TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_supplier_capacity(
          id UUID PRIMARY KEY,supplier_code TEXT NOT NULL,sku TEXT NOT NULL,period_start DATE NOT NULL,
          period_end DATE NOT NULL,maximum_units NUMERIC NOT NULL,committed_units NUMERIC NOT NULL,
          minimum_order_qty NUMERIC NOT NULL,lead_time_days INTEGER NOT NULL,
          created_at TIMESTAMPTZ NOT NULL,UNIQUE(supplier_code,sku,period_start,period_end))""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_supply_plans(
          id UUID PRIMARY KEY,sku TEXT NOT NULL,period_start DATE NOT NULL,period_end DATE NOT NULL,
          demand_units NUMERIC NOT NULL,on_hand_units NUMERIC NOT NULL,inbound_units NUMERIC NOT NULL,
          safety_stock_units NUMERIC NOT NULL,production_units NUMERIC NOT NULL,procurement_units NUMERIC NOT NULL,
          logistics_capacity_units NUMERIC NOT NULL,total_cost NUMERIC NOT NULL,risk_score NUMERIC NOT NULL,
          owner TEXT NOT NULL,status TEXT NOT NULL,approved_at TIMESTAMPTZ NULL,created_at TIMESTAMPTZ NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_supply_plan_events(
          id UUID PRIMARY KEY,supply_plan_id UUID NOT NULL REFERENCES procure_supply_plans(id) ON DELETE CASCADE,
          event_type TEXT NOT NULL,note TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL)""")

def install_supply_planning_routes(app,conn,auth,emit):
    @router.post("/demand-signals",status_code=201)
    def add_signal(b:DemandSignalIn,authorization:str|None=Header(None)):
        auth("procure.demand.write",authorization)
        typ=b.signal_type.lower()
        if typ not in SIGNAL_TYPES: raise HTTPException(422,"unsupported_demand_signal_type")
        with conn() as c:return c.execute("""INSERT INTO procure_demand_signals VALUES(
          %s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
          (str(uuid4()),b.sku,typ,b.period_start,b.quantity,b.confidence_pct,b.source_ref,b.note,now())).fetchone()

    @router.get("/demand-signals/{sku}")
    def demand_signals(sku:str,authorization:str|None=Header(None)):
        auth("procure.demand.read",authorization)
        with conn() as c:return c.execute("""SELECT * FROM procure_demand_signals WHERE sku=%s
          ORDER BY period_start,created_at""",(sku,)).fetchall()

    @router.get("/demand-signals/{sku}/consensus")
    def demand_consensus(sku:str,authorization:str|None=Header(None)):
        auth("procure.demand.read",authorization)
        with conn() as c:
            rows=c.execute("""SELECT signal_type,quantity,confidence_pct FROM procure_demand_signals
              WHERE sku=%s""",(sku,)).fetchall()
        if not rows: raise HTTPException(404,"demand_signals_not_found")
        weighted=sum(float(r["quantity"])*float(r["confidence_pct"])/100 for r in rows)
        weights=sum(float(r["confidence_pct"])/100 for r in rows)
        consensus=weighted/weights if weights else 0
        by_type={}
        for r in rows: by_type.setdefault(r["signal_type"],0.0); by_type[r["signal_type"]]+=float(r["quantity"])
        return {"sku":sku,"consensus_units":round(consensus,2),"signals":len(rows),"by_type":by_type,"generated_at":now()}

    @router.put("/supplier-capacity")
    def set_supplier_capacity(b:SupplierCapacityIn,authorization:str|None=Header(None)):
        auth("procure.vendors.write",authorization)
        if b.period_end<b.period_start: raise HTTPException(422,"period_end_before_start")
        if b.committed_units>b.maximum_units: raise HTTPException(422,"committed_exceeds_maximum")
        with conn() as c:return c.execute("""INSERT INTO procure_supplier_capacity VALUES(
          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT(supplier_code,sku,period_start,period_end) DO UPDATE SET
          maximum_units=EXCLUDED.maximum_units,committed_units=EXCLUDED.committed_units,
          minimum_order_qty=EXCLUDED.minimum_order_qty,lead_time_days=EXCLUDED.lead_time_days,
          created_at=EXCLUDED.created_at RETURNING *""",
          (str(uuid4()),b.supplier_code,b.sku,b.period_start,b.period_end,b.maximum_units,b.committed_units,
           b.minimum_order_qty,b.lead_time_days,now())).fetchone()

    @router.get("/supplier-capacity/{sku}")
    def supplier_capacity(sku:str,authorization:str|None=Header(None)):
        auth("procure.vendors.read",authorization)
        with conn() as c:
            rows=c.execute("""SELECT *,maximum_units-committed_units available_units FROM procure_supplier_capacity
              WHERE sku=%s ORDER BY period_start,supplier_code""",(sku,)).fetchall()
        total=sum(float(r["available_units"]) for r in rows)
        return {"sku":sku,"available_supplier_capacity":round(total,2),"suppliers":rows}

    @router.get("/plans")
    def list_supply_plans(status:str|None=None,authorization:str|None=Header(None)):
        auth("procure.production.read",authorization)
        with conn() as c:
            if status:
                return c.execute("""SELECT * FROM procure_supply_plans
                  WHERE status=%s ORDER BY period_start DESC,created_at DESC LIMIT 500""",(status,)).fetchall()
            return c.execute("""SELECT * FROM procure_supply_plans
              ORDER BY period_start DESC,created_at DESC LIMIT 500""").fetchall()

    @router.post("/plans",status_code=201)
    def create_supply_plan(b:SupplyPlanIn,authorization:str|None=Header(None)):
        auth("procure.production.write",authorization)
        if b.period_end<b.period_start: raise HTTPException(422,"period_end_before_start")
        required=max(0.0,b.demand_units+b.safety_stock_units-b.on_hand_units-b.inbound_units)
        planned=b.production_units+b.procurement_units
        effective=min(planned,b.logistics_capacity_units) if b.logistics_capacity_units>0 else planned
        gap=max(0.0,required-effective)
        status="balanced" if gap==0 else "gap"
        t=now()
        with conn() as c:
            row=c.execute("""INSERT INTO procure_supply_plans VALUES(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft',NULL,%s,%s) RETURNING *""",
              (str(uuid4()),b.sku,b.period_start,b.period_end,b.demand_units,b.on_hand_units,b.inbound_units,
               b.safety_stock_units,b.production_units,b.procurement_units,b.logistics_capacity_units,b.total_cost,
               b.risk_score,b.owner,t,t)).fetchone()
        return {"plan":row,"balance":{"required_supply_units":round(required,2),"planned_effective_units":round(effective,2),
          "gap_units":round(gap,2),"status":status}}

    @router.post("/plans/{plan_id}/approve")
    def approve_plan(plan_id:str,b:SupplyPlanDecision,authorization:str|None=Header(None)):
        auth("procure.production.write",authorization); t=now()
        with conn() as c:
            plan=c.execute("SELECT * FROM procure_supply_plans WHERE id=%s FOR UPDATE",(plan_id,)).fetchone()
            if not plan: raise HTTPException(404,"supply_plan_not_found")
            required=max(0.0,float(plan["demand_units"])+float(plan["safety_stock_units"])-float(plan["on_hand_units"])-float(plan["inbound_units"]))
            planned=float(plan["production_units"])+float(plan["procurement_units"])
            effective=min(planned,float(plan["logistics_capacity_units"])) if float(plan["logistics_capacity_units"])>0 else planned
            if effective<required: raise HTTPException(409,"supply_plan_has_unresolved_gap")
            row=c.execute("""UPDATE procure_supply_plans SET status='approved',approved_at=%s,updated_at=%s
              WHERE id=%s RETURNING *""",(t,t,plan_id)).fetchone()
            c.execute("INSERT INTO procure_supply_plan_events VALUES(%s,%s,'approved',%s,%s)",
              (str(uuid4()),plan_id,b.note,t))
        delivery=emit("UNG-VECTOR","PROCURE.SUPPLY_PLAN.APPROVED",{"supply_plan_id":plan_id,"sku":row["sku"],
          "period_start":str(row["period_start"]),"period_end":str(row["period_end"]),
          "production_units":float(row["production_units"]),"procurement_units":float(row["procurement_units"])})
        return {"plan":row,"integration_delivery":delivery}

    @router.post("/plans/{plan_id}/execute")
    def execute_plan(plan_id:str,b:SupplyPlanDecision,authorization:str|None=Header(None)):
        auth("procure.production.write",authorization); t=now()
        with conn() as c:
            row=c.execute("""UPDATE procure_supply_plans SET status='executing',updated_at=%s
              WHERE id=%s AND status='approved' RETURNING *""",(t,plan_id)).fetchone()
            if not row: raise HTTPException(409,"supply_plan_not_approved")
            c.execute("INSERT INTO procure_supply_plan_events VALUES(%s,%s,'execution_started',%s,%s)",
              (str(uuid4()),plan_id,b.note,t))
        return row

    @router.get("/plans/{plan_id}")
    def get_plan(plan_id:str,authorization:str|None=Header(None)):
        auth("procure.production.read",authorization)
        with conn() as c:
            plan=c.execute("SELECT * FROM procure_supply_plans WHERE id=%s",(plan_id,)).fetchone()
            if not plan: raise HTTPException(404,"supply_plan_not_found")
            events=c.execute("SELECT * FROM procure_supply_plan_events WHERE supply_plan_id=%s ORDER BY created_at",(plan_id,)).fetchall()
        return {"plan":plan,"events":events}

    app.include_router(router)

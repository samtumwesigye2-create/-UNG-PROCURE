from datetime import datetime, timezone
from math import ceil
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1/planning", tags=["S&OP, MPS, MRP and Procurement Planning"])

def now():
    return datetime.now(timezone.utc)

class BomItemIn(BaseModel):
    component_sku: str
    quantity_per: float = Field(gt=0)
    scrap_pct: float = Field(default=0, ge=0, le=100)

class BomIn(BaseModel):
    product_sku: str
    version: str = "1"
    lot_size: float = Field(default=1, gt=0)
    items: list[BomItemIn]

class CapacityIn(BaseModel):
    work_center: str
    period_start: datetime
    available_hours: float = Field(ge=0)
    efficiency_pct: float = Field(default=100, gt=0, le=200)

class SopIn(BaseModel):
    period_start: datetime
    product_family: str
    demand_qty: float = Field(ge=0)
    supply_qty: float = Field(ge=0)
    inventory_target: float = Field(default=0, ge=0)
    status: str = "draft"

class MpsIn(BaseModel):
    product_sku: str
    period_start: datetime
    planned_qty: float = Field(gt=0)
    work_center: str | None = None
    hours_per_unit: float = Field(default=0, ge=0)
    source: str = "sop"

class MrpRunIn(BaseModel):
    product_sku: str
    period_start: datetime
    planned_qty: float = Field(gt=0)
    on_hand: dict[str, float] = {}
    scheduled_receipts: dict[str, float] = {}
    safety_stock: dict[str, float] = {}

class ProcurementPlanIn(BaseModel):
    title: str
    business_need: str
    budget: float = Field(ge=0)
    currency: str = "USD"
    owner: str
    market_analysis: str = ""
    sourcing_strategy: str = ""
    requirements: str = ""
    evaluation_method: str = ""
    contract_kpis: str = ""
    schedule_controls: str = ""

class GateDecision(BaseModel):
    gate: str
    passed: bool
    note: str = ""

class ProductionOrderIn(BaseModel):
    mps_id: str | None = None
    product_sku: str
    planned_qty: float = Field(gt=0)
    work_center: str | None = None
    planned_start: datetime
    planned_end: datetime | None = None

class ProductionUpdate(BaseModel):
    action: str
    completed_qty: float | None = Field(default=None, ge=0)
    good_qty: float | None = Field(default=None, ge=0)
    scrap_qty: float | None = Field(default=None, ge=0)
    downtime_minutes: float | None = Field(default=None, ge=0)

def init_planning(conn):
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS procure_boms(
          id UUID PRIMARY KEY, product_sku TEXT NOT NULL, version TEXT NOT NULL,
          lot_size DOUBLE PRECISION NOT NULL, status TEXT NOT NULL DEFAULT 'active',
          created_at TIMESTAMPTZ NOT NULL, UNIQUE(product_sku,version))""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_bom_items(
          id UUID PRIMARY KEY, bom_id UUID NOT NULL REFERENCES procure_boms(id) ON DELETE CASCADE,
          component_sku TEXT NOT NULL, quantity_per DOUBLE PRECISION NOT NULL,
          scrap_pct DOUBLE PRECISION NOT NULL DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_capacity(
          id UUID PRIMARY KEY, work_center TEXT NOT NULL, period_start TIMESTAMPTZ NOT NULL,
          available_hours DOUBLE PRECISION NOT NULL, efficiency_pct DOUBLE PRECISION NOT NULL,
          created_at TIMESTAMPTZ NOT NULL, UNIQUE(work_center,period_start))""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_sop_plans(
          id UUID PRIMARY KEY, period_start TIMESTAMPTZ NOT NULL, product_family TEXT NOT NULL,
          demand_qty DOUBLE PRECISION NOT NULL, supply_qty DOUBLE PRECISION NOT NULL,
          inventory_target DOUBLE PRECISION NOT NULL, status TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_mps(
          id UUID PRIMARY KEY, product_sku TEXT NOT NULL, period_start TIMESTAMPTZ NOT NULL,
          planned_qty DOUBLE PRECISION NOT NULL, work_center TEXT NULL,
          hours_per_unit DOUBLE PRECISION NOT NULL DEFAULT 0, source TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'planned', actual_qty DOUBLE PRECISION NOT NULL DEFAULT 0,
          created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_mrp_runs(
          id UUID PRIMARY KEY, product_sku TEXT NOT NULL, period_start TIMESTAMPTZ NOT NULL,
          planned_qty DOUBLE PRECISION NOT NULL, generated_at TIMESTAMPTZ NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_mrp_requirements(
          id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES procure_mrp_runs(id) ON DELETE CASCADE,
          component_sku TEXT NOT NULL, gross_requirement DOUBLE PRECISION NOT NULL,
          on_hand DOUBLE PRECISION NOT NULL, scheduled_receipts DOUBLE PRECISION NOT NULL,
          safety_stock DOUBLE PRECISION NOT NULL, net_requirement DOUBLE PRECISION NOT NULL,
          planned_order_qty DOUBLE PRECISION NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_production_orders(
          id UUID PRIMARY KEY, order_number TEXT UNIQUE NOT NULL, mps_id UUID NULL REFERENCES procure_mps(id),
          product_sku TEXT NOT NULL, planned_qty DOUBLE PRECISION NOT NULL, completed_qty DOUBLE PRECISION NOT NULL DEFAULT 0,
          work_center TEXT NULL, planned_start TIMESTAMPTZ NOT NULL, planned_end TIMESTAMPTZ NULL,
          actual_start TIMESTAMPTZ NULL, actual_end TIMESTAMPTZ NULL, status TEXT NOT NULL DEFAULT 'planned',
          good_qty DOUBLE PRECISION NOT NULL DEFAULT 0, scrap_qty DOUBLE PRECISION NOT NULL DEFAULT 0,
          downtime_minutes DOUBLE PRECISION NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_plans(
          id UUID PRIMARY KEY, plan_number TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
          business_need TEXT NOT NULL, budget DOUBLE PRECISION NOT NULL, currency TEXT NOT NULL,
          owner TEXT NOT NULL, market_analysis TEXT NOT NULL, sourcing_strategy TEXT NOT NULL,
          requirements TEXT NOT NULL, evaluation_method TEXT NOT NULL, contract_kpis TEXT NOT NULL,
          schedule_controls TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
          created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_plan_gates(
          id UUID PRIMARY KEY, plan_id UUID NOT NULL REFERENCES procure_plans(id) ON DELETE CASCADE,
          gate TEXT NOT NULL, passed BOOLEAN NOT NULL, note TEXT NOT NULL,
          decided_at TIMESTAMPTZ NOT NULL, UNIQUE(plan_id,gate))""")

def install_planning_routes(app, conn, auth):
    @router.post("/bom", status_code=201)
    def create_bom(b: BomIn, authorization: str | None = Header(None)):
        auth("procure.production.write", authorization)
        if not b.items: raise HTTPException(422, "bom_items_required")
        t=now()
        with conn() as c:
            bom=c.execute("""INSERT INTO procure_boms(id,product_sku,version,lot_size,status,created_at)
              VALUES(%s,%s,%s,%s,'active',%s)
              ON CONFLICT(product_sku,version) DO UPDATE SET lot_size=EXCLUDED.lot_size,status='active'
              RETURNING *""",(str(uuid4()),b.product_sku,b.version,b.lot_size,t)).fetchone()
            c.execute("DELETE FROM procure_bom_items WHERE bom_id=%s",(bom["id"],))
            for item in b.items:
                c.execute("""INSERT INTO procure_bom_items(id,bom_id,component_sku,quantity_per,scrap_pct)
                  VALUES(%s,%s,%s,%s,%s)""",(str(uuid4()),bom["id"],item.component_sku,item.quantity_per,item.scrap_pct))
            items=c.execute("SELECT component_sku,quantity_per,scrap_pct FROM procure_bom_items WHERE bom_id=%s ORDER BY component_sku",(bom["id"],)).fetchall()
        return {"bom":bom,"items":items}

    @router.get("/bom/{product_sku}")
    def get_bom(product_sku:str, authorization:str|None=Header(None)):
        auth("procure.production.read", authorization)
        with conn() as c:
            bom=c.execute("SELECT * FROM procure_boms WHERE product_sku=%s AND status='active' ORDER BY created_at DESC LIMIT 1",(product_sku,)).fetchone()
            if not bom: raise HTTPException(404,"bom_not_found")
            items=c.execute("SELECT component_sku,quantity_per,scrap_pct FROM procure_bom_items WHERE bom_id=%s ORDER BY component_sku",(bom["id"],)).fetchall()
        return {"bom":bom,"items":items}

    @router.put("/capacity")
    def set_capacity(b:CapacityIn, authorization:str|None=Header(None)):
        auth("procure.production.write", authorization); t=now()
        with conn() as c:
            return c.execute("""INSERT INTO procure_capacity(id,work_center,period_start,available_hours,efficiency_pct,created_at)
              VALUES(%s,%s,%s,%s,%s,%s)
              ON CONFLICT(work_center,period_start) DO UPDATE SET available_hours=EXCLUDED.available_hours,efficiency_pct=EXCLUDED.efficiency_pct
              RETURNING *""",(str(uuid4()),b.work_center,b.period_start,b.available_hours,b.efficiency_pct,t)).fetchone()

    @router.post("/sop", status_code=201)
    def create_sop(b:SopIn, authorization:str|None=Header(None)):
        auth("procure.production.write", authorization); t=now()
        with conn() as c:
            return c.execute("""INSERT INTO procure_sop_plans VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
              (str(uuid4()),b.period_start,b.product_family,b.demand_qty,b.supply_qty,b.inventory_target,b.status,t,t)).fetchone()

    @router.get("/sop")
    def list_sop(authorization:str|None=Header(None)):
        auth("procure.production.read", authorization)
        with conn() as c:return c.execute("SELECT * FROM procure_sop_plans ORDER BY period_start DESC LIMIT 200").fetchall()

    @router.post("/mps", status_code=201)
    def create_mps(b:MpsIn, authorization:str|None=Header(None)):
        auth("procure.production.write", authorization); t=now()
        required_hours=b.planned_qty*b.hours_per_unit
        feasibility={"status":"not_checked"}
        if b.work_center:
            with conn() as c:
                cap=c.execute("""SELECT * FROM procure_capacity WHERE work_center=%s AND period_start=%s""",(b.work_center,b.period_start)).fetchone()
                if cap:
                    effective=cap["available_hours"]*(cap["efficiency_pct"]/100.0)
                    feasibility={"status":"feasible" if required_hours<=effective else "capacity_shortfall",
                                 "required_hours":required_hours,"effective_available_hours":effective,
                                 "shortfall_hours":max(0,required_hours-effective)}
                row=c.execute("""INSERT INTO procure_mps VALUES(%s,%s,%s,%s,%s,%s,%s,'planned',0,%s,%s) RETURNING *""",
                  (str(uuid4()),b.product_sku,b.period_start,b.planned_qty,b.work_center,b.hours_per_unit,b.source,t,t)).fetchone()
        else:
            with conn() as c:
                row=c.execute("""INSERT INTO procure_mps VALUES(%s,%s,%s,%s,%s,%s,%s,'planned',0,%s,%s) RETURNING *""",
                  (str(uuid4()),b.product_sku,b.period_start,b.planned_qty,None,b.hours_per_unit,b.source,t,t)).fetchone()
        return {"mps":row,"capacity_check":feasibility}

    @router.get("/mps")
    def list_mps(authorization:str|None=Header(None)):
        auth("procure.production.read", authorization)
        with conn() as c:return c.execute("SELECT * FROM procure_mps ORDER BY period_start DESC LIMIT 500").fetchall()

    @router.post("/mps/{mps_id}/actual")
    def record_actual(mps_id:str, actual_qty:float, authorization:str|None=Header(None)):
        auth("procure.production.write", authorization)
        if actual_qty<0: raise HTTPException(422,"actual_qty_must_be_non_negative")
        with conn() as c:
            row=c.execute("UPDATE procure_mps SET actual_qty=%s,status='released',updated_at=%s WHERE id=%s RETURNING *",(actual_qty,now(),mps_id)).fetchone()
            if not row: raise HTTPException(404,"mps_not_found")
        adherence=100.0 if row["planned_qty"]==0 else max(0.0,100.0*(1-abs(row["actual_qty"]-row["planned_qty"])/row["planned_qty"]))
        return {"mps":row,"schedule_adherence_pct":round(adherence,2)}

    @router.post("/mrp/run")
    def run_mrp(b:MrpRunIn, authorization:str|None=Header(None)):
        auth("procure.production.write", authorization)
        with conn() as c:
            bom=c.execute("SELECT * FROM procure_boms WHERE product_sku=%s AND status='active' ORDER BY created_at DESC LIMIT 1",(b.product_sku,)).fetchone()
            if not bom: raise HTTPException(404,"active_bom_not_found")
            items=c.execute("SELECT * FROM procure_bom_items WHERE bom_id=%s",(bom["id"],)).fetchall()
            run_id=str(uuid4()); t=now()
            c.execute("INSERT INTO procure_mrp_runs VALUES(%s,%s,%s,%s,%s)",(run_id,b.product_sku,b.period_start,b.planned_qty,t))
            out=[]
            lots=max(1.0,b.planned_qty/bom["lot_size"])
            for item in items:
                gross=lots*item["quantity_per"]*(1+item["scrap_pct"]/100.0)
                on=float(b.on_hand.get(item["component_sku"],0))
                receipts=float(b.scheduled_receipts.get(item["component_sku"],0))
                safety=float(b.safety_stock.get(item["component_sku"],0))
                net=max(0.0,gross+safety-on-receipts)
                planned=float(ceil(net)) if net>0 else 0.0
                row=c.execute("""INSERT INTO procure_mrp_requirements VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                  (str(uuid4()),run_id,item["component_sku"],gross,on,receipts,safety,net,planned)).fetchone()
                out.append(row)
        return {"run_id":run_id,"product_sku":b.product_sku,"planned_qty":b.planned_qty,"requirements":out}

    @router.get("/mrp/{run_id}")
    def get_mrp(run_id:str, authorization:str|None=Header(None)):
        auth("procure.production.read", authorization)
        with conn() as c:
            run=c.execute("SELECT * FROM procure_mrp_runs WHERE id=%s",(run_id,)).fetchone()
            if not run: raise HTTPException(404,"mrp_run_not_found")
            reqs=c.execute("SELECT * FROM procure_mrp_requirements WHERE run_id=%s ORDER BY component_sku",(run_id,)).fetchall()
        return {"run":run,"requirements":reqs}

    @router.post("/production-orders", status_code=201)
    def create_production_order(b:ProductionOrderIn, authorization:str|None=Header(None)):
        auth("procure.production.write", authorization); t=now()
        with conn() as c:
            n=c.execute("SELECT COUNT(*) n FROM procure_production_orders").fetchone()["n"]+1
            number=f"MO-{t.year}-{n:06d}"
            row=c.execute("""INSERT INTO procure_production_orders(
              id,order_number,mps_id,product_sku,planned_qty,completed_qty,work_center,planned_start,planned_end,
              actual_start,actual_end,status,good_qty,scrap_qty,downtime_minutes,created_at,updated_at)
              VALUES(%s,%s,%s,%s,%s,0,%s,%s,%s,NULL,NULL,'planned',0,0,0,%s,%s) RETURNING *""",
              (str(uuid4()),number,b.mps_id,b.product_sku,b.planned_qty,b.work_center,b.planned_start,b.planned_end,t,t)).fetchone()
        return row

    @router.post("/production-orders/{order_id}/action")
    def production_order_action(order_id:str,b:ProductionUpdate,authorization:str|None=Header(None)):
        auth("procure.production.write", authorization)
        allowed={"release","start","report","complete","hold","cancel"}
        if b.action not in allowed: raise HTTPException(422,"invalid_production_action")
        t=now()
        with conn() as c:
            row=c.execute("SELECT * FROM procure_production_orders WHERE id=%s FOR UPDATE",(order_id,)).fetchone()
            if not row: raise HTTPException(404,"production_order_not_found")
            status={"release":"released","start":"in_progress","report":row["status"],"complete":"complete","hold":"hold","cancel":"cancelled"}[b.action]
            actual_start=row["actual_start"] or (t if b.action=="start" else None)
            actual_end=t if b.action=="complete" else row["actual_end"]
            completed=row["completed_qty"] if b.completed_qty is None else b.completed_qty
            good=row["good_qty"] if b.good_qty is None else b.good_qty
            scrap=row["scrap_qty"] if b.scrap_qty is None else b.scrap_qty
            downtime=row["downtime_minutes"] if b.downtime_minutes is None else b.downtime_minutes
            out=c.execute("""UPDATE procure_production_orders SET status=%s,actual_start=%s,actual_end=%s,
              completed_qty=%s,good_qty=%s,scrap_qty=%s,downtime_minutes=%s,updated_at=%s WHERE id=%s RETURNING *""",
              (status,actual_start,actual_end,completed,good,scrap,downtime,t,order_id)).fetchone()
        adherence=100.0 if out["planned_qty"]==0 else max(0.0,100.0*(1-abs(out["completed_qty"]-out["planned_qty"])/out["planned_qty"]))
        quality=100.0*(out["good_qty"]/(out["good_qty"]+out["scrap_qty"])) if (out["good_qty"]+out["scrap_qty"])>0 else None
        return {"order":out,"production_schedule_adherence_pct":round(adherence,2),"quality_yield_pct":round(quality,2) if quality is not None else None}

    @router.get("/production-orders")
    def list_production_orders(authorization:str|None=Header(None)):
        auth("procure.production.read", authorization)
        with conn() as c:return c.execute("SELECT * FROM procure_production_orders ORDER BY planned_start DESC LIMIT 500").fetchall()

    @router.post("/procurement-plans", status_code=201)
    def create_procurement_plan(b:ProcurementPlanIn, authorization:str|None=Header(None)):
        auth("procure.requests.write", authorization); t=now()
        with conn() as c:
            n=c.execute("SELECT COUNT(*) n FROM procure_plans").fetchone()["n"]+1
            number=f"PLAN-{t.year}-{n:05d}"
            return c.execute("""INSERT INTO procure_plans VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft',%s,%s) RETURNING *""",
              (str(uuid4()),number,b.title,b.business_need,b.budget,b.currency.upper(),b.owner,b.market_analysis,b.sourcing_strategy,b.requirements,b.evaluation_method,b.contract_kpis,b.schedule_controls,t,t)).fetchone()

    @router.post("/procurement-plans/{plan_id}/gate")
    def decide_gate(plan_id:str,b:GateDecision,authorization:str|None=Header(None)):
        auth("procure.requests.write", authorization)
        allowed={"need_approved","budget_confirmed","evaluation_preset","contract_owner_named","supplier_due_diligence","market_analysis","sourcing_strategy"}
        if b.gate not in allowed: raise HTTPException(422,"unsupported_gate")
        with conn() as c:
            if not c.execute("SELECT id FROM procure_plans WHERE id=%s",(plan_id,)).fetchone(): raise HTTPException(404,"plan_not_found")
            row=c.execute("""INSERT INTO procure_plan_gates VALUES(%s,%s,%s,%s,%s,%s)
              ON CONFLICT(plan_id,gate) DO UPDATE SET passed=EXCLUDED.passed,note=EXCLUDED.note,decided_at=EXCLUDED.decided_at RETURNING *""",
              (str(uuid4()),plan_id,b.gate,b.passed,b.note,now())).fetchone()
            gates=c.execute("SELECT gate,passed,note,decided_at FROM procure_plan_gates WHERE plan_id=%s ORDER BY gate",(plan_id,)).fetchall()
        return {"gate":row,"all_gates":gates}

    @router.get("/procurement-plans")
    def list_procurement_plans(authorization:str|None=Header(None)):
        auth("procure.requests.read", authorization)
        with conn() as c:
            plans=c.execute("SELECT * FROM procure_plans ORDER BY created_at DESC").fetchall()
            for p in plans:
                p["gates"]=c.execute("SELECT gate,passed,note,decided_at FROM procure_plan_gates WHERE plan_id=%s ORDER BY gate",(p["id"],)).fetchall()
        return plans

    app.include_router(router)

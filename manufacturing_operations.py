from datetime import datetime, timezone
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router=APIRouter(prefix="/v1/production",tags=["Production Operations"])

def now(): return datetime.now(timezone.utc)

class WorkCenterIn(BaseModel):
    code:str
    name:str
    planned_hours_per_day:float=Field(default=8,gt=0)
    ideal_rate_per_hour:float=Field(default=1,gt=0)

class ProductionOrderIn(BaseModel):
    product_sku:str
    quantity:float=Field(gt=0)
    work_center:str
    mps_id:str|None=None
    planned_start:datetime
    planned_end:datetime

class ProductionStatusIn(BaseModel):
    status:str
    good_qty:float=Field(default=0,ge=0)
    reject_qty:float=Field(default=0,ge=0)
    runtime_minutes:float=Field(default=0,ge=0)
    downtime_minutes:float=Field(default=0,ge=0)

def init_production(conn):
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS procure_work_centers(
          code TEXT PRIMARY KEY,name TEXT NOT NULL,planned_hours_per_day DOUBLE PRECISION NOT NULL,
          ideal_rate_per_hour DOUBLE PRECISION NOT NULL,active BOOLEAN NOT NULL DEFAULT TRUE,
          updated_at TIMESTAMPTZ NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_production_orders(
          id UUID PRIMARY KEY,order_number TEXT UNIQUE NOT NULL,product_sku TEXT NOT NULL,
          quantity DOUBLE PRECISION NOT NULL,work_center TEXT NOT NULL REFERENCES procure_work_centers(code),
          mps_id UUID NULL,planned_start TIMESTAMPTZ NOT NULL,planned_end TIMESTAMPTZ NOT NULL,
          actual_start TIMESTAMPTZ NULL,actual_end TIMESTAMPTZ NULL,status TEXT NOT NULL DEFAULT 'planned',
          good_qty DOUBLE PRECISION NOT NULL DEFAULT 0,reject_qty DOUBLE PRECISION NOT NULL DEFAULT 0,
          runtime_minutes DOUBLE PRECISION NOT NULL DEFAULT 0,downtime_minutes DOUBLE PRECISION NOT NULL DEFAULT 0,
          created_at TIMESTAMPTZ NOT NULL,updated_at TIMESTAMPTZ NOT NULL)""")

def install_production_routes(app,conn,auth):
    @router.put("/work-centers/{code}")
    def upsert_work_center(code:str,b:WorkCenterIn,authorization:str|None=Header(None)):
        auth("procure.production.write",authorization)
        if code!=b.code: raise HTTPException(422,"code_mismatch")
        with conn() as c:return c.execute("""INSERT INTO procure_work_centers(code,name,planned_hours_per_day,ideal_rate_per_hour,active,updated_at)
          VALUES(%s,%s,%s,%s,TRUE,%s)
          ON CONFLICT(code) DO UPDATE SET name=EXCLUDED.name,planned_hours_per_day=EXCLUDED.planned_hours_per_day,
          ideal_rate_per_hour=EXCLUDED.ideal_rate_per_hour,active=TRUE,updated_at=EXCLUDED.updated_at RETURNING *""",
          (code,b.name,b.planned_hours_per_day,b.ideal_rate_per_hour,now())).fetchone()

    @router.post("/orders",status_code=201)
    def create_order(b:ProductionOrderIn,authorization:str|None=Header(None)):
        auth("procure.production.write",authorization)
        if b.planned_end<=b.planned_start: raise HTTPException(422,"planned_end_must_follow_start")
        with conn() as c:
            if not c.execute("SELECT code FROM procure_work_centers WHERE code=%s AND active=TRUE",(b.work_center,)).fetchone(): raise HTTPException(404,"work_center_not_found")
            n=c.execute("SELECT COUNT(*) n FROM procure_production_orders").fetchone()["n"]+1
            no=f"MO-{now().year}-{n:06d}"
            return c.execute("""INSERT INTO procure_production_orders
              (id,order_number,product_sku,quantity,work_center,mps_id,planned_start,planned_end,status,created_at,updated_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'planned',%s,%s) RETURNING *""",
              (str(uuid4()),no,b.product_sku,b.quantity,b.work_center,b.mps_id,b.planned_start,b.planned_end,now(),now())).fetchone()

    @router.patch("/orders/{order_id}")
    def update_order(order_id:str,b:ProductionStatusIn,authorization:str|None=Header(None)):
        auth("procure.production.write",authorization)
        allowed={"released","in_progress","complete","cancelled"}
        if b.status not in allowed: raise HTTPException(422,"invalid_status")
        with conn() as c:
            row=c.execute("SELECT * FROM procure_production_orders WHERE id=%s FOR UPDATE",(order_id,)).fetchone()
            if not row: raise HTTPException(404,"production_order_not_found")
            actual_start=row["actual_start"]; actual_end=row["actual_end"]
            if b.status=="in_progress" and actual_start is None: actual_start=now()
            if b.status=="complete":
                actual_start=actual_start or now(); actual_end=now()
            return c.execute("""UPDATE procure_production_orders SET status=%s,actual_start=%s,actual_end=%s,
              good_qty=%s,reject_qty=%s,runtime_minutes=%s,downtime_minutes=%s,updated_at=%s WHERE id=%s RETURNING *""",
              (b.status,actual_start,actual_end,b.good_qty,b.reject_qty,b.runtime_minutes,b.downtime_minutes,now(),order_id)).fetchone()

    @router.get("/orders")
    def list_orders(status:str|None=None,authorization:str|None=Header(None)):
        auth("procure.production.read",authorization)
        with conn() as c:
            if status:return c.execute("SELECT * FROM procure_production_orders WHERE status=%s ORDER BY planned_start DESC",(status,)).fetchall()
            return c.execute("SELECT * FROM procure_production_orders ORDER BY planned_start DESC LIMIT 500").fetchall()

    @router.get("/kpis")
    def production_kpis(authorization:str|None=Header(None)):
        auth("procure.production.read",authorization)
        with conn() as c:
            rows=c.execute("""SELECT p.*,w.ideal_rate_per_hour FROM procure_production_orders p
              JOIN procure_work_centers w ON w.code=p.work_center WHERE p.status='complete'""").fetchall()
        if not rows:return {"status":"no-data","observations":[]}
        runtime=sum(float(r["runtime_minutes"]) for r in rows); downtime=sum(float(r["downtime_minutes"]) for r in rows)
        planned=runtime+downtime
        availability=(runtime/planned) if planned>0 else 0
        total_units=sum(float(r["good_qty"])+float(r["reject_qty"]) for r in rows)
        good=sum(float(r["good_qty"]) for r in rows)
        ideal_units=sum((float(r["runtime_minutes"])/60.0)*float(r["ideal_rate_per_hour"]) for r in rows)
        performance=(total_units/ideal_units) if ideal_units>0 else 0
        quality=(good/total_units) if total_units>0 else 0
        oee=100*availability*performance*quality
        lead=[(r["actual_end"]-r["actual_start"]).total_seconds()/60 for r in rows if r["actual_start"] and r["actual_end"]]
        adherence=[]
        for r in rows:
            if r["quantity"]>0: adherence.append(max(0,1-abs(float(r["good_qty"])-float(r["quantity"]))/float(r["quantity"])))
        return {"observations":[
          {"kpi_key":"overall_equipment_effectiveness","value":round(oee,4)},
          {"kpi_key":"production_lead_time","value":round(sum(lead)/len(lead),4) if lead else None},
          {"kpi_key":"production_schedule_adherence","value":round(100*sum(adherence)/len(adherence),4) if adherence else None},
          {"kpi_key":"capacity_utilization","value":round(100*availability,4)}
        ],"generated_at":now()}

    app.include_router(router)

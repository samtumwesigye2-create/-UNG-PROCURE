from datetime import datetime, timezone
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

router=APIRouter(prefix="/v1/supply-chain-workflow",tags=["End-to-End Supply Chain Workflow"])

STAGES=[
  (1,"plan_demand_supply"),
  (2,"source_suppliers"),
  (3,"procure_materials"),
  (4,"manage_inventory"),
  (5,"production_operations"),
  (6,"quality_assurance"),
  (7,"warehousing_storage"),
  (8,"order_fulfillment_delivery"),
  (9,"continuous_improvement")
]

def now(): return datetime.now(timezone.utc)

class WorkflowIn(BaseModel):
    name:str
    sku:str|None=None
    owner:str=""
    reference:str=""
    note:str=""

class StageUpdate(BaseModel):
    status:str
    note:str=""

VALID={"pending","ready","in_progress","blocked","complete","skipped"}

def init_supply_chain_workflow(conn):
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS procure_supply_chain_workflows(
          id UUID PRIMARY KEY,name TEXT NOT NULL,sku TEXT NULL,owner TEXT NOT NULL,reference TEXT NOT NULL,
          status TEXT NOT NULL,note TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL,updated_at TIMESTAMPTZ NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_supply_chain_workflow_stages(
          id UUID PRIMARY KEY,workflow_id UUID NOT NULL REFERENCES procure_supply_chain_workflows(id) ON DELETE CASCADE,
          stage_no INTEGER NOT NULL,stage_code TEXT NOT NULL,status TEXT NOT NULL,note TEXT NOT NULL,
          started_at TIMESTAMPTZ NULL,completed_at TIMESTAMPTZ NULL,updated_at TIMESTAMPTZ NOT NULL,
          UNIQUE(workflow_id,stage_no))""")

def install_supply_chain_workflow_routes(app,conn,auth,emit):
    @router.post("",status_code=201)
    def create_workflow(b:WorkflowIn,authorization:str|None=Header(None)):
        auth("procure.production.write",authorization); t=now(); wid=str(uuid4())
        with conn() as c:
            wf=c.execute("""INSERT INTO procure_supply_chain_workflows VALUES(
              %s,%s,%s,%s,%s,'active',%s,%s,%s) RETURNING *""",
              (wid,b.name,b.sku,b.owner,b.reference,b.note,t,t)).fetchone()
            for no,code in STAGES:
                c.execute("""INSERT INTO procure_supply_chain_workflow_stages(
                  id,workflow_id,stage_no,stage_code,status,note,started_at,completed_at,updated_at)
                  VALUES(%s,%s,%s,%s,%s,'',NULL,NULL,%s)""",
                  (str(uuid4()),wid,no,code,"ready" if no==1 else "pending",t))
        return {"workflow":wf,"stages":[{"stage_no":n,"stage_code":c} for n,c in STAGES]}

    @router.get("/{workflow_id}")
    def get_workflow(workflow_id:str,authorization:str|None=Header(None)):
        auth("procure.production.read",authorization)
        with conn() as c:
            wf=c.execute("SELECT * FROM procure_supply_chain_workflows WHERE id=%s",(workflow_id,)).fetchone()
            if not wf: raise HTTPException(404,"workflow_not_found")
            stages=c.execute("""SELECT * FROM procure_supply_chain_workflow_stages
              WHERE workflow_id=%s ORDER BY stage_no""",(workflow_id,)).fetchall()
        return {"workflow":wf,"stages":stages}

    @router.post("/{workflow_id}/stages/{stage_no}")
    def update_stage(workflow_id:str,stage_no:int,b:StageUpdate,authorization:str|None=Header(None)):
        auth("procure.production.write",authorization)
        status=b.status.lower()
        if status not in VALID: raise HTTPException(422,"invalid_stage_status")
        t=now()
        with conn() as c:
            row=c.execute("""SELECT * FROM procure_supply_chain_workflow_stages
              WHERE workflow_id=%s AND stage_no=%s FOR UPDATE""",(workflow_id,stage_no)).fetchone()
            if not row: raise HTTPException(404,"workflow_stage_not_found")
            started=row["started_at"] or (t if status=="in_progress" else None)
            completed=t if status in {"complete","skipped"} else row["completed_at"]
            out=c.execute("""UPDATE procure_supply_chain_workflow_stages SET
              status=%s,note=%s,started_at=%s,completed_at=%s,updated_at=%s WHERE id=%s RETURNING *""",
              (status,b.note,started,completed,t,row["id"])).fetchone()
            if status=="complete" and stage_no<9:
                c.execute("""UPDATE procure_supply_chain_workflow_stages SET status='ready',updated_at=%s
                  WHERE workflow_id=%s AND stage_no=%s AND status='pending'""",(t,workflow_id,stage_no+1))
            states=c.execute("""SELECT stage_no,status FROM procure_supply_chain_workflow_stages
              WHERE workflow_id=%s ORDER BY stage_no""",(workflow_id,)).fetchall()
            overall="complete" if all(x["status"] in {"complete","skipped"} for x in states) else                     ("blocked" if any(x["status"]=="blocked" for x in states) else "active")
            c.execute("UPDATE procure_supply_chain_workflows SET status=%s,updated_at=%s WHERE id=%s",
              (overall,t,workflow_id))
        target={
          4:"UNG-VECTOR",5:"UNG-VECTOR",6:"UNG-VECTOR",7:"UNG-VECTOR",
          8:"UNG-VECTOR",9:"UNG-NOVA"
        }.get(stage_no,"UNG-NEXUS")
        delivery=emit(target,"PROCURE.SUPPLY_CHAIN.STAGE_UPDATED",{
          "workflow_id":workflow_id,"stage_no":stage_no,"stage_code":out["stage_code"],
          "status":status,"note":b.note
        })
        return {"stage":out,"workflow_status":overall,"integration_delivery":delivery}

    @router.get("")
    def list_workflows(status:str|None=None,authorization:str|None=Header(None)):
        auth("procure.production.read",authorization)
        with conn() as c:
            if status:
                return c.execute("""SELECT * FROM procure_supply_chain_workflows
                  WHERE status=%s ORDER BY created_at DESC LIMIT 500""",(status,)).fetchall()
            return c.execute("""SELECT * FROM procure_supply_chain_workflows
              ORDER BY created_at DESC LIMIT 500""").fetchall()

    app.include_router(router)

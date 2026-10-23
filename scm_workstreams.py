"""Three grouped SCM workstreams: auditable records, validation and lifecycle.
Operational integration adapters and regulator approvals are separate requirements.
"""
from datetime import datetime,timezone
from uuid import uuid4
from typing import Any
from fastapi import APIRouter,Header,HTTPException,Query
from pydantic import BaseModel,Field
from psycopg.types.json import Jsonb

GROUPS={
 "trade-transport":{
  "maritime-operations":("vessel","voyage","port_of_loading","port_of_discharge"),
  "customs-compliance":("declaration_reference","jurisdiction","hs_code","review_status"),
  "freight-networks":("carrier","origin","destination","service_level"),
  "route-optimization":("origin","destination","transport_mode","route_options"),
  "lpi-benchmarking":("country","period","source","indicator","value")},
 "supplier-governance":{
  "supplier-due-diligence":("supplier_id","risk_level","evidence_source","reviewed_at"),
  "labour-compliance":("supplier_id","standard","finding","remediation_status"),
  "cyber-scrm":("supplier_id","control","assessment","evidence_source"),
  "supplier-resilience":("supplier_id","disruption_type","impact","mitigation"),
  "traceability-assurance":("lot_id","event_type","location","event_time")},
 "planning-sustainability":{
  "scope3-accounting":("category","activity_quantity","activity_unit","emission_factor","factor_source"),
  "food-loss-analysis":("sku","quantity_lost","unit","loss_reason"),
  "circularity-management":("sku","quantity","unit","disposition"),
  "integrated-business-planning":("planning_period","demand","supply","scenario"),
  "ai-resilience-analysis":("scenario","assumptions","risk_score","model_version")}
}
INDEX={key:(group,required) for group,entries in GROUPS.items() for key,required in entries.items()}
STATES={"draft":{"submitted"},"submitted":{"reviewed","rejected"},
 "reviewed":{"approved","rejected"},"approved":set(),"rejected":{"draft"}}
router=APIRouter(prefix="/v1/scm/workstreams",tags=["SCM Workstreams"])
class Record(BaseModel):
 reference:str=Field(min_length=1,max_length=160)
 payload:dict[str,Any]
 source_system:str=Field(default="UNG-PROCURE",min_length=2,max_length=100)
class Transition(BaseModel):
 status:str
 reason:str=Field(min_length=5,max_length=2000)

def validate(capability,payload):
 if capability not in INDEX:raise HTTPException(404,"unknown_workstream")
 required=INDEX[capability][1]
 missing=[field for field in required if field not in payload or payload[field] is None or payload[field]==""]
 if missing:raise HTTPException(422,{"missing_fields":missing})
 if capability=="scope3-accounting":
  if type(payload["category"]) is not int or not 1<=payload["category"]<=15:
   raise HTTPException(422,"scope3_category_invalid")
  for k in ("activity_quantity","emission_factor"):
   if type(payload[k]) not in (float,int) or payload[k]<0:raise HTTPException(422,k+"_invalid")
  payload["calculated_kg_co2e"]=round(payload["activity_quantity"]*payload["emission_factor"],6)
 if capability=="lpi-benchmarking":
  if type(payload["value"]) not in (float,int) or payload["value"]<0:raise HTTPException(422,"lpi_value_invalid")
 if capability=="ai-resilience-analysis":
  if type(payload["risk_score"]) not in (float,int) or not 0<=payload["risk_score"]<=100:
   raise HTTPException(422,"risk_score_out_of_range")
 if capability=="food-loss-analysis":
  if type(payload["quantity_lost"]) not in (float,int) or payload["quantity_lost"]<0:
   raise HTTPException(422,"quantity_lost_invalid")
 return payload

def install_scm_workstreams(app,conn,auth):
 def schema(c):
  c.execute("""CREATE TABLE IF NOT EXISTS scm_workstream_records(
   id UUID PRIMARY KEY,workstream TEXT NOT NULL,reference TEXT NOT NULL,
   source_system TEXT NOT NULL,payload JSONB NOT NULL,status TEXT NOT NULL,
   created_at TIMESTAMPTZ NOT NULL,updated_at TIMESTAMPTZ NOT NULL,
   UNIQUE(workstream,reference))""")
  c.execute("""CREATE TABLE IF NOT EXISTS scm_workstream_audit(
   id UUID PRIMARY KEY,record_id UUID NOT NULL REFERENCES scm_workstream_records(id),
   action TEXT NOT NULL,reason TEXT NOT NULL,occurred_at TIMESTAMPTZ NOT NULL)""")
 def audit(c,rid,action,reason):
  c.execute("INSERT INTO scm_workstream_audit VALUES(%s,%s,%s,%s,%s)",
   (str(uuid4()),rid,action,reason,datetime.now(timezone.utc)))
 @router.get("/catalog")
 def catalog(authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization)
  return {"groups":{g:{k:{"required_fields":fields} for k,fields in entries.items()} for g,entries in GROUPS.items()},
          "total":len(INDEX),"note":"Record workflow foundation; external integration and independent compliance verification pending"}
 @router.post("/{capability}/records",status_code=201)
 def create(capability:str,body:Record,authorization:str|None=Header(None)):
  auth("procure.scm.write",authorization)
  data=validate(capability,dict(body.payload))
  rid=str(uuid4());now=datetime.now(timezone.utc)
  with conn() as c:
   schema(c)
   c.execute("""INSERT INTO scm_workstream_records
   (id,workstream,reference,source_system,payload,status,created_at,updated_at)
   VALUES(%s,%s,%s,%s,%s,'draft',%s,%s)""",
   (rid,capability,body.reference,body.source_system,Jsonb(data),now,now))
   audit(c,rid,"created","Initial workstream record")
  return {"id":rid,"workstream":capability,"status":"draft"}
 @router.get("/{capability}/records")
 def list_records(capability:str,limit:int=Query(100,ge=1,le=500),authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization)
  if capability not in INDEX:raise HTTPException(404,"unknown_workstream")
  with conn() as c:
   schema(c)
   return c.execute("SELECT * FROM scm_workstream_records WHERE workstream=%s ORDER BY created_at DESC LIMIT %s",(capability,limit)).fetchall()
 @router.post("/{capability}/records/{reference}/transition")
 def transition(capability:str,reference:str,body:Transition,authorization:str|None=Header(None)):
  auth("procure.scm.write",authorization)
  if capability not in INDEX:raise HTTPException(404,"unknown_workstream")
  with conn() as c:
   schema(c)
   row=c.execute("SELECT id,status FROM scm_workstream_records WHERE workstream=%s AND reference=%s FOR UPDATE",(capability,reference)).fetchone()
   if not row:raise HTTPException(404,"record_not_found")
   if body.status not in STATES[row["status"]]:raise HTTPException(409,"invalid_status_transition")
   c.execute("UPDATE scm_workstream_records SET status=%s,updated_at=%s WHERE id=%s",
    (body.status,datetime.now(timezone.utc),row["id"]))
   audit(c,row["id"],body.status,body.reason)
  return {"reference":reference,"workstream":capability,"status":body.status}
 @router.get("/{capability}/records/{reference}/audit")
 def history(capability:str,reference:str,authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization)
  if capability not in INDEX:raise HTTPException(404,"unknown_workstream")
  with conn() as c:
   schema(c)
   row=c.execute("SELECT id FROM scm_workstream_records WHERE workstream=%s AND reference=%s",(capability,reference)).fetchone()
   if not row:raise HTTPException(404,"record_not_found")
   return c.execute("SELECT action,reason,occurred_at FROM scm_workstream_audit WHERE record_id=%s ORDER BY occurred_at,id",(row["id"],)).fetchall()
 app.include_router(router)

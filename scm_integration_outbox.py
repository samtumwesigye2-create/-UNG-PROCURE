"""Transactional SCM event outbox. Approval-gated, retryable, idempotent by event ID.
Events are sent to NEXUS for downstream routing; delivery is not proof of consumption.
"""
from datetime import datetime,timezone
from uuid import uuid4
from fastapi import APIRouter,Header,HTTPException,Query
from pydantic import BaseModel,Field
from psycopg.types.json import Jsonb

TARGETS={
 "trade-transport":("UNG-VECTOR","UNG-NOVA","UNG-ATLAS"),
 "supplier-governance":("UNG-SENTINEL","UNG-NOVA","UNG-ATLAS"),
 "planning-sustainability":("UNG-VECTOR","UNG-NOVA","UNG-ATLAS")}
router=APIRouter(prefix="/v1/scm/integration",tags=["SCM Integration"])
class Dispatch(BaseModel):
 capability:str
 reference:str=Field(min_length=1,max_length=160)
 target_system:str
def install_scm_integration(app,conn,auth,emit):
 from scm_workstreams import INDEX
 def schema(c):
  c.execute("""CREATE TABLE IF NOT EXISTS scm_integration_outbox(
   id UUID PRIMARY KEY,workstream_record_id UUID NOT NULL REFERENCES scm_workstream_records(id),
   target_system TEXT NOT NULL,message_type TEXT NOT NULL,payload JSONB NOT NULL,
   state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
   last_response JSONB,created_at TIMESTAMPTZ NOT NULL,updated_at TIMESTAMPTZ NOT NULL,
   UNIQUE(workstream_record_id,target_system))""")
 @router.get("/targets")
 def targets(authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization)
  return {"groups":TARGETS,"transport":"UNG-NEXUS","delivery_guarantee":"at-least-once; downstream must deduplicate event_id"}
 @router.post("/queue",status_code=201)
 def queue(body:Dispatch,authorization:str|None=Header(None)):
  auth("procure.scm.write",authorization)
  if body.capability not in INDEX:raise HTTPException(404,"unknown_workstream")
  group=INDEX[body.capability][0]
  if body.target_system not in TARGETS[group]:raise HTTPException(422,"target_not_allowed_for_group")
  with conn() as c:
   schema(c)
   row=c.execute("""SELECT id,status,payload FROM scm_workstream_records
    WHERE workstream=%s AND reference=%s FOR UPDATE""",(body.capability,body.reference)).fetchone()
   if not row:raise HTTPException(404,"workstream_record_not_found")
   if row["status"]!="approved":raise HTTPException(409,"record_must_be_approved")
   event_id=str(uuid4());now=datetime.now(timezone.utc)
   payload={"event_id":event_id,"workstream":body.capability,"reference":body.reference,
    "record_id":str(row["id"]),"approval_status":"approved","data":row["payload"]}
   event=c.execute("""INSERT INTO scm_integration_outbox
    (id,workstream_record_id,target_system,message_type,payload,state,created_at,updated_at)
    VALUES(%s,%s,%s,%s,%s,'pending',%s,%s)
    ON CONFLICT(workstream_record_id,target_system) DO NOTHING RETURNING id,state""",
    (event_id,row["id"],body.target_system,"SCM.WORKSTREAM.APPROVED",Jsonb(payload),now,now)).fetchone()
   if event is None:
    event=c.execute("SELECT id,state FROM scm_integration_outbox WHERE workstream_record_id=%s AND target_system=%s",
     (row["id"],body.target_system)).fetchone()
  return {"event_id":str(event["id"]),"state":event["state"],"target_system":body.target_system}
 @router.post("/events/{event_id}/deliver")
 def deliver(event_id:str,authorization:str|None=Header(None)):
  auth("procure.scm.write",authorization)
  with conn() as c:
   schema(c)
   row=c.execute("""SELECT id,target_system,message_type,payload,state,attempts
    FROM scm_integration_outbox WHERE id=%s FOR UPDATE NOWAIT""",(event_id,)).fetchone()
   if not row:raise HTTPException(404,"event_not_found")
   if row["state"]=="delivered":return {"event_id":event_id,"state":"delivered","already_delivered":True}
   if row["attempts"]>=10:raise HTTPException(409,"retry_limit_reached")
   # Lock held while sending, preventing concurrent duplicate delivery.
   result=emit(row["target_system"],row["message_type"],row["payload"])
   state="delivered" if result.get("status")=="delivered" else "failed"
   c.execute("""UPDATE scm_integration_outbox SET state=%s,attempts=attempts+1,
    last_response=%s,updated_at=%s WHERE id=%s""",
    (state,Jsonb(result),datetime.now(timezone.utc),event_id))
  return {"event_id":event_id,"state":state,"delivery":result}
 @router.get("/events")
 def events(state:str|None=None,limit:int=Query(100,ge=1,le=500),
  authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization)
  if state is not None and state not in {"pending","failed","delivered"}:
   raise HTTPException(422,"invalid_state")
  with conn() as c:
   schema(c)
   return c.execute("""SELECT id,workstream_record_id,target_system,message_type,state,
    attempts,last_response,created_at,updated_at FROM scm_integration_outbox
    WHERE (%s IS NULL OR state=%s) ORDER BY created_at DESC LIMIT %s""",
    (state,state,limit)).fetchall()
 app.include_router(router)

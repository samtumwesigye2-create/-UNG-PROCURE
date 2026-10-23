"""Tender lifecycle and bid evaluation. UNG-native, PostgreSQL backed."""
from datetime import datetime,timezone
from decimal import Decimal
from uuid import uuid4
from fastapi import APIRouter,Header,HTTPException
from pydantic import BaseModel,Field
from psycopg.types.json import Jsonb

router=APIRouter(prefix="/v1/scm/tenders",tags=["SCM Tender Execution"])
class TenderIn(BaseModel):
 reference:str=Field(min_length=1,max_length=100)
 title:str=Field(min_length=3,max_length=240)
 closing_at:datetime
 currency:str=Field(pattern=r"^[A-Z]{3}$")
 evaluation_weights:dict[str,float]=Field(default_factory=lambda:{"price":60,"quality":40})
class BidIn(BaseModel):
 supplier_id:str=Field(min_length=1,max_length=120)
 price:Decimal=Field(gt=0)
 quality_score:float=Field(ge=0,le=100)
 notes:str=Field(default="",max_length=2000)
class AwardIn(BaseModel):
 bid_id:str
 reason:str=Field(min_length=5,max_length=2000)

def install_tender_execution(app,conn,auth):
 def schema(c):
  c.execute("""CREATE TABLE IF NOT EXISTS scm_tenders(
   id UUID PRIMARY KEY,reference TEXT UNIQUE NOT NULL,title TEXT NOT NULL,
   status TEXT NOT NULL,closing_at TIMESTAMPTZ NOT NULL,currency TEXT NOT NULL,
   weights JSONB NOT NULL,awarded_bid UUID,created_at TIMESTAMPTZ NOT NULL)""")
  c.execute("""CREATE TABLE IF NOT EXISTS scm_tender_bids(
   id UUID PRIMARY KEY,tender_id UUID NOT NULL REFERENCES scm_tenders(id),
   supplier_id TEXT NOT NULL,price NUMERIC(18,2) NOT NULL,quality_score DOUBLE PRECISION NOT NULL,
   notes TEXT NOT NULL,submitted_at TIMESTAMPTZ NOT NULL,
   UNIQUE(tender_id,supplier_id))""")
  c.execute("""CREATE TABLE IF NOT EXISTS scm_tender_events(
   id UUID PRIMARY KEY,tender_id UUID NOT NULL REFERENCES scm_tenders(id),
   action TEXT NOT NULL,details JSONB NOT NULL,occurred_at TIMESTAMPTZ NOT NULL)""")
 def event(c,tid,action,details):
  c.execute("INSERT INTO scm_tender_events VALUES(%s,%s,%s,%s,%s)",
   (str(uuid4()),tid,action,Jsonb(details),datetime.now(timezone.utc)))
 @router.post("",status_code=201)
 def create(body:TenderIn,authorization:str|None=Header(None)):
  auth("procure.scm.write",authorization)
  if body.closing_at.tzinfo is None or body.closing_at<=datetime.now(timezone.utc):
   raise HTTPException(422,"closing_at_must_be_future_and_timezone_aware")
  w=body.evaluation_weights
  if not w or set(w)!={"price","quality"} or any(v<0 for v in w.values()) or abs(sum(w.values())-100)>0.001:
   raise HTTPException(422,"weights_must_be_nonnegative_and_sum_to_100")
  tid=str(uuid4())
  with conn() as c:
   schema(c)
   c.execute("INSERT INTO scm_tenders VALUES(%s,%s,%s,'draft',%s,%s,%s,NULL,%s)",
    (tid,body.reference,body.title,body.closing_at,body.currency,Jsonb(w),datetime.now(timezone.utc)))
   event(c,tid,"created",{"reference":body.reference})
  return {"id":tid,"status":"draft"}
 @router.post("/{tender_id}/publish")
 def publish(tender_id:str,authorization:str|None=Header(None)):
  auth("procure.scm.write",authorization)
  with conn() as c:
   schema(c)
   row=c.execute("UPDATE scm_tenders SET status='published' WHERE id=%s AND status='draft' AND closing_at>now() RETURNING id,status",(tender_id,)).fetchone()
   if not row:raise HTTPException(409,"tender_not_publishable")
   event(c,tender_id,"published",{})
  return row
 @router.post("/{tender_id}/bids",status_code=201)
 def submit(tender_id:str,body:BidIn,authorization:str|None=Header(None)):
  auth("procure.scm.write",authorization)
  bid_id=str(uuid4())
  with conn() as c:
   schema(c)
   t=c.execute("SELECT status,closing_at FROM scm_tenders WHERE id=%s FOR UPDATE",(tender_id,)).fetchone()
   if not t:raise HTTPException(404,"tender_not_found")
   if t["status"]!="published" or t["closing_at"]<=datetime.now(timezone.utc):
    raise HTTPException(409,"bidding_closed")
   existing=c.execute("SELECT 1 FROM scm_tender_bids WHERE tender_id=%s AND supplier_id=%s",(tender_id,body.supplier_id)).fetchone()
   if existing:raise HTTPException(409,"supplier_already_bid")
   c.execute("INSERT INTO scm_tender_bids VALUES(%s,%s,%s,%s,%s,%s,%s)",
    (bid_id,tender_id,body.supplier_id,body.price,body.quality_score,body.notes,datetime.now(timezone.utc)))
   event(c,tender_id,"bid_submitted",{"bid_id":bid_id,"supplier_id":body.supplier_id})
  return {"id":bid_id,"tender_id":tender_id,"status":"submitted"}
 @router.post("/{tender_id}/close")
 def close(tender_id:str,authorization:str|None=Header(None)):
  auth("procure.scm.write",authorization)
  with conn() as c:
   schema(c)
   row=c.execute("UPDATE scm_tenders SET status='closed' WHERE id=%s AND status='published' RETURNING id,status",(tender_id,)).fetchone()
   if not row:raise HTTPException(409,"tender_not_closable")
   event(c,tender_id,"closed",{})
  return row
 @router.get("/{tender_id}/evaluation")
 def evaluate(tender_id:str,authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization)
  with conn() as c:
   schema(c)
   t=c.execute("SELECT * FROM scm_tenders WHERE id=%s",(tender_id,)).fetchone()
   if not t:raise HTTPException(404,"tender_not_found")
   if t["status"] not in ("closed","awarded"):raise HTTPException(409,"evaluation_requires_closed_tender")
   bids=c.execute("SELECT * FROM scm_tender_bids WHERE tender_id=%s",(tender_id,)).fetchall()
  if not bids:return {"tender_id":tender_id,"ranking":[]}
  low=min(b["price"] for b in bids)
  w=t["weights"]
  ranking=sorted(({"bid_id":str(b["id"]),"supplier_id":b["supplier_id"],
   "price":str(b["price"]),"score":round(float(low/b["price"])*w["price"]+
   b["quality_score"]*w["quality"]/100,4)} for b in bids),
   key=lambda x:(-x["score"],Decimal(x["price"]),x["supplier_id"]))
  return {"tender_id":tender_id,"ranking":ranking,"awarded_bid":str(t["awarded_bid"]) if t["awarded_bid"] else None}
 @router.post("/{tender_id}/award")
 def award(tender_id:str,body:AwardIn,authorization:str|None=Header(None)):
  auth("procure.scm.write",authorization)
  with conn() as c:
   schema(c)
   t=c.execute("SELECT status FROM scm_tenders WHERE id=%s FOR UPDATE",(tender_id,)).fetchone()
   if not t or t["status"]!="closed":raise HTTPException(409,"tender_not_awardable")
   b=c.execute("SELECT id FROM scm_tender_bids WHERE id=%s AND tender_id=%s",(body.bid_id,tender_id)).fetchone()
   if not b:raise HTTPException(404,"bid_not_found")
   c.execute("UPDATE scm_tenders SET status='awarded',awarded_bid=%s WHERE id=%s",(body.bid_id,tender_id))
   event(c,tender_id,"awarded",{"bid_id":body.bid_id,"reason":body.reason})
  return {"tender_id":tender_id,"status":"awarded","bid_id":body.bid_id}
 @router.get("/{tender_id}/audit")
 def audit(tender_id:str,authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization)
  with conn() as c:
   schema(c)
   return c.execute("SELECT action,details,occurred_at FROM scm_tender_events WHERE tender_id=%s ORDER BY occurred_at,id",(tender_id,)).fetchall()
 app.include_router(router)

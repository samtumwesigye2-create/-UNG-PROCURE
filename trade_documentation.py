"""Trade documentation and Incoterms validation for UNG SCM.
This records compliance evidence; it does not issue customs clearance.
"""
from datetime import datetime,timezone
from uuid import uuid4
from fastapi import APIRouter,Header,HTTPException
from pydantic import BaseModel,Field
from psycopg.types.json import Jsonb

TERMS={"EXW","FCA","CPT","CIP","DAP","DPU","DDP","FAS","FOB","CFR","CIF"}
WATER_ONLY={"FAS","FOB","CFR","CIF"}
DOCUMENT_TYPES={"commercial_invoice","packing_list","bill_of_lading","air_waybill",
 "certificate_of_origin","export_declaration","import_declaration","insurance_certificate",
 "inspection_certificate","license","other"}
router=APIRouter(prefix="/v1/scm/trade",tags=["Trade Documentation"])
class ShipmentIn(BaseModel):
 reference:str=Field(min_length=1,max_length=160)
 origin_country:str=Field(pattern=r"^[A-Z]{2}$")
 destination_country:str=Field(pattern=r"^[A-Z]{2}$")
 transport_mode:str
 incoterm:str
 currency:str=Field(pattern=r"^[A-Z]{3}$")
 declared_value:float=Field(ge=0)
 hs_code:str|None=Field(default=None,max_length=16)
class DocumentIn(BaseModel):
 document_type:str
 document_reference:str=Field(min_length=1,max_length=160)
 issuer:str=Field(min_length=1,max_length=160)
 issued_at:datetime
 metadata:dict=Field(default_factory=dict)
def validate_shipment(b):
 if b.incoterm not in TERMS:raise HTTPException(422,"unknown_incoterm")
 if b.transport_mode not in {"road","rail","air","sea","inland_waterway","multimodal"}:
  raise HTTPException(422,"unknown_transport_mode")
 if b.incoterm in WATER_ONLY and b.transport_mode not in {"sea","inland_waterway"}:
  raise HTTPException(422,"incoterm_requires_water_transport")
 return True
def install_trade_routes(app,conn,auth):
 def schema(c):
  c.execute("""CREATE TABLE IF NOT EXISTS scm_trade_shipments(
   id UUID PRIMARY KEY,reference TEXT UNIQUE NOT NULL,origin_country TEXT NOT NULL,
   destination_country TEXT NOT NULL,transport_mode TEXT NOT NULL,incoterm TEXT NOT NULL,
   currency TEXT NOT NULL,declared_value NUMERIC(18,2) NOT NULL,hs_code TEXT,
   status TEXT NOT NULL DEFAULT 'documentation_pending',created_at TIMESTAMPTZ NOT NULL)""")
  c.execute("""CREATE TABLE IF NOT EXISTS scm_trade_documents(
   id UUID PRIMARY KEY,shipment_id UUID NOT NULL REFERENCES scm_trade_shipments(id),
   document_type TEXT NOT NULL,document_reference TEXT NOT NULL,issuer TEXT NOT NULL,
   issued_at TIMESTAMPTZ NOT NULL,metadata JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL,
   UNIQUE(shipment_id,document_type,document_reference))""")
 @router.post("/shipments",status_code=201)
 def create(b:ShipmentIn,authorization:str|None=Header(None)):
  auth("procure.scm.write",authorization);validate_shipment(b)
  sid=str(uuid4())
  with conn() as c:
   schema(c)
   c.execute("""INSERT INTO scm_trade_shipments
   (id,reference,origin_country,destination_country,transport_mode,incoterm,currency,declared_value,hs_code,created_at)
   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
   (sid,b.reference,b.origin_country,b.destination_country,b.transport_mode,b.incoterm,
    b.currency,b.declared_value,b.hs_code,datetime.now(timezone.utc)))
  return {"id":sid,"status":"documentation_pending","customs_clearance":"not_verified"}
 @router.post("/shipments/{shipment_id}/documents",status_code=201)
 def add_document(shipment_id:str,b:DocumentIn,authorization:str|None=Header(None)):
  auth("procure.scm.write",authorization)
  if b.document_type not in DOCUMENT_TYPES:raise HTTPException(422,"unsupported_document_type")
  if b.issued_at.tzinfo is None:raise HTTPException(422,"issued_at_timezone_required")
  with conn() as c:
   schema(c)
   if not c.execute("SELECT 1 FROM scm_trade_shipments WHERE id=%s",(shipment_id,)).fetchone():
    raise HTTPException(404,"shipment_not_found")
   did=str(uuid4())
   c.execute("""INSERT INTO scm_trade_documents
   (id,shipment_id,document_type,document_reference,issuer,issued_at,metadata,created_at)
   VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
   (did,shipment_id,b.document_type,b.document_reference,b.issuer,b.issued_at,Jsonb(b.metadata),datetime.now(timezone.utc)))
  return {"id":did,"shipment_id":shipment_id,"status":"recorded"}
 @router.get("/shipments/{shipment_id}")
 def details(shipment_id:str,authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization)
  with conn() as c:
   schema(c)
   shipment=c.execute("SELECT * FROM scm_trade_shipments WHERE id=%s",(shipment_id,)).fetchone()
   if not shipment:raise HTTPException(404,"shipment_not_found")
   docs=c.execute("SELECT id,document_type,document_reference,issuer,issued_at,created_at FROM scm_trade_documents WHERE shipment_id=%s ORDER BY created_at",(shipment_id,)).fetchall()
  return {"shipment":shipment,"documents":docs,"customs_clearance":"not_verified",
          "notice":"Document presence does not establish legal compliance or customs release."}
 @router.get("/reference")
 def reference(authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization)
  return {"incoterms_2020":sorted(TERMS),"water_only":sorted(WATER_ONLY),
          "document_types":sorted(DOCUMENT_TYPES),"customs_integration":"not_configured"}
 app.include_router(router)

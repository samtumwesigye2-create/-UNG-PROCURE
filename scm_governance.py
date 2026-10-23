"""UNG-native supply chain governance, trade, sustainability and SCOR records.
No external vendor services required. Records persist in the configured PostgreSQL DB.
"""
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

CAPABILITIES = {
 "scm-foundations":"SCM Foundations","scor":"SCOR Framework",
 "strategic-sourcing":"Strategic Sourcing","procurement":"Procurement",
 "supplier-risk":"Supplier Risk Checks","open-tenders":"Open Tenders",
 "demand-forecasting":"Demand Forecasting","sop-ibp":"S&OP and IBP",
 "inventory":"Inventory","reordering":"Reordering",
 "operations":"Operations Management","warehouse":"Warehouse",
 "scm-analytics":"SCM Analytics","freight-networks":"Freight Networks",
 "incoterms":"Incoterms","export-logistics":"Export Logistics",
 "maritime":"Maritime Transport","trade-facilitation":"Trade Facilitation",
 "route-planning":"Route Planning","lpi":"Logistics Performance Index",
 "cyber-scrm":"Cyber Supply Chain Risk Management",
 "traceability":"Product Traceability","labour-rights":"Labour Rights",
 "food-loss":"Food Loss Analysis","scope3":"Scope 3 Carbon",
 "circularity":"Circularity","ai-resilience":"AI Supply Resilience"
}
SCOR_METRICS = {"perfect_order_rate":"percent","otif":"percent","order_cycle_hours":"hours",
 "cash_to_cash_days":"days","supply_chain_cost_ratio":"percent","inventory_turns":"ratio"}
INCOTERMS_2020 = ("EXW","FCA","CPT","CIP","DAP","DPU","DDP","FAS","FOB","CFR","CIF")
SEA_ONLY = {"FAS","FOB","CFR","CIF"}
router = APIRouter(prefix="/v1/scm", tags=["Supply Chain Governance"])

class RecordIn(BaseModel):
 reference: str = Field(min_length=1,max_length=160)
 data: dict[str,Any] = Field(default_factory=dict)
 source_system: str = Field(default="UNG-PROCURE",min_length=2,max_length=80)

def validate(kind,data):
 if kind not in CAPABILITIES: raise HTTPException(404,"unknown_capability")
 if kind=="incoterms":
  term=str(data.get("term","")).upper()
  if term not in INCOTERMS_2020: raise HTTPException(422,"invalid_incoterms_2020_rule")
  mode=data.get("mode")
  if mode and term in SEA_ONLY and mode not in ("sea","inland_waterway"):
   raise HTTPException(422,"incoterm_requires_water_transport")
 if kind=="scope3":
  if data.get("category") not in range(1,16): raise HTTPException(422,"scope3_category_must_be_1_to_15")
  for field in ("activity_quantity","emission_factor"):
   value=data.get(field)
   if not isinstance(value,(int,float)) or isinstance(value,bool) or value<0:
    raise HTTPException(422,field+"_must_be_nonnegative_number")
  if not data.get("activity_unit") or not data.get("factor_source"):
   raise HTTPException(422,"activity_unit_and_factor_source_required")
  data["calculated_kg_co2e"]=round(data["activity_quantity"]*data["emission_factor"],6)
 if kind=="scor":
  metric=data.get("metric")
  if metric not in SCOR_METRICS: raise HTTPException(422,"unknown_scor_metric")
  value=data.get("value")
  if not isinstance(value,(int,float)) or isinstance(value,bool) or value<0:
   raise HTTPException(422,"metric_value_must_be_nonnegative")
  if SCOR_METRICS[metric]=="percent" and value>100: raise HTTPException(422,"percentage_above_100")
 if kind=="open-tenders":
  if data.get("status","draft") not in ("draft","published","closed","awarded","cancelled"):
   raise HTTPException(422,"invalid_tender_status")
 if kind=="labour-rights":
  if data.get("status","pending") not in ("pending","compliant","noncompliant","remediation"):
   raise HTTPException(422,"invalid_compliance_status")
 return data

def install_scm_governance(app,conn,auth):
 def schema(c):
  c.execute("""CREATE TABLE IF NOT EXISTS ung_scm_governance (
   id UUID PRIMARY KEY, capability TEXT NOT NULL, reference TEXT NOT NULL,
   source_system TEXT NOT NULL, data JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL,
   UNIQUE(capability,reference))""")
  c.execute("CREATE INDEX IF NOT EXISTS idx_scm_governance_capability ON ung_scm_governance(capability,created_at DESC)")
 @router.get("/capabilities")
 def capabilities(authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization)
  return {"total":len(CAPABILITIES),"capabilities":CAPABILITIES,"scor_metrics":SCOR_METRICS,
          "incoterms_2020":INCOTERMS_2020}
 @router.post("/{kind}/records",status_code=201)
 def upsert(kind:str,body:RecordIn,authorization:str|None=Header(None)):
  auth("procure.scm.write",authorization)
  data=validate(kind,dict(body.data))
  with conn() as c:
   schema(c)
   row=c.execute("""INSERT INTO ung_scm_governance
     (id,capability,reference,source_system,data,created_at)
     VALUES (%s,%s,%s,%s,%s,%s)
     ON CONFLICT(capability,reference) DO UPDATE SET
     source_system=EXCLUDED.source_system,data=EXCLUDED.data
     RETURNING id,capability,reference,source_system,data,created_at""",
     (str(uuid4()),kind,body.reference,body.source_system,Jsonb(data),datetime.now(timezone.utc))).fetchone()
  return row
 @router.get("/{kind}/records")
 def records(kind:str,limit:int=Query(100,ge=1,le=500),authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization)
  if kind not in CAPABILITIES:raise HTTPException(404,"unknown_capability")
  with conn() as c:
   schema(c)
   return c.execute("SELECT id,capability,reference,source_system,data,created_at FROM ung_scm_governance WHERE capability=%s ORDER BY created_at DESC LIMIT %s",(kind,limit)).fetchall()
 @router.get("/{kind}/records/{reference}")
 def record(kind:str,reference:str,authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization)
  if kind not in CAPABILITIES:raise HTTPException(404,"unknown_capability")
  with conn() as c:
   schema(c)
   row=c.execute("SELECT id,capability,reference,source_system,data,created_at FROM ung_scm_governance WHERE capability=%s AND reference=%s",(kind,reference)).fetchone()
  if row is None:raise HTTPException(404,"record_not_found")
  return row
 app.include_router(router)

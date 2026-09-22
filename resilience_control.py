from datetime import datetime, timezone
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1/resilience", tags=["Resilient Supply Chain"])

def now():
    return datetime.now(timezone.utc)

def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))

class ResilienceAssessmentIn(BaseModel):
    node: str
    supplier_code: str | None = None
    supplier_tier: int = Field(1, ge=1, le=5)
    supplier_risk: float = Field(0, ge=0, le=100)
    logistics_risk: float = Field(0, ge=0, le=100)
    capacity_gap_pct: float = Field(0, ge=0)
    inventory_days: float = Field(30, ge=0)
    lead_time_variability_pct: float = Field(0, ge=0)
    alternate_supplier_count: int = Field(0, ge=0)
    recovery_time_hours: float = Field(0, ge=0)
    criticality: str = "medium"

class ActionUpdate(BaseModel):
    note: str = ""

def inventory_shortage_risk(days):
    d=float(days)
    if d <= 1: return 100.0
    if d <= 3: return 75.0
    if d <= 7: return 45.0
    if d <= 14: return 20.0
    return 5.0

def assess(b: ResilienceAssessmentIn):
    criticality=b.criticality.lower().strip()
    if criticality not in {"low","medium","high","critical"}:
        raise ValueError("criticality_must_be_low_medium_high_or_critical")
    inv=inventory_shortage_risk(b.inventory_days)
    cap=clamp(b.capacity_gap_pct)
    ltv=clamp(b.lead_time_variability_pct)
    base=(0.30*b.supplier_risk + 0.25*b.logistics_risk + 0.20*cap +
          0.15*inv + 0.10*ltv)
    multiplier={"low":0.85,"medium":1.0,"high":1.15,"critical":1.30}[criticality]
    mitigation=min(20.0,b.alternate_supplier_count*5.0)
    risk=clamp(base*multiplier-mitigation)
    status="red" if risk >= 70 else ("amber" if risk >= 40 else "green")
    actions=[]
    if b.supplier_risk >= 60 or b.alternate_supplier_count == 0:
        actions.append("qualify_alternate_supplier")
    if b.logistics_risk >= 60:
        actions.append("prepare_alternate_route_or_carrier")
    if b.capacity_gap_pct > 0:
        actions.append("rebalance_capacity_or_reschedule")
    if b.inventory_days <= 7:
        actions.append("protect_or_reallocate_inventory")
    if b.lead_time_variability_pct >= 40:
        actions.append("increase_lead_time_monitoring_and_safety_buffer")
    if b.recovery_time_hours > 24:
        actions.append("reduce_recovery_time_with_preapproved_continuity_playbook")
    if not actions:
        actions.append("monitor")
    return {
        "risk_score":round(risk,2),
        "continuity_score":round(100.0-risk,2),
        "status":status,
        "inventory_shortage_risk":inv,
        "recommended_actions":actions
    }

def init_resilience(conn):
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS procure_resilience_assessments(
          id UUID PRIMARY KEY,node TEXT NOT NULL,supplier_code TEXT NULL,supplier_tier INTEGER NOT NULL,
          supplier_risk DOUBLE PRECISION NOT NULL,logistics_risk DOUBLE PRECISION NOT NULL,
          capacity_gap_pct DOUBLE PRECISION NOT NULL,inventory_days DOUBLE PRECISION NOT NULL,
          lead_time_variability_pct DOUBLE PRECISION NOT NULL,alternate_supplier_count INTEGER NOT NULL,
          recovery_time_hours DOUBLE PRECISION NOT NULL,criticality TEXT NOT NULL,
          risk_score DOUBLE PRECISION NOT NULL,continuity_score DOUBLE PRECISION NOT NULL,
          status TEXT NOT NULL,recommended_actions JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS procure_resilience_actions(
          id UUID PRIMARY KEY,assessment_id UUID NOT NULL REFERENCES procure_resilience_assessments(id),
          action TEXT NOT NULL,status TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL,completed_at TIMESTAMPTZ NULL)""")

def install_resilience_routes(app, conn, auth, emit):
    @router.post("/assessments", status_code=201)
    def create_assessment(b:ResilienceAssessmentIn, authorization:str|None=Header(None)):
        auth("procure.production.write", authorization)
        try:
            result=assess(b)
        except ValueError as e:
            raise HTTPException(422,str(e))
        t=now(); aid=str(uuid4())
        with conn() as c:
            row=c.execute("""INSERT INTO procure_resilience_assessments(
              id,node,supplier_code,supplier_tier,supplier_risk,logistics_risk,capacity_gap_pct,
              inventory_days,lead_time_variability_pct,alternate_supplier_count,recovery_time_hours,
              criticality,risk_score,continuity_score,status,recommended_actions,created_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
              (aid,b.node,b.supplier_code,b.supplier_tier,b.supplier_risk,b.logistics_risk,
               b.capacity_gap_pct,b.inventory_days,b.lead_time_variability_pct,
               b.alternate_supplier_count,b.recovery_time_hours,b.criticality.lower(),
               result["risk_score"],result["continuity_score"],result["status"],
               result["recommended_actions"],t)).fetchone()
            for action in result["recommended_actions"]:
                c.execute("""INSERT INTO procure_resilience_actions
                  (id,assessment_id,action,status,note,created_at,completed_at)
                  VALUES(%s,%s,%s,'open','',%s,NULL)""",(str(uuid4()),aid,action,t))
        delivery={"status":"not_required"}
        if result["status"] in {"amber","red"}:
            delivery=emit("UNG-NEXUS","PROCURE.RESILIENCE.RISK_DETECTED",{
                "assessment_id":aid,"node":b.node,"supplier_code":b.supplier_code,
                **result
            })
        return {"assessment":row,"analysis":result,"integration_delivery":delivery}

    @router.get("/assessments")
    def list_assessments(status:str|None=None, authorization:str|None=Header(None)):
        auth("procure.production.read", authorization)
        with conn() as c:
            if status:
                return c.execute("""SELECT * FROM procure_resilience_assessments
                  WHERE status=%s ORDER BY created_at DESC LIMIT 500""",(status,)).fetchall()
            return c.execute("""SELECT * FROM procure_resilience_assessments
              ORDER BY created_at DESC LIMIT 500""").fetchall()

    @router.get("/control-tower")
    def control_tower(authorization:str|None=Header(None)):
        auth("procure.production.read", authorization)
        with conn() as c:
            totals=c.execute("""SELECT COUNT(*) total,
              COUNT(*) FILTER(WHERE status='red') red,
              COUNT(*) FILTER(WHERE status='amber') amber,
              COUNT(*) FILTER(WHERE status='green') green,
              COALESCE(AVG(continuity_score),100) continuity,
              COALESCE(AVG(recovery_time_hours),0) avg_recovery_hours
              FROM procure_resilience_assessments""").fetchone()
            open_actions=c.execute("""SELECT COUNT(*) n FROM procure_resilience_actions
              WHERE status='open'""").fetchone()["n"]
            critical=c.execute("""SELECT id,node,supplier_code,risk_score,continuity_score,status,
              capacity_gap_pct,inventory_days,recovery_time_hours,created_at
              FROM procure_resilience_assessments
              WHERE status IN ('red','amber') ORDER BY risk_score DESC,created_at DESC LIMIT 25""").fetchall()
        return {
            "workflow":["sense","assess","respond","recover"],
            "network":totals,
            "open_actions":open_actions,
            "priority_risks":critical,
            "generated_at":now()
        }

    @router.get("/actions")
    def list_actions(status:str="open", authorization:str|None=Header(None)):
        auth("procure.production.read", authorization)
        with conn() as c:
            return c.execute("""SELECT a.*,r.node,r.supplier_code,r.risk_score
              FROM procure_resilience_actions a JOIN procure_resilience_assessments r ON r.id=a.assessment_id
              WHERE a.status=%s ORDER BY r.risk_score DESC,a.created_at ASC LIMIT 500""",(status,)).fetchall()

    @router.post("/actions/{action_id}/complete")
    def complete_action(action_id:str,b:ActionUpdate,authorization:str|None=Header(None)):
        auth("procure.production.write", authorization); t=now()
        with conn() as c:
            row=c.execute("""UPDATE procure_resilience_actions SET status='complete',note=%s,completed_at=%s
              WHERE id=%s RETURNING *""",(b.note,t,action_id)).fetchone()
            if not row: raise HTTPException(404,"resilience_action_not_found")
        return row

    app.include_router(router)

from datetime import datetime, timezone
from fastapi import Header, HTTPException

def now(): return datetime.now(timezone.utc)

EXPECTED_TABLES=[
    "procure_demand_signals","procure_supplier_capacity","procure_supply_plans","procure_supply_plan_events",
    "procure_sourcing_strategies","procure_rfps","procure_rfp_vendors","procure_supplier_evaluations",
    "procure_contracts","procure_contract_amendments","procure_supplier_relationship_reviews",
    "procure_tco_assessments","procure_resilience_assessments","procure_resilience_actions","procure_supply_chain_workflows","procure_supply_chain_workflow_stages"
]

def install_extended_acceptance_routes(app,conn,auth):
    @app.get("/v1/acceptance/extended")
    def extended_acceptance(authorization:str|None=Header(None)):
        auth("procure.production.read",authorization)
        checks=[]
        with conn() as c:
            for table in EXPECTED_TABLES:
                exists=c.execute("SELECT to_regclass(%s) r",(table,)).fetchone()["r"]
                checks.append({"check":table,"status":"PASS" if exists else "FAIL"})
            # Supply-plan integrity: approved plans must have an approval timestamp.
            bad_approved=c.execute("""SELECT COUNT(*) n FROM procure_supply_plans
              WHERE status='approved' AND approved_at IS NULL""").fetchone()["n"]
            checks.append({"check":"approved_supply_plans_have_timestamp","status":"PASS" if bad_approved==0 else "FAIL","violations":bad_approved})
            # Supplier-capacity integrity.
            bad_capacity=c.execute("""SELECT COUNT(*) n FROM procure_supplier_capacity
              WHERE committed_units>maximum_units OR period_end<period_start""").fetchone()["n"]
            checks.append({"check":"supplier_capacity_valid","status":"PASS" if bad_capacity==0 else "FAIL","violations":bad_capacity})
            # Contract dates and amendment linkage.
            bad_contracts=c.execute("""SELECT COUNT(*) n FROM procure_contracts
              WHERE end_date IS NOT NULL AND end_date<start_date""").fetchone()["n"]
            checks.append({"check":"contract_date_integrity","status":"PASS" if bad_contracts==0 else "FAIL","violations":bad_contracts})
            # TCO arithmetic integrity.
            bad_tco=c.execute("""SELECT COUNT(*) n FROM procure_tco_assessments
              WHERE ABS(total_cost-(quantity*unit_price+freight+duties+handling+service_lifecycle+quality_cost+financing_cost))>0.01
                 OR (quantity>0 AND ABS(cost_per_unit-(total_cost/quantity))>0.01)""").fetchone()["n"]
            checks.append({"check":"tco_arithmetic_integrity","status":"PASS" if bad_tco==0 else "FAIL","violations":bad_tco})
            # Resilience score range integrity.
            bad_risk=c.execute("""SELECT COUNT(*) n FROM procure_resilience_assessments
              WHERE risk_score<0 OR risk_score>100 OR continuity_score<0 OR continuity_score>100""").fetchone()["n"]
            checks.append({"check":"resilience_score_integrity","status":"PASS" if bad_risk==0 else "FAIL","violations":bad_risk})
        status="PASS" if all(x["status"]=="PASS" for x in checks) else "FAIL"
        return {"service":"UNG-PROCURE","status":status,"checks":checks,"generated_at":now()}

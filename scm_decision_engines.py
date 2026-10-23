"""Deterministic SCM decision engines with explicit inputs, assumptions and provenance.
These calculations support human decisions; they do not certify customs or safety compliance.
"""
from heapq import heappush,heappop
from math import isfinite
from fastapi import APIRouter,Header,HTTPException
from pydantic import BaseModel,Field

router=APIRouter(prefix="/v1/scm/decisions",tags=["SCM Decision Engines"])
class Edge(BaseModel):
 origin:str
 destination:str
 cost:float=Field(ge=0)
 duration_hours:float=Field(ge=0)
 emissions_kg:float=Field(ge=0)
class RouteRequest(BaseModel):
 origin:str
 destination:str
 edges:list[Edge]=Field(min_length=1,max_length=5000)
 optimize_for:str="cost"
class SupplierAssessment(BaseModel):
 supplier_id:str
 financial_risk:float=Field(ge=0,le=100)
 delivery_risk:float=Field(ge=0,le=100)
 cyber_risk:float=Field(ge=0,le=100)
 labour_risk:float=Field(ge=0,le=100)
 concentration_risk:float=Field(ge=0,le=100)
class EmissionActivity(BaseModel):
 category:int=Field(ge=1,le=15)
 quantity:float=Field(ge=0)
 unit:str=Field(min_length=1)
 emission_factor:float=Field(ge=0)
 factor_unit:str=Field(min_length=1)
 factor_source:str=Field(min_length=1)
class EmissionsRequest(BaseModel):
 activities:list[EmissionActivity]=Field(min_length=1,max_length=1000)
class LossItem(BaseModel):
 sku:str
 produced:float=Field(ge=0)
 lost:float=Field(ge=0)
 unit:str
class LossRequest(BaseModel):
 items:list[LossItem]=Field(min_length=1,max_length=1000)
class PlanRequest(BaseModel):
 demand:float=Field(ge=0)
 supply:float=Field(ge=0)
 opening_inventory:float=Field(ge=0)
 safety_stock:float=Field(ge=0)
 unit_shortage_cost:float=Field(ge=0)
 unit_holding_cost:float=Field(ge=0)

def shortest_route(req:RouteRequest):
 if req.optimize_for not in {"cost","duration_hours","emissions_kg"}:
  raise HTTPException(422,"unsupported_objective")
 graph={}
 for edge in req.edges:
  graph.setdefault(edge.origin,[]).append(edge)
 # (objective, hops, path, node, totals) yields deterministic tie-breaking.
 queue=[(0.0,0,(req.origin,),req.origin,(0.0,0.0,0.0))]
 best={}
 while queue:
  score,hops,path,node,totals=heappop(queue)
  if node in best and best[node]<=(score,hops,path):continue
  best[node]=(score,hops,path)
  if node==req.destination:
   return {"reachable":True,"path":list(path),"objective":req.optimize_for,
    "cost":round(totals[0],6),"duration_hours":round(totals[1],6),
    "emissions_kg":round(totals[2],6),
    "assumptions":"Directed static network; excludes capacity, traffic, border restrictions and schedules"}
  for e in graph.get(node,[]):
   if e.destination in path:continue
   w=getattr(e,req.optimize_for)
   if not all(isfinite(v) for v in (e.cost,e.duration_hours,e.emissions_kg)):
    raise HTTPException(422,"nonfinite_edge_metric")
   t=(totals[0]+e.cost,totals[1]+e.duration_hours,totals[2]+e.emissions_kg)
   heappush(queue,(score+w,hops+1,path+(e.destination,),e.destination,t))
 return {"reachable":False,"path":[],"objective":req.optimize_for}

def supplier_score(a:SupplierAssessment):
 weights={"financial_risk":0.25,"delivery_risk":0.25,"cyber_risk":0.2,
          "labour_risk":0.15,"concentration_risk":0.15}
 score=round(sum(getattr(a,k)*v for k,v in weights.items()),2)
 return {"supplier_id":a.supplier_id,"risk_score":score,
  "risk_level":"high" if score>=70 else "medium" if score>=40 else "low",
  "weights":weights,"method":"Transparent weighted heuristic, not a validated predictive model"}

def emissions(req:EmissionsRequest):
 by_category={}
 for a in req.activities:
  if not isfinite(a.quantity) or not isfinite(a.emission_factor):
   raise HTTPException(422,"nonfinite_emission_value")
  # Factor unit must match the activity unit, e.g. kgCO2e/tonne-km.
  if a.factor_unit!="kgCO2e/"+a.unit:raise HTTPException(422,"factor_unit_mismatch")
  by_category[a.category]=by_category.get(a.category,0)+a.quantity*a.emission_factor
 return {"total_kg_co2e":round(sum(by_category.values()),6),
  "by_category_kg_co2e":{str(k):round(v,6) for k,v in sorted(by_category.items())},
  "method":"Activity multiplied by supplied factor; factors not independently verified"}

def food_loss(req:LossRequest):
 total_produced=sum(x.produced for x in req.items)
 total_lost=sum(x.lost for x in req.items)
 for x in req.items:
  if x.lost>x.produced:raise HTTPException(422,"loss_exceeds_production")
 units={x.unit for x in req.items}
 return {"by_sku":[{"sku":x.sku,"unit":x.unit,"loss_rate_percent":
  round(100*x.lost/x.produced,4) if x.produced else 0} for x in req.items],
  "aggregate_loss_rate_percent":round(100*total_lost/total_produced,4) if total_produced else 0
   if len(units)==1 else None,
  "aggregate_unit":next(iter(units)) if len(units)==1 else None}

def plan(req:PlanRequest):
 available=req.supply+req.opening_inventory
 fulfilled=min(available,req.demand)
 shortage=max(0,req.demand-available)
 ending=max(0,available-req.demand)
 return {"fulfilled":fulfilled,"shortage":shortage,"ending_inventory":ending,
  "safety_stock_gap":max(0,req.safety_stock-ending),
  "estimated_cost":round(shortage*req.unit_shortage_cost+ending*req.unit_holding_cost,4),
  "method":"Single-period deterministic scenario; no probabilistic demand forecast"}

def install_decision_routes(app,auth):
 @router.post("/route")
 def route(b:RouteRequest,authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization);return shortest_route(b)
 @router.post("/supplier-risk")
 def risk(b:SupplierAssessment,authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization);return supplier_score(b)
 @router.post("/scope3")
 def scope3(b:EmissionsRequest,authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization);return emissions(b)
 @router.post("/food-loss")
 def loss(b:LossRequest,authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization);return food_loss(b)
 @router.post("/plan")
 def scenario(b:PlanRequest,authorization:str|None=Header(None)):
  auth("procure.scm.read",authorization);return plan(b)
 app.include_router(router)

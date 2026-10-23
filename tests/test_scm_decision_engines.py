import pytest
from fastapi import HTTPException
from app import app
from scm_decision_engines import (
 Edge,RouteRequest,shortest_route,SupplierAssessment,supplier_score,
 EmissionActivity,EmissionsRequest,emissions,LossItem,LossRequest,food_loss,
 PlanRequest,plan)

def test_route_minimum_cost():
 r=shortest_route(RouteRequest(origin="A",destination="C",edges=[
  Edge(origin="A",destination="C",cost=30,duration_hours=1,emissions_kg=5),
  Edge(origin="A",destination="B",cost=5,duration_hours=2,emissions_kg=2),
  Edge(origin="B",destination="C",cost=6,duration_hours=2,emissions_kg=2)]))
 assert r["path"]==["A","B","C"] and r["cost"]==11

def test_supplier_risk():
 a=SupplierAssessment(supplier_id="SUP-1",financial_risk=100,delivery_risk=100,
  cyber_risk=100,labour_risk=100,concentration_risk=100)
 assert supplier_score(a)["risk_score"]==100

def test_scope3_factor_units():
 a=EmissionActivity(category=4,quantity=20,unit="tonne-km",emission_factor=0.5,
  factor_unit="kgCO2e/tonne-km",factor_source="source")
 assert emissions(EmissionsRequest(activities=[a]))["total_kg_co2e"]==10
 with pytest.raises(HTTPException):
  emissions(EmissionsRequest(activities=[a.model_copy(update={"factor_unit":"kgCO2e/kg"})]))

def test_food_loss_no_mixed_units():
 r=food_loss(LossRequest(items=[LossItem(sku="A",produced=100,lost=10,unit="kg"),
  LossItem(sku="B",produced=100,lost=20,unit="pieces")]))
 assert r["aggregate_loss_rate_percent"] is None

def test_sop_scenario():
 r=plan(PlanRequest(demand=100,supply=60,opening_inventory=10,safety_stock=5,
  unit_shortage_cost=3,unit_holding_cost=1))
 assert r["shortage"]==30 and r["estimated_cost"]==90

def test_routes_mounted():
 paths={r.path for r in app.routes}
 assert all("/v1/scm/decisions/"+p in paths for p in
  ("route","supplier-risk","scope3","food-loss","plan"))

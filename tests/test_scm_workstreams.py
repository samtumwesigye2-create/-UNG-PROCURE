import pytest
from fastapi import HTTPException
from app import app
from scm_workstreams import GROUPS,INDEX,validate,STATES

def test_three_groups_cover_fifteen_workstreams():
    assert len(GROUPS)==3
    assert all(len(g)==5 for g in GROUPS.values())
    assert len(INDEX)==15

def test_routes_mounted():
    paths={r.path for r in app.routes}
    assert "/v1/scm/workstreams/catalog" in paths
    assert "/v1/scm/workstreams/{capability}/records" in paths
    assert "/v1/scm/workstreams/{capability}/records/{reference}/transition" in paths

def test_all_workstreams_require_evidence_fields():
    for capability in INDEX:
        with pytest.raises(HTTPException) as exc:
            validate(capability,{})
        assert exc.value.status_code==422

def test_scope3_calculation():
    d={"category":4,"activity_quantity":120,"activity_unit":"tonne-km",
       "emission_factor":0.25,"factor_source":"documented source"}
    assert validate("scope3-accounting",d)["calculated_kg_co2e"]==30

def test_ai_score_range():
    with pytest.raises(HTTPException):
        validate("ai-resilience-analysis",{"scenario":"flood","assumptions":{},
         "risk_score":101,"model_version":"1"})

def test_approval_transitions():
    assert "approved" not in STATES["draft"]
    assert "approved" in STATES["reviewed"]
    assert not STATES["approved"]

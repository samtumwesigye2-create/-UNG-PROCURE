from fastapi import HTTPException
from scm_governance import CAPABILITIES, validate, INCOTERMS_2020

def test_catalog_has_all_27_capabilities():
    assert len(CAPABILITIES) == 27
    assert len(INCOTERMS_2020) == 11

def test_incoterms_validation():
    assert validate("incoterms", {"term":"FOB","mode":"sea"})["term"]=="FOB"
    try:
        validate("incoterms", {"term":"FOB","mode":"air"})
    except HTTPException as e:
        assert e.status_code==422
    else:
        assert False, "Sea-only term accepted for air"

def test_scope3_calculation_and_provenance():
    result=validate("scope3",{"category":4,"activity_quantity":100,
        "emission_factor":0.45,"activity_unit":"tonne-km",
        "factor_source":"verified dataset"})
    assert result["calculated_kg_co2e"]==45
    try:
        validate("scope3",{"category":16,"activity_quantity":10,"emission_factor":1,
            "activity_unit":"km","factor_source":"dataset"})
    except HTTPException as e:
        assert e.status_code==422
    else:
        assert False, "Invalid category accepted"

def test_scor_and_tenders():
    assert validate("scor",{"metric":"otif","value":97})["value"]==97
    assert validate("open-tenders",{"status":"published"})["status"]=="published"

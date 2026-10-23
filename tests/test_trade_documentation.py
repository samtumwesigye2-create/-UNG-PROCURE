from fastapi import HTTPException
from app import app
from trade_documentation import ShipmentIn,validate_shipment

def test_trade_routes_mounted():
    paths={r.path for r in app.routes}
    assert "/v1/scm/trade/shipments" in paths
    assert "/v1/scm/trade/shipments/{shipment_id}/documents" in paths
    assert "/v1/scm/trade/shipments/{shipment_id}" in paths

def test_incoterm_modes():
    data=dict(reference="EXP-1",origin_country="UG",destination_country="KE",
              transport_mode="sea",incoterm="FOB",currency="USD",declared_value=100)
    assert validate_shipment(ShipmentIn(**data))
    data["transport_mode"]="air"
    try:validate_shipment(ShipmentIn(**data))
    except HTTPException as exc:assert exc.status_code==422
    else:assert False,"FOB accepted for air transport"

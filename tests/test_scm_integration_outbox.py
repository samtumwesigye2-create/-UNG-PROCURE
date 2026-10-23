from app import app
from scm_integration_outbox import TARGETS
from scm_workstreams import INDEX

def test_every_workstream_has_allowed_destinations():
    assert len(INDEX)==15
    assert all(INDEX[c][0] in TARGETS for c in INDEX)
    assert all(len(TARGETS[INDEX[c][0]])>=2 for c in INDEX)

def test_integration_routes_registered():
    paths={r.path for r in app.routes}
    assert "/v1/scm/integration/queue" in paths
    assert "/v1/scm/integration/events/{event_id}/deliver" in paths
    assert "/v1/scm/integration/events" in paths

def test_supplier_data_not_routed_to_vector_by_default():
    assert "UNG-SENTINEL" in TARGETS["supplier-governance"]
    assert "UNG-VECTOR" not in TARGETS["supplier-governance"]

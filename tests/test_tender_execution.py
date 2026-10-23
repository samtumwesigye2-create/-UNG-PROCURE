from datetime import datetime,timedelta,timezone
from fastapi.testclient import TestClient
from app import app

def test_tender_requires_janus():
    c=TestClient(app)
    assert c.post("/v1/scm/tenders",json={}).status_code in (401,403)

def test_tender_validation_before_database():
    from tender_execution import TenderIn
    from pydantic import ValidationError
    try:
        TenderIn(reference="RFQ-1",title="Parts",closing_at=datetime.now(timezone.utc)+timedelta(days=5),currency="US")
    except ValidationError:
        pass
    else:
        assert False,"Invalid currency accepted"

def test_tender_routes_registered():
    paths={r.path for r in app.routes}
    for p in ("/v1/scm/tenders","/v1/scm/tenders/{tender_id}/publish",
              "/v1/scm/tenders/{tender_id}/bids",
              "/v1/scm/tenders/{tender_id}/evaluation",
              "/v1/scm/tenders/{tender_id}/award",
              "/v1/scm/tenders/{tender_id}/audit"):
        assert p in paths

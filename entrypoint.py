from app import app
from inbound_layers import router as inbound_router
from procure_kpis import router as procure_kpis_router

app.include_router(inbound_router)
app.include_router(procure_kpis_router)

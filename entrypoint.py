from app import app
from inbound_layers import router as inbound_router
from procure_kpis import router as procure_kpis_router
from supplier_profiles import router as supplier_profiles_router
from purchasing_documents import router as purchasing_documents_router
from procure_matching import router as procure_matching_router

app.include_router(inbound_router)
app.include_router(procure_kpis_router)
app.include_router(supplier_profiles_router)
app.include_router(purchasing_documents_router)
app.include_router(procure_matching_router)

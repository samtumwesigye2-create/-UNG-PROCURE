from app import app
from inbound_layers import router as inbound_router

app.include_router(inbound_router)

"""Canonical PROCURE planning events for UNG-NEXUS.

Keeps planning-domain payloads stable so MPS/MRP can feed VECTOR and analytics
without coupling consumers to PROCURE database tables.
"""
from datetime import datetime, timezone
from uuid import uuid4

SCHEMA_VERSION = "1.0"

def _event(message_type: str, payload: dict, target_system: str) -> dict:
    return {
        "source_system": "UNG-PROCURE",
        "target_system": target_system,
        "message_type": message_type,
        "message_id": str(uuid4()),
        "correlation_id": payload.get("correlation_id"),
        "trace_id": payload.get("trace_id"),
        "schema_version": SCHEMA_VERSION,
        "priority": 50,
        "classification": "internal",
        "payload": {
            **payload,
            "event_time": datetime.now(timezone.utc).isoformat(),
        },
    }

def mps_released(mps: dict) -> dict:
    return _event("planning.mps.released", {
        "mps_id": str(mps["id"]),
        "product_sku": mps["product_sku"],
        "period_start": mps["period_start"].isoformat(),
        "planned_qty": float(mps["planned_qty"]),
        "actual_qty": float(mps.get("actual_qty") or 0),
        "work_center": mps.get("work_center"),
        "source": mps.get("source"),
        "status": mps.get("status"),
    }, "UNG-VECTOR")

def mrp_completed(run: dict, requirements: list[dict]) -> dict:
    return _event("planning.mrp.completed", {
        "run_id": str(run["id"]),
        "product_sku": run["product_sku"],
        "period_start": run["period_start"].isoformat(),
        "planned_qty": float(run["planned_qty"]),
        "requirements": [{
            "component_sku": x["component_sku"],
            "gross_requirement": float(x["gross_requirement"]),
            "on_hand": float(x["on_hand"]),
            "scheduled_receipts": float(x["scheduled_receipts"]),
            "safety_stock": float(x["safety_stock"]),
            "net_requirement": float(x["net_requirement"]),
            "planned_order_qty": float(x["planned_order_qty"]),
        } for x in requirements],
    }, "UNG-VECTOR")

def production_reported(order: dict) -> dict:
    return _event("planning.production.reported", {
        "order_id": str(order["id"]),
        "order_number": order["order_number"],
        "product_sku": order["product_sku"],
        "planned_qty": float(order["planned_qty"]),
        "completed_qty": float(order["completed_qty"]),
        "good_qty": float(order["good_qty"]),
        "scrap_qty": float(order["scrap_qty"]),
        "downtime_minutes": float(order["downtime_minutes"]),
        "status": order["status"],
        "work_center": order.get("work_center"),
    }, "UNG-NOVA")

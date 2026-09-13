# UNG-PROCURE Demand Intelligence Design

## Purpose
Add demand classification, forecasting, and replenishment recommendation capability to UNG-PROCURE without disrupting existing requisition, bidding, award, purchase-order, matching, supplier, JANUS, NEXUS, MIDAS, or VECTOR behavior.

## Scope
The first implementation phase covers:

1. Demand-history analysis per SKU.
2. ADI and CV² calculation.
3. Classification into Smooth, Erratic, Lumpy, or Intermittent demand.
4. Forecast generation using deterministic baseline models appropriate to the available history.
5. Forecast-quality metrics.
6. Safety-stock and reorder-point calculations.
7. Replenishment recommendations that can be consumed by the existing procurement flow.
8. NEXUS event emission for replenishment recommendations.
9. Unit and API-level acceptance tests.

Advanced statistical packages such as SARIMA/Holt-Winters, supplier performance scoring, contracts/SLA, and MIDAS AP are intentionally out of this implementation phase and remain separate follow-on work.

## Architecture
Create a focused `demand_intelligence.py` module. It owns pure calculation logic plus database initialization for demand/replenishment records. `app.py` owns HTTP endpoints, JANUS authorization, persistence orchestration, and NEXUS event emission using the service's existing `emit()` helper.

The module must not call NEXUS, VECTOR, MIDAS, or JANUS directly. This keeps forecasting failures isolated from ordinary procurement operations and preserves existing service boundaries.

## Data Model

### demand_history
- `id UUID PRIMARY KEY`
- `sku TEXT NOT NULL`
- `period_start TIMESTAMPTZ NOT NULL`
- `quantity DOUBLE PRECISION NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL`
- unique `(sku, period_start)`

### demand_recommendations
- `id UUID PRIMARY KEY`
- `sku TEXT NOT NULL`
- `demand_class TEXT NOT NULL`
- `adi DOUBLE PRECISION NOT NULL`
- `cv2 DOUBLE PRECISION NOT NULL`
- `forecast_quantity DOUBLE PRECISION NOT NULL`
- `forecast_model TEXT NOT NULL`
- `mae DOUBLE PRECISION`
- `wape DOUBLE PRECISION`
- `on_hand DOUBLE PRECISION NOT NULL`
- `inbound DOUBLE PRECISION NOT NULL`
- `allocated DOUBLE PRECISION NOT NULL`
- `supplier_lead_time_days DOUBLE PRECISION NOT NULL`
- `safety_stock DOUBLE PRECISION NOT NULL`
- `reorder_point DOUBLE PRECISION NOT NULL`
- `recommended_order_quantity DOUBLE PRECISION NOT NULL`
- `stockout_risk TEXT NOT NULL`
- `status TEXT NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL`

## Classification Rules
Use the approved thresholds:

- Smooth: `ADI < 1.32` and `CV² < 0.49`
- Erratic: `ADI < 1.32` and `CV² >= 0.49`
- Lumpy: `ADI >= 1.32` and `CV² < 0.49`
- Intermittent: `ADI >= 1.32` and `CV² >= 0.49`

ADI is the average interval between periods containing non-zero demand. CV² is squared coefficient of variation over non-zero demand quantities. Empty or all-zero histories are handled explicitly rather than divided by zero.

## Forecasting Baseline
This phase implements deterministic, dependency-light baselines:

- Smooth: simple exponential smoothing.
- Erratic: weighted moving average.
- Lumpy: Croston-style intermittent forecast.
- Intermittent: SBA-corrected Croston forecast.

The service backtests applicable candidate baselines when sufficient history exists and records MAE and WAPE. The implementation must never claim SARIMA/Holt-Winters support until those algorithms are actually present and tested.

## Replenishment
Inputs:

- forecast demand per analysis period
- on-hand inventory
- inbound inventory
- allocated inventory
- supplier lead time in days
- service-level factor
- review period in days

Calculations:

- `available = on_hand + inbound - allocated`
- `lead_time_demand = forecast_quantity * (supplier_lead_time_days / review_period_days)`
- `safety_stock = service_level_factor * demand_stddev * sqrt(max(supplier_lead_time_days / review_period_days, 0))`
- `reorder_point = lead_time_demand + safety_stock`
- reorder when `available <= reorder_point`
- recommended quantity is enough to restore stock to `reorder_point + forecast_quantity`, never below zero

Risk is HIGH when available stock is below expected lead-time demand, MEDIUM when below reorder point, otherwise LOW.

## API Surface

### `POST /v1/demand/history`
Permission: `procure.demand.write`
Stores one SKU/period quantity sample idempotently by `(sku, period_start)`.

### `GET /v1/demand/history/{sku}`
Permission: `procure.demand.read`
Returns ordered history.

### `POST /v1/demand/analyze/{sku}`
Permission: `procure.demand.write`
Accepts inventory and lead-time inputs, analyzes stored history, persists a recommendation, and emits `PROCURE.REPLENISHMENT.RECOMMENDED` to `UNG-VECTOR` through NEXUS when a reorder is recommended. The response includes event-delivery status but recommendation creation does not fail if NEXUS is unavailable.

### `GET /v1/demand/recommendations`
Permission: `procure.demand.read`
Returns recent recommendations.

`/v1/system` must advertise demand classification, baseline forecasting, and replenishment recommendation capabilities only after these endpoints exist.

## Error Handling
- Reject negative demand quantities or inventory inputs with HTTP 422.
- Reject non-positive review periods and negative lead times with HTTP 422.
- Return 404-style domain error when analysis is requested for a SKU with no history.
- All-zero demand is valid and yields zero forecast/reorder demand.
- NEXUS failures are returned as integration metadata and persisted recommendation data remains valid.

## Testing
Unit tests cover:
- all four ADI/CV² classes
- empty/all-zero histories
- forecast baselines
- MAE/WAPE edge cases
- safety stock/reorder calculations
- inbound stock preventing duplicate reorder pressure
- allocated stock increasing reorder pressure
- high/medium/low stockout risk

API/integration tests cover:
- module initialization registered from startup
- new capabilities exposed in `/v1/system`
- authorization permissions assigned to endpoints
- NEXUS event type and target are correct
- existing procurement capabilities and flows remain unchanged

## Success Criteria
The feature is complete only when tests prove classification, forecasting, replenishment, API wiring, and non-breaking integration with existing UNG-PROCURE behavior. Design status alone must never be reported as operational production status.

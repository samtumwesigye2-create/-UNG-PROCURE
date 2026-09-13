# Demand Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add tested demand classification, baseline forecasting, and replenishment recommendations to UNG-PROCURE while preserving all existing procurement flows.

**Architecture:** Add a focused `demand_intelligence.py` module containing deterministic pure calculations and schema initialization. Wire it through `app.py` for persistence, JANUS authorization, and NEXUS emission; existing procurement, matching, supplier, MIDAS, and VECTOR integrations remain unchanged.

**Tech Stack:** Python 3, FastAPI 0.116.1, Pydantic 2.11.7, psycopg 3.2.9, standard library math/statistics, pytest-style tests already used by the repository.

**Spec:** `docs/superpowers/specs/2026-09-13-demand-intelligence-design.md`

## Global Constraints

- Preserve all existing UNG-PROCURE endpoints and capability strings.
- Do not add heavy forecasting dependencies in this phase.
- Do not claim SARIMA, Holt-Winters, supplier-SLA, or MIDAS-AP functionality in this phase.
- Calculation functions must be deterministic and directly unit-testable without database or network access.
- NEXUS delivery failure must not roll back a valid replenishment recommendation.
- Negative demand/inventory inputs are invalid; all-zero demand history is valid.
- New JANUS permissions are exactly `procure.demand.read` and `procure.demand.write`.

---

### Task 1: Demand classification and metrics core

**Files:**
- Create: `demand_intelligence.py`
- Create: `tests/test_demand_intelligence.py`

**Interfaces:**
- Produces: `calculate_adi(history: list[float]) -> float`
- Produces: `calculate_cv2(history: list[float]) -> float`
- Produces: `classify_demand(history: list[float]) -> dict`
- Produces: `mae(actual: list[float], predicted: list[float]) -> float | None`
- Produces: `wape(actual: list[float], predicted: list[float]) -> float | None`

- [ ] **Step 1: Write failing tests for ADI/CV² boundaries and zero history**

```python
from demand_intelligence import calculate_adi, calculate_cv2, classify_demand


def test_all_zero_history_is_smooth_zero_demand():
    result = classify_demand([0, 0, 0, 0])
    assert result["demand_class"] == "smooth"
    assert result["adi"] == 0.0
    assert result["cv2"] == 0.0


def test_erratic_boundary():
    result = classify_demand([1, 10, 1, 10, 1, 10])
    assert result["adi"] < 1.32
    assert result["cv2"] >= 0.49
    assert result["demand_class"] == "erratic"


def test_lumpy_and_intermittent_classification():
    lumpy = classify_demand([2, 0, 2, 0, 2, 0, 2])
    intermittent = classify_demand([1, 0, 10, 0, 1, 0, 10])
    assert lumpy["adi"] >= 1.32 and lumpy["cv2"] < 0.49
    assert lumpy["demand_class"] == "lumpy"
    assert intermittent["adi"] >= 1.32 and intermittent["cv2"] >= 0.49
    assert intermittent["demand_class"] == "intermittent"
```

- [ ] **Step 2: Run the focused tests and verify import failure**

Run: `pytest tests/test_demand_intelligence.py -v`
Expected: FAIL because `demand_intelligence` does not exist.

- [ ] **Step 3: Implement deterministic classification and error metrics**

```python
from math import sqrt
from statistics import mean, pstdev

ADI_THRESHOLD = 1.32
CV2_THRESHOLD = 0.49


def _clean(history):
    values = [float(v) for v in history]
    if any(v < 0 for v in values):
        raise ValueError("demand_must_be_non_negative")
    return values


def calculate_adi(history):
    values = _clean(history)
    indexes = [i for i, value in enumerate(values) if value > 0]
    if not indexes:
        return 0.0
    if len(indexes) == 1:
        return float(len(values))
    gaps = [b - a for a, b in zip(indexes, indexes[1:])]
    return float(mean(gaps))


def calculate_cv2(history):
    values = [v for v in _clean(history) if v > 0]
    if not values or mean(values) == 0:
        return 0.0
    return float((pstdev(values) / mean(values)) ** 2)


def classify_demand(history):
    adi = calculate_adi(history)
    cv2 = calculate_cv2(history)
    if adi == 0.0:
        demand_class = "smooth"
    elif adi < ADI_THRESHOLD and cv2 < CV2_THRESHOLD:
        demand_class = "smooth"
    elif adi < ADI_THRESHOLD and cv2 >= CV2_THRESHOLD:
        demand_class = "erratic"
    elif adi >= ADI_THRESHOLD and cv2 < CV2_THRESHOLD:
        demand_class = "lumpy"
    else:
        demand_class = "intermittent"
    return {"adi": adi, "cv2": cv2, "demand_class": demand_class}


def mae(actual, predicted):
    if not actual or len(actual) != len(predicted):
        return None
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual)


def wape(actual, predicted):
    if not actual or len(actual) != len(predicted):
        return None
    denominator = sum(abs(a) for a in actual)
    if denominator == 0:
        return 0.0
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / denominator
```

- [ ] **Step 4: Run tests and verify pass**

Run: `pytest tests/test_demand_intelligence.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add demand_intelligence.py tests/test_demand_intelligence.py
git commit -m "feat: add demand classification core"
```

### Task 2: Forecasting baselines

**Files:**
- Modify: `demand_intelligence.py`
- Modify: `tests/test_demand_intelligence.py`

**Interfaces:**
- Produces: `forecast_demand(history: list[float], demand_class: str | None = None) -> dict`
- Returns keys: `forecast_quantity`, `forecast_model`, `mae`, `wape`

- [ ] **Step 1: Add failing forecast tests**

```python
from demand_intelligence import forecast_demand


def test_smooth_forecast_uses_exponential_smoothing():
    result = forecast_demand([10, 11, 10, 12, 11], "smooth")
    assert result["forecast_model"] == "simple_exponential_smoothing"
    assert result["forecast_quantity"] > 0


def test_intermit_forecasts_are_non_negative():
    croston = forecast_demand([5, 0, 0, 5, 0, 0, 5], "lumpy")
    sba = forecast_demand([1, 0, 0, 10, 0, 0, 1], "intermittent")
    assert croston["forecast_model"] == "croston"
    assert sba["forecast_model"] == "sba_croston"
    assert croston["forecast_quantity"] >= 0
    assert sba["forecast_quantity"] >= 0
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `pytest tests/test_demand_intelligence.py -k forecast -v`
Expected: FAIL because `forecast_demand` is missing.

- [ ] **Step 3: Implement dependency-light baselines**

Implement private helpers `_ses`, `_weighted_moving_average`, `_croston`, `_sba_croston` and `forecast_demand`. Use alpha `0.2` for SES/Croston and weights `[0.2, 0.3, 0.5]` over the latest three periods for weighted moving average. Produce one-step rolling predictions for MAE/WAPE when at least three observations exist.

- [ ] **Step 4: Run complete demand-intelligence unit tests**

Run: `pytest tests/test_demand_intelligence.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add demand_intelligence.py tests/test_demand_intelligence.py
git commit -m "feat: add demand forecasting baselines"
```

### Task 3: Replenishment calculations

**Files:**
- Modify: `demand_intelligence.py`
- Modify: `tests/test_demand_intelligence.py`

**Interfaces:**
- Produces: `calculate_replenishment(history, forecast_quantity, on_hand, inbound, allocated, supplier_lead_time_days, review_period_days=30.0, service_level_factor=1.65) -> dict`
- Returns: `available`, `safety_stock`, `reorder_point`, `recommended_order_quantity`, `stockout_risk`, `status`

- [ ] **Step 1: Add failing replenishment tests**

```python
from demand_intelligence import calculate_replenishment


def test_inbound_stock_can_prevent_reorder():
    result = calculate_replenishment([100, 110, 90], 100, 50, 300, 0, 30)
    assert result["status"] == "no_reorder"
    assert result["recommended_order_quantity"] == 0.0


def test_allocated_stock_increases_reorder_pressure():
    result = calculate_replenishment([100, 110, 90], 100, 150, 0, 125, 30)
    assert result["status"] == "reorder"
    assert result["recommended_order_quantity"] > 0


def test_stockout_risk_is_high_below_lead_time_demand():
    result = calculate_replenishment([100, 100, 100], 100, 10, 0, 0, 30)
    assert result["stockout_risk"] == "high"
```

- [ ] **Step 2: Run and verify failure**

Run: `pytest tests/test_demand_intelligence.py -k replenishment -v`
Expected: FAIL because `calculate_replenishment` is missing.

- [ ] **Step 3: Implement the approved replenishment formula**

Validate all inventory inputs are non-negative, `supplier_lead_time_days >= 0`, and `review_period_days > 0`; compute available stock, lead-time demand, demand standard deviation, safety stock, reorder point, order quantity, and high/medium/low risk exactly as defined in the spec.

- [ ] **Step 4: Run full unit suite**

Run: `pytest tests/test_demand_intelligence.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add demand_intelligence.py tests/test_demand_intelligence.py
git commit -m "feat: add replenishment recommendations"
```

### Task 4: Persist demand history and recommendations

**Files:**
- Modify: `demand_intelligence.py`
- Modify: `app.py`
- Create: `tests/test_demand_api_contract.py`

**Interfaces:**
- Produces: `init_demand_intelligence(conn_factory) -> None`
- Adds models: `DemandHistoryIn`, `DemandAnalysisIn`
- Adds endpoints: `POST /v1/demand/history`, `GET /v1/demand/history/{sku}`, `POST /v1/demand/analyze/{sku}`, `GET /v1/demand/recommendations`

- [ ] **Step 1: Add contract tests that inspect module and app source**

```python
from pathlib import Path


def test_app_registers_demand_intelligence():
    text = Path("app.py").read_text()
    assert "init_demand_intelligence" in text
    assert "procure.demand.read" in text
    assert "procure.demand.write" in text
    assert "/v1/demand/analyze/{sku}" in text


def test_schema_contains_unique_sku_period():
    text = Path("demand_intelligence.py").read_text()
    assert "UNIQUE(sku,period_start)" in text.replace(" ", "")
```

- [ ] **Step 2: Run contract tests and verify failure**

Run: `pytest tests/test_demand_api_contract.py -v`
Expected: FAIL before API/schema wiring exists.

- [ ] **Step 3: Add schema initialization and FastAPI endpoints**

Import `init_demand_intelligence`, `classify_demand`, `forecast_demand`, and `calculate_replenishment` into `app.py`. Call schema initialization from startup. Use an UPSERT for history keyed on `(sku, period_start)`. Analysis must load ordered history, persist the full recommendation record, and return it.

- [ ] **Step 4: Run contract tests and all existing tests**

Run: `pytest -v`
Expected: existing procurement tests plus new demand tests PASS.

- [ ] **Step 5: Commit**

```bash
git add demand_intelligence.py app.py tests/test_demand_api_contract.py
git commit -m "feat: expose demand intelligence API"
```

### Task 5: NEXUS replenishment event and capability advertisement

**Files:**
- Modify: `app.py`
- Modify: `tests/test_demand_api_contract.py`
- Modify: `tests/test_procure_integration_flow.py`

**Interfaces:**
- Emits target: `UNG-VECTOR`
- Emits message type: `PROCURE.REPLENISHMENT.RECOMMENDED`
- `/v1/system` adds: `demand-classification`, `baseline-demand-forecasting`, `replenishment-recommendations`

- [ ] **Step 1: Add failing integration-contract assertions**

```python
def test_replenishment_event_contract():
    text = Path("app.py").read_text()
    assert "PROCURE.REPLENISHMENT.RECOMMENDED" in text
    assert "UNG-VECTOR" in text
    assert "demand-classification" in text
    assert "baseline-demand-forecasting" in text
    assert "replenishment-recommendations" in text
```

Extend `test_procure_integration_flow.py` to keep asserting the existing MIDAS/VECTOR purchase-order event types so the new event cannot replace them accidentally.

- [ ] **Step 2: Run integration contract tests and verify failure**

Run: `pytest tests/test_demand_api_contract.py tests/test_procure_integration_flow.py -v`
Expected: new assertions FAIL.

- [ ] **Step 3: Wire best-effort NEXUS delivery**

In `POST /v1/demand/analyze/{sku}`, only when `status == "reorder"`, call:

```python
integration = emit(
    "UNG-VECTOR",
    "PROCURE.REPLENISHMENT.RECOMMENDED",
    recommendation_payload,
)
```

Return `integration` in the response. Do not raise based solely on failed event delivery. Add the three exact capability strings to `/v1/system` and increment service version to `1.4.0` consistently in root, health, User-Agent, and system metadata.

- [ ] **Step 4: Run entire test suite**

Run: `pytest -v`
Expected: PASS with no regression in existing requisition, award, supplier, purchasing-document, or matching tests.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_demand_api_contract.py tests/test_procure_integration_flow.py
git commit -m "feat: publish replenishment recommendations"
```

### Task 6: Verification and readiness evidence

**Files:**
- Modify: `docs/superpowers/specs/2026-09-13-demand-intelligence-design.md` only if verification reveals a design correction.

**Interfaces:**
- No new runtime interface; produces verification evidence for review.

- [ ] **Step 1: Run syntax compilation**

Run: `python -m py_compile app.py demand_intelligence.py supplier_profiles.py purchasing_documents.py procure_matching.py procure_kpis.py inbound_layers.py`
Expected: exit code 0.

- [ ] **Step 2: Run full tests**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 3: Inspect diff for unsupported claims**

Run: `git diff main...HEAD -- app.py demand_intelligence.py tests/ docs/`
Expected: no advertised capability for algorithms or systems not implemented in this plan.

- [ ] **Step 4: Confirm existing procurement event contracts remain**

Run: `grep -n "PROCURE.PURCHASE_ORDER.AWARDED\|PROCURE.PURCHASE_ORDER.RECEIVING_EXPECTED\|PROCURE.REPLENISHMENT.RECOMMENDED" app.py`
Expected: all three event types present.

- [ ] **Step 5: Commit any verification-only corrections if required**

```bash
git add app.py demand_intelligence.py tests docs
git commit -m "test: finalize demand intelligence acceptance"
```

If no corrections were required, do not create an empty commit.

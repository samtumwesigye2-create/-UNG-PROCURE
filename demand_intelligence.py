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
    if not values:
        return 0.0
    avg = mean(values)
    if avg == 0:
        return 0.0
    return float((pstdev(values) / avg) ** 2)


def classify_demand(history):
    adi = calculate_adi(history)
    cv2 = calculate_cv2(history)
    if adi == 0.0 or (adi < ADI_THRESHOLD and cv2 < CV2_THRESHOLD):
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
    return sum(abs(float(a) - float(p)) for a, p in zip(actual, predicted)) / len(actual)


def mape(actual, predicted):
    if not actual or len(actual) != len(predicted):
        return None
    pairs=[(float(a),float(p)) for a,p in zip(actual,predicted) if float(a)!=0]
    if not pairs:
        return 0.0
    return sum(abs((a-p)/a) for a,p in pairs)/len(pairs)


def wmape(actual, predicted):
    return wape(actual, predicted)


def forecast_bias(actual, predicted):
    if not actual or len(actual) != len(predicted):
        return None
    denominator=sum(abs(float(a)) for a in actual)
    if denominator == 0:
        return 0.0
    return sum(float(p)-float(a) for a,p in zip(actual,predicted))/denominator


def wape(actual, predicted):
    if not actual or len(actual) != len(predicted):
        return None
    denominator = sum(abs(float(a)) for a in actual)
    if denominator == 0:
        return 0.0
    return sum(abs(float(a) - float(p)) for a, p in zip(actual, predicted)) / denominator


def _ses(history, alpha=0.2):
    values = _clean(history)
    if not values:
        return 0.0
    level = values[0]
    for value in values[1:]:
        level = alpha * value + (1 - alpha) * level
    return max(0.0, float(level))


def _weighted_moving_average(history):
    values = _clean(history)
    if not values:
        return 0.0
    tail = values[-3:]
    weights = [0.2, 0.3, 0.5][-len(tail):]
    total_weight = sum(weights)
    return max(0.0, sum(v * w for v, w in zip(tail, weights)) / total_weight)


def _croston(history, alpha=0.2):
    values = _clean(history)
    nonzero = [(i, value) for i, value in enumerate(values) if value > 0]
    if not nonzero:
        return 0.0
    z = nonzero[0][1]
    p = float(nonzero[0][0] + 1)
    last_index = nonzero[0][0]
    for index, value in nonzero[1:]:
        interval = index - last_index
        z = z + alpha * (value - z)
        p = p + alpha * (interval - p)
        last_index = index
    return max(0.0, z / p if p > 0 else 0.0)


def _sba_croston(history, alpha=0.2):
    return max(0.0, (1 - alpha / 2) * _croston(history, alpha))


def _rolling_predictions(history, model_name):
    values = _clean(history)
    if len(values) < 3:
        return [], []
    actual = []
    predicted = []
    for i in range(2, len(values)):
        prior = values[:i]
        if model_name == "simple_exponential_smoothing":
            pred = _ses(prior)
        elif model_name == "weighted_moving_average":
            pred = _weighted_moving_average(prior)
        elif model_name == "croston":
            pred = _croston(prior)
        else:
            pred = _sba_croston(prior)
        actual.append(values[i])
        predicted.append(pred)
    return actual, predicted


def forecast_demand(history, demand_class=None):
    values = _clean(history)
    if demand_class is None:
        demand_class = classify_demand(values)["demand_class"]
    model_map = {
        "smooth": ("simple_exponential_smoothing", _ses),
        "erratic": ("weighted_moving_average", _weighted_moving_average),
        "lumpy": ("croston", _croston),
        "intermittent": ("sba_croston", _sba_croston),
    }
    if demand_class not in model_map:
        raise ValueError("unsupported_demand_class")
    model_name, model = model_map[demand_class]
    forecast = model(values)
    actual, predicted = _rolling_predictions(values, model_name)
    return {
        "forecast_quantity": float(forecast),
        "forecast_model": model_name,
        "mae": mae(actual, predicted),
        "wape": wape(actual, predicted),
        "mape": mape(actual, predicted),
        "wmape": wmape(actual, predicted),
        "forecast_bias": forecast_bias(actual, predicted),
    }


def calculate_replenishment(
    history,
    forecast_quantity,
    on_hand,
    inbound,
    allocated,
    supplier_lead_time_days,
    review_period_days=30.0,
    service_level_factor=1.65,
):
    values = _clean(history)
    forecast_quantity = float(forecast_quantity)
    on_hand = float(on_hand)
    inbound = float(inbound)
    allocated = float(allocated)
    lead_days = float(supplier_lead_time_days)
    review_days = float(review_period_days)
    service_factor = float(service_level_factor)
    if any(v < 0 for v in [forecast_quantity, on_hand, inbound, allocated, lead_days, service_factor]):
        raise ValueError("replenishment_inputs_must_be_non_negative")
    if review_days <= 0:
        raise ValueError("review_period_days_must_be_positive")
    available = on_hand + inbound - allocated
    lead_ratio = lead_days / review_days
    lead_time_demand = forecast_quantity * lead_ratio
    demand_stddev = pstdev(values) if len(values) > 1 else 0.0
    safety_stock = service_factor * demand_stddev * sqrt(max(lead_ratio, 0.0))
    reorder_point = lead_time_demand + safety_stock
    status = "reorder" if available <= reorder_point else "no_reorder"
    target = reorder_point + forecast_quantity
    recommended = max(0.0, target - available) if status == "reorder" else 0.0
    if available < lead_time_demand:
        risk = "high"
    elif available <= reorder_point:
        risk = "medium"
    else:
        risk = "low"
    return {
        "available": float(available),
        "lead_time_demand": float(lead_time_demand),
        "safety_stock": float(safety_stock),
        "reorder_point": float(reorder_point),
        "recommended_order_quantity": float(recommended),
        "stockout_risk": risk,
        "status": status,
    }


def init_demand_intelligence(conn_factory):
    with conn_factory() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS demand_history(
                id UUID PRIMARY KEY,
                sku TEXT NOT NULL,
                period_start TIMESTAMPTZ NOT NULL,
                quantity DOUBLE PRECISION NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE(sku,period_start)
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS demand_recommendations(
                id UUID PRIMARY KEY,
                sku TEXT NOT NULL,
                demand_class TEXT NOT NULL,
                adi DOUBLE PRECISION NOT NULL,
                cv2 DOUBLE PRECISION NOT NULL,
                forecast_quantity DOUBLE PRECISION NOT NULL,
                forecast_model TEXT NOT NULL,
                mae DOUBLE PRECISION,
                wape DOUBLE PRECISION,
                mape DOUBLE PRECISION,
                wmape DOUBLE PRECISION,
                forecast_bias DOUBLE PRECISION,
                on_hand DOUBLE PRECISION NOT NULL,
                inbound DOUBLE PRECISION NOT NULL,
                allocated DOUBLE PRECISION NOT NULL,
                supplier_lead_time_days DOUBLE PRECISION NOT NULL,
                safety_stock DOUBLE PRECISION NOT NULL,
                reorder_point DOUBLE PRECISION NOT NULL,
                recommended_order_quantity DOUBLE PRECISION NOT NULL,
                stockout_risk TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )"""
        )
        c.execute("ALTER TABLE demand_recommendations ADD COLUMN IF NOT EXISTS mape DOUBLE PRECISION")
        c.execute("ALTER TABLE demand_recommendations ADD COLUMN IF NOT EXISTS wmape DOUBLE PRECISION")
        c.execute("ALTER TABLE demand_recommendations ADD COLUMN IF NOT EXISTS forecast_bias DOUBLE PRECISION")

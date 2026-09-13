from demand_intelligence import calculate_adi, calculate_cv2, classify_demand, forecast_demand, calculate_replenishment


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

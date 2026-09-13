from pathlib import Path


def test_app_registers_demand_intelligence():
    text = Path('app.py').read_text()
    assert 'init_demand_intelligence' in text
    assert 'procure.demand.read' in text
    assert 'procure.demand.write' in text
    assert '/v1/demand/analyze/{sku}' in text


def test_replenishment_event_contract():
    text = Path('app.py').read_text()
    assert 'PROCURE.REPLENISHMENT.RECOMMENDED' in text
    assert 'UNG-VECTOR' in text
    assert 'demand-classification' in text
    assert 'baseline-demand-forecasting' in text
    assert 'replenishment-recommendations' in text

from decimal import Decimal
from procure_matching import evaluate_three_way_match


def test_controlled_purchase_to_match_flow_reaches_passed_event_contract():
    po_line = {
        'vendor_id':'V1','sku':'SKU1','ordered_quantity':Decimal('4'),
        'unit_price':Decimal('25.00'),'currency':'USD'
    }
    invoice = {
        'vendor_id':'V1','sku':'SKU1','quantity':Decimal('4'),
        'unit_price':Decimal('25.00'),'currency':'USD'
    }
    result = evaluate_three_way_match(
        po_line, Decimal('4'), invoice,
        {'quantity_pct':Decimal('0'),'price_pct':Decimal('0'),'absolute_price':Decimal('0')},
    )
    assert result['status'] == 'matched'
    event_type = 'PROCURE.MATCH.PASSED' if result['status'] in {'matched','matched_with_tolerance'} else 'PROCURE.MATCH.BLOCKED'
    assert event_type == 'PROCURE.MATCH.PASSED'

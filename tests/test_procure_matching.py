from decimal import Decimal
from procure_matching import allocate_receipt, evaluate_three_way_match


def test_allocate_receipt_caps_at_open_quantity():
    assert allocate_receipt(Decimal('10'), Decimal('6'), Decimal('7')) == Decimal('4')


def test_allocate_receipt_accepts_partial_quantity():
    assert allocate_receipt(Decimal('10'), Decimal('2'), Decimal('3')) == Decimal('3')


def test_exact_three_way_match_passes():
    result = evaluate_three_way_match(
        {'vendor_id':'V1','sku':'SKU1','ordered_quantity':Decimal('10'),'unit_price':Decimal('5.00'),'currency':'USD'},
        Decimal('10'),
        {'vendor_id':'V1','sku':'SKU1','quantity':Decimal('10'),'unit_price':Decimal('5.00'),'currency':'USD'},
        {'quantity_pct':Decimal('0'),'price_pct':Decimal('0'),'absolute_price':Decimal('0')},
    )
    assert result['status'] == 'matched'


def test_price_variance_blocks():
    result = evaluate_three_way_match(
        {'vendor_id':'V1','sku':'SKU1','ordered_quantity':Decimal('10'),'unit_price':Decimal('5.00'),'currency':'USD'},
        Decimal('10'),
        {'vendor_id':'V1','sku':'SKU1','quantity':Decimal('10'),'unit_price':Decimal('6.00'),'currency':'USD'},
        {'quantity_pct':Decimal('0'),'price_pct':Decimal('1'),'absolute_price':Decimal('0')},
    )
    assert result['status'] == 'blocked_price_variance'


def test_within_tolerance_passes_with_tolerance():
    result = evaluate_three_way_match(
        {'vendor_id':'V1','sku':'SKU1','ordered_quantity':Decimal('10'),'unit_price':Decimal('5.00'),'currency':'USD'},
        Decimal('10'),
        {'vendor_id':'V1','sku':'SKU1','quantity':Decimal('10.1'),'unit_price':Decimal('5.04'),'currency':'USD'},
        {'quantity_pct':Decimal('2'),'price_pct':Decimal('2'),'absolute_price':Decimal('0.10')},
    )
    assert result['status'] == 'matched_with_tolerance'

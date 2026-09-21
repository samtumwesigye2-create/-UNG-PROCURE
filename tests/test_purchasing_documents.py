from decimal import Decimal
import pytest
from purchasing_documents import validate_positive_decimal, rank_quotes


def test_rejects_zero_quantity():
    with pytest.raises(ValueError, match='quantity_must_be_positive'):
        validate_positive_decimal(Decimal('0'), 'quantity')


def test_accepts_positive_unit_price():
    assert validate_positive_decimal(Decimal('12.50'), 'unit_price') is True


def test_quote_ranking_price_then_lead_time():
    quotes = [
        {'unit_price': Decimal('11'), 'lead_time_days': 1},
        {'unit_price': Decimal('10'), 'lead_time_days': 5},
        {'unit_price': Decimal('10'), 'lead_time_days': 2},
    ]
    ranked = rank_quotes(quotes)
    assert [(q['unit_price'], q['lead_time_days']) for q in ranked] == [
        (Decimal('10'), 2), (Decimal('10'), 5), (Decimal('11'), 1)
    ]


def test_acceptance_budget_math_is_120_with_30_remaining():
    ceiling=Decimal('150.00')
    materials=Decimal('106.00')
    shipping=Decimal('14.00')
    total=materials+shipping
    assert total==Decimal('120.00')
    assert ceiling-total==Decimal('30.00')


def test_quote_ranking_prefers_lower_price_before_lead_time():
    quotes=[
        {'unit_price':Decimal('2.50'),'lead_time_days':1},
        {'unit_price':Decimal('1.50'),'lead_time_days':7},
    ]
    assert rank_quotes(quotes)[0]['unit_price']==Decimal('1.50')

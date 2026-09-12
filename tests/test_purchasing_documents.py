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

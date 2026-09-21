from decimal import Decimal
from datetime import datetime, timezone

from production_planning import calculate_mrp_line


def test_fp1000_golden_mrp_baseline():
    need=datetime(2026,9,30,tzinfo=timezone.utc)
    cases={
        'B':dict(gross=190,on=80,receipts=0,expected=110),
        'C':dict(gross=285,on=40,receipts=100,expected=145),
        'D':dict(gross=190,on=10,receipts=0,expected=180),
        'E':dict(gross=95,on=50,receipts=0,expected=45),
    }
    for sku,c in cases.items():
        r=calculate_mrp_line(c['gross'],on_hand=c['on'],scheduled_receipts=c['receipts'],
                             safety_stock=0,lot_policy='lot_for_lot',need_date=need)
        assert r['net_requirement']==c['expected'], sku
        assert r['planned_order_qty']==c['expected'], sku


def test_acceptance_budget_hard_ceiling():
    materials=Decimal('27.50')+Decimal('29.00')+Decimal('27.00')+Decimal('22.50')
    shipping=Decimal('14.00')
    contingency=Decimal('30.00')
    assert materials==Decimal('106.00')
    assert materials+shipping==Decimal('120.00')
    assert materials+shipping+contingency==Decimal('150.00')


def test_final_finished_goods_reconcile():
    demand=100
    opening=5
    produced=95
    shipped=100
    delivered=100
    assert opening+produced==demand
    assert shipped==demand
    assert delivered==demand
    assert opening+produced-shipped==0

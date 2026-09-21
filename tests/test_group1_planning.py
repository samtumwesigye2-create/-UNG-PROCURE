import pytest
from datetime import datetime, timezone

from production_planning import calculate_mrp_line, material_planning_defaults


def test_mrp_lot_for_lot_matches_net_requirement():
    d=datetime(2026,9,30,tzinfo=timezone.utc)
    r=calculate_mrp_line(285,on_hand=40,scheduled_receipts=100,safety_stock=0,
                         lot_policy='lot_for_lot',order_multiple=1,lead_time_days=7,need_date=d)
    assert r['net_requirement']==145
    assert r['planned_order_qty']==145
    assert r['release_date'].date().isoformat()=='2026-09-23'


def test_mrp_fixed_order_quantity_rounds_up():
    r=calculate_mrp_line(110,on_hand=0,scheduled_receipts=0,lot_policy='fixed',
                         fixed_order_qty=50,order_multiple=1)
    assert r['planned_order_qty']==150


def test_mrp_minimum_and_order_multiple_are_enforced():
    r=calculate_mrp_line(7,on_hand=0,scheduled_receipts=0,lot_policy='minimum',
                         min_order_qty=10,order_multiple=6)
    assert r['planned_order_qty']==12


def test_mrp_no_shortage_produces_no_order():
    r=calculate_mrp_line(50,on_hand=70,scheduled_receipts=0,safety_stock=0)
    assert r['net_requirement']==0
    assert r['planned_order_qty']==0


def test_mrp_rejects_bad_policy_and_multiple():
    with pytest.raises(ValueError,match='unsupported_lot_policy'):
        calculate_mrp_line(10,lot_policy='weird')
    with pytest.raises(ValueError,match='order_multiple_must_be_positive'):
        calculate_mrp_line(10,order_multiple=0)


def test_vector_material_master_defaults_are_applied():
    master={
        'planning':{
            'mrp_policy':'minimum',
            'safety_stock':5,
            'min_order_qty':10,
            'order_multiple':6,
            'lead_time_days':4,
            'make_buy':'buy',
        },
        'sources':[
            {'supplier_id':'SUP-A','approved':True},
            {'supplier_id':'SUP-B','approved':False},
        ],
    }
    d=material_planning_defaults(master)
    assert d['lot_policy']=='minimum'
    assert d['safety_stock']==5
    assert d['min_order_qty']==10
    assert d['order_multiple']==6
    assert d['lead_time_days']==4
    assert d['make_buy']=='buy'
    assert [x['supplier_id'] for x in d['approved_sources']]==['SUP-A']


def test_vector_unknown_policy_falls_back_to_lot_for_lot():
    d=material_planning_defaults({'planning':{'mrp_policy':'custom'}})
    assert d['lot_policy']=='lot_for_lot'
